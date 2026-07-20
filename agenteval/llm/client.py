"""OpenAI 兼容 LLM 客户端（纯 stdlib urllib，PLAN 第 13 节）。

- POST {base_url}/chat/completions，支持 messages / tools / tool_choice /
  temperature / max_tokens；
- 超时 + 指数退避重试（限流尊重 Retry-After）；
- 错误分类：auth / rate_limit / network / server / unknown，各有专属异常；
- 返回统一结构 ChatResponse{content, tool_calls, usage}。

无 Key 时的友好指引：异常消息明确指向 .env.example，并提示 mock 演示路径。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class LLMError(Exception):
    """LLM 调用失败的基类。category 供 Harness 归类（E15 等）。"""
    category = "unknown"


class LLMAuthError(LLMError):
    category = "auth"


class LLMRateLimitError(LLMError):
    category = "rate_limit"

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMNetworkError(LLMError):
    category = "network"


class LLMServerError(LLMError):
    category = "server"


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)  # [{id, name, arguments(dict)}]
    usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    raw: dict = field(default_factory=dict, repr=False)


_NO_KEY_HINT = (
    "未配置 LLM_API_KEY。请 `cp .env.example .env` 并填入 Key（默认 MiniMax，"
    "换成任何 OpenAI 兼容服务只需改 LLM_BASE_URL/LLM_MODEL）；"
    "无 Key 也可完整演示：python3 -m agenteval.cli run --target mock:good"
)


class LLMClient:
    """OpenAI 兼容 chat + tool-calling 客户端。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout_s: float = 60.0,
                 max_retries: int = 3):
        if not api_key:
            raise LLMAuthError(_NO_KEY_HINT)
        if not base_url:
            raise LLMAuthError("未配置 LLM_BASE_URL，请检查 .env（参考 .env.example）")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model or ""
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    # ------------------------------------------------------------------

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str | None = None, temperature: float = 0.0,
             max_tokens: int | None = None) -> ChatResponse:
        payload: dict = {"model": self.model, "messages": messages,
                         "temperature": temperature}
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if max_tokens:
            payload["max_tokens"] = max_tokens

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._post(body)
            except LLMRateLimitError as e:
                last_error = e
                if attempt >= self.max_retries:
                    break
                # 尊重 Retry-After，否则指数退避
                time.sleep(e.retry_after if e.retry_after else 0.5 * (2 ** attempt))
            except (LLMServerError, LLMNetworkError) as e:
                last_error = e
                if attempt >= self.max_retries:
                    break
                time.sleep(0.5 * (2 ** attempt))
            except LLMError:
                raise  # auth / unknown 不重试
        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------

    def _post(self, body: bytes) -> ChatResponse:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise self._classify_http_error(e) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMNetworkError(f"网络错误: {e}") from e
        return self._parse_response(data)

    @staticmethod
    def _classify_http_error(e: urllib.error.HTTPError) -> LLMError:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = ""
        if e.code in (401, 403):
            return LLMAuthError(f"鉴权失败（HTTP {e.code}），请检查 API Key: {detail}")
        if e.code == 429:
            retry_after = None
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra:
                try:
                    retry_after = float(ra)
                except ValueError:
                    retry_after = None
            return LLMRateLimitError(f"限流（HTTP 429）: {detail}", retry_after=retry_after)
        if 500 <= e.code < 600:
            return LLMServerError(f"服务端错误（HTTP {e.code}）: {detail}")
        return LLMError(f"HTTP {e.code}: {detail}")

    @staticmethod
    def _parse_response(data: dict) -> ChatResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"响应缺少 choices: {str(data)[:300]}")
        message = choices[0].get("message") or {}
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                arguments = {"_raw": raw_args}
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": arguments,
            })
        usage = data.get("usage") or {}
        return ChatResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
            },
            raw=data,
        )
