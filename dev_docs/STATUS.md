# STATUS — 总进度看板

> 仅总编排代理维护。更新时间：2026-09-21（阶段 1-2 完成）

## 阶段状态

| # | 阶段 | 状态 | 分支 | 备注 |
|---|---|---|---|---|
| 0 | 环境准备（文档规范/密钥脱敏/git 基线） | ✅ 完成 | main `8716f7f` | chromium 1148 已装；LLM-Key.txt/PLAN.md 已 gitignore |
| 1a | 录制器 plan+spec | ✅ 完成 | main `52070fa` | GLM 规划 |
| 1b | 蒸馏器 plan+spec | ✅ 完成 | main `52070fa` | 与 1a 并发 |
| 1c | spec 独立审查 | ✅ 完成 | main `52070fa` | 双 REVISE→必改项已修订，grep 核验，契约零漂移 |
| 2a | 录制器实现+测试 | ✅ 完成 | feat/recorder → main `a00ef10` | 20 例全绿（后 +1 回归 = 21） |
| 2b | 蒸馏器实现+测试 | ✅ 完成 | feat/distiller → main `6d43ada` | 64 例全绿 |
| 2c | 集成冒烟 + 归因缺陷修复 | ✅ 完成 | fix/recorder-snapshot-attribution → main `4f0df82` | 冒烟发现 Then 早于状态变化的归因缺陷，已修复+回归；门禁 85 passed |
| 3 | 合并后分析 | 🔄 进行中 | | GLM 分析代理 |
| 4 | 评测框架与 UI 变异实验 | 🔄 进行中（plan+spec 中） | feat/evaluation | 实验 LLM=deepseek-chat |
| 5 | 失败归因器 | ⬜ 未开始 | feat/attributor | |
| 6 | 子进程编排 CLI | ⬜ 未开始 | feat/cli | |
| 7 | 全链路验收 + 文档收尾 | ⬜ 未开始 | | |

## 决策日志

- 2026-09-21 D0：确立看板与文档规范；录制器测试走 pytest+playwright（本机无 node）。
- 2026-09-21 D1：spec 修订采用"原规划代理返工 + 总编排 grep 核验"收敛，不追加整轮复审（修订为一行级、机械可验）。
- 2026-09-21 D1：测试布局改为 `tests/record2gherkin/<module>/` 子目录 + 目录级 conftest（避免并行模块写同一 conftest）。
- 2026-09-21 D1：集成冒烟发现快照归因缺陷（spec 级设计缺陷，非实现 bug）→ 修复规则：差集文本归给触发时刻最新已入队事件。bookmarklet.txt 作为产物入库（无 CI 构建链，入库保证开箱可用）。
- 2026-09-21 D1：集成冒烟脚本沉淀于 `dev_runs/integration_smoke.py`（gitignore），9/9 通过。
