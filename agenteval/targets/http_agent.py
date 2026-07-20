"""通用 HTTP Target（PLAN 第 5 节）：接入任何外部 Agent API 的评测适配器。

配置文件（JSON）示例见 configs/http_agent.example.json：
- url / method / headers（值可用 ${ENV_VAR} 引用环境变量中的密钥）；
- request_template：请求体模板，支持 {{question}} 与 {{as_of}} 占位；
- response_mapping：响应字段点路径映射（answer 必填，messages 可选）。

这样"评测外部 Agent"不需要写代码，填一份配置即可（docs/extending.md 的落点）。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from agenteval.core.trace import ToolCallRecord
from agenteval.targets.base import RunContext, TargetResult

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value):
    """把字符串中的 ${VAR} 展开为环境变量（未设置则置空并保留可见占位说明）。"""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _dig(data, dotted: str):
    """按点路径取值：'choices.0.message.content' -> 逐层下钻。"""
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(f"点路径 {dotted!r} 在 {part!r} 处中断")
    return cur


class HttpAgentTarget:
    """配置驱动的通用 HTTP Agent Target。"""

    def __init__(self, config_path: str | Path):
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
        cfg = _expand_env(cfg)
        self.cfg = cfg
        self.name = cfg.get("name", "http")
        self.url = cfg["url"]
        self.method = cfg.get("method", "POST").upper()
        self.headers = cfg.get("headers", {})
        self.template = cfg["request_template"]
        self.mapping = cfg.get("response_mapping", {"answer": "answer"})
        self.timeout_s = float(cfg.get("timeout_s", 60))

    def _render_body(self, question: str, as_of: str) -> bytes:
        text = json.dumps(self.template, ensure_ascii=False)
        text = text.replace("{{question}}", question).replace("{{as_of}}", as_of)
        return text.encode("utf-8")

    def run(self, case_input: dict, ctx: RunContext) -> TargetResult:
        question = case_input["question"]
        as_of = case_input["as_of"]
        body = self._render_body(question, as_of)

        start = time.perf_counter()
        record = ToolCallRecord(
            name="http_request",
            arguments={"url": self.url, "method": self.method},
        )
        req = urllib.request.Request(
            self.url, data=body, method=self.method,
            headers={"Content-Type": "application/json", **self.headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            record.result = json.dumps(payload, ensure_ascii=False)[:2000]
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            record.error = f"{type(e).__name__}: {e}"
            record.duration_ms = (time.perf_counter() - start) * 1000
            return TargetResult(
                messages=[{"role": "user", "content": question}],
                tool_calls=[record],
                final_answer="",
                steps=1,
            )
        record.duration_ms = (time.perf_counter() - start) * 1000

        answer = str(_dig(payload, self.mapping["answer"]))
        messages = [{"role": "user", "content": question}]
        if "messages" in self.mapping:
            try:
                messages.extend(_dig(payload, self.mapping["messages"]))
            except (KeyError, IndexError, ValueError):
                pass  # messages 映射为可选项，失败不致命
        messages.append({"role": "assistant", "content": answer})
        return TargetResult(
            messages=messages, tool_calls=[record], final_answer=answer, steps=1,
        )
