"""指标聚合（PLAN 第 9 节）：pass@1 / pass^k / Wilson CI / 成本延迟 / 失败分布。

聚合层级：overall、by split、by tag。
只统计"有判定"的 trial；judge 被跳过（passed=None）不影响 case 通过判定
（case 通过 = 所有非跳过 grader 通过，rules 一票否决）。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from agenteval.core.trace import Trace


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% 置信区间（小样本下比正态近似稳健）。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _pass_at_1(passed: list[bool]) -> float:
    return sum(passed) / len(passed) if passed else 0.0


def _pass_at_k(case_pass: dict[str, list[bool]]) -> float:
    """pass^k：全部 k 个 trial 都通过的 case 占比。"""
    if not case_pass:
        return 0.0
    full = sum(1 for results in case_pass.values() if results and all(results))
    return full / len(case_pass)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, math.ceil(pct / 100 * len(values)) - 1))
    return values[idx]


def _group_summary(traces: list[Trace], trials_per_case: int) -> dict:
    passed_list = [t.passed for t in traces]
    n = len(passed_list)
    k = sum(passed_list)
    lo, hi = wilson_ci(k, n)

    case_pass: dict[str, list[bool]] = defaultdict(list)
    for t in traces:
        case_pass[t.case_id].append(t.passed)

    tool_calls = [tc for t in traces for tc in t.tool_calls]
    tool_errors = sum(1 for tc in tool_calls if not tc.ok)
    durations = [t.duration_ms for t in traces]

    return {
        "n_trials": n,
        "n_cases": len(case_pass),
        "pass_at_1": round(_pass_at_1(passed_list), 4),
        "pass_at_1_ci": [round(lo, 4), round(hi, 4)],
        "pass_at_k": round(_pass_at_k(case_pass), 4),
        "trials_per_case": trials_per_case,
        "infra_error_rate": round(
            sum(1 for t in traces if t.infra_error) / n, 4) if n else 0.0,
        "tool_error_rate": round(tool_errors / len(tool_calls), 4) if tool_calls else 0.0,
        "avg_steps": round(sum(t.steps for t in traces) / n, 2) if n else 0.0,
        "latency_ms": {
            "avg": round(sum(durations) / n, 1) if n else 0.0,
            "p50": round(_percentile(durations, 50), 1),
            "p95": round(_percentile(durations, 95), 1),
        },
        "total_tokens": sum(t.usage.get("total_tokens", 0) for t in traces),
        "total_cost_usd": round(sum(t.usage.get("cost_usd", 0.0) for t in traces), 6),
    }


def reason_code_distribution(traces: list[Trace]) -> dict[str, int]:
    """失败 trial 的 reason codes 计数（一个 trial 可含多个 code）。"""
    counter: Counter = Counter()
    for t in traces:
        if t.passed:
            continue
        if t.infra_error:
            counter["E15"] += 1
        for verdict in t.verdicts.values():
            for code in verdict.get("reason_codes", []):
                counter[code] += 1
    return dict(counter.most_common())


def summarize(traces: list[Trace], trials_per_case: int) -> dict:
    """三层聚合：overall / by_split / by_tag + 失败分布 + 逐 case 明细。"""
    summary = {
        "overall": _group_summary(traces, trials_per_case),
        "by_split": {},
        "by_tag": {},
        "reason_codes": reason_code_distribution(traces),
        "cases": [],
    }

    by_split: dict[str, list[Trace]] = defaultdict(list)
    for t in traces:
        by_split[t.split].append(t)
    for split, group in sorted(by_split.items()):
        summary["by_split"][split] = _group_summary(group, trials_per_case)

    by_tag: dict[str, list[Trace]] = defaultdict(list)
    for t in traces:
        for tag in (t.env_state.get("case_tags") or []):
            by_tag[tag].append(t)
    for tag, group in sorted(by_tag.items()):
        summary["by_tag"][tag] = _group_summary(group, trials_per_case)

    case_map: dict[str, list[Trace]] = defaultdict(list)
    for t in traces:
        case_map[t.case_id].append(t)
    for case_id, group in sorted(case_map.items()):
        codes: Counter = Counter()
        for t in group:
            if t.passed:
                continue
            for verdict in t.verdicts.values():
                for code in verdict.get("reason_codes", []):
                    codes[code] += 1
            if t.infra_error:
                codes["E15"] += 1
        passed_n = sum(1 for t in group if t.passed)
        summary["cases"].append({
            "case_id": case_id,
            "split": group[0].split,
            "passed_trials": passed_n,
            "total_trials": len(group),
            "pass": passed_n == len(group),
            "reason_codes": [c for c, _ in codes.most_common()],
        })
    return summary
