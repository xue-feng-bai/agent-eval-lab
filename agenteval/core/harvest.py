"""失败回流（PLAN 第 10 节）：失败 trial -> 回归用例草稿。

`cli harvest <run_id>` 把失败 trial 生成 draft:true 的用例草稿追加到
datasets/regression.jsonl（幂等：同 run 同 case 不重复追加）。
草稿只含观察行为与 TODO 注释，人工补真值（reference_sql/expect）并移除
draft 标记后才转正；lint-dataset 对未处理草稿给出警告。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agenteval.core.dataset import load_case_file
from agenteval.core.harness import load_run
from agenteval.graders.base import REASON_CODES


def _next_draft_id(existing_ids: set[str]) -> str:
    """reg-9xx 号段为 harvest 草稿专用，避免与手工回归用例冲突。"""
    n = 901
    while f"reg-{n}" in existing_ids:
        n += 1
    return f"reg-{n}"


def harvest(run_dir: str | Path, regression_file: str | Path) -> list[dict]:
    """生成草稿并追加到 regression.jsonl，返回本次新增的草稿列表。"""
    run_dir = Path(run_dir)
    regression_file = Path(regression_file)
    meta, traces, _summary = load_run(run_dir)
    run_id = meta.get("run_id", run_dir.name)

    existing = load_case_file(regression_file)
    existing_ids = {c.id for c in existing}
    # 幂等键：notes 中的 (run_id, case_id) 组合
    existing_keys = set()
    for line in regression_file.read_text(encoding="utf-8").splitlines():
        m = re.search(r'"harvest_key":\s*"([^"]+)"', line)
        if m:
            existing_keys.add(m.group(1))

    # 每个 case 只取第一个失败 trial 做代表
    seen_cases: set[str] = set()
    drafts: list[dict] = []
    for t in traces:
        if t.passed or t.case_id in seen_cases:
            continue
        seen_cases.add(t.case_id)
        key = f"{run_id}:{t.case_id}"
        if key in existing_keys:
            continue

        codes: list[str] = []
        for v in t.verdicts.values():
            codes.extend(v.get("reason_codes", []))
        if t.infra_error:
            codes.append("E15")
        codes = sorted(set(codes))
        code_text = "、".join(f"{c}({REASON_CODES.get(c, '?')})" for c in codes) or "未知"

        question = next((m.get("content", "") for m in t.messages
                         if m.get("role") == "user"), "")
        answer = (t.final_answer or "")[:300]

        draft_id = _next_draft_id(existing_ids | {d["id"] for d in drafts})
        draft = {
            "id": draft_id,
            "split": "regression",
            "question": question,
            "as_of": "2026-06-30",
            "difficulty": "medium",
            "tags": ["回归"],
            "expect": {"kind": "sql_answer"},
            "graders": ["rules", "sql_result", "llm_judge"],
            "draft": True,
            "harvest_key": key,
            "notes": (f"TODO(harvest)：来自 run {run_id} 的失败样本（trial#{t.trial_index}），"
                      f"失败分类: {code_text}。\n观察行为: {answer or '（无回答）'}\n"
                      "请人工核对正确口径，补充 reference_sql 与 expect.result 后，"
                      "删除 draft/harvest_key 标记转正。"),
        }
        drafts.append(draft)

    if drafts:
        with regression_file.open("a", encoding="utf-8") as f:
            for d in drafts:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return drafts
