"""Target 协议：被测对象抽象（框架可迁移性的关键，PLAN 第 5 节）。

评测核心（Harness/评分器/指标）只面向 TargetResult，不认识任何具体 Agent。
接入新 Agent = 实现一个 run() 返回 TargetResult 的类（或直接用 http_agent 配置）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agenteval.core.trace import ToolCallRecord


@dataclass
class RunContext:
    """一次 Trial 的运行上下文（由 Harness 准备，Target 只读使用）。"""
    case: Any                 # agenteval.core.dataset.Case
    trial_index: int
    db_path: Path             # 本 trial 的沙箱库隔离副本
    config: dict              # configs/default.json 内容
    model: str | None = None
    prompt_version: str | None = None


@dataclass
class TargetResult:
    """被测对象的一次运行产出，统一承载消息/工具/答案/用量。"""
    messages: list[dict] = field(default_factory=list)        # [{role, content, ...}]
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_answer: str = ""
    usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0})
    env_state: dict = field(default_factory=dict)             # 可选，供状态断言
    steps: int = 0


@runtime_checkable
class Target(Protocol):
    """被测对象协议。name 形如 'mock:good' / 'sql_agent' / 'http'。"""
    name: str

    def run(self, case_input: dict, ctx: RunContext) -> TargetResult:
        """case_input = {"question": str, "as_of": "YYYY-MM-DD"}"""
        ...
