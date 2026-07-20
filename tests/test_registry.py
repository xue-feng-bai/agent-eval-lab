"""registry 测试：run 登记、list、diff（指标 delta + 翻转清单）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agenteval.core import registry
from agenteval.core.trace import Trace, append_trace_jsonl


def _write_run(runs_root: Path, run_id: str, case_pass: dict[str, bool],
               split_pass: dict[str, float]):
    """手工构造一个最小 run 目录（meta + trials + summary 由 load_run 现算）。"""
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps({
        "run_id": run_id, "created_at": "2026-07-01T00:00:00+00:00",
        "target": "mock:good", "model": None, "prompt_version": None,
        "suites": ["core", "safety"], "trials": 1, "dataset_hash": "abc",
    }), encoding="utf-8")
    for cid, passed in case_pass.items():
        split = "safety" if cid.startswith("safe") else "core"
        t = Trace(case_id=cid, split=split, trial_index=0, target="mock:good",
                  passed=passed)
        append_trace_jsonl(run_dir / "trials.jsonl", t)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.index = self.root / "index.sqlite"
        self.runs_root = self.root / "runs"
        self.runs_root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _register_two(self):
        _write_run(self.runs_root, "runA", {"core-001": True, "safe-001": True},
                   {"core": 1.0, "safety": 1.0})
        _write_run(self.runs_root, "runB", {"core-001": False, "safe-001": True},
                   {"core": 0.5, "safety": 1.0})
        for rid in ("runA", "runB"):
            meta, traces, summary = _load(self.runs_root, rid)
            registry.register_run(self.index, rid, meta, summary)

    def test_register_and_list(self):
        self._register_two()
        runs = registry.list_runs(self.index)
        self.assertEqual(len(runs), 2)
        ids = {r["run_id"] for r in runs}
        self.assertEqual(ids, {"runA", "runB"})
        for r in runs:
            self.assertIsInstance(r["pass_at_1"], float)

    def test_diff_flips(self):
        self._register_two()
        result = registry.diff_runs(self.runs_root, "runA", "runB")
        flips = {f["case_id"]: f for f in result["flips"]}
        self.assertIn("core-001", flips)
        self.assertEqual(flips["core-001"]["change"], "pass→fail")
        self.assertTrue(flips["core-001"]["regression"])
        self.assertEqual(result["regression_count"], 1)
        # 指标 delta 表覆盖 overall + 各 split
        splits = {row["split"] for row in result["metric_deltas"]}
        self.assertIn("overall", splits)
        self.assertIn("core", splits)


def _load(runs_root, rid):
    from agenteval.core.harness import load_run
    return load_run(Path(runs_root) / rid)


if __name__ == "__main__":
    unittest.main()
