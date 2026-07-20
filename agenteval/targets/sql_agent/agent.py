"""内置被测对象：Text-to-SQL 数据分析 Agent（tool-calling 循环）。

- 系统提示从 prompts/ 文件读取（{{as_of}} 占位注入）；
- 循环：LLM -> tool_calls -> ToolExecutor 执行 -> 回灌结果 -> 直到最终回答或步数上限；
- 完整记录消息流、工具调用、Token 与成本（models.json 单价折算）；
- model 以 "mock" 开头时使用 MockLLM（无 Key 端到端验证本循环）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agenteval import config as cfg
from agenteval.core.trace import ToolCallRecord
from agenteval.targets.base import RunContext, TargetResult
from agenteval.targets.sql_agent.tools import TOOL_SCHEMAS, ToolExecutor

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(prompt_version: str) -> str:
    """读取 prompts/<version>.md；version 不含扩展名。"""
    path = PROMPTS_DIR / f"{prompt_version}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"提示词文件不存在: {path}（可用: "
            + ", ".join(p.stem for p in PROMPTS_DIR.glob('*.md')) + "）")
    return path.read_text(encoding="utf-8")


class SqlAgentTarget:
    """ReAct 风格工具调用循环的数据分析 Agent。"""

    name = "sql_agent"

    def __init__(self, model: str | None = None, prompt_version: str = "v1_baseline",
                 llm=None, config: dict | None = None):
        self.model = model
        self.prompt_version = prompt_version
        self.config = config or cfg.load_json_config("default")
        if llm is not None:
            self.llm = llm
        elif model and model.startswith("mock"):
            from agenteval.llm.mock_llm import MockLLM
            persona = model.split(":", 1)[1] if ":" in model else "good"
            self.llm = MockLLM(persona=persona)
        else:
            from agenteval.llm.client import LLMClient
            llm_cfg = cfg.get_llm_config("agent")
            self.llm = LLMClient(
                api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"],
                model=model or llm_cfg["model"],
                timeout_s=float(self.config.get("timeout_s", 60)),
                max_retries=int(self.config.get("max_retries", 3)),
            )

    # ------------------------------------------------------------------

    def run(self, case_input: dict, ctx: RunContext) -> TargetResult:
        question = case_input["question"]
        as_of = case_input["as_of"]
        max_steps = int(ctx.config.get("max_steps", 10))
        temperature = float(ctx.config.get("temperature", 0.0))

        system = load_prompt(self.prompt_version).replace("{{as_of}}", as_of)
        # as_of 同时以机器可解析形式注入（MockLLM 依赖此行还原锚点）
        system += f"\n\n数据基准日期 as_of={as_of}"
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]

        executor = ToolExecutor(ctx.db_path,
                                row_limit=int(ctx.config.get("run_sql_row_limit", 200)))
        tool_records: list[ToolCallRecord] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        final_answer = ""
        steps = 0

        for step in range(1, max_steps + 1):
            steps = step
            resp = self.llm.chat(messages, tools=TOOL_SCHEMAS,
                                 temperature=temperature)
            usage["prompt_tokens"] += resp.usage.get("prompt_tokens", 0)
            usage["completion_tokens"] += resp.usage.get("completion_tokens", 0)
            usage["total_tokens"] += resp.usage.get("total_tokens", 0)

            if resp.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": resp.content or "",
                    "tool_calls": [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"],
                                      "arguments": json.dumps(
                                          tc["arguments"], ensure_ascii=False)}}
                        for tc in resp.tool_calls
                    ],
                })
                for tc in resp.tool_calls:
                    record = executor.execute(tc["name"], tc["arguments"])
                    tool_records.append(record)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": record.result if record.ok else f"ERROR: {record.error}",
                    })
            else:
                final_answer = resp.content
                messages.append({"role": "assistant", "content": final_answer})
                break
        else:
            # 达到步数上限仍未收尾：如实截断（rules 会判 E12）
            final_answer = final_answer or "（已达到最大步数限制，未能完成分析）"

        cost = cfg.compute_cost_usd(self.model, usage["prompt_tokens"],
                                    usage["completion_tokens"])
        return TargetResult(
            messages=messages, tool_calls=tool_records, final_answer=final_answer,
            usage={**usage, "cost_usd": cost}, steps=steps,
        )
