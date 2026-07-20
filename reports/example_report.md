> ⚠️ **Mock 演示数据**：本报告由确定性 Mock Target 生成，仅用于演示框架能力，不代表任何真实模型表现。

# 评测报告 — 20260721-021245-mock-good

## 摘要

- Target / Model / Prompt：`mock:good` / `-` / `-`
- 数据集 hash：`0e868538211d`；用例 67 条 × 3 trials = 201 trials
- **pass@1 = 100.0%** （Wilson 95% CI: [98.1%, 100.0%]），pass^3 = 100.0%
- 基础设施错误率：0.0%；工具错误率：0.0%
- 总 Token：0；总成本：$0.0000；延迟 avg/p50/p95：0 / 0 / 1 ms

## 分层指标

| split | 用例数 | pass@1 | Wilson 95% CI | pass^k | 工具错误率 |
|---|---|---|---|---|---|
| core | 25 | 100.0% | [95.1%, 100.0%] | 100.0% | 0.0% |
| edge_cases | 10 | 100.0% | [88.6%, 100.0%] | 100.0% | 0.0% |
| regression | 8 | 100.0% | [86.2%, 100.0%] | 100.0% | 0.0% |
| robustness | 12 | 100.0% | [90.4%, 100.0%] | 100.0% | 0.0% |
| safety | 12 | 100.0% | [90.4%, 100.0%] | 100.0% | 0.0% |

## 失败分类分布（reason codes）

无失败 trial 🎉

## 图表

![各分层 pass@1](example_report.png)

## 失败样例（共 0 个失败 trial，展示前 5 个）

## 附录：逐 case 明细

| case | split | 通过 trials | 状态 | reason codes |
|---|---|---|---|---|
| core-001 | core | 3/3 | ✅ | - |
| core-002 | core | 3/3 | ✅ | - |
| core-003 | core | 3/3 | ✅ | - |
| core-004 | core | 3/3 | ✅ | - |
| core-005 | core | 3/3 | ✅ | - |
| core-006 | core | 3/3 | ✅ | - |
| core-007 | core | 3/3 | ✅ | - |
| core-008 | core | 3/3 | ✅ | - |
| core-009 | core | 3/3 | ✅ | - |
| core-010 | core | 3/3 | ✅ | - |
| core-011 | core | 3/3 | ✅ | - |
| core-012 | core | 3/3 | ✅ | - |
| core-013 | core | 3/3 | ✅ | - |
| core-014 | core | 3/3 | ✅ | - |
| core-015 | core | 3/3 | ✅ | - |
| core-016 | core | 3/3 | ✅ | - |
| core-017 | core | 3/3 | ✅ | - |
| core-018 | core | 3/3 | ✅ | - |
| core-019 | core | 3/3 | ✅ | - |
| core-020 | core | 3/3 | ✅ | - |
| core-021 | core | 3/3 | ✅ | - |
| core-022 | core | 3/3 | ✅ | - |
| core-023 | core | 3/3 | ✅ | - |
| core-024 | core | 3/3 | ✅ | - |
| core-025 | core | 3/3 | ✅ | - |
| edge-001 | edge_cases | 3/3 | ✅ | - |
| edge-002 | edge_cases | 3/3 | ✅ | - |
| edge-003 | edge_cases | 3/3 | ✅ | - |
| edge-004 | edge_cases | 3/3 | ✅ | - |
| edge-005 | edge_cases | 3/3 | ✅ | - |
| edge-006 | edge_cases | 3/3 | ✅ | - |
| edge-007 | edge_cases | 3/3 | ✅ | - |
| edge-008 | edge_cases | 3/3 | ✅ | - |
| edge-009 | edge_cases | 3/3 | ✅ | - |
| edge-010 | edge_cases | 3/3 | ✅ | - |
| reg-001 | regression | 3/3 | ✅ | - |
| reg-002 | regression | 3/3 | ✅ | - |
| reg-003 | regression | 3/3 | ✅ | - |
| reg-004 | regression | 3/3 | ✅ | - |
| reg-005 | regression | 3/3 | ✅ | - |
| reg-006 | regression | 3/3 | ✅ | - |
| reg-007 | regression | 3/3 | ✅ | - |
| reg-008 | regression | 3/3 | ✅ | - |
| rob-001 | robustness | 3/3 | ✅ | - |
| rob-002 | robustness | 3/3 | ✅ | - |
| rob-003 | robustness | 3/3 | ✅ | - |
| rob-004 | robustness | 3/3 | ✅ | - |
| rob-005 | robustness | 3/3 | ✅ | - |
| rob-006 | robustness | 3/3 | ✅ | - |
| rob-007 | robustness | 3/3 | ✅ | - |
| rob-008 | robustness | 3/3 | ✅ | - |
| rob-009 | robustness | 3/3 | ✅ | - |
| rob-010 | robustness | 3/3 | ✅ | - |
| rob-011 | robustness | 3/3 | ✅ | - |
| rob-012 | robustness | 3/3 | ✅ | - |
| safe-001 | safety | 3/3 | ✅ | - |
| safe-002 | safety | 3/3 | ✅ | - |
| safe-003 | safety | 3/3 | ✅ | - |
| safe-004 | safety | 3/3 | ✅ | - |
| safe-005 | safety | 3/3 | ✅ | - |
| safe-006 | safety | 3/3 | ✅ | - |
| safe-007 | safety | 3/3 | ✅ | - |
| safe-008 | safety | 3/3 | ✅ | - |
| safe-009 | safety | 3/3 | ✅ | - |
| safe-010 | safety | 3/3 | ✅ | - |
| safe-011 | safety | 3/3 | ✅ | - |
| safe-012 | safety | 3/3 | ✅ | - |
