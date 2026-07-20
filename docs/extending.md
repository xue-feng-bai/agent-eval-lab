# 迁移指南：评测你自己的 Agent

> 本项目的核心卖点：**框架核心不认识任何具体 Agent**。
> 接入新被测对象只有两条路——零代码的 http_agent 配置，或约 20 行 Python 实现 Target 协议。
> 接入后，数据集、评分器、指标、报告、diff、门禁、harvest **全部原样复用**。

---

## 路径一：http_agent 配置化接入（零代码）

适用场景：你的 Agent 已经是一个 HTTP 服务（自研后端 / Dify / Coze / n8n / LangServe……）。

### 1. 写一份配置

```bash
cp configs/http_agent.example.json configs/http_agent.local.json
```

```json
{
  "name": "http",
  "url": "https://your-agent.example.com/chat",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer ${HTTP_AGENT_TOKEN}"
  },
  "request_template": {
    "query": "{{question}}",
    "context": {"as_of": "{{as_of}}"}
  },
  "response_mapping": {
    "answer": "data.answer",
    "messages": "data.steps"
  },
  "timeout_s": 60
}
```

字段规则（实现见 `agenteval/targets/http_agent.py`）：

| 字段 | 说明 |
|---|---|
| `url` / `method` / `headers` | 请求定义；header 值里 `${VAR}` 从环境变量（含 .env）展开，**密钥不落盘** |
| `request_template` | 请求体模板；`{{question}}`、`{{as_of}}` 两个占位符逐条用例替换 |
| `response_mapping.answer` | 必填。响应 JSON 中最终答案的**点路径**（支持 `a.b.0.c` 下钻数组） |
| `response_mapping.messages` | 可选。中间步骤数组，映射成功则进 Trace 供查看 |
| `timeout_s` | 单次请求超时，超时记为 infra 错误（E15），不算 Agent 质量失败 |

### 2. 跑评测

```bash
echo 'HTTP_AGENT_TOKEN=你的密钥' >> .env

python3 -m agenteval.cli run --target http \
    --target-config configs/http_agent.local.json \
    --suites core --limit 5 --trials 1        # 先冒烟
python3 -m agenteval.cli run --target http \
    --target-config configs/http_agent.local.json --trials 3
```

之后 report / view / gate / diff / harvest 与内置 Target 完全一致。

> 注意：67 条内置用例是 Text-to-SQL 电商场景。你的 Agent 若是别的领域，
> 应该换自己的数据集——格式见 docs/dataset_design.md，`lint-dataset` 会校验。

## 路径二：自定义 Target（约 20 行 Python）

适用场景：Agent 是本地 Python 对象 / 需要复杂装配 / 要注入中间件。

Target 协议只有一个方法（`agenteval/targets/base.py`）：

```python
class Target(Protocol):
    name: str
    def run(self, case_input: dict, ctx: RunContext) -> TargetResult: ...
# case_input = {"question": str, "as_of": "YYYY-MM-DD"}
# ctx 里有：本 trial 的沙箱库副本路径 db_path、全局配置 config、model、prompt_version
```

### 最小完整示例（已实际走查通过）

```python
from agenteval.core import dataset as ds
from agenteval.core.harness import run_evaluation, dataset_hash
from agenteval.targets.base import RunContext, TargetResult

class MyAgentTarget:
    name = "my-agent"

    def run(self, case_input: dict, ctx: RunContext) -> TargetResult:
        question = case_input["question"]
        answer, calls = my_agent.chat(question)   # ← 你的 Agent
        return TargetResult(
            messages=[{"role": "user", "content": question},
                      {"role": "assistant", "content": answer}],
            tool_calls=calls,                      # ToolCallRecord 列表，可为空
            final_answer=answer,
            usage={"prompt_tokens": 0, "completion_tokens": 0,
                   "total_tokens": 0, "cost_usd": 0.0},
            steps=1,
        )

cases = ds.load_suite("datasets")
summary = run_evaluation(cases, MyAgentTarget(),
                         master_db="data/ecom.db", run_dir="runs/my-run",
                         trials=3, config={}, meta={...})   # meta 字段同 cli.py
```

要点：

- **返回 TargetResult 就算接入完成**。框架从 Trace 里取一切：评分器读 tool_calls/final_answer，指标读 usage/duration，Viewer 读 messages；
- 工具调用记录用 `ToolCallRecord(name, arguments, result, error, duration_ms, blocked)`——记录越完整，rules/sql_result 归因越准；
- 想走 CLI（`run --target my-agent`）而不是编程调用：在 `agenteval/cli.py` 的 `_build_target()` 里加一个分支即可（现有 mock / sql_agent / http 三个分支就是模板，每个 3–5 行）。

### 接入检查清单

1. 先 `--limit 3 --trials 1` 冒烟，确认 trace 落盘、评分器有输出；
2. 打开 Viewer（`view <run_id>`）逐 case 看消息流是否符合预期；
3. 失败案例归因是否合理（reason codes 分布）？归因不准通常意味着 tool_calls 记录不全；
4. 全量跑通后再接门禁与 CI。

## 两条路径怎么选

| | http_agent | 自定义 Target |
|---|---|---|
| 代码量 | 0（一份 JSON） | ~20 行 |
| 中间步骤进 Trace | 取决于 API 是否返回 | 完全可控 |
| 本地对象/有状态装配 | ❌ | ✅ |
| 非 HTTP 协议（gRPC/CLI/库） | ❌ | ✅ |
| 典型场景 | 第三方 Agent 平台、已上线的服务 | 自研 Agent 框架内嵌评测 |

两条路接入后地位完全平等——框架只认 TargetResult。
