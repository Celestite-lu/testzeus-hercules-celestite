# STATUS — 总进度看板

> 仅总编排代理维护。更新时间：2026-09-21

## 阶段状态

| # | 阶段 | 状态 | 分支 | 备注 |
|---|---|---|---|---|
| 0 | 环境准备（文档规范/密钥脱敏/git 基线） | 🔄 进行中 | main | |
| 1a | 录制器 plan+spec | ⬜ 未开始 | | GLM-Flash 规划 |
| 1b | 蒸馏器 plan+spec | ⬜ 未开始 | | GLM-Flash 规划，与 1a 并发 |
| 1c | spec 独立审查 | ⬜ 未开始 | | GLM-Flash 审查 |
| 2a | 录制器实现+测试 | ⬜ 未开始 | feat/recorder | DeepSeek-Flash 实现 |
| 2b | 蒸馏器实现+测试 | ⬜ 未开始 | feat/distiller | DeepSeek-Flash 实现 |
| 3 | 测试门禁 → 合并 main → 合并后分析 | ⬜ 未开始 | | |
| 4 | 评测框架与 UI 变异实验 | ⬜ 未开始 | feat/evaluation | 实验 LLM=deepseek-chat |
| 5 | 失败归因器 | ⬜ 未开始 | feat/attributor | |
| 6 | 子进程编排 CLI | ⬜ 未开始 | feat/cli | |
| 7 | 全链路验收 + 文档收尾 | ⬜ 未开始 | | |

## 决策日志

- 2026-09-21 D0：确立本看板与文档规范；录制器测试走 pytest+playwright（本机无 node）。
