"""human 评分器：Judge 人工校准（PLAN 第 8/10 节）。

流程：export-labels 导出抽样 CSV -> 人工标注 -> calibrate 计算
Judge 与人工的一致率、Cohen's Kappa、分维度混淆矩阵。

CSV 列：
- 导出侧自动填：case_id/trial_index/split/question/answer_excerpt/
  judge_score/judge_pass/judge_dim1..4（从 Judge detail 解析的各维度 0/1）；
- 人工侧填写：human_label（整体 0/1，必填）；dim1..dim4（各维度 0/1，选填，
  填写后 calibrate 额外输出分维度混淆矩阵）。
"""

from __future__ import annotations

import csv
import random
import re
from pathlib import Path

from agenteval.core.trace import load_traces_jsonl
from agenteval.graders.llm_judge import DIMENSIONS

LABEL_FIELDS = ["case_id", "trial_index", "split", "question", "answer_excerpt",
                "judge_score", "judge_pass",
                "judge_dim1", "judge_dim2", "judge_dim3", "judge_dim4",
                "human_label", "dim1", "dim2", "dim3", "dim4"]

# 解析 llm_judge detail："维度名: 1（理由） | 维度名: 0（理由）"
_DIM_RE = re.compile(r"([^|：]+):\s*([01])（")


def _parse_judge_dims(detail: str) -> list[str]:
    """从 Judge detail 文本解析四个维度的 0/1；解析失败返回空串占位。"""
    scores = [s for _name, s in _DIM_RE.findall(detail or "")]
    if len(scores) == len(DIMENSIONS):
        return scores
    return [""] * len(DIMENSIONS)


def export_labels(run_dir: str | Path, out_csv: str | Path,
                  sample_ratio: float = 0.3, seed: int = 42) -> int:
    """从 run 的 trials.jsonl 抽样导出待标注 CSV，返回抽样条数。

    只导出 Judge 实际给出了分数的 trial（judge 被跳过的无法校准）。
    """
    run_dir = Path(run_dir)
    traces = load_traces_jsonl(run_dir / "trials.jsonl")
    rng = random.Random(seed)
    rows = []
    for t in traces:
        judge = t.verdicts.get("llm_judge") or {}
        if judge.get("score") is None:
            continue
        if rng.random() > sample_ratio:
            continue
        dim_scores = _parse_judge_dims(judge.get("detail", ""))
        rows.append({
            "case_id": t.case_id,
            "trial_index": t.trial_index,
            "split": t.split,
            "question": _question_of(t),
            "answer_excerpt": (t.final_answer or "").replace("\n", " ")[:200],
            "judge_score": judge.get("score"),
            "judge_pass": int(bool(judge.get("passed"))),
            "judge_dim1": dim_scores[0], "judge_dim2": dim_scores[1],
            "judge_dim3": dim_scores[2], "judge_dim4": dim_scores[3],
            "human_label": "",
            "dim1": "", "dim2": "", "dim3": "", "dim4": "",
        })
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LABEL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _question_of(trace) -> str:
    for m in trace.messages:
        if m.get("role") == "user":
            return str(m.get("content", ""))[:200]
    return ""


def _kappa(po: float, pe: float) -> float:
    """Cohen's Kappa：po 观察一致率，pe 期望一致率。"""
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1 - pe)


def _pair_stats(pairs: list[tuple[int, int]]) -> dict:
    """pairs = [(judge01, human01)]，返回一致率 / Kappa / 混淆矩阵。"""
    n = len(pairs)
    if n == 0:
        return {"n": 0, "agreement": None, "kappa": None,
                "confusion": {"tp": 0, "fp": 0, "fn": 0, "tn": 0}}
    tp = sum(1 for j, h in pairs if j == 1 and h == 1)
    fp = sum(1 for j, h in pairs if j == 1 and h == 0)
    fn = sum(1 for j, h in pairs if j == 0 and h == 1)
    tn = sum(1 for j, h in pairs if j == 0 and h == 0)
    po = (tp + tn) / n
    pj = (tp + fp) / n  # Judge 判过率
    ph = (tp + fn) / n  # 人工判过率
    pe = pj * ph + (1 - pj) * (1 - ph)
    return {"n": n, "agreement": round(po, 4), "kappa": round(_kappa(po, pe), 4),
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}}


def calibrate(labels_csv: str | Path) -> dict:
    """读取已标注 CSV，计算整体与（可选）分维度的 Judge vs 人工校准指标。"""
    labels_csv = Path(labels_csv)
    with labels_csv.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    pairs: list[tuple[int, int]] = []
    dim_pairs: dict[str, list[tuple[int, int]]] = {d: [] for d in DIMENSIONS}
    skipped = 0
    for r in rows:
        label = (r.get("human_label") or "").strip()
        if label not in ("0", "1"):
            skipped += 1
            continue
        human = int(label)
        judge = int(float(r.get("judge_pass") or 0))
        pairs.append((judge, human))
        for i, dim in enumerate(DIMENSIONS, start=1):
            dv = (r.get(f"dim{i}") or "").strip()
            jv = (r.get(f"judge_dim{i}") or "").strip()
            if dv in ("0", "1") and jv in ("0", "1"):
                dim_pairs[dim].append((int(jv), int(dv)))

    result = {"file": str(labels_csv), "skipped_unlabeled": skipped,
              "overall": _pair_stats(pairs)}
    labeled_dims = {d: p for d, p in dim_pairs.items() if p}
    if labeled_dims:
        result["per_dimension"] = {d: _pair_stats(p) for d, p in labeled_dims.items()}
    return result
