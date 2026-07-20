"""评测集测试：五个 JSONL 全量加载校验 + reference_sql 在真实种子库上执行。

核心防线（PLAN 第 18 节"参考 SQL 与种子数据不一致"风险的对策）：
每条 sql_answer / multi_step 用例的 reference_sql 必须在真实种子库上
执行成功且返回 ≥1 行；本意就是空结果的用例须显式 allow_empty 豁免。
"""

from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from agenteval.core.dataset import (EXPECTED_SPLIT_COUNTS, KINDS, TAGS,
                                    filter_cases, load_suite)
from agenteval.sandbox.db import build_database, connect_readonly

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = PROJECT_ROOT / "datasets"

SQL_KINDS = ("sql_answer", "multi_step")


class DatasetLoadTest(unittest.TestCase):
    """加载层：可解析、schema 合法、id 唯一、分层数量正确。"""

    @classmethod
    def setUpClass(cls):
        # load_suite 内部已做全量校验；能成功返回本身就代表零错误
        cls.cases = load_suite(DATASETS_DIR)

    def test_total_count(self):
        self.assertEqual(len(self.cases), 67)

    def test_split_counts(self):
        counts = Counter(c.split for c in self.cases)
        for split, expected in EXPECTED_SPLIT_COUNTS.items():
            self.assertEqual(counts.get(split), expected, f"{split} 条数不符")

    def test_ids_globally_unique(self):
        ids = [c.id for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_kinds_and_tags_legal(self):
        for c in self.cases:
            self.assertIn(c.kind, KINDS, c.id)
            self.assertGreaterEqual(len(c.tags), 1, c.id)
            for t in c.tags:
                self.assertIn(t, TAGS, f"{c.id} 含未登记标签 {t}")

    def test_no_unresolved_draft(self):
        for c in self.cases:
            self.assertNotEqual(c.raw.get("draft"), True, f"{c.id} 是未转正草稿")

    def test_regression_notes_tell_failure_story(self):
        for c in filter_cases(self.cases, split="regression"):
            self.assertGreaterEqual(len(c.notes), 20, c.id)
            self.assertIn("曾", c.notes, f"{c.id} notes 应包含历史失败故事")

    def test_sql_cases_carry_readonly_reference(self):
        for c in self.cases:
            if c.kind in SQL_KINDS:
                self.assertTrue(c.reference_sql, c.id)
                self.assertIsNotNone(c.expect.get("result"), c.id)
            else:
                self.assertIsNone(c.expect.get("reference_sql"), c.id)

    def test_filter_cases(self):
        core = filter_cases(self.cases, split="core")
        self.assertEqual(len(core), 25)
        gmv = filter_cases(self.cases, tag="GMV")
        self.assertTrue(all("GMV" in c.tags for c in gmv))
        easy = filter_cases(self.cases, difficulty="easy")
        self.assertTrue(all(c.difficulty == "easy" for c in easy))


class ReferenceSqlExecutionTest(unittest.TestCase):
    """执行层：每条 reference_sql 在真实种子库上跑通且结果非空（allow_empty 豁免）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmp.name) / "ecom.db"
        build_database(cls.db_path)
        cls.cases = load_suite(DATASETS_DIR)
        # 只读连接执行：顺带验证所有参考 SQL 都过得了沙箱 authorizer
        cls.ro = connect_readonly(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls.ro.close()
        cls._tmp.cleanup()

    def test_all_reference_sql_execute_nonempty(self):
        failures: list[str] = []
        executed = 0
        exempted: list[str] = []
        for c in self.cases:
            if c.kind not in SQL_KINDS:
                continue
            executed += 1
            allow_empty = c.expect.get("result", {}).get("allow_empty", False)
            try:
                rows = self.ro.execute(c.reference_sql).fetchall()
            except Exception as e:  # noqa: BLE001 - 测试要聚合全部失败再报
                failures.append(f"{c.id}: 执行失败 {type(e).__name__}: {e}")
                continue
            if not rows:
                if allow_empty:
                    exempted.append(c.id)
                else:
                    failures.append(f"{c.id}: 结果为空且未声明 allow_empty")
        self.assertGreaterEqual(executed, 50, "参考 SQL 数量异常偏少")
        self.assertEqual(exempted, ["edge-010"], "豁免清单漂移，请检查")
        self.assertEqual(failures, [],
                         f"{len(failures)} 条 reference_sql 未通过:\n" + "\n".join(failures))

    def test_reference_sql_is_readonly_under_sandbox(self):
        """沙箱只读连接下逐条执行全部参考 SQL，任何写企图都会被拦截——
        全部通过即证明参考 SQL 本身只读（authorizer 未抛错）。"""
        for c in self.cases:
            if c.kind in SQL_KINDS:
                self.ro.execute(c.reference_sql).fetchall()


if __name__ == "__main__":
    unittest.main()
