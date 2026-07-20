"""Harness 运行器（PLAN 第 4/10 节）：Case × k trials、环境隔离、全量 Trace。

流程：加载 suite -> 每 Case k 个独立 trial（独立 DB 副本）-> Target.run ->
组装 Trace -> 逐个运行 case.graders -> 写 runs/<run_id>/{meta.json, trials.jsonl}。
任何单 trial 异常都不中断整个 run（记为 E15 基础设施错误）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from agenteval.core import metrics
from agenteval.core.dataset import Case
from agenteval.core.trace import Trace, append_trace_jsonl, load_traces_jsonl
from agenteval.graders.base import GraderContext
from agenteval.sandbox.db import make_trial_copy
from agenteval.targets.base import RunContext, Target

# grader 名 -> grade 函数（human 不自动运行，仅 calibrate 时离线使用）
def _grader_registry() -> dict:
    from agenteval.graders import llm_judge, rules, sql_result
    return {"rules": rules.grade, "sql_result": sql_result.grade,
            "llm_judge": llm_judge.grade}


def dataset_hash(datasets_dir: str | Path) -> str:
    """数据集内容指纹（五个 JSONL 拼接的 sha256 前 12 位）。"""
    h = hashlib.sha256()
    for path in sorted(Path(datasets_dir).glob("*.jsonl")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _git_commit(root: Path) -> str | None:
    """尽力而为取 git commit；非 git 目录返回 None（不报错）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def generate_run_id(target_name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = target_name.replace(":", "-").replace("/", "-")
    return f"{ts}-{safe}"


def run_evaluation(cases: list[Case], target: Target, *, master_db: Path,
                   run_dir: Path, trials: int, config: dict,
                   meta: dict, on_progress=None) -> dict:
    """执行一次完整评测，写盘并返回 summary。

    meta：配置快照（target/model/prompt 版本/数据 hash/时间/git 等）。
    on_progress：可选回调(case_id, trial_index, passed)，供 CLI 打点。
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    trials_path = run_dir / "trials.jsonl"
    if trials_path.exists():
        trials_path.unlink()

    graders = _grader_registry()
    traces: list[Trace] = []

    for case in cases:
        for t_idx in range(trials):
            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            trial_db = make_trial_copy(master_db)
            trace = Trace(case_id=case.id, split=case.split, trial_index=t_idx,
                          target=target.name, model=meta.get("model"),
                          prompt_version=meta.get("prompt_version"),
                          started_at=started.isoformat(timespec="seconds"))
            trace.env_state["case_tags"] = list(case.tags)
            try:
                ctx = RunContext(case=case, trial_index=t_idx, db_path=trial_db,
                                 config=config, model=meta.get("model"),
                                 prompt_version=meta.get("prompt_version"))
                result = target.run({"question": case.question, "as_of": case.as_of}, ctx)
                trace.messages = result.messages
                trace.tool_calls = result.tool_calls
                trace.final_answer = result.final_answer
                trace.usage = result.usage
                trace.steps = result.steps
                trace.env_state.update(result.env_state)
            except Exception as e:  # noqa: BLE001 - 单 trial 崩溃不中断 run
                trace.infra_error = f"{type(e).__name__}: {e}"
                trace.messages = [{"role": "user", "content": case.question}]
                trace.env_state["traceback"] = traceback.format_exc()[-1500:]
            trace.duration_ms = (time.perf_counter() - t0) * 1000

            # ---- 评分：case.graders 逐个执行；judge 跳过（None）不影响判定 ----
            verdicts: dict[str, dict] = {}
            gctx = GraderContext(case=case, trace=trace, db_path=trial_db, config=config)
            for gname in case.graders:
                fn = graders.get(gname)
                if fn is None:
                    continue  # human 等离线 grader 不在 run 内执行
                try:
                    verdict = fn(gctx)
                except Exception as e:  # noqa: BLE001 - grader 崩溃记 E15 但不中断
                    from agenteval.graders.base import Verdict
                    verdict = Verdict(False, 0.0, ["E15"],
                                      f"grader {gname} 自身异常: {type(e).__name__}: {e}")
                verdicts[gname] = verdict.to_dict()
            trace.verdicts = verdicts
            decisive = [v["passed"] for v in verdicts.values() if v["passed"] is not None]
            trace.passed = bool(decisive) and all(decisive)
            if trace.infra_error:
                trace.passed = False

            append_trace_jsonl(trials_path, trace)
            traces.append(trace)
            if on_progress:
                on_progress(case.id, t_idx, trace.passed)
            shutil.rmtree(trial_db.parent, ignore_errors=True)

    summary = metrics.summarize(traces, trials)

    meta_out = dict(meta)
    meta_out["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta_out["summary_overall"] = summary["overall"]
    (run_dir / "meta.json").write_text(
        json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def load_run(run_dir: str | Path) -> tuple[dict, list[Trace], dict]:
    """读取一个 run：返回 (meta, traces, summary)；summary 缺失时现算。"""
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    traces = load_traces_jsonl(run_dir / "trials.jsonl")
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        trials = meta.get("trials") or 1
        summary = metrics.summarize(traces, int(trials))
    return meta, traces, summary
