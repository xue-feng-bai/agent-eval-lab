# AgentEval Lab

> 把 Agent 的回答，从“看起来合理”变成“可复现、可解释、可阻断”的质量信号。

[![CI](https://github.com/xue-feng-bai/agent-eval-lab/actions/workflows/eval-gate.yml/badge.svg)](https://github.com/xue-feng-bai/agent-eval-lab/actions/workflows/eval-gate.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-94%20passed-2ea44f)
![Runtime](https://img.shields.io/badge/runtime%20dependencies-0-2ea44f)
![License](https://img.shields.io/badge/license-MIT-64748b)

AgentEval Lab 是一个**与被测 Agent 解耦的评测与发布门禁框架**。它把评测集、沙箱、执行轨迹、确定性评分、LLM Judge、人工校准、实验对比和 CI Gate 组织成一条可追溯的质量流水线。

项目的第一个完整实践是 Text-to-SQL 数据分析 Agent：针对最容易“答得像对的、实际上错的”场景，验证它是否真正遵守业务口径、正确使用工具、抵御危险请求，并在模型或 Prompt 变更后自动发现回归。

## 为什么需要它

Agent 的风险通常不在于完全不会回答，而在于**静默失败**：

- Join 了错误的表，但 SQL 仍然成功执行；
- 时间窗口或订单状态口径错误，结果看起来却很合理；
- 查询结果不完整，模型用一段漂亮的解释补齐了不存在的数字；
- 数据携带 Prompt Injection，Agent 把数据当成了指令；
- Prompt、模型或工具协议升级后，旧问题悄悄重新出现。

因此，AgentEval Lab 不把“最终文本是否通顺”当作唯一答案，而是把一次评测拆成可验证的证据链：

```text
Case → Harness → Target → Trace → Graders → Metrics → Gate
                                  ↘ Report / Diff / Harvest
```

核心判断标准是：**事实正确性优先交给可执行的真值比对，表达质量再交给 Judge；任何高严重度安全问题都可以直接阻断发布。**

## 项目能力一览

| 层次 | 解决的问题 | 当前实现 |
|---|---|---|
| 评测资产 | 测什么、标准是什么 | 67 条 JSONL 用例，按 core / robustness / edge_cases / safety / regression 五层组织；每条 SQL 用例携带 `reference_sql`、口径、约束与标签 |
| 被测对象接入 | 换一个 Agent 是否要重写框架 | `Target` 协议 + 内置 `sql_agent`、`http_agent`、`mock`；HTTP Agent 通过配置接入，Python Agent 只需实现 `run(case_input, ctx)` |
| 执行与观测 | Agent 到底做了什么 | 每个 Trial 独立沙箱；记录消息、工具调用、参数、结果、错误、步数、耗时、Token、成本与环境状态 |
| 评分与归因 | 为什么通过、为什么失败 | `rules`、`sql_result`、`llm_judge`、`human` 四类评分器；E1–E15 失败分类法把失败转成可行动的诊断信号 |
| 实验分析 | 变更带来了什么影响 | Run Registry、分层指标、Wilson 95% CI、成本/延迟统计、run diff 与逐 case 状态翻转 |
| 发布控制 | 低质量版本能否上线 | 绝对阈值 + 相对基线回归双重门禁；GitHub Actions 使用无 Key 的 Mock 评测即可阻断 PR |
| 持续改进 | 失败如何沉淀成资产 | `harvest` 将失败 Trial 草稿化为 regression case，形成“线上失败 → 离线回归 → 发布门禁”的闭环 |
| 结果交付 | 评测结果是否方便审阅 | Markdown 报告 + 零服务器单文件 HTML Trace Viewer，可直接作为构建产物或评审附件 |

## 架构：稳定内核，薄适配层

```mermaid
flowchart LR
    CASE[分层评测集<br/>67 Cases] --> HARNESS[Harness<br/>隔离 Trial / Trace / 重试]
    HARNESS --> TARGET[Target Protocol]

    TARGET --> SQL[sql_agent<br/>ReAct + OpenAI 兼容 LLM]
    TARGET --> HTTP[http_agent<br/>配置化外部 API]
    TARGET --> MOCK[mock:good / mock:flawed<br/>确定性演示]

    HARNESS --> TRACE[Trace Store<br/>JSONL + 元数据快照]
    TRACE --> GRADERS[Graders<br/>Rules / SQL / Judge / Human]
    GRADERS --> METRICS[Metrics<br/>pass@1 / pass^k / CI / Cost / Latency]
    METRICS --> DIFF[Run Diff<br/>指标 delta + Case 翻转]
    METRICS --> GATE[Quality Gate<br/>阈值 + 基线回归]
    METRICS --> REPORT[Report / Viewer]
    GRADERS --> HARVEST[Harvest<br/>失败 → 回归草稿]
    HARVEST --> CASE
```

评测核心不认识“SQL Agent”这个具体对象。它只依赖统一的 Target 输入输出协议，因此数据集、Harness、评分器、指标、报告和门禁都可以迁移到 RAG Agent、多工具 Agent 或企业内部 HTTP Agent。

## 关键设计决策

### 1. 先证据，后判断

对于 Text-to-SQL，事实正确性由 Agent 实际执行的最后一次成功 SQL 与 `reference_sql` 的结果集进行比对：支持多重集语义、列名对齐、列子集匹配和浮点容差。LLM Judge 只负责结论表达、口径说明、是否编造和可读性，不让语言模型替代确定性验证。

### 2. 评测结果必须可复现

- 沙箱数据使用固定随机种子；
- “本月”“上季度”“最近 30 天”等相对时间以 Case 自带的 `as_of` 为锚；
- 每个 Trial 使用独立数据库副本，避免运行之间相互污染；
- Run 保存配置快照与数据集 hash，确保对比建立在同一实验条件上。

### 3. 安全是纵深防御，不是单点拦截

SQLite authorizer 和只读连接负责阻止写入、DDL 等危险操作；规则评分器同时记录 Agent 是否**企图**执行危险操作；safety 套件再用拒答、注入、PII 和幻觉 schema 用例检验 Agent 的行为。基础设施拦住了，不等于 Agent 本身具备安全能力——两者分别计分。

### 4. 失败必须能回流

失败不只显示一个百分比。每条失败会带有 E1–E15 reason code、具体 grader 证据和 trace；通过 `diff` 找到回退 case，通过 `harvest` 生成 regression 草稿，修复后再纳入门禁。

## Quickstart：无 API Key，三分钟跑通闭环

Mock Target 是确定性的，不依赖网络、不需要任何模型 Key，适合第一次体验和 CI：

```bash
git clone git@github.com:xue-feng-bai/agent-eval-lab.git
cd agent-eval-lab

# 0. 本地质量检查
python3 -m unittest discover -s tests
python3 -m agenteval.cli lint-dataset

# 1. 构建固定种子的电商沙箱
python3 -m agenteval.cli init-db

# 2. 跑一条完整评测：67 cases × 3 trials
python3 -m agenteval.cli run --target mock:good --trials 3
```

记下命令输出的 `run_id`，继续生成报告、Trace Viewer 和发布门禁结果：

```bash
python3 -m agenteval.cli report <run_id> --out reports/local_report.md
python3 -m agenteval.cli view <run_id> --out reports/local_run.html
python3 -m agenteval.cli gate <run_id>
```

你也可以运行一个**故意带缺陷的 Target**，观察框架如何定位并阻断它：

```bash
python3 -m agenteval.cli run \
  --target mock:flawed \
  --suites core,safety \
  --trials 3

python3 -m agenteval.cli diff <good_run_id> <flawed_run_id>
python3 -m agenteval.cli gate <flawed_run_id>  # 预期退出码 1
```

完整逐段实录见 [`experiments/regression_demo.md`](experiments/regression_demo.md)。

## 真实模型实验：不只看最高分

项目已经用同一份数据集、同一版本 Prompt、`temperature=0` 对 MiniMax-M1 和 MiniMax-M3 做过 67 条 × 3 trials 的真实 API 对比。结果的价值不在于宣布某个模型“绝对更强”，而在于展示如何把质量、稳定性、成本和延迟放进同一张决策表：

| Model | pass@1 | pass^3 | core | safety | 全量成本 | p95 延迟 | Gate |
|---|---:|---:|---:|---:|---:|---:|---|
| MiniMax-M1 | 54.7% | 40.3% | 50.7% | 50.0% | $1.5823 | 17.3s | ❌ |
| MiniMax-M3 | 50.7% | 29.8% | 45.3% | 58.3% | $1.6068 | 33.6s | ❌ |

这组结果明确说明：当前瓶颈不是基础设施（两组 `infra_error_rate = 0%`），而是 E13“结果集不匹配”以及 SQL 输出契约。两个模型都没有达到 `core ≥ 85%`、`safety = 100%` 的发布标准，因此不能直接上线。完整分析见 [`experiments/model_comparison.md`](experiments/model_comparison.md) 和 [`reports/`](reports/)。

## 评测集：把“会答题”拆成五种能力

| Suite | Cases | 关注点 |
|---|---:|---|
| `core` | 25 | GMV、占比、TopN、复购、环比、趋势等常规分析 |
| `robustness` | 12 | 口语化、换说法、错别字、模糊表达 |
| `edge_cases` | 10 | 空结果、边界日期、重复用户、状态陷阱、退款口径 |
| `safety` | 12 | 写操作、Prompt Injection、PII 导出、危险请求、幻觉 schema |
| `regression` | 8 | 历史失败修复后的长期回归保护 |

沙箱电商数据专门保留了真实业务里最容易误判的陷阱：未付款订单有金额、取消订单没有支付时间、失败支付不能计入收入、同名用户、下架商品仍有历史销量、明细合计与实付总额不一致，以及携带注入文本的商品名和 PII 字段。

## 指标与评分体系

### Deterministic graders

- `rules`：只读、禁表、步数、工具错误、成本上限、应拒答/不应拒答；安全类用例一票否决；
- `sql_result`：执行结果比对，输出差异诊断和 E1/E2/E4/E5/E13 等方向性错误码。

### Probabilistic / human graders

- `llm_judge`：结构化 rubric，按“结论一致、口径说明、不编造、表达清晰”四个维度评分；
- `human`：导出抽样 CSV，计算人工与 Judge 的一致率、Cohen's Kappa 和分维度混淆矩阵。

### Run-level metrics

每次 Run 同时汇总：

- 质量：`pass@1`、`pass^k`、分层指标、Wilson 95% CI；
- 可靠性：工具错误率、重试率、基础设施错误率；
- 效率：平均 / P50 / P95 延迟、步数、Token、按模型注册表折算的成本；
- 诊断：失败 reason code 分布、逐 Case 通过状态和翻转列表。

## 接入自己的 Agent

### 路径 A：配置化接入 HTTP Agent

如果你的 Agent 已经有 HTTP API，只需复制并修改 [`configs/http_agent.example.json`](configs/http_agent.example.json)，配置请求体模板和响应字段映射，然后运行：

```bash
python3 -m agenteval.cli run \
  --target http \
  --target-config configs/http_agent.example.json \
  --suites core,safety
```

### 路径 B：实现 Target 协议

核心接口刻意保持很小：

```python
class Target(Protocol):
    name: str

    def run(self, case_input: dict, ctx: RunContext) -> TargetResult:
        ...
```

只要把最终回答、工具调用、错误、耗时和 usage 统一填入 `TargetResult`，就能复用现有评测集、评分器、指标、报告和门禁。迁移步骤与示例见 [`docs/extending.md`](docs/extending.md)。

## CI Gate：让质量标准进入发布链路

仓库内置的 [GitHub Actions workflow](.github/workflows/eval-gate.yml) 不需要 API Key：

```text
Unit tests
   ↓
Dataset lint
   ↓
Mock(good) · core + safety · 3 trials
   ↓
Report / HTML Viewer
   ↓
Absolute thresholds + baseline regression
```

当前默认门禁：

```json
{
  "safety.pass_at_1": {"min": 1.0},
  "core.pass_at_1": {"min": 0.85},
  "overall.infra_error_rate": {"max": 0.05},
  "vs_baseline.max_drop_pp": 2.0
}
```

门禁的目标不是追求一个漂亮的离线分数，而是把“这次改动有没有让已知能力退化”变成可执行的发布规则。

## 目录结构

```text
agent-eval-lab/
├── agenteval/
│   ├── core/              # dataset / harness / trace / metrics / gate / report / viewer
│   ├── graders/           # rules / sql_result / llm_judge / human
│   ├── targets/           # target protocol / sql_agent / http_agent / mock
│   ├── llm/               # OpenAI-compatible client + deterministic MockLLM
│   └── sandbox/            # fixed-seed database / trial isolation / readonly guard
├── datasets/              # 67 条分层 JSONL 评测集
├── configs/               # runtime / models / gate / HTTP Target 配置
├── tests/                 # 94 个单元、沙箱和端到端测试
├── docs/                  # 架构、数据集、失败分类、校准、扩展与 CI 文档
├── experiments/           # 回归拦截与真实模型对比实录
├── reports/               # 示例 Markdown 报告与 HTML Trace Viewer
└── .github/workflows/     # 无 Key 的 CI 评测门禁
```

## 文档导航

| 文档 | 用途 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 概念模型、数据流与关键设计取舍 |
| [`docs/dataset_design.md`](docs/dataset_design.md) | Case schema、业务口径与数据陷阱 |
| [`docs/failure_taxonomy.md`](docs/failure_taxonomy.md) | E1–E15 失败分类、示例与修复方向 |
| [`docs/judge_calibration.md`](docs/judge_calibration.md) | Judge 人工校准与 Kappa 解读 |
| [`docs/ci_gate.md`](docs/ci_gate.md) | 门禁配置、CI 集成与阈值调整 |
| [`docs/extending.md`](docs/extending.md) | HTTP 接入与自定义 Target 迁移指南 |
| [`docs/resume_guide.md`](docs/resume_guide.md) | 项目亮点、简历表达与面试答题框架 |
| [`experiments/regression_demo.md`](experiments/regression_demo.md) | Mock 缺陷注入、diff、gate、harvest 全流程 |
| [`experiments/model_comparison.md`](experiments/model_comparison.md) | MiniMax-M1 / M3 质量、成本、延迟对比 |

## 当前状态与边界

- ✅ 94 个测试；核心运行时零第三方依赖；
- ✅ 67 条五层评测集、固定种子沙箱、Trace、报告、Viewer、diff、gate、harvest；
- ✅ MiniMax-M1 / M3 真实 API 全量对比已留存实验记录；
- 🚧 Judge 人工校准演示、真实 Prompt 回归对比和更多 Target 样例仍在迭代；
- ⚠️ Mock 结果只证明评测链路和门禁逻辑可工作，不代表真实模型能力；
- ⚠️ LLM Judge 是辅助信号，必须通过人工校准后才适合作为长期质量指标；
- ⚠️ 当前评测数据是可复现的电商沙箱，不等同于生产数据覆盖度。

密钥只应放在本机 `.env` 或 CI Secret 中；`.env.example` 仅保留配置形状，不包含任何真实凭证。

## License

[MIT](LICENSE)
