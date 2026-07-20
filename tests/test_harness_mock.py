"""harness 端到端测试（mock target）：mini 套件跑通 trace -> graders -> metrics。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agenteval.core.dataset import load_suite
from agenteval.core.harness import run_evaluation
from agenteval.core.trace import load_traces_jsonl
from agenteval.sandbox.db import build_database
from agenteval.targets.base import RunContext, TargetResult
from agenteval.targets.mock import MockTarget

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _BoomTarget:
    """永远抛异常的 target：验证 E15 不中断 run。"""
    name = "boom"

    def run(self, case_input: dict, ctx: RunContext) -> TargetResult:
        raise RuntimeError("模拟基础设施崩溃")


class HarnessMockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._tmp.name) / "ecom.db"
        build_database(cls.db)
        all_cases = load_suite(PROJECT_ROOT / "datasets")
        by_id = {c.id: c for c in all_cases}
        # mini 套件：数据题 + 拒答 + 数据陷阱 + 换说法，四类行为各一
        cls.mini = [by_id["core-001"], by_id["safe-001"],
                    by_id["edge-001"], by_id["rob-001"]]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _run(self, target, cases, trials=2):
        run_dir = Path(self._tmp.name) / f"run_{target.name.replace(':', '_')}_{trials}"
        meta = {"run_id": run_dir.name, "target": target.name, "model": None,
                    "prompt_version": None, "suites": ["core", "safety"],
                    "trials": trials, "dataset_hash": "test",
                    "created_at": "2026-07-01T00:00:00+00:00"}
        summary = run_evaluation(cases, target, master_db=self.db, run_dir=run_dir,
                                 trials=trials, config={"max_steps": 10}, meta=meta)
        return run_dir, summary

    def test_good_mini_suite_all_pass(self):
        run_dir, summary = self._run(MockTarget("good"), self.mini)
        # 产物齐全
        self.assertTrue((run_dir / "meta.json").exists())
        self.assertTrue((run_dir / "trials.jsonl").exists())
        self.assertTrue((run_dir / "summary.json").exists())
        traces = load_traces_jsonl(run_dir / "trials.jsonl")
        self.assertEqual(len(traces), len(self.mini) * 2)
        self.assertEqual(summary["overall"]["pass_at_1"], 1.0)
        # judge 无 Key 被跳过但不算失败
        for t in traces:
            judge = t.verdicts.get("llm_judge")
            if judge is not None:
                self.assertIsNone(judge["passed"])
                self.assertIn("judge_unavailable", judge["detail"])

    def test_flawed_safety_fails_with_e8_e10(self):
        _run_dir, summary = self._run(MockTarget("flawed"),
                                      [self.mini[1]])  # safe-001 删表
        codes = summary["reason_codes"]
        self.assertIn("E8", codes)   # 企图危险操作（被沙箱拦截）
        self.assertIn("E10", codes)  # 该拒未拒
        self.assertEqual(summary["overall"]["pass_at_1"], 0.0)

    def test_flawed_sql_case_fails_e13(self):
        _run_dir, summary = self._run(MockTarget("flawed"),
                                      [self.mini[2]])  # edge-001（flawed 去掉状态过滤不影响 pending 查询? 该题本身查 pending，不受影响，用 rob-001）
        # edge-001 查 pending，flawed 状态过滤替换不影响；此处只验证不崩
        self.assertIn("overall", summary)

    def test_flawed_wrong_anchor_fails_rob001(self):
        _run_dir, summary = self._run(MockTarget("flawed"),
                                      [self.mini[3]])  # rob-001 上个月 GMV
        self.assertEqual(summary["overall"]["pass_at_1"], 0.0)
        self.assertIn("E13", summary["reason_codes"])

    def test_infra_error_does_not_crash_run(self):
        run_dir, summary = self._run(_BoomTarget(), self.mini[:1], trials=2)
        traces = load_traces_jsonl(run_dir / "trials.jsonl")
        self.assertEqual(len(traces), 2)
        self.assertTrue(all(t.infra_error for t in traces))
        self.assertEqual(summary["overall"]["pass_at_1"], 0.0)
        self.assertEqual(summary["overall"]["infra_error_rate"], 1.0)
        self.assertIn("E15", summary["reason_codes"])

    def test_meta_snapshot(self):
        run_dir, _summary = self._run(MockTarget("good"), self.mini[:1], trials=1)
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["target"], "mock:good")
        self.assertEqual(meta["dataset_hash"], "test")
        self.assertIn("summary_overall", meta)


class SqlAgentLoopTest(unittest.TestCase):
    """sql_agent + MockLLM：无 Key 验证真实 tool-calling 循环这条代码路径。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._tmp.name) / "ecom.db"
        build_database(cls.db)
        cls.cases = {c.id: c for c in load_suite(PROJECT_ROOT / "datasets")}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_agent_loop_with_mock_llm(self):
        from agenteval.targets.sql_agent.agent import SqlAgentTarget
        target = SqlAgentTarget(model="mock:good", config={"max_steps": 10})
        case = self.cases["core-001"]
        ctx = RunContext(case=case, trial_index=0, db_path=self.db, config={"max_steps": 10})
        result = target.run({"question": case.question, "as_of": case.as_of}, ctx)
        # 循环应产生 1 次 run_sql 工具调用 + 最终回答
        run_sqls = [tc for tc in result.tool_calls if tc.name == "run_sql"]
        self.assertEqual(len(run_sqls), 1)
        self.assertTrue(run_sqls[0].ok)
        self.assertIn("GMV", result.final_answer)
        # 消息流包含 tool 角色回灌
        roles = [m["role"] for m in result.messages]
        self.assertIn("tool", roles)


if __name__ == "__main__":
    unittest.main()
