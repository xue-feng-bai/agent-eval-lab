"""Markdown 评测报告（PLAN 第 10 节）。

内容：摘要卡片、分层指标表（含 Wilson CI）、失败分类分布、成本延迟、
逐 case 明细附录、失败样例（引用 trace 摘要）。
图表可选：matplotlib 不可用/出错时静默跳过（只出表格，绝不崩）。
"""

from __future__ import annotations

from pathlib import Path

from agenteval.core.harness import load_run
from agenteval.graders.base import REASON_CODES


def _fmt_pct(x) -> str:
    return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "-"


def _try_render_chart(summary: dict, out_png: Path) -> bool:
    """可选图表：分 split pass@1 条形图。任何失败都静默降级。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        try:
            from daimon_runtime import setup_plot  # 受管运行时中文字体
        except ImportError:
            # 受管运行时未在 sys.path 时，从解释器位置推导 runtime 根目录再试一次
            import sys
            from pathlib import Path as _P
            sys.path.insert(0, str(_P(sys.executable).parent.parent.parent))
            from daimon_runtime import setup_plot
        setup_plot()
    except Exception:
        return False
    try:
        splits = list(summary["by_split"].keys())
        values = [summary["by_split"][s]["pass_at_1"] for s in splits]
        fig, ax = plt.subplots(figsize=(7, 3.5))
        ax.bar(splits, values, color="#4C8BF5")
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("pass@1")
        ax.set_title("各分层 pass@1")
        for i, v in enumerate(values):
            ax.text(i, v + 0.02, f"{v * 100:.0f}%", ha="center", fontsize=9)
        fig.tight_layout()
        fig.savefig(out_png, bbox_inches="tight")
        plt.close(fig)
        return True
    except Exception:
        return False


def generate_report(run_dir: str | Path, out_path: str | Path | None = None) -> str:
    run_dir = Path(run_dir)
    meta, traces, summary = load_run(run_dir)
    overall = summary["overall"]
    is_mock = str(meta.get("target", "")).startswith("mock")

    lines: list[str] = []
    if is_mock:
        lines.append("> ⚠️ **Mock 演示数据**：本报告由确定性 Mock Target 生成，"
                     "仅用于演示框架能力，不代表任何真实模型表现。\n")
    lines.append(f"# 评测报告 — {meta.get('run_id', run_dir.name)}\n")
    lines.append("## 摘要\n")
    lines.append(f"- Target / Model / Prompt：`{meta.get('target')}` / "
                 f"`{meta.get('model') or '-'}` / `{meta.get('prompt_version') or '-'}`")
    lines.append(f"- 数据集 hash：`{meta.get('dataset_hash')}`；"
                 f"用例 {overall['n_cases']} 条 × {overall['trials_per_case']} trials "
                 f"= {overall['n_trials']} trials")
    lines.append(f"- **pass@1 = {_fmt_pct(overall['pass_at_1'])}** "
                 f"（Wilson 95% CI: [{_fmt_pct(overall['pass_at_1_ci'][0])}, "
                 f"{_fmt_pct(overall['pass_at_1_ci'][1])}]），"
                 f"pass^{overall['trials_per_case']} = {_fmt_pct(overall['pass_at_k'])}")
    lines.append(f"- 基础设施错误率：{_fmt_pct(overall['infra_error_rate'])}；"
                 f"工具错误率：{_fmt_pct(overall['tool_error_rate'])}")
    lines.append(f"- 总 Token：{overall['total_tokens']}；"
                 f"总成本：${overall['total_cost_usd']:.4f}；"
                 f"延迟 avg/p50/p95：{overall['latency_ms']['avg']:.0f} / "
                 f"{overall['latency_ms']['p50']:.0f} / {overall['latency_ms']['p95']:.0f} ms\n")

    # 分层指标表
    lines.append("## 分层指标\n")
    lines.append("| split | 用例数 | pass@1 | Wilson 95% CI | pass^k | 工具错误率 |")
    lines.append("|---|---|---|---|---|---|")
    for split, g in sorted(summary["by_split"].items()):
        lines.append(f"| {split} | {g['n_cases']} | {_fmt_pct(g['pass_at_1'])} | "
                     f"[{_fmt_pct(g['pass_at_1_ci'][0])}, {_fmt_pct(g['pass_at_1_ci'][1])}] | "
                     f"{_fmt_pct(g['pass_at_k'])} | {_fmt_pct(g['tool_error_rate'])} |")
    lines.append("")

    # 失败分类分布
    lines.append("## 失败分类分布（reason codes）\n")
    if summary["reason_codes"]:
        lines.append("| code | 含义 | 次数 |")
        lines.append("|---|---|---|")
        for code, n in summary["reason_codes"].items():
            lines.append(f"| {code} | {REASON_CODES.get(code, '?')} | {n} |")
    else:
        lines.append("无失败 trial 🎉")
    lines.append("")

    # 图表（可选）
    out_path = Path(out_path) if out_path else run_dir / "report.md"
    chart_png = out_path.with_suffix(".png")
    if _try_render_chart(summary, chart_png):
        lines.append(f"## 图表\n\n![各分层 pass@1]({chart_png.name})\n")

    # 失败样例（前 5 个失败 trial 的裁判理由）
    failed = [t for t in traces if not t.passed]
    lines.append(f"## 失败样例（共 {len(failed)} 个失败 trial，展示前 5 个）\n")
    for t in failed[:5]:
        lines.append(f"### {t.case_id} trial#{t.trial_index}")
        q = next((m.get("content", "") for m in t.messages if m.get("role") == "user"), "")
        lines.append(f"- 问题：{q[:120]}")
        lines.append(f"- 回答摘要：{(t.final_answer or '')[:200]}")
        for gname, v in t.verdicts.items():
            if v.get("passed") is False:
                codes = ",".join(v.get("reason_codes", []))
                lines.append(f"- ❌ {gname} [{codes}]：{v.get('detail', '')[:200]}")
        lines.append("")

    # 逐 case 明细附录
    lines.append("## 附录：逐 case 明细\n")
    lines.append("| case | split | 通过 trials | 状态 | reason codes |")
    lines.append("|---|---|---|---|---|")
    for c in summary["cases"]:
        status = "✅" if c["pass"] else "❌"
        lines.append(f"| {c['case_id']} | {c['split']} | "
                     f"{c['passed_trials']}/{c['total_trials']} | {status} | "
                     f"{', '.join(c['reason_codes']) or '-'} |")
    lines.append("")

    text = "\n".join(lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return text
