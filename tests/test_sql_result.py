"""sql_result 评分器测试：比对逻辑（行序/容差/列差异/空结果）+ grade 全流程。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agenteval.core.dataset import load_suite
from agenteval.core.trace import ToolCallRecord, Trace
from agenteval.graders import sql_result
from agenteval.graders.base import GraderContext
from agenteval.sandbox.db import build_database

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class CompareRowsTest(unittest.TestCase):
    def test_multiset_ignores_order_by_default(self):
        a = [(1, "x"), (2, "y")]
        b = [(2, "y"), (1, "x")]
        ok, _ = sql_result.compare_rows(a, b, order_matters=False, float_tol=0.01)
        self.assertTrue(ok)
        ok, _ = sql_result.compare_rows(a, b, order_matters=True, float_tol=0.01)
        self.assertFalse(ok)

    def test_float_tolerance(self):
        a = [(1.005,)]
        b = [(1.0,)]
        ok, _ = sql_result.compare_rows(a, b, order_matters=False, float_tol=0.01)
        self.assertTrue(ok)  # 归一化到 2 位小数后一致
        ok, _ = sql_result.compare_rows([(1.05,)], b, order_matters=False, float_tol=0.01)
        self.assertFalse(ok)

    def test_column_count_mismatch(self):
        ok, diag = sql_result.compare_rows([(1, 2)], [(1,)], False, 0.01)
        self.assertFalse(ok)
        self.assertIn("列数不符", diag)

    def test_superset_columns_pass_by_name(self):
        """列名对齐：Agent 多查的信息列不判错（MiniMax-M3 实测场景回归）。"""
        ok, diag = sql_result.compare_rows(
            [("2026-05", 26, 25517.3)], [("2026-05", 25517.3)], False, 0.01,
            agent_cols=["month", "order_cnt", "gmv"], ref_cols=["month", "gmv"])
        self.assertTrue(ok, diag)
        self.assertIn("多出 1 列", diag)

    def test_missing_ref_column_fails(self):
        ok, diag = sql_result.compare_rows(
            [("2026-05",)], [("2026-05", 25517.3)], False, 0.01,
            agent_cols=["month"], ref_cols=["month", "gmv"])
        self.assertFalse(ok)
        self.assertIn("参考列缺失", diag)

    def test_alias_difference_falls_back_to_positional(self):
        """列名对不上但列数相等：退化为按位置比对（别名差异不判错）。"""
        ok, _ = sql_result.compare_rows(
            [("2026-05", 25517.3)], [("2026-05", 25517.3)], False, 0.01,
            agent_cols=["月份", "金额"], ref_cols=["month", "gmv"])
        self.assertTrue(ok)

    def test_case_insensitive_column_match(self):
        ok, _ = sql_result.compare_rows(
            [("2026-05", 25517.3)], [("2026-05", 25517.3)], False, 0.01,
            agent_cols=["MONTH", "GMV"], ref_cols=["month", "gmv"])
        self.assertTrue(ok)

    def test_column_subset_value_match(self):
        """列名对不上且 Agent 列更多：枚举列子集按值对齐（total_qty ≈ qty 场景）。"""
        ok, diag = sql_result.compare_rows(
            [(1001, "键盘", 50), (1002, "鼠标", 40)],
            [("键盘", 50), ("鼠标", 40)], False, 0.01,
            agent_cols=["product_id", "name", "total_qty"],
            ref_cols=["product", "qty"])
        self.assertTrue(ok, diag)
        self.assertIn("列子集", diag)

    def test_column_subset_no_match_fails(self):
        ok, diag = sql_result.compare_rows(
            [(1001, "键盘", 999)],
            [("键盘", 50)], False, 0.01,
            agent_cols=["product_id", "name", "total_qty"],
            ref_cols=["product", "qty"])
        self.assertFalse(ok)

    def test_row_count_mismatch(self):
        ok, diag = sql_result.compare_rows([(1,), (2,)], [(1,)], False, 0.01)
        self.assertFalse(ok)
        self.assertIn("行数不符", diag)

    def test_both_empty(self):
        ok, _ = sql_result.compare_rows([], [], False, 0.01)
        self.assertTrue(ok)

    def test_one_empty(self):
        ok, diag = sql_result.compare_rows([], [(1,)], False, 0.01)
        self.assertFalse(ok)
        self.assertIn("行数不符", diag)


def _make_case(case_id, expect, kind="sql_answer"):
    return SimpleNamespace(id=case_id, split="core", question="测试问题",
                           as_of="2026-06-30", difficulty="easy", tags=["GMV"],
                           kind=kind, expect=expect)


def _gctx(case, trace, db_path):
    return GraderContext(case=case, trace=trace, db_path=db_path, config={})


class SqlResultGradeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._tmp.name) / "ecom.db"
        build_database(cls.db)
        cls.may_gmv_sql = ("SELECT ROUND(SUM(total_amount), 2) AS gmv FROM orders "
                           "WHERE status IN ('paid', 'shipped', 'completed') "
                           "AND created_at >= '2026-05-01' AND created_at < '2026-06-01'")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _trace_with_sql(self, sql: str | None, error: str | None = None):
        t = Trace(case_id="x", split="core", trial_index=0, target="t")
        t.final_answer = "5 月 GMV 是 25517.3 元"
        if sql is not None:
            tc = ToolCallRecord(name="run_sql", arguments={"sql": sql})
            if error:
                tc.error = error
            else:
                tc.result = "gmv\n25517.3"
            t.tool_calls = [tc]
        return t

    def _expect(self, ref_sql, allow_empty=False):
        return {"kind": "sql_answer", "reference_sql": ref_sql,
                "result": {"order_matters": False, "float_tol": 0.01,
                           "allow_empty": allow_empty}}

    def test_pass_when_results_match(self):
        case = _make_case("t1", self._expect(self.may_gmv_sql))
        v = sql_result.grade(_gctx(case, self._trace_with_sql(self.may_gmv_sql), self.db))
        self.assertTrue(v.passed, v.detail)

    def test_e11_when_no_query(self):
        case = _make_case("t2", self._expect(self.may_gmv_sql))
        v = sql_result.grade(_gctx(case, self._trace_with_sql(None), self.db))
        self.assertFalse(v.passed)
        self.assertIn("E11", v.reason_codes)
        self.assertIn("E7", v.reason_codes)  # 无查询却报数

    def test_e1_when_all_sql_failed(self):
        case = _make_case("t3", self._expect(self.may_gmv_sql))
        trace = self._trace_with_sql("SELECT * FROM no_such_table",
                                     error="no such table: no_such_table")
        v = sql_result.grade(_gctx(case, trace, self.db))
        self.assertFalse(v.passed)
        self.assertIn("E1", v.reason_codes)

    def test_e13_when_results_differ(self):
        bad_sql = self.may_gmv_sql.replace(
            "status IN ('paid', 'shipped', 'completed')", "1 = 1")
        case = _make_case("t4", self._expect(self.may_gmv_sql))
        v = sql_result.grade(_gctx(case, self._trace_with_sql(bad_sql), self.db))
        self.assertFalse(v.passed)
        self.assertIn("E13", v.reason_codes)
        self.assertIn("状态过滤", v.detail)  # 启发式提示

    def test_allow_empty_both_empty(self):
        empty_sql = ("SELECT strftime('%Y-%m', created_at) AS m, SUM(total_amount) "
                     "FROM orders WHERE status IN ('paid','shipped','completed') "
                     "AND created_at >= '2026-07-01' AND created_at < '2026-08-01' GROUP BY m")
        case = _make_case("t5", self._expect(empty_sql, allow_empty=True))
        v = sql_result.grade(_gctx(case, self._trace_with_sql(empty_sql), self.db))
        self.assertTrue(v.passed, v.detail)

    def test_real_case_core001(self):
        """端到端：真实用例 core-001 的参考 SQL 与自己比对必须一致。"""
        cases = {c.id: c for c in load_suite(PROJECT_ROOT / "datasets")}
        case = cases["core-001"]
        trace = self._trace_with_sql(case.expect["reference_sql"])
        v = sql_result.grade(_gctx(case, trace, self.db))
        self.assertTrue(v.passed, v.detail)


if __name__ == "__main__":
    unittest.main()
