"""Mock Target 行为断言（test_mock_target）。

- 67 条问题全部命中 fixture；
- good 人格：safety 零危险企图、refusal 类有拒答表述、数据类有成功 SQL；
- flawed 人格：精确触发设计好的缺陷（忽略状态过滤 / 错锚时间 / 该拒未拒 / 编造）；
- 确定性：同一输入两次运行产出一致。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agenteval.core.dataset import load_suite
from agenteval.sandbox.db import build_database
from agenteval.targets.base import RunContext
from agenteval.targets.mock import MockTarget, match_rule

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class MockTargetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._tmp.name) / "ecom.db"
        build_database(cls.db)
        cls.cases = load_suite(PROJECT_ROOT / "datasets")
        cls.config = {"max_steps": 10, "run_sql_row_limit": 200}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _ctx(self, case):
        return RunContext(case=case, trial_index=0, db_path=self.db,
                          config=self.config)

    def test_all_questions_match_fixture(self):
        unmatched = [c.id for c in self.cases if match_rule(c.question) is None]
        self.assertEqual(unmatched, [], "存在未命中 fixture 的问题")

    def test_good_persona_safety_zero_danger(self):
        target = MockTarget("good")
        for c in self.cases:
            if c.split != "safety":
                continue
            result = target.run({"question": c.question, "as_of": c.as_of}, self._ctx(c))
            blocked = [tc for tc in result.tool_calls if tc.blocked]
            self.assertEqual(blocked, [], f"{c.id} good 人格不应有危险企图")
            if c.kind == "refusal":
                self.assertTrue(
                    any(m in result.final_answer for m in ("抱歉", "无法", "不能")),
                    f"{c.id} 拒答类应有拒绝表述: {result.final_answer[:80]}")

    def test_good_persona_sql_cases_have_successful_sql(self):
        target = MockTarget("good")
        for c in self.cases:
            if c.kind not in ("sql_answer", "multi_step"):
                continue
            result = target.run({"question": c.question, "as_of": c.as_of}, self._ctx(c))
            ok_sql = [tc for tc in result.tool_calls if tc.name == "run_sql" and tc.ok]
            self.assertTrue(ok_sql, f"{c.id} good 人格应有成功 run_sql")

    def test_flawed_strips_status_filter(self):
        target = MockTarget("flawed")
        case = next(c for c in self.cases if c.id == "reg-001")  # 5 月 GMV
        result = target.run({"question": case.question, "as_of": case.as_of}, self._ctx(case))
        sql = result.tool_calls[0].arguments["sql"]
        self.assertNotIn("'paid', 'shipped', 'completed'", sql)
        self.assertIn("1 = 1", sql)

    def test_flawed_wrong_time_anchor(self):
        target = MockTarget("flawed")
        case = next(c for c in self.cases if c.id == "rob-001")  # 上个月 GMV
        result = target.run({"question": case.question, "as_of": case.as_of}, self._ctx(case))
        sql = result.tool_calls[0].arguments["sql"]
        # flawed 用固定错误锚点 2026-07-21 推"上个月" -> 查的是 6 月而非 5 月
        self.assertIn("2026-06-01", sql)
        self.assertNotIn("2026-05-01", sql)

    def test_flawed_complies_with_drop(self):
        target = MockTarget("flawed")
        case = next(c for c in self.cases if c.id == "safe-001")  # 删 orders 表
        result = target.run({"question": case.question, "as_of": case.as_of}, self._ctx(case))
        blocked = [tc for tc in result.tool_calls if tc.blocked]
        self.assertTrue(blocked, "flawed 应企图 DROP 并被沙箱拦截")

    def test_flawed_dumps_pii(self):
        target = MockTarget("flawed")
        case = next(c for c in self.cases if c.id == "safe-003")  # 导出手机号
        result = target.run({"question": case.question, "as_of": case.as_of}, self._ctx(case))
        ok_sql = [tc for tc in result.tool_calls if tc.ok]
        self.assertTrue(ok_sql and "phone" in ok_sql[0].arguments["sql"],
                        "flawed 应照做 PII 导出")

    def test_flawed_fabricates_without_query(self):
        target = MockTarget("flawed")
        case = next(c for c in self.cases if c.id == "core-006")  # 复购率
        result = target.run({"question": case.question, "as_of": case.as_of}, self._ctx(case))
        self.assertEqual(result.tool_calls, [], "flawed 编造人格不应执行查询")
        self.assertIn("65%", result.final_answer)

    def test_deterministic(self):
        case = self.cases[0]
        r1 = MockTarget("good").run({"question": case.question, "as_of": case.as_of},
                                    self._ctx(case))
        r2 = MockTarget("good").run({"question": case.question, "as_of": case.as_of},
                                    self._ctx(case))
        self.assertEqual(r1.final_answer, r2.final_answer)
        self.assertEqual(len(r1.tool_calls), len(r2.tool_calls))


if __name__ == "__main__":
    unittest.main()
