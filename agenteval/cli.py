"""AgentEval Lab 统一命令行入口。

子命令：
    init-db        构建沙箱种子库（默认 data/ecom.db）
    lint-dataset   校验 datasets/ 全部评测集（含未转正草稿警告）
    run            执行一次评测（Case × k trials，写 runs/<run_id>/）
    report         生成 Markdown 报告
    view           生成单文件 HTML Trace Viewer
    list           历史 run 一览
    diff           两个 run 的指标 delta + 逐 case 翻转清单
    gate           发布门禁（绝对阈值 + 相对基线回归）
    harvest        失败样本回流为回归草稿
    export-labels  导出 Judge 人工校准标注 CSV
    calibrate      计算 Judge vs 人工一致率 / Cohen's Kappa
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from agenteval import config as cfg
from agenteval.core import dataset as ds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "ecom.db"
DEFAULT_DATASETS = PROJECT_ROOT / "datasets"
RUNS_DIR = PROJECT_ROOT / "runs"
INDEX_DB = RUNS_DIR / "index.sqlite"


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _resolve_run_dir(ref: str) -> Path:
    """run 引用可以是 run_id 或目录路径。"""
    p = Path(ref)
    if p.is_dir():
        return p
    candidate = RUNS_DIR / ref
    if candidate.is_dir():
        return candidate
    raise SystemExit(f"找不到 run: {ref}（runs/ 下可用 run_id 见 `list`）")


def _ensure_master_db() -> Path:
    if not DEFAULT_DB.exists():
        from agenteval.sandbox.db import build_database
        print(f"主库不存在，自动构建: {DEFAULT_DB}")
        build_database(DEFAULT_DB)
    return DEFAULT_DB


def _build_target(args, config):
    target = args.target
    if target.startswith("mock"):
        from agenteval.targets.mock import MockTarget
        persona = target.split(":", 1)[1] if ":" in target else "good"
        return MockTarget(persona)
    if target == "sql_agent":
        from agenteval.targets.sql_agent.agent import SqlAgentTarget
        return SqlAgentTarget(model=args.model,
                              prompt_version=args.prompt_version or "v1_baseline",
                              config=config)
    if target == "http":
        if not args.target_config:
            raise SystemExit("--target http 需要 --target-config <配置文件>"
                             "（参考 configs/http_agent.example.json）")
        from agenteval.targets.http_agent import HttpAgentTarget
        return HttpAgentTarget(args.target_config)
    raise SystemExit(f"未知 target: {target}（可用: mock:good / mock:flawed / sql_agent / http）")


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def _cmd_init_db(args) -> int:
    from agenteval.sandbox.db import build_database

    db_path = build_database(args.db)
    print(f"✅ 种子库已构建: {db_path}")

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        tables = ["users", "categories", "products", "orders",
                  "order_items", "payments", "refunds"]
        print("表行数：")
        for t in tables:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<12} {n}")
        pending = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
        cancelled = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='cancelled'").fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM payments WHERE status='failed'").fetchone()[0]
        print(f"陷阱自检: pending {pending} / cancelled {cancelled} / failed payments {failed}")
    finally:
        conn.close()
    return 0


def _cmd_lint_dataset(args) -> int:
    datasets_dir = Path(args.dir)
    try:
        cases = ds.load_suite(datasets_dir)
    except ds.DatasetError as e:
        print(str(e))
        print("\n❌ 校验未通过")
        return 1

    from collections import Counter
    print(f"数据集目录: {datasets_dir}\n")
    print("分层（split）统计：")
    by_split = Counter(c.split for c in cases)
    for split, expected in ds.EXPECTED_SPLIT_COUNTS.items():
        print(f"  {split:<12} {by_split[split]:>3} 条（期望 {expected}）")
    print(f"  {'总计':<12} {len(cases):>3} 条")

    print("\nkind 分布：")
    for kind, n in sorted(Counter(c.kind for c in cases).items()):
        print(f"  {kind:<15} {n}")

    print("\n难度分布：")
    for diff, n in sorted(Counter(c.difficulty for c in cases).items()):
        print(f"  {diff:<8} {n}")

    sql_cases = [c for c in cases if c.kind in ("sql_answer", "multi_step")]
    print(f"\n携带 reference_sql 的用例: {len(sql_cases)} 条")

    drafts = ds.load_drafts(datasets_dir)
    if drafts:
        print(f"\n⚠️  发现 {len(drafts)} 条未转正草稿（harvest 回流，需人工补真值后移除 draft 标记）：")
        for d in drafts:
            print(f"  - {d.get('id')}: {str(d.get('question', ''))[:50]}")
    print("\n✅ 全部校验通过（schema / kind / tags / id 唯一 / 分层数量）"
              + (f"，另有 {len(drafts)} 条草稿待处理" if drafts else ""))
    return 0


def _cmd_run(args) -> int:
    from agenteval.core.harness import (dataset_hash, generate_run_id,
                                        run_evaluation)
    from agenteval.core import registry

    cfg.load_env()
    config = cfg.load_json_config("default")
    master_db = _ensure_master_db()

    cases = ds.load_suite(DEFAULT_DATASETS)
    suites = None if args.suites in (None, "all") else {
        s.strip() for s in args.suites.split(",") if s.strip()}
    if suites:
        unknown = suites - set(ds.SPLIT_FILES)
        if unknown:
            raise SystemExit(f"未知 split: {sorted(unknown)}（可选: {sorted(ds.SPLIT_FILES)}）")
        cases = [c for c in cases if c.split in suites]
    if args.limit is not None:
        # 冒烟用途：按数据集顺序截前 N 条，快速验证链路
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("筛选后没有用例")

    trials = args.trials or int(config.get("trials", 3))
    target = _build_target(args, config)

    run_id = args.out or generate_run_id(target.name)
    run_dir = RUNS_DIR / run_id
    meta = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": target.name,
        "model": args.model,
        "prompt_version": args.prompt_version,
        "suites": sorted(suites) if suites else sorted(ds.SPLIT_FILES),
        "trials": trials,
        "dataset_hash": dataset_hash(DEFAULT_DATASETS),
        "git_commit": None,
        "config_snapshot": cfg.snapshot_config(),
    }
    from agenteval.core.harness import _git_commit
    meta["git_commit"] = _git_commit(PROJECT_ROOT)

    print(f"run_id: {run_id}")
    print(f"target={target.name} model={args.model or '-'} "
          f"prompt={args.prompt_version or '-'} trials={trials} cases={len(cases)}")

    done = [0]
    total = len(cases) * trials

    def _progress(case_id, t_idx, passed):
        done[0] += 1
        mark = "✓" if passed else "✗"
        print(f"\r[{done[0]}/{total}] {case_id}#{t_idx} {mark}   ", end="", flush=True)

    summary = run_evaluation(
        cases, target, master_db=master_db, run_dir=run_dir,
        trials=trials, config=config, meta=meta, on_progress=_progress)
    print()

    registry.register_run(INDEX_DB, run_id, meta, summary)

    overall = summary["overall"]
    print(f"\npass@1 = {overall['pass_at_1'] * 100:.1f}%  "
          f"pass^{trials} = {overall['pass_at_k'] * 100:.1f}%  "
          f"infra_error = {overall['infra_error_rate'] * 100:.1f}%  "
          f"cost = ${overall['total_cost_usd']:.4f}")
    print("分层 pass@1： " + "  ".join(
        f"{s} {g['pass_at_1'] * 100:.0f}%" for s, g in sorted(summary["by_split"].items())))
    if summary["reason_codes"]:
        from agenteval.graders.base import REASON_CODES
        dist = ", ".join(f"{c}({REASON_CODES.get(c, c)})×{n}"
                         for c, n in summary["reason_codes"].items())
        print(f"失败分类: {dist}")
    print(f"\n产物: {run_dir}")
    print(f"下一步: report/view/gate，例如 python3 -m agenteval.cli gate {run_id}")
    return 0


def _cmd_report(args) -> int:
    from agenteval.core.report import generate_report
    run_dir = _resolve_run_dir(args.run)
    out = Path(args.out) if args.out else run_dir / "report.md"
    generate_report(run_dir, out)
    print(f"✅ 报告已生成: {out}")
    return 0


def _cmd_view(args) -> int:
    from agenteval.core.viewer import generate_viewer
    run_dir = _resolve_run_dir(args.run)
    out = Path(args.out) if args.out else run_dir / "viewer.html"
    generate_viewer(run_dir, out)
    print(f"✅ Trace Viewer 已生成: {out}（浏览器直接打开即可）")
    return 0


def _cmd_list(args) -> int:
    from agenteval.core import registry
    runs = registry.list_runs(INDEX_DB)
    if not runs:
        print("（暂无历史 run）")
        return 0
    print(f"{'run_id':<30} {'target':<14} {'model':<12} {'prompt':<13} "
          f"{'trials':>6} {'pass@1':>7} {'pass^k':>7} {'created_at'}")
    print("-" * 110)
    for r in runs:
        print(f"{r['run_id']:<30} {r['target']:<14} {str(r['model'] or '-'):<12} "
              f"{str(r['prompt_version'] or '-'):<13} {r['trials']:>6} "
              f"{r['pass_at_1'] * 100:>6.1f}% {r['pass_at_k'] * 100:>6.1f}% {r['created_at'][:19]}")
    return 0


def _cmd_diff(args) -> int:
    from agenteval.core import registry
    result = registry.diff_runs(RUNS_DIR, args.run_a, args.run_b)
    print(f"diff: {result['run_a']}  ->  {result['run_b']}\n")
    print(f"{'split':<12} {'A pass@1':>9} {'B pass@1':>9} {'delta(pp)':>10}")
    print("-" * 46)
    for row in result["metric_deltas"]:
        pa = f"{row['pass_at_1_a'] * 100:.1f}%" if row["pass_at_1_a"] is not None else "-"
        pb = f"{row['pass_at_1_b'] * 100:.1f}%" if row["pass_at_1_b"] is not None else "-"
        delta = f"{row['delta_pp']:+.2f}" if row["delta_pp"] is not None else "-"
        print(f"{row['split']:<12} {pa:>9} {pb:>9} {delta:>10}")
    print(f"\n逐 case 翻转（{len(result['flips'])} 条，其中回退 {result['regression_count']} 条）：")
    for f in result["flips"]:
        mark = "🔴" if f["regression"] else "🟢"
        print(f"  {mark} {f['case_id']}: {f['change']}")
    return 1 if result["regression_count"] and args.fail_on_regression else 0


def _cmd_gate(args) -> int:
    from agenteval.core.gate import evaluate_gate
    run_dir = _resolve_run_dir(args.run)
    gate_cfg = cfg.load_json_config("gate")
    baseline_dir = _resolve_run_dir(args.baseline) if args.baseline else None
    result = evaluate_gate(run_dir, gate_cfg, baseline_dir)

    print(f"门禁评估: {run_dir.name}" + (f" ｜ 基线: {args.baseline}" if args.baseline else ""))
    for c in result["checks"]:
        mark = "✅" if c["ok"] else "❌"
        actual = c["actual"] if c["actual"] is not None else "-"
        print(f"  {mark} {c['name']}: 要求 {c['op']} {c['expect']}，实际 {actual}"
              + (f"（{c['note']}）" if c["note"] else ""))
    if result["passed"]:
        print("\n✅ 门禁通过")
        return 0
    print("\n❌ 门禁未通过")
    return 1


def _cmd_harvest(args) -> int:
    from agenteval.core.harvest import harvest
    run_dir = _resolve_run_dir(args.run)
    drafts = harvest(run_dir, DEFAULT_DATASETS / "regression.jsonl")
    if not drafts:
        print("没有新的失败样本可回流（或已回流过）")
        return 0
    print(f"✅ 已生成 {len(drafts)} 条回归草稿（draft=true）追加到 datasets/regression.jsonl：")
    for d in drafts:
        print(f"  - {d['id']}: {d['question'][:50]}")
    print("请人工补真值并移除 draft 标记；lint-dataset 会持续警告未处理草稿。")
    return 0


def _cmd_export_labels(args) -> int:
    from agenteval.graders.human import export_labels
    run_dir = _resolve_run_dir(args.run)
    out = Path(args.out) if args.out else run_dir / "labels.csv"
    n = export_labels(run_dir, out, sample_ratio=args.ratio)
    print(f"✅ 已导出 {n} 条待标注样本: {out}")
    print("人工填写 human_label（0/1，可选 dim1..dim4）后运行 calibrate。")
    return 0


def _cmd_calibrate(args) -> int:
    from agenteval.graders.human import calibrate
    result = calibrate(args.csv)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agenteval", description="AgentEval Lab —— 可迁移的 Agent 评测框架")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="构建电商沙箱种子库（默认 data/ecom.db）")
    p.add_argument("--db", default=str(DEFAULT_DB), help="输出 DB 路径")
    p.set_defaults(func=_cmd_init_db)

    p = sub.add_parser("lint-dataset", help="加载并校验全部评测集 JSONL")
    p.add_argument("--dir", default=str(DEFAULT_DATASETS), help="评测集目录")
    p.set_defaults(func=_cmd_lint_dataset)

    p = sub.add_parser("run", help="执行一次评测")
    p.add_argument("--suites", default="all",
                       help="逗号分隔的 split 列表或 all（默认 all）")
    p.add_argument("--target", default="mock:good",
                       help="mock:good / mock:flawed / sql_agent / http")
    p.add_argument("--model", default=None, help="模型名（sql_agent 用；mock* 走 MockLLM）")
    p.add_argument("--trials", type=int, default=None, help="每 case 的 trial 数（默认取配置）")
    p.add_argument("--limit", type=int, default=None,
                   help="只跑前 N 条用例（冒烟用途）")
    p.add_argument("--prompt-version", default=None, help="sql_agent 提示词版本")
    p.add_argument("--target-config", default=None, help="http target 的配置文件路径")
    p.add_argument("--out", default=None, help="自定义 run_id")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("report", help="生成 Markdown 报告")
    p.add_argument("run", help="run_id 或 run 目录")
    p.add_argument("--out", default=None, help="输出路径（默认 <run>/report.md）")
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("view", help="生成单文件 HTML Trace Viewer")
    p.add_argument("run", help="run_id 或 run 目录")
    p.add_argument("--out", default=None, help="输出路径（默认 <run>/viewer.html）")
    p.set_defaults(func=_cmd_view)

    p = sub.add_parser("list", help="历史 run 一览")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("diff", help="两个 run 的指标 delta + 逐 case 翻转")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.add_argument("--fail-on-regression", action="store_true",
                   help="存在 pass→fail 回退时退出码置 1")
    p.set_defaults(func=_cmd_diff)

    p = sub.add_parser("gate", help="发布门禁评估（不达标退出码 1）")
    p.add_argument("run", help="run_id 或 run 目录")
    p.add_argument("--baseline", default=None, help="基线 run_id（启用相对回归检查）")
    p.set_defaults(func=_cmd_gate)

    p = sub.add_parser("harvest", help="失败样本回流为回归草稿")
    p.add_argument("run", help="run_id 或 run 目录")
    p.set_defaults(func=_cmd_harvest)

    p = sub.add_parser("export-labels", help="导出 Judge 人工校准标注 CSV")
    p.add_argument("run", help="run_id 或 run 目录")
    p.add_argument("--out", default=None, help="输出 CSV（默认 <run>/labels.csv）")
    p.add_argument("--ratio", type=float, default=0.3, help="抽样比例（默认 0.3）")
    p.set_defaults(func=_cmd_export_labels)

    p = sub.add_parser("calibrate", help="计算 Judge vs 人工一致率 / Cohen's Kappa")
    p.add_argument("csv", help="已标注的 labels CSV")
    p.set_defaults(func=_cmd_calibrate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
