"""脚本化 MockLLM（PLAN 第 12 节）：确定性驱动 sql_agent 的工具调用循环。

与 LLMClient 同接口（chat(messages, tools, ...) -> ChatResponse），
但不开网络、不要 Key：根据对话中的用户问题命中手工 fixture
（与 targets/mock.py 同一份规则表，good 人格），
第一轮返回 run_sql 工具调用，拿到工具结果后返回最终答案。

用途：
- 无 Key 端到端验证 sql_agent 的 agent loop（CI / 演示）；
- 与 mock target 的关系：mock target 直连 fixture 适合全量回归演示；
  MockLLM 则验证"真实 agent 循环 + 工具协议"这条代码路径。
"""

from __future__ import annotations

import re

from agenteval.llm.client import ChatResponse
from agenteval.targets.mock import match_rule, resolve_rule

_AS_OF_RE = re.compile(r"as_of[=：]\s*(\d{4}-\d{2}-\d{2})")


class MockLLM:
    """脚本化 LLM：正则命中 -> 预设工具序列；未命中 -> 诚实兜底。"""

    def __init__(self, persona: str = "good"):
        self.persona = persona
        self.model = f"mock-llm-{persona}"

    # ------------------------------------------------------------------

    @staticmethod
    def _extract_context(messages: list[dict]) -> tuple[str, str]:
        """从消息流中还原 (question, as_of)。"""
        question = ""
        as_of = "2026-06-30"
        for m in messages:
            if m.get("role") == "system":
                hit = _AS_OF_RE.search(m.get("content", ""))
                if hit:
                    as_of = hit.group(1)
            elif m.get("role") == "user" and not question:
                question = m.get("content", "")
        return question, as_of

    # ------------------------------------------------------------------

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str | None = None, temperature: float = 0.0,
             max_tokens: int | None = None) -> ChatResponse:
        question, as_of = self._extract_context(messages)
        # 已执行过的 run_sql 次数 = 工具消息条数
        done = sum(1 for m in messages if m.get("role") == "tool")

        rule = match_rule(question)
        if rule is None:
            return ChatResponse(content=(
                "抱歉，这个问题超出了我目前能可靠回答的范围，我不确定该如何准确统计，"
                "建议补充更明确的口径后再问。"))

        behavior, sqls, answer_text = resolve_rule(rule, as_of, self.persona)

        if behavior == "query" and done < len(sqls):
            # 下一步：发起第 done 个 run_sql 工具调用
            return ChatResponse(
                content="",
                tool_calls=[{
                    "id": f"mockcall_{done}",
                    "name": "run_sql",
                    "arguments": {"sql": sqls[done]},
                }],
            )

        if behavior == "comply" and done < len(sqls):
            # flawed 人格：照做危险/越权操作（会被沙箱拦截）
            return ChatResponse(
                content="",
                tool_calls=[{
                    "id": f"mockcall_{done}",
                    "name": "run_sql",
                    "arguments": {"sql": sqls[done]},
                }],
            )

        # 收尾：基于工具返回内容组织最终答案
        tool_result = ""
        for m in reversed(messages):
            if m.get("role") == "tool":
                tool_result = m.get("content", "")
                break
        if behavior == "query":
            content = f"{answer_text}\n{tool_result}\n（时间锚定 as_of={as_of}）"
        elif behavior == "comply":
            content = f"{answer_text}\n{tool_result}".strip()
        else:  # fabricate / refuse_write / refuse_pii / honest / clarify
            content = answer_text
        return ChatResponse(content=content)
