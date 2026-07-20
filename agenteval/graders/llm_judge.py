"""llm_judge 评分器：rubric 结构化 Judge（PLAN 第 8 节）。

- 四个维度各 0/1 + ≤50 字理由：结论与查询结果一致 / 口径假设说明 / 不编造数字 / 表达清晰；
- 只评答案文本质量，事实正确性交给 sql_result；
- 强制 JSON 输出，解析失败时以"只输出 JSON"重试修复一次；
- 未配置 Key 时跳过（passed=None, score=None, detail 注明 judge_unavailable），
  不判失败、不计入汇总。
"""

from __future__ import annotations

import json
import re

from agenteval import config as cfg
from agenteval.graders.base import GraderContext, Verdict
from agenteval.llm.client import LLMClient, LLMError

DIMENSIONS = [
    "结论与查询结果一致",
    "口径假设说明",
    "不编造数字",
    "表达清晰",
]

_PROMPT = """你是评测评委。请基于以下信息，按 rubric 给被测 Agent 的最终回答打分。

【用户问题】{question}
【数据基准日期】{as_of}
【Agent 的查询与结果摘要】{query_summary}
【Agent 最终回答】{answer}

rubric 四个维度（各 0 或 1 分，理由 ≤50 字）：
1. 结论与查询结果一致：回答中的数字/结论与其查询结果一致（无查询则该维度 0 分，除非本就不需要查询）；
2. 口径假设说明：说明了统计口径/时间窗口/假设，或对歧义做了澄清；
3. 不编造数字：没有无依据的精确数字、没有虚构事实；
4. 表达清晰：结构清楚、单位明确、易读。

只输出 JSON（不要输出其他内容）：
{{"dimensions": [{{"name": "...", "score": 0, "reason": "..."}}]}}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class JudgeUnavailable(Exception):
    """Judge 未配置或调用失败：跳过而非判失败。"""


def _parse_judge_json(text: str) -> dict:
    m = _JSON_RE.search(text or "")
    if not m:
        raise ValueError("Judge 输出不含 JSON")
    data = json.loads(m.group(0))
    dims = data.get("dimensions")
    if not isinstance(dims, list) or len(dims) != len(DIMENSIONS):
        raise ValueError("dimensions 结构不符")
    for d in dims:
        if d.get("score") not in (0, 1):
            raise ValueError("维度 score 必须为 0/1")
    return data


def _call_judge(client: LLMClient, prompt: str, max_tokens: int) -> dict:
    """调用 Judge 并解析 JSON；失败则用"只输出 JSON"重试修复一次。"""
    messages = [{"role": "user", "content": prompt}]
    resp = client.chat(messages, temperature=0.0, max_tokens=max_tokens)
    try:
        return _parse_judge_json(resp.content)
    except (ValueError, json.JSONDecodeError):
        retry = messages + [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": "格式错误。请只输出符合要求的 JSON，不要输出任何其他内容。"},
        ]
        resp2 = client.chat(retry, temperature=0.0, max_tokens=max_tokens)
        return _parse_judge_json(resp2.content)


def _query_summary(trace, limit: int = 800) -> str:
    parts = []
    for tc in trace.tool_calls:
        if tc.name == "run_sql":
            status = "成功" if tc.ok else f"失败({tc.error})"
            parts.append(f"SQL: {tc.arguments.get('sql', '')[:200]} -> {status}")
    return ("\n".join(parts) or "（无查询）")[:limit]


def grade(gctx: GraderContext) -> Verdict:
    case, trace = gctx.case, gctx.trace
    judge_cfg = cfg.get_llm_config("judge")
    if not judge_cfg.get("api_key"):
        return Verdict(None, None, [],
                       "judge_unavailable: 未配置 JUDGE_API_KEY/LLM_API_KEY，跳过文本质量评分")

    default = gctx.config.get("judge", {})
    try:
        client = LLMClient(api_key=judge_cfg["api_key"],
                           base_url=judge_cfg["base_url"],
                           model=judge_cfg["model"],
                           timeout_s=float(gctx.config.get("timeout_s", 60)),
                           max_retries=int(gctx.config.get("max_retries", 3)))
        prompt = _PROMPT.format(
            question=case.question, as_of=case.as_of,
            query_summary=_query_summary(trace),
            answer=(trace.final_answer or "")[:1200],
        )
        data = _call_judge(client, prompt, int(default.get("max_tokens", 800)))
    except (LLMError, ValueError, json.JSONDecodeError) as e:
        # Judge 自身失败不算被测对象失败（避免把网络问题记成 Agent 质量问题）
        return Verdict(None, None, [],
                       f"judge_unavailable: Judge 调用/解析失败，跳过: {type(e).__name__}: {e}")

    dims = data["dimensions"]
    score = sum(d["score"] for d in dims) / len(dims)
    pass_score = float(default.get("pass_score", 0.75))
    passed = score >= pass_score
    detail = " | ".join(f"{d.get('name', DIMENSIONS[i])}: {d['score']}（{d.get('reason', '')}）"
                        for i, d in enumerate(dims))
    return Verdict(passed, round(score, 4), [] if passed else ["E14"], detail)
