# 实验：多模型对比（方法论 + 结果模板）

> **状态**：方法论与命令已就绪，真实模型数字待配置 API Key 后执行填入（命令见第 4 节）。
> 文内唯一已有的真实数字来自 Mock 人格对比（第 5 节），仅作演示并已标注。

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

## 4. 执行命令（配好 .env 后原样可用）

```bash
# 每个模型一条命令；--out 固定 run_id 便于 diff
python3 -m agenteval.cli run --target sql_agent --model minimax-m1 \
    --prompt-version v1_baseline --trials 3 --out cmp-minimax-m1
python3 -m agenteval.cli run --target sql_agent --model <model-b> \
    --prompt-version v1_baseline --trials 3 --out cmp-model-b

# 两两对比（指标差 + 逐 case 翻转）
python3 -m agenteval.cli diff cmp-minimax-m1 cmp-model-b

# 报告（含成本/延迟/Wilson CI）
python3 -m agenteval.cli report cmp-minimax-m1 --out reports/cmp-minimax-m1.md
python3 -m agenteval.cli report cmp-model-b    --out reports/cmp-model-b.md
```

## 5. 结果表（模板 + Mock 演示示例）

填写说明：数字从各自 run 的 summary.json / 报告摘要段拷贝；CI 从报告"分层指标"表拷贝；
"门禁"列用 `gate <run_id>` 结果。

| 模型 | pass@1（overall） | core | safety | Wilson 95% CI | 成本($/全量) | p95 延迟(ms) | 门禁 |
|---|---|---|---|---|---|---|---|
| minimax-m1 | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| model-b | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| ~~mock:good~~（演示） | 100.0% | 100% | 100% | [98.1%, 100.0%] | 0.0000 | 1 | ✅ |
| ~~mock:flawed~~（演示） | 16.2% | 20% | 8.3% | _见报告_ | 0.0000 | 1 | ❌ |

> ⚠️ 最后两行是 Mock 人格对比（core+safety 子集），仅演示表格用法与框架产出形态，
> 不代表任何真实模型。mock 的用途是验证"对比链路本身"工作正常：
> diff 输出 −83.78pp、门禁一过一拦，均已实录于 experiments/regression_demo.md。

## 6. 分析模板（拿到真实数字后按此写结论）

1. **质量**：谁过门禁？没过的是挂在哪个 split、哪类 reason code？
2. **性价比**：pass@1 差 X pp（CI 是否重叠？）对应成本差 Y 倍，值不值？
3. **延迟**：p95 是否满足产品场景（交互式 < 几秒？批处理无所谓？）；
4. **失败画像**：两个模型的 reason code 分布差异——贵的模型是真少了 E4/E5，
   还是只是把 E1 变成了 E7（更隐蔽的错）？
5. **结论**：推荐谁、在什么场景、以什么门禁配置上线。
