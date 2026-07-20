# 架构文档

> 对应 PLAN 第 4/5 节。本文档解释框架的概念模型、数据流，以及每个关键设计决策背后的"为什么"。

---

## 1. 概念模型

| 概念 | 定义 | 载体 |
|---|---|---|
| **Case** | 一条评测用例：问题 + as_of 时间锚 + expect（reference_sql/拒绝期望/约束）+ graders 列表 | `datasets/*.jsonl` 一行 |
| **Suite** | 一组 Case 的集合，按 split 分层（core/robustness/edge_cases/safety/regression） | `datasets/` 目录 |
| **Target** | 被测对象抽象。协议只有一个方法：`run(case_input, ctx) -> TargetResult` | `agenteval/targets/base.py` |
| **Trial** | 一次 Case 执行。同一 Case 跑 k 个 Trial 用于度量稳定性 | trials.jsonl 一行 |
| **Run** | 一次完整评测：N Case × k Trial + 配置快照 + 指标汇总 | `runs/<run_id>/` |
| **Trace** | 一个 Trial 的完整执行轨迹：消息、工具调用、返回、耗时、Token、成本 | `agenteval/core/trace.py` |
| **Grader** | 评分器。输入 Case + Trace + 沙箱库副本，输出 Verdict | `agenteval/graders/` |
| **Verdict** | 一个 Grader 的判定：passed（True/False/None 三态）+ score + reason_codes + detail | `agenteval/graders/base.py` |
| **Gate** | 发布门禁：绝对阈值 + 相对基线回归检查，输出通过/拦截 | `agenteval/core/gate.py` |

关键关系：**Case 通过 = 所有必需 Grader 通过；rules 永远必需且一票否决**。Verdict.passed 的第三态 None 表示"跳过"（如 Judge 无 Key）——不算失败，汇总时排除该维度，而不是含糊地记 0 分。

## 2. 数据流

```
datasets/*.jsonl ──lint/hash──> Harness ──每个 Trial──> 复制沙箱库副本（隔离）
                                  │
                                  v
                    Target.run(case_input, ctx) ──> TargetResult（消息/工具/答案/用量）
                                  │
                                  v
                    Graders（rules → sql_result → llm_judge）──> Verdicts
                                  │
                                  v
                    trials.jsonl（全量 Trace）──> metrics 聚合（pass@1/pass^k/Wilson）
                                  │
                    ┌─────────────┼──────────────┐
                    v             v              v
              registry 登记   report/viewer    gate（阈值+基线）
                    │             │              │
                    v             v              v
              diff 对比      Markdown/HTML    退出码 0/1 → CI
                                  │
                    失败 Trial ──harvest──> regression 草稿 ──人工补真值──> 转正
```

一次 Run 的落盘结构：

```
runs/<run_id>/
├── meta.json       # target/model/prompt/trials/dataset_hash/git_commit/配置快照
├── trials.jsonl    # 每行一个 Trial 的完整 Trace + Verdicts
├── summary.json    # 聚合指标（overall + by_split + reason code 分布）
└── ...
runs/index.sqlite   # Registry：所有 run 的登记索引（list/diff 的数据源）
```

## 3. 关键设计决策

### 3.1 为什么零依赖（纯标准库）？

- **可迁移性**：评测框架是被测系统的"环境税"。依赖越少，嵌进任何项目/CI 的成本越低。HTTP 客户端用 urllib 而非 httpx/openai SDK，原因在此——协议是 OpenAI 兼容的 JSON POST，标准库足够。
- **可复现性**：没有第三方版本漂移。三年后在任何机器上 clone 下来，行为一致。
- **可读性**：评审者不需要先理解一堆框架的框架。
- 代价是部分功能要自己写（Wilson 区间、Kappa、HTML 生成），但这些都是几十行的确定性代码，换来上述三点是值得的。
- 唯一例外：报告图表用 matplotlib 是**可选增强**，import 失败静默降级为纯表格，绝不影响主流程。

### 3.2 为什么时间锚定（as_of）？

"上个月 GMV 是多少"这类问题的正确答案随"今天"变化。如果评测集用真实当前日期，同一条用例在周一通过、周五失败，指标就失去意义。因此：

- 每条 Case 自带 `as_of` 字段，所有相对时间由它推导；
- 种子数据用固定随机种子生成，任何机器重建逐行一致；
- Mock 目标的 fixture SQL 也由 as_of 推导窗口，flawed 人格的"锚错日期"缺陷同样是确定性的。

结果：**评测集是时间不变量**。CI 上今天跑和明年跑，期望值完全相同。

### 3.3 为什么执行比对优先于 LLM-as-Judge？

LLM Judge 评"答案对不对"有两个根本问题：它自己也会算错；它的判断无法稳定复现。本框架把判断拆成两层：

- **事实正确性 → sql_result 评分器**：在种子库上真实执行 Case 自带的 `reference_sql`，与 Agent 的最终查询结果做集合比对（支持行序无关、浮点容差）。这是确定性的、可复现的、不需要任何 Key。
- **表达质量 → llm_judge**：只评"结论与查询结果一致 / 口径假设说明 / 不编造数字 / 表达清晰"四个维度，且 structured rubric + 必须过人工校准（Kappa 不达标就重修 rubric，见 docs/judge_calibration.md）。
- Judge 无 Key 时三态跳过而不是记 0 分——**测不到的维度不冤枉被测对象，也不放水**。

### 3.4 为什么防御纵深（safety 不止一道）？

单层防护都有绕过面，safety 由四层组成：

1. **沙箱层**：SQLite authorizer 在数据库驱动层拦截 DROP/DELETE/UPDATE/ATTACH 等写操作——即使 Agent 生成了危险 SQL 也执行不了；
2. **检测层**：rules 评分器检测"企图"（被拦截的调用记录 `blocked=True`，记 E8）与"该拒未拒"（答案无拒绝表述，记 E10）——执行不了 ≠ 没企图；
3. **诱饵层**：safety 用例包含注入指令、PII 索取、不存在的表（幻觉诱饵），主动勾引犯错；
4. **门禁层**：`safety.pass_at_1 >= 1.0` 是绝对红线，一条不过即拦截发布。

### 3.5 为什么 Trial 级沙箱隔离？

每个 Trial 复制一份独立的 SQLite 副本再执行。代价是毫秒级拷贝开销，换来：

- Agent 的写操作（如果有）不污染后续 Trial；
- 评分器可以在"Agent 操作后的库状态"上做状态断言，与 reference 库对比；
- 并行/trials 之间无任何共享状态，结果确定。

### 3.6 为什么 run 登记进 Registry 而不是松散文件？

每次 run 落盘之外还登记进 `runs/index.sqlite`（run_id、target、model、prompt、dataset_hash、指标）。这让 `list`/`diff` 成为一等公民：任何两次 run 可比（指标差 + 逐 case 翻转），且 meta 里的 dataset_hash 与配置快照保证了"这两次 run 可比"是可验证的——hash 不同会在报告中显式提示。
