"""metrics 测试：pass@1 / pass^k / Wilson CI / 聚合 / 失败分布。"""

from __future__ import annotations

import unittest

from agenteval.core import metrics
from agenteval.core.trace import Trace


def _trace(case_id, split, passed, tags=None, infra=None, codes=None):
    t = Trace(case_id=case_id, split=split, trial_index=0, target="t",
              passed=passed, infra_error=infra)
    t.env_state["case_tags"] = tags or []
    t.duration_ms = 10.0
    if codes:
        t.verdicts = {"rules": {"passed": False, "score": 0.0,
                                "reason_codes": codes, "detail": ""}}
    return t


class WilsonTest(unittest.TestCase):
    def test_bounds(self):
        lo, hi = metrics.wilson_ci(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.0)
        lo, hi = metrics.wilson_ci(10, 10)
        self.assertEqual(hi, 1.0)
        lo, hi = metrics.wilson_ci(5, 10)
        self.assertLessEqual(lo, 0.5)
        self.assertGreaterEqual(hi, 0.5)

    def test_zero_n(self):
        self.assertEqual(metrics.wilson_ci(0, 0), (0.0, 0.0))

    def test_ci_shrinks_with_n(self):
        _, hi_small = metrics.wilson_ci(5, 10)
        lo_large, hi_large = metrics.wilson_ci(50, 100)
        self.assertLess(hi_large - lo_large, hi_small)


class SummarizeTest(unittest.TestCase):
    def setUp(self):
        # 3 个 case × 2 trials：case1 全过，case2 过一个，case3 全挂(E4)
        self.traces = [
            _trace("c1", "core", True, tags=["GMV"]),
            _trace("c1", "core", True, tags=["GMV"]),
            _trace("c2", "core", True, tags=["退款"]),
            _trace("c2", "core", False, tags=["退款"], codes=["E13"]),
            _trace("c3", "safety", False, codes=["E10"]),
            _trace("c3", "safety", False, codes=["E10"], infra="boom"),
        ]

    def test_pass_at_1_and_k(self):
        s = metrics.summarize(self.traces, trials_per_case=2)
        self.assertAlmostEqual(s["overall"]["pass_at_1"], 0.5)
        # pass^2：仅 c1 全过
        self.assertAlmostEqual(s["overall"]["pass_at_k"], 1 / 3, places=3)

    def test_by_split(self):
        s = metrics.summarize(self.traces, 2)
        self.assertAlmostEqual(s["by_split"]["core"]["pass_at_1"], 0.75)
        self.assertAlmostEqual(s["by_split"]["safety"]["pass_at_1"], 0.0)

    def test_by_tag(self):
        s = metrics.summarize(self.traces, 2)
        self.assertIn("GMV", s["by_tag"])
        self.assertAlmostEqual(s["by_tag"]["GMV"]["pass_at_1"], 1.0)

    def test_reason_code_distribution(self):
        dist = metrics.reason_code_distribution(self.traces)
        self.assertEqual(dist.get("E10"), 2)
        self.assertEqual(dist.get("E13"), 1)
        self.assertEqual(dist.get("E15"), 1)

    def test_infra_error_rate(self):
        s = metrics.summarize(self.traces, 2)
        self.assertAlmostEqual(s["overall"]["infra_error_rate"], 1 / 6, places=3)

    def test_cases_detail(self):
        s = metrics.summarize(self.traces, 2)
        c3 = next(c for c in s["cases"] if c["case_id"] == "c3")
        self.assertFalse(c3["pass"])
        self.assertIn("E10", c3["reason_codes"])


if __name__ == "__main__":
    unittest.main()
