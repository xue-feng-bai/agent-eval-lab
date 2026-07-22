"""sql_result 评分器：SQL 执行结果比对（PLAN 第 8 节）。

流程：取 Agent 最后一次**成功**的 run_sql 的 SQL，在干净副本上执行，
与 reference_sql 的结果比对：
- 多重集语义（默认无序）；order_matters=true 时严格按行序；
- 浮点按 float_tol 容差（按容差推导小数位做归一化再比对）；
- 列对齐：按列名对齐参考列（大小写不敏感）。Agent 多查的信息列不判错
  （答案正确性看"用户问的是什么"，不看 SELECT 列表宽窄）；
  参考列缺失判 E13；列名完全对不上且列数相等时退化为按位置比对；
- 诊断：行数不符 / 数值不符 / 列数不符；
- reason codes：语法错误 -> E1；无成功查询 -> E11（数据题无查询报数 -> 附 E7）；
  结果不符 -> E13（E3/E4/E5 的启发式提示放 detail，不进 reason codes）。
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from itertools import combinations

from agenteval.graders.base import GraderContext, Verdict
from agenteval.sandbox.db import connect_readonly


def _decimals_from_tol(tol: float) -> int:
    """由容差推导归一化小数位：0.01 -> 2。"""
    return max(0, math.ceil(-math.log10(tol)))


def _normalize(value, decimals: int):
    """数值归一化：float 按容差小数位四舍五入，其余原样（None 保持 None）。"""
    if isinstance(value, float):
        return round(value, decimals)
    return value


def _normalize_rows(rows: list[tuple], decimals: int) -> list[tuple]:
    return [tuple(_normalize(v, decimals) for v in row) for row in rows]


def _align_by_name(agent_rows: list[tuple], agent_cols: list[str],
                   ref_cols: list[str]) -> tuple[list[tuple] | None, str | None]:
    """按列名（大小写不敏感）把 Agent 行投影到参考列顺序。

    全部参考列命中 -> (投影后的行, None)；
    有参考列缺失 -> (None, 诊断信息)。
    """
    index = {}
    for i, name in enumerate(agent_cols):
        index.setdefault((name or "").lower(), i)
    missing = [c for c in ref_cols if (c or "").lower() not in index]
    if missing:
        return None, (f"参考列缺失: {missing}（Agent 列: {agent_cols}）")
    order = [index[(c or "").lower()] for c in ref_cols]
    extra = len(agent_cols) - len(ref_cols)
    projected = [tuple(row[i] for i in order) for row in agent_rows]
    note = f"（按列名对齐，Agent 多出 {extra} 列已忽略）" if extra > 0 else ""
    return projected, note or ""


def compare_rows(agent_rows: list[tuple], ref_rows: list[tuple],
                 order_matters: bool, float_tol: float,
                 agent_cols: list[str] | None = None,
                 ref_cols: list[str] | None = None) -> tuple[bool, str]:
    """比对两个结果集，返回 (是否一致, 诊断信息)。

    提供列名时优先按列名对齐（参考列必须齐全，Agent 多出的列忽略）；
    列名对不上时依次退化：列数相等按位置比对（别名差异）；
    Agent 列更多时枚举列子集找值匹配（如 total_qty ≈ qty 的命名差异）。
    """
    align_note = ""
    if agent_cols is not None and ref_cols is not None and ref_cols:
        projected, note = _align_by_name(agent_rows, agent_cols, ref_cols)
        if projected is not None:
            agent_rows = projected
            align_note = note or ""
        elif agent_rows and ref_rows:
            n_agent, n_ref = len(agent_rows[0]), len(ref_rows[0])
            if n_agent > n_ref and n_agent <= 6:
                ok, diag = _try_column_subsets(agent_rows, ref_rows,
                                               order_matters, float_tol)
                if ok:
                    return True, diag
                return False, f"{note}；列子集比对亦未命中（{diag}）"
            if n_agent != n_ref:
                return False, note  # Agent 列更少，无法覆盖参考列

    decimals = _decimals_from_tol(float_tol)
    a = _normalize_rows(agent_rows, decimals)
    b = _normalize_rows(ref_rows, decimals)

    if not a and not b:
        return True, "双方均为空结果" + align_note
    if not a or not b:
        return False, f"行数不符: Agent {len(a)} 行 vs 参考 {len(b)} 行（一方为空）"

    if len(a[0]) != len(b[0]):
        return False, f"列数不符: Agent {len(a[0])} 列 vs 参考 {len(b[0])} 列"
    if len(a) != len(b):
        return False, f"行数不符: Agent {len(a)} 行 vs 参考 {len(b)} 行"

    if order_matters:
        if a == b:
            return True, "行序一致，全部匹配" + align_note
        for i, (ra, rb) in enumerate(zip(a, b)):
            if ra != rb:
                return False, f"第 {i + 1} 行不符: Agent {ra} vs 参考 {rb}"
        return False, "行序比对失败"
    if Counter(a) == Counter(b):
        return True, "多重集一致（忽略行序）" + align_note
    diff_a = Counter(a) - Counter(b)
    diff_b = Counter(b) - Counter(a)
    return False, (f"数值不符: Agent 多出 {sum(diff_a.values())} 行 / "
                   f"缺少 {sum(diff_b.values())} 行；示例 Agent 独有: "
                   f"{next(iter(diff_a), None)}，参考独有: {next(iter(diff_b), None)}")


def _try_column_subsets(agent_rows: list[tuple], ref_rows: list[tuple],
                        order_matters: bool, float_tol: float) -> tuple[bool, str]:
    """列名对不上且 Agent 列更多时，枚举保序列子集找值匹配。

    用于别名命名差异场景（如 name/total_qty vs product/qty）：
    参考列的值若完整出现在 Agent 结果的某个列子集中，判通过。
    """
    n_agent, n_ref = len(agent_rows[0]), len(ref_rows[0])
    decimals = _decimals_from_tol(float_tol)
    b = _normalize_rows(ref_rows, decimals)
    if len(agent_rows) != len(ref_rows):
        return False, f"行数不符: Agent {len(agent_rows)} 行 vs 参考 {len(ref_rows)} 行"
    for idxs in combinations(range(n_agent), n_ref):
        a = _normalize_rows([tuple(row[i] for i in idxs) for row in agent_rows], decimals)
        if order_matters and a == b:
            return True, f"按列子集 {list(idxs)} 值匹配（列名对不上，按值对齐）"
        if not order_matters and Counter(a) == Counter(b):
            return True, f"按列子集 {list(idxs)} 值匹配（列名对不上，按值对齐）"
    return False, "所有列子集数值均不符"


def _execute(db_path, sql: str) -> tuple[list[str], list[tuple]]:
    """执行 SQL，返回 (列名列表, 行列表)。"""
    conn = connect_readonly(db_path)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, cur.fetchall()
    finally:
        conn.close()


def _heuristic_hints(agent_sql: str, ref_sql: str, agent_rows, ref_rows) -> list[str]:
    """细分原因启发式（写进 detail，不进 reason codes）。"""
    hints = []
    if "status in" in ref_sql.lower() and "status in" not in agent_sql.lower():
        hints.append("疑似缺少有效订单状态过滤（E4 方向）")
    if ref_rows and agent_rows and len(agent_rows) > len(ref_rows) * 1.5:
        hints.append("Agent 行数明显偏多，疑似 join 重复计数（E3 方向）")
    if "group by" in ref_sql.lower() and "group by" not in agent_sql.lower():
        hints.append("疑似缺少分组聚合（E5 方向）")
    ref_dates = set(__import__("re").findall(r"\d{4}-\d{2}-\d{2}", ref_sql))
    agent_dates = set(__import__("re").findall(r"\d{4}-\d{2}-\d{2}", agent_sql))
    if ref_dates and agent_dates and ref_dates != agent_dates:
        hints.append(f"时间窗口不一致（E4 方向）: 参考 {sorted(ref_dates)} vs Agent {sorted(agent_dates)}")
    return hints


def grade(gctx: GraderContext) -> Verdict:
    case, trace = gctx.case, gctx.trace
    expect = case.expect
    ref_sql = expect["reference_sql"]
    result_cfg = expect.get("result", {})
    order_matters = bool(result_cfg.get("order_matters", False))
    float_tol = float(result_cfg.get("float_tol", 0.01))

    run_sqls = [tc for tc in trace.tool_calls if tc.name == "run_sql"]
    successful = [tc for tc in run_sqls if tc.ok]
    if not successful:
        if run_sqls:
            return Verdict(False, 0.0, ["E1"],
                           f"所有 run_sql 均执行失败，最后一次错误: {run_sqls[-1].error}")
        codes = ["E11"]
        detail = "未执行任何 run_sql 查询"
        if trace.final_answer:
            codes.append("E7")
            detail += "，却在未查询的情况下给出数据性回答（疑似编造）"
        return Verdict(False, 0.0, codes, detail)

    agent_sql = str(successful[-1].arguments.get("sql", ""))

    try:
        ref_cols, ref_rows = _execute(gctx.db_path, ref_sql)
    except sqlite3.Error as e:
        # 参考 SQL 本身坏了属于评测集 bug（M1 测试防线之外的情况）
        return Verdict(False, 0.0, ["E15"], f"reference_sql 执行失败（评测集问题）: {e}")
    try:
        agent_cols, agent_rows = _execute(gctx.db_path, agent_sql)
    except sqlite3.Error as e:
        return Verdict(False, 0.0, ["E1"], f"Agent 最后成功 SQL 复跑失败: {e}")

    ok, diagnosis = compare_rows(agent_rows, ref_rows, order_matters, float_tol,
                                 agent_cols=agent_cols, ref_cols=ref_cols)
    if ok:
        return Verdict(True, 1.0, [], f"结果一致（{diagnosis}）")
    hints = _heuristic_hints(agent_sql, ref_sql, agent_rows, ref_rows)
    detail = diagnosis
    if hints:
        detail += "；" + "；".join(hints)
    return Verdict(False, 0.0, ["E13"], detail)
