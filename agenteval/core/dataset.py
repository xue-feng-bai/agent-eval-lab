"""评测数据集：JSONL 加载、schema 校验、split/tag/difficulty 过滤。

用例 schema 见 docs/dataset_design.md；本模块只认"框架层"概念
（Case/split/kind/tags/graders），不认识 SQL 业务语义 ——
reference_sql 的可执行性由 tests/test_dataset.py 在真实种子库上验证。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 受控词表（新增取值请先更新 docs/dataset_design.md）
# ---------------------------------------------------------------------------

KINDS = {"sql_answer", "refusal", "honest_unknown", "clarification", "multi_step"}

GRADERS = {"rules", "sql_result", "llm_judge", "human"}

DIFFICULTIES = {"easy", "medium", "hard"}

# 标签受控词表：保持小而准，避免近义标签发散导致按 tag 聚合失真
TAGS = {
    "GMV", "净GMV", "客单价", "复购", "环比", "占比", "TopN", "趋势", "分布",
    "聚合", "退款", "支付", "渠道", "城市", "类目", "商品", "用户", "订单", "折扣",
    "时间口径", "多步分析", "数据陷阱", "边界", "空结果",
    "口语化", "错别字", "歧义", "澄清",
    "拒答", "注入", "PII", "幻觉诱饵", "安全", "下架商品", "回归",
}

# split -> （文件名, id 前缀, 期望条数）。分层数量是 PLAN 第 7 节的硬约定。
SPLIT_FILES = {
    "core": "core.jsonl",
    "robustness": "robustness.jsonl",
    "edge_cases": "edge_cases.jsonl",
    "safety": "safety.jsonl",
    "regression": "regression.jsonl",
}
ID_PREFIX = {"core": "core", "robustness": "rob", "edge_cases": "edge",
             "safety": "safe", "regression": "reg"}
EXPECTED_SPLIT_COUNTS = {"core": 25, "robustness": 12, "edge_cases": 10,
                         "safety": 12, "regression": 8}

# reference_sql 必须是只读查询：以 SELECT/WITH 开头，且不含任何写/DDL 关键词
_SQL_HEAD = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|"
    r"replace|vacuum|reindex|truncate|grant)\b", re.IGNORECASE)


class DatasetError(Exception):
    """数据集校验失败；message 中聚合全部错误（每行一条）。"""


@dataclass
class Case:
    """一条评测用例。expect/graders 保留原始 dict，供评分器按需取用。"""
    id: str
    split: str
    question: str
    as_of: str
    difficulty: str
    tags: list[str]
    expect: dict
    graders: list[str]
    notes: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.expect["kind"]

    @property
    def reference_sql(self) -> str | None:
        return self.expect.get("reference_sql")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def validate_case(obj: dict, source: str = "<case>") -> list[str]:
    """校验单条用例，返回错误信息列表（空列表 = 通过）。source 用于报错定位。"""
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(f"{source}: {msg}")

    if not isinstance(obj, dict):
        return [f"{source}: 用例必须是 JSON object"]

    # ---- 必填标量字段 ----
    case_id = obj.get("id")
    if not isinstance(case_id, str) or not re.fullmatch(r"[a-z]+-\d{3}", case_id or ""):
        err(f"id 缺失或格式非法（应形如 core-001）: {case_id!r}")
        case_id = case_id or "<no-id>"

    split = obj.get("split")
    if split not in SPLIT_FILES:
        err(f"split 非法: {split!r}（合法值: {sorted(SPLIT_FILES)}）")
    else:
        expected_prefix = ID_PREFIX[split]
        if isinstance(case_id, str) and not case_id.startswith(expected_prefix + "-"):
            err(f"id 前缀与 split 不符: split={split} 应用 {expected_prefix}- 开头，实际 {case_id}")

    question = obj.get("question")
    if not isinstance(question, str) or len(question.strip()) < 5:
        err("question 缺失或过短（应为用户自然语言提问）")

    as_of = obj.get("as_of")
    if not isinstance(as_of, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of or ""):
        err(f"as_of 缺失或格式非法（YYYY-MM-DD）: {as_of!r}")

    difficulty = obj.get("difficulty")
    if difficulty not in DIFFICULTIES:
        err(f"difficulty 非法: {difficulty!r}（合法值: {sorted(DIFFICULTIES)}）")

    tags = obj.get("tags")
    if (not isinstance(tags, list) or not tags
            or any(not isinstance(t, str) for t in tags)):
        err("tags 必须是非空字符串数组")
    else:
        bad = [t for t in tags if t not in TAGS]
        if bad:
            err(f"tags 含未登记标签: {bad}（如需新增请先更新受控词表）")
        if len(tags) != len(set(tags)):
            err("tags 存在重复")

    # ---- 草稿：draft=True 的是 harvest 回流的未处理草稿 ----
    # 不参与正式评测（加载时跳过），lint 单独给出警告（见 load_drafts / lint-dataset），
    # 人工补真值并移除 draft 标记后才接受完整校验。
    if obj.get("draft") is True:
        return []

    # ---- expect ----
    expect = obj.get("expect")
    if not isinstance(expect, dict):
        err("expect 缺失或不是 object")
        expect = {}
    kind = expect.get("kind")
    if kind not in KINDS:
        err(f"expect.kind 非法: {kind!r}（合法值: {sorted(KINDS)}）")

    sql_kinds = {"sql_answer", "multi_step"}
    ref_sql = expect.get("reference_sql")
    if kind in sql_kinds:
        if not isinstance(ref_sql, str) or not ref_sql.strip():
            err(f"kind={kind} 必须提供非空 reference_sql")
        else:
            if not _SQL_HEAD.search(ref_sql):
                err("reference_sql 必须以 SELECT 或 WITH 开头")
            m = _SQL_FORBIDDEN.search(ref_sql)
            if m:
                err(f"reference_sql 含写/DDL 关键词 {m.group(0)!r}（只允许只读查询）")
        result = expect.get("result")
        if not isinstance(result, dict):
            err("kind 为 sql_answer/multi_step 时必须提供 expect.result 配置")
        else:
            if not isinstance(result.get("order_matters"), bool):
                err("expect.result.order_matters 必须是 bool")
            tol = result.get("float_tol")
            if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol <= 0:
                err("expect.result.float_tol 必须是正数")
            if "allow_empty" in result and not isinstance(result["allow_empty"], bool):
                err("expect.result.allow_empty 必须是 bool")
        rt = expect.get("required_tables")
        if rt is not None and (not isinstance(rt, list)
                               or any(not isinstance(t, str) for t in rt)):
            err("expect.required_tables 必须是字符串数组")
    else:
        if ref_sql is not None:
            err(f"kind={kind} 不应携带 reference_sql（仅 sql_answer/multi_step 允许）")

    constraints = expect.get("constraints")
    if constraints is not None:
        if not isinstance(constraints, dict):
            err("expect.constraints 必须是 object")
        else:
            ms, mte = constraints.get("max_steps"), constraints.get("max_tool_errors")
            if ms is not None and (not isinstance(ms, int) or isinstance(ms, bool) or ms < 1):
                err("expect.constraints.max_steps 必须是正整数")
            if mte is not None and (not isinstance(mte, int) or isinstance(mte, bool) or mte < 0):
                err("expect.constraints.max_tool_errors 必须是非负整数")

    # ---- graders：rules 永远必需（PLAN 第 8 节） ----
    graders = obj.get("graders")
    if (not isinstance(graders, list) or not graders
            or any(not isinstance(g, str) for g in graders)):
        err("graders 必须是非空字符串数组")
        graders = []
    else:
        bad = [g for g in graders if g not in GRADERS]
        if bad:
            err(f"graders 含未登记评分器: {bad}（合法值: {sorted(GRADERS)}）")
        if "rules" not in graders:
            err("graders 必须包含 rules（确定性规则对所有用例一票否决）")
        if kind in sql_kinds and "sql_result" not in graders:
            err(f"kind={kind} 的 graders 必须包含 sql_result")
        if kind not in sql_kinds and "sql_result" in graders:
            err(f"kind={kind} 没有参考 SQL，不应包含 sql_result")

    # ---- notes：regression 必须写"历史失败故事" ----
    notes = obj.get("notes", "")
    if notes is not None and not isinstance(notes, str):
        err("notes 必须是字符串")
    if split == "regression" and (not isinstance(notes, str) or len(notes) < 20):
        err("regression 用例必须在 notes 中记录历史失败故事（曾经怎么错、怎么修的）")

    return errors


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

def _case_from_obj(obj: dict) -> Case:
    return Case(
        id=obj["id"], split=obj["split"], question=obj["question"],
        as_of=obj["as_of"], difficulty=obj["difficulty"], tags=list(obj["tags"]),
        expect=obj["expect"], graders=list(obj["graders"]),
        notes=obj.get("notes", ""), raw=obj,
    )


def load_case_file(path: str | Path) -> list[Case]:
    """加载并校验单个 JSONL 文件；有任何错误则聚合抛出 DatasetError。"""
    path = Path(path)
    cases: list[Case] = []
    errors: list[str] = []
    seen_ids: set[str] = set()

    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            src = f"{path.name}:{lineno}"
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"{src}: JSON 解析失败: {e}")
                continue
            before = len(errors)
            if isinstance(obj, dict) and obj.get("draft") is True:
                # 未转正草稿：不参与评测与计数，仅登记 id 防重（lint 另出警告）
                if isinstance(obj.get("id"), str):
                    if obj["id"] in seen_ids:
                        errors.append(f"{src}: id 文件内重复: {obj['id']}")
                    seen_ids.add(obj["id"])
                continue
            errors.extend(validate_case(obj, src))
            if isinstance(obj, dict) and isinstance(obj.get("id"), str):
                if obj["id"] in seen_ids:
                    errors.append(f"{src}: id 文件内重复: {obj['id']}")
                seen_ids.add(obj["id"])
            # 本行零错误才放行进入用例列表
            if len(errors) == before and isinstance(obj, dict):
                cases.append(_case_from_obj(obj))

    if errors:
        raise DatasetError(f"{path.name} 校验失败:\n" + "\n".join(f"  ✗ {e}" for e in errors))
    return cases


def load_drafts(datasets_dir: str | Path) -> list[dict]:
    """扫描全部 split 文件中的未转正草稿（draft=true），供 lint 警告与 harvest 幂等。"""
    datasets_dir = Path(datasets_dir)
    drafts: list[dict] = []
    for filename in SPLIT_FILES.values():
        path = datasets_dir / filename
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("draft") is True:
                    drafts.append(obj)
    return drafts


def load_suite(datasets_dir: str | Path) -> list[Case]:
    """加载全部五个 split 文件，做全局校验（id 唯一、分层数量），返回全量用例。"""
    datasets_dir = Path(datasets_dir)
    all_cases: list[Case] = []
    errors: list[str] = []

    for split, filename in SPLIT_FILES.items():
        path = datasets_dir / filename
        if not path.exists():
            errors.append(f"缺少评测集文件: {path}")
            continue
        try:
            cases = load_case_file(path)
        except DatasetError as e:
            errors.append(str(e))
            continue
        for c in cases:
            if c.split != split:
                errors.append(f"{filename}: 用例 {c.id} 的 split={c.split} 与文件归属 {split} 不符")
        expected = EXPECTED_SPLIT_COUNTS[split]
        if len(cases) != expected:
            errors.append(f"{filename}: 条数不符，期望 {expected} 条，实际 {len(cases)} 条")
        all_cases.extend(cases)

    seen: set[str] = set()
    for c in all_cases:
        if c.id in seen:
            errors.append(f"id 全局重复: {c.id}")
        seen.add(c.id)

    if errors:
        raise DatasetError("评测集校验失败:\n" + "\n".join(f"  ✗ {e}" for e in errors))
    return all_cases


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------

def filter_cases(cases: list[Case], split: str | None = None,
                 tag: str | None = None, difficulty: str | None = None) -> list[Case]:
    """按 split / tag / difficulty 过滤（条件为与关系，None 表示不过滤）。"""
    out = cases
    if split is not None:
        out = [c for c in out if c.split == split]
    if tag is not None:
        out = [c for c in out if tag in c.tags]
    if difficulty is not None:
        out = [c for c in out if c.difficulty == difficulty]
    return out
