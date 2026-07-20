# Judge 校准指南

> 对应 PLAN 第 8/10 节。LLM-as-Judge 是框架里唯一"不确定"的评分手段，
> 它的可信度不靠信仰，靠一条可执行的人工校准流程。

---

## 1. 为什么 Judge 必须校准

LLM Judge 有两个系统性风险：

1. **它自己会错**：rubric 写得再细，模型对"口径假设说明是否充分"这类判断仍有主观波动；
2. **偏差会传染**：如果 Judge 系统性偏松，所有被测对象的分数都被注水，门禁形同虚设——而且你看不出来。

所以本框架的原则是：**事实判断不给 Judge**（数字对不对由 sql_result 执行比对决定，见 docs/architecture.md 3.3），Judge 只评表达质量四维度：

| 维度 | 评什么 |
|---|---|
| 结论与查询结果一致 | 文字结论与工具返回的数据对得上 |
| 口径假设说明 | 相对时间/有效订单等口径是否显式说明 |
| 不编造数字 | 答案中的数字必须来自查询结果 |
| 表达清晰 | 结构清楚、有单位、可读 |

即使是这四个"软"维度，也需要定期用人工标注验证 Judge 的判断与人一致——这就是 calibrate 流程。

## 2. 校准全流程

### 2.1 导出待标注样本

```bash
python3 -m agenteval.cli export-labels <run_id> --out labels.csv --ratio 0.3
```

- 只导出 **Judge 实际给出了分数**的 trial（judge 被跳过的 run 没有可校准对象，导出 0 条是预期行为）；
- `--ratio` 抽样比例（默认 0.3），固定随机种子，可复现；
- CSV 自动带出 Judge 的整体判定与四维度 0/1（从 Judge detail 解析）。

### 2.2 人工标注

填 `human_label` 列（0/1，必填，整体判定）；`dim1`–`dim4` 选填，填写后 calibrate 额外输出分维度混淆矩阵。

标注建议：

- **盲标**：先遮住 judge_* 列再标，避免被 Judge 带偏；
- 抽样量建议 ≥ 30 条，否则 Kappa 波动太大；
- 标注口径有歧义时记下来——那是 rubric 要改的地方，不是标注者的问题。

### 2.3 计算校准指标

```bash
python3 -m agenteval.cli calibrate labels.csv
```

输出（示例为一次 10 条标注的合成验证数据）：

```json
{
  "skipped_unlabeled": 0,
  "overall": {"n": 10, "agreement": 0.8, "kappa": 0.6,
              "confusion": {"tp": 4, "fp": 1, "fn": 1, "tn": 4}},
  "per_dimension": {"结论与查询结果一致": {"agreement": 1.0, "kappa": 1.0, ...}, ...}
}
```

- **agreement**：观察一致率 (tp+tn)/n；
- **kappa**：Cohen's Kappa，扣除随机一致后的真实一致度；
- **confusion**：tp/fp/fn/tn——fp 多说明 Judge 偏松，fn 多说明偏严；
- **per_dimension**：定位是哪个维度在拖低一致率。

## 3. Kappa 怎么解读

| Kappa | 解读 | 行动 |
|---|---|---|
| ≥ 0.8 | 高度一致 | 可以信，维持现状，定期复校 |
| 0.6 – 0.8 | 基本一致 | 可用；看混淆矩阵找偏松/偏严维度，微调 rubric |
| 0.4 – 0.6 | 中等一致 | 谨慎使用；重修拖后腿维度的 rubric 描述与示例 |
| < 0.4 | 一致性差 | **Judge 结果不可用**：重修 rubric（拆维度、加正反例、明确边界），必要时换 Judge 模型 |

经验法则：

- **fp 明显多于 fn** → Judge 偏松，rubric 里把"什么算不达标"写具体（加反例）；
- **fn 明显多于 fp** → Judge 偏严，检查 rubric 是否要求了答案里本不该要求的东西；
- 某个维度 Kappa 显著低于其他 → 该维度定义太主观，拆成更小的可判断项，或从通过条件降级为参考分。

## 4. 校准之后

- rubric 改动落在 `agenteval/graders/llm_judge.py` 的 prompt 模板里，改动即版本（git diff 可审）；
- 重跑同一数据集对比新旧 Judge 分数分布，确认变化方向符合预期；
- 把校准结论（样本量、Kappa、主要分歧点、rubric 改动）记进实验实录，形成审计链。
