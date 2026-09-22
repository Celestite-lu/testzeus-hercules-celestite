# STATUS — 总进度看板

> 仅总编排代理维护。更新时间：2026-09-22（r1 基准定局 43.2%，进入 r2 分析-改进循环）

## 阶段状态

| # | 阶段 | 状态 | 产出 |
|---|---|---|---|
| 0 | 环境准备 | ✅ | dev_docs 规范、AGENTS.md 规约、密钥脱敏、git 基线 `8716f7f` |
| 1 | 录制器 | ✅ | feat/recorder + 2 fix；21 例；bookmarklet + 注入式 JS；集成冒烟揪出归因缺陷并修复 |
| 2 | 蒸馏器 | ✅ | feat/distiller + P0-1 fix；67 例；确定性三层（骨架零模型/润色可选/事实回查） |
| 3 | 评测框架 | ✅ | feat/evaluation `35dd43f`；68 例；demo 应用/变异引擎/执行器/指标 |
| 4 | 真实实验 | ✅ | pilot003 3/3（复盘三修复 `fix/pilot-infra`）；**exp001：主指标 1.00 vs 基线 0.667，M3 变异 6/6 vs 0/6**，30 次执行 411 万 token；报告 `dev_docs/evaluation/experiment-report.md` |
| 5 | 归因器 | ✅ | feat/attributor `742a442`；81 例；真实失败样本（F5/M4）验证诚实降级 |
| 6 | CLI | ✅ | feat/cli `d3aefbb`；54 例；四命令离线演示 + **真实 LLM 全链路验收通过**（F1 真实执行 passed，62s/7.3 万 token） |
| 7.6 | r2 改进包 | ✅ | feat/benchmark-r2 `2ac5cd3`（harness+引擎双提交，117/409 passed，回放锁定 +5）；**r2 实验决定改用 GLM**（coding plan，glm-5.3-flash；deepseek 余额尽）——provider 补丁与引擎验证双代理并行中 |
| 7.5 | MiniWoB++ benchmark | 🔄 | 模块已合并 `82f73dc`（68 例，全局 360）；**r1 完成：官方 43.2%/首实例 47.2%，安全闭环 GREEN（零作弊）**，报告 `round1-report.md`；双分析代理（失败模式+架构反思）进行中 → r2 计划 |
| 7 | 终审分析 | ✅ | `dev_docs/reports/final-analysis.md`：**COMPLETE-WITH-NOTES**（数字独立复核属实；演示规避话术与简历口径校准见报告 §4） |

**全局测试基线：292 passed**（recorder 21 + distiller 68 + evaluation 68 + attributor 81 + cli 54）。

## 决策日志（全量）

- 2026-09-22（r1 分析）：架构反思关键发现——①延迟元凶是 executor 强制重感知双倍轮次而非 planner 轮次（超时格 51.3 轮 vs 通过格 19.9）；②HUD 加固移除了 agent 可见的成功信号→页面已过后空转到超时（r2 修复方向：终局信号回供或判分优先）；③重导航治理双方案（sessionStorage 单次开局 + prompt 撤销"刷新"教学）。9 假设见 analysis-r1-architecture.md，主推组合预期 50-56%。

- D0：文档规范/AGENTS 规约/密钥 gitignore；pytest+playwright（无 node）。
- D1：spec 修订 = 原代理返工 + 总编排 grep 核验；模块子目录级 conftest；快照归因缺陷修复（归给触发时刻最新事件）。
- D2：P0-1 submit 去重（蒸馏器规则层）；P0-2 占位符注入落 CLI generate；pilot 复盘三修复（uBlock 禁用 / deepseek-v4-pro / 标题文件名清洗）。
- D3：exp001 stage full 只含 generated 侧，基线需单独 `--stage baseline` 补入（manifest 缺数据 ≠ 执行失败）；基线最终口径 M0-M2/M4=1.0、M3=0.0（非稻草人）。
- 收尾：CLI 真实链路验收（record 复用 F1 录制 → generate → run 真实执行 passed → analyze）；实验报告 + record2gherkin/README 固化 `ef25a9c`。
