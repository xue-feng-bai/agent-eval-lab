"""Run 注册表（PLAN 第 10 节）：runs/index.sqlite 登记与历史对比。

- register_run：run 结束后登记关键指标；
- list_runs：`cli list` 表格输出；
- diff_runs：`cli diff A B` 指标 delta + 逐 case 通过状态翻转清单。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agenteval.core.harness import load_run

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    created_at     TEXT,
    target         TEXT,
    model          TEXT,
    prompt_version TEXT,
    suites         TEXT,
    trials         INTEGER,
    dataset_hash   TEXT,
    git_commit     TEXT,
    pass_at_1      REAL,
    pass_at_k      REAL,
    infra_error_rate REAL,
    total_cost_usd REAL,
    metrics_json   TEXT
)
"""


def _connect(index_path: str | Path) -> sqlite3.Connection:
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path))
    conn.execute(_SCHEMA)
    return conn


def register_run(index_path: str | Path, run_id: str, meta: dict, summary: dict) -> None:
    overall = summary["overall"]
    with _connect(index_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, meta.get("created_at"), meta.get("target"), meta.get("model"),
             meta.get("prompt_version"), ",".join(meta.get("suites", [])),
             int(meta.get("trials", 1)), meta.get("dataset_hash"), meta.get("git_commit"),
             overall["pass_at_1"], overall["pass_at_k"], overall["infra_error_rate"],
             overall["total_cost_usd"],
             json.dumps({"overall": overall, "by_split": summary["by_split"],
                         "reason_codes": summary["reason_codes"]},
                        ensure_ascii=False)),
        )


def list_runs(index_path: str | Path) -> list[dict]:
    index_path = Path(index_path)
    if not index_path.exists():
        return []
    with _connect(index_path) as conn:
        rows = conn.execute(
            "SELECT run_id, created_at, target, model, prompt_version, suites, trials, "
            "pass_at_1, pass_at_k, infra_error_rate, total_cost_usd "
            "FROM runs ORDER BY created_at DESC").fetchall()
    cols = ["run_id", "created_at", "target", "model", "prompt_version", "suites",
            "trials", "pass_at_1", "pass_at_k", "infra_error_rate", "total_cost_usd"]
    return [dict(zip(cols, r)) for r in rows]


def get_run(index_path: str | Path, run_id: str) -> dict | None:
    with _connect(index_path) as conn:
        row = conn.execute("SELECT metrics_json FROM runs WHERE run_id = ?",
                           (run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _case_pass_map(runs_root: Path, run_id: str) -> dict[str, bool]:
    """从 trials.jsonl 还原逐 case 通过状态（全部 trial 通过才算过）。"""
    _meta, traces, _summary = load_run(runs_root / run_id)
    m: dict[str, list[bool]] = {}
    for t in traces:
        m.setdefault(t.case_id, []).append(t.passed)
    return {cid: all(v) for cid, v in m.items()}


def diff_runs(runs_root: str | Path, run_a: str, run_b: str) -> dict:
    """对比两个 run：指标 delta + 逐 case 翻转清单（pass->fail 重点标注）。"""
    runs_root = Path(runs_root)
    _ma, _ta, sa = load_run(runs_root / run_a)
    _mb, _tb, sb = load_run(runs_root / run_b)

    splits = sorted(set(sa["by_split"]) | set(sb["by_split"]))
    delta_rows = []
    for split in ["overall"] + splits:
        ga = sa["overall"] if split == "overall" else sa["by_split"].get(split)
        gb = sb["overall"] if split == "overall" else sb["by_split"].get(split)
        pa = ga["pass_at_1"] if ga else None
        pb = gb["pass_at_1"] if gb else None
        delta = None if (pa is None or pb is None) else round((pb - pa) * 100, 2)
        delta_rows.append({"split": split, "pass_at_1_a": pa, "pass_at_1_b": pb,
                           "delta_pp": delta})

    pass_a = _case_pass_map(runs_root, run_a)
    pass_b = _case_pass_map(runs_root, run_b)
    flips = []
    for case_id in sorted(set(pass_a) | set(pass_b)):
        a, b = pass_a.get(case_id), pass_b.get(case_id)
        if a is None or b is None or a == b:
            continue
        flips.append({"case_id": case_id,
                      "change": "pass→fail" if a and not b else "fail→pass",
                      "regression": bool(a and not b)})
    return {"run_a": run_a, "run_b": run_b, "metric_deltas": delta_rows,
            "flips": flips,
            "regression_count": sum(1 for f in flips if f["regression"])}
