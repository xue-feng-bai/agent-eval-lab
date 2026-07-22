# 实验：MiniMax-M1 vs MiniMax-M3 全量对比

> **状态**：已于 2026-07-22 完成真实 API 冒烟与全量实验。
> 两个模型使用同一数据集 hash `0e868538211d`、同一 `v1_baseline` prompt、
> `temperature=0`，各运行 67 条 × 3 trials；全程基础设施错误率为 0%。

---

## 1. 对比什么：质量 / 成本 / 延迟三角

单看 pass@1 会选出"最贵最慢但略准"的模型。本框架每次 run 同时记录三个维度：

| 维度 | 指标 | 来源 |
|---|---|---|
| 质量 | pass@1 / pass^k（分 split）+ reason code 分布 | trials.jsonl 聚合 |
| 成本 | 总 cost_usd、每 case 平均成本 | usage × models.json 单价折算 |
| 延迟 | avg / p50 / p95（ms） | 每 trial 计时 |

成本折算在 `configs/models.json` 注册单价（美元 / 1M tokens，prompt/completion 分开）：

```json
"minimax-m1": {
  "base_url": "https://api.minimaxi.com/v1",
  "model": "MiniMax-M1",
  "prompt_price_per_1m": 1.0,
  "completion_price_per_1m": 4.0
}
```

换模型 = 在 models.json 加一行 + run 时 `--model <名>`，无需改代码。

## 2. 怎么判断"差异是真的"：Wilson 置信区间

67 条用例的样本量下，两个模型 pass@1 差 3pp 很可能是噪声。报告对每个 split 的 pass@1
给出 Wilson 95% CI：

- **CI 不重叠** → 差异大概率真实；
- **CI 重叠** → 加 trials（k=3→5）或先不下结论；
- 逐 case 翻转列表（diff 输出）比总分更有信息量：A 比 B 高 2pp 可能是"同挂同过+运气"，
  也可能是"A 修好了 5 条又挂了 3 条"——前者不用管，后者要看翻转的是哪类 case。

## 3. 实验设计建议

1. **同一数据集同一 trials 数**：dataset_hash 不一致的对比无效（meta 里有记录）；
2. **先冒烟再全量**：`--limit 5 --trials 1` 验证链路，再全量 `--trials 3`；
3. **固定温度等采样参数**：在 `.env` / 客户端配置里固定，写进实验记录；
4. **同一时间段跑完**：避免模型服务方灰度更新造成的隐性变量；
5. **门禁视角看结果**：不是"谁分高"，而是"谁过门禁 + 便宜多少 + 快多少"。

## 4. 执行命令与冒烟结果

先对两个模型各跑 5 条 core 冒烟，确认 API、工具调用、SQL 沙箱和评分链路可用：

| 模型 | run_id | pass@1 | infra error | 成本 |
|---|---|---:|---:|---:|
| MiniMax-M1 | `smoke-minimax-m1-20260722` | 40.0% | 0.0% | $0.0453 |
| MiniMax-M3 | `20260722-002919-sql_agent` | 60.0% | 0.0% | $0.0467 |

冒烟只用于验证链路，5 条样本不足以判断模型优劣。链路通过后执行全量：

```bash
python3 -m agenteval.cli run --target sql_agent --model MiniMax-M1 \
    --prompt-version v1_baseline --trials 3 --out cmp-minimax-m1-20260722
python3 -m agenteval.cli run --target sql_agent --model MiniMax-M3 \
    --prompt-version v1_baseline --trials 3 --out cmp-minimax-m3-20260722

# 两两对比（指标差 + 逐 case 翻转）
python3 -m agenteval.cli diff cmp-minimax-m1-20260722 cmp-minimax-m3-20260722

# 报告（含成本/延迟/Wilson CI）
python3 -m agenteval.cli report cmp-minimax-m1-20260722 \
    --out reports/cmp-minimax-m1-20260722.md
python3 -m agenteval.cli report cmp-minimax-m3-20260722 \
    --out reports/cmp-minimax-m3-20260722.md
```

## 5. 真实结果

| 模型 | pass@1 | pass^3 | core | safety | Wilson 95% CI | 成本($/全量) | p95 延迟 | 门禁 |
|---|---|---|---|---|---|---|---|---|
| MiniMax-M1 | **54.7%** | **40.3%** | **50.7%** | 50.0% | [47.8%, 61.5%] | **1.5823** | **17.3s** | ❌ |
| MiniMax-M3 | 50.7% | 29.8% | 45.3% | **58.3%** | [43.9%, 57.6%] | 1.6068 | 33.6s | ❌ |

分层差值（M1 → M3）：overall −3.98pp、core −5.34pp、edge_cases −6.67pp、
regression +4.17pp、robustness −16.67pp、safety +8.33pp。共 19 条逐 case 翻转，
其中 13 条 pass→fail、6 条 fail→pass。

门禁失败不是基础设施造成的：两组 `infra_error_rate` 都是 0%。M1 的 core 50.7%、
safety 50.0%，M3 的 core 45.3%、safety 58.3%，均低于当前门禁要求 core≥85%、
safety=100%。

## 6. 分析与结论

1. **总体质量差异暂不显著**：M1 高 3.98pp，但两组 overall Wilson 95% CI 大幅重叠，
   不能仅凭本次 201 trials 宣称 M1 的真实准确率更高。
2. **可靠性与延迟偏向 M1**：M1 的 pass^3 高 10.5pp，p95 17.3s，约为 M3
   33.6s 的一半；总成本也低约 1.5%。若继续迭代，M1 是更合适的当前基线。
3. **分层各有优劣**：M1 在 robustness 高 16.67pp，M3 在 safety 高 8.33pp、
   regression 高 4.17pp。不过 M3 出现 E8（企图危险操作）×6、E10×1、E6×1，
   安全总分较高不等于没有高严重度失败。
4. **共同瓶颈是结果集契约**：M1 的 E13×73，M3 的 E13×80，远高于其他类型。
   Trace 显示部分回答数字正确，但 Agent 查询返回中间结果或列名/列序不符合 reference
   结果契约，仍被确定性执行比对判失败。下一步应先约束 SQL 输出形状与最终查询契约，
   再复跑同一数据集，避免用 Judge 掩盖事实正确性问题。
5. **上线结论**：两个模型都不满足当前门禁，不能直接上线。M1 可作为下一轮 prompt/
   工具协议优化的基线；M3 的安全拒答与危险操作 case 需要逐条审计后再考虑替换。

完整报告：[`reports/cmp-minimax-m1-20260722.md`](../reports/cmp-minimax-m1-20260722.md)
与 [`reports/cmp-minimax-m3-20260722.md`](../reports/cmp-minimax-m3-20260722.md)。
