"""发布门禁（PLAN 第 11 节）：绝对阈值 + 相对基线回归检查。

- absolute：如 safety.pass_at_1 >= 1.0、overall.infra_error_rate <= 0.05；
- vs_baseline：与基线 run 对比，指定 split 的 pass@1 下跌超过 max_drop_pp 即拦截；
- 门禁指标在 run 中缺失时按**不通过**处理（严格原则：测不到 = 不达标）。
"""

from __future__ import annotations

from agenteval.core.harness import load_run


def _metric(summary: dict, dotted: str):
    """取 'safety.pass_at_1' / 'overall.infra_error_rate' 形式的指标值。"""
    scope, _, name = dotted.rpartition(".")
    if scope == "overall":
        return summary["overall"].get(name)
    return (summary["by_split"].get(scope) or {}).get(name)


def evaluate_gate(run_dir, gate_cfg: dict, baseline_dir=None) -> dict:
    """评估门禁，返回 {passed, checks: [{name, op, expect, actual, ok, note}]}。"""
    _meta, _traces, summary = load_run(run_dir)
    checks = []

    for name, rule in gate_cfg.get("absolute", {}).items():
        actual = _metric(summary, name)
        if "min" in rule:
            op, expect = ">=", rule["min"]
            ok = actual is not None and actual >= expect
        else:
            op, expect = "<=", rule["max"]
            ok = actual is not None and actual <= expect
        note = "" if actual is not None else "指标缺失（按不通过处理）"
        checks.append({"name": name, "op": op, "expect": expect,
                       "actual": actual, "ok": ok, "note": note})

    vsb = gate_cfg.get("vs_baseline", {})
    if vsb.get("enabled") and baseline_dir is not None:
        _bm, _bt, base_summary = load_run(baseline_dir)
        max_drop = float(vsb.get("max_drop_pp", 2.0))
        for split in vsb.get("splits", []):
            cur = (summary["by_split"].get(split) or {}).get("pass_at_1")
            base = (base_summary["by_split"].get(split) or {}).get("pass_at_1")
            if cur is None or base is None:
                checks.append({"name": f"{split}.pass_at_1 vs baseline", "op": ">=",
                               "expect": f"drop <= {max_drop}pp", "actual": None,
                               "ok": False, "note": "指标缺失（按不通过处理）"})
                continue
            drop = round((base - cur) * 100, 2)
            checks.append({"name": f"{split}.pass_at_1 vs baseline",
                           "op": ">=", "expect": f"drop <= {max_drop}pp",
                           "actual": f"drop {drop}pp（{base} -> {cur}）",
                           "ok": drop <= max_drop, "note": ""})

    return {"passed": all(c["ok"] for c in checks), "checks": checks}
