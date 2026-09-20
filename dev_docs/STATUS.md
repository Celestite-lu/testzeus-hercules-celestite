# STATUS — 总进度看板

> 仅总编排代理维护。更新时间：2026-09-21 03:32（CLI 合并，四命令离线验收通过；sweep 运行中）

## 阶段状态

| # | 阶段 | 状态 | 分支/提交 | 备注 |
|---|---|---|---|---|
| 0 | 环境准备 | ✅ | main `8716f7f` | chromium 1148；密钥/PLAN 已 gitignore |
| 1 | 录制器+蒸馏器 全生命周期 | ✅ | feat/recorder、feat/distiller、fix/* | 全局 88→157 passed；集成冒烟 9/9 |
| 2 | 阶段 1 合并后分析 + P0-1 修复 | ✅ | fix/distiller-submit-dedup | GO-WITH-CAVEATS |
| 3 | 评测框架（plan/spec/审查×2轮/实现） | ✅ | feat/evaluation `35dd43f` | 68 例模块测试 |
| 4 | pilot 真实实验 | ✅ | pilot003 | **3/3 全过**（含 M3 动态 id 变异）；复盘修了 3 个基础设施问题（见决策日志） |
| 5 | 归因器（plan/spec/审查/实现） | ✅ | feat/attributor `742a442` | 81 例全绿；全局 238 passed |
| 6 | 全量 sweep（30 格） | 🔄 运行中 | exp001 | 后台 exec_9a6cf2bd，预计 2-3h |
| 7 | CLI（plan/spec/审查三轮收敛/实现/合并） | ✅ | feat/cli `d3aefbb` | 54 例全绿，全局 292 passed；四命令离线演示链路验收通过（退出码全 0，P0-2 实填充、脱敏、规则定类全验证） |
| 8 | 全链路验收 + 文档收尾 | ⬜ | | |

## 决策日志

- 2026-09-21 D0：确立看板与文档规范；录制器测试走 pytest+playwright（本机无 node）。
- 2026-09-21 D1：spec 修订采用"原规划代理返工 + 总编排 grep 核验"收敛；测试布局 `tests/record2gherkin/<module>/` 子目录级 conftest；集成冒烟发现快照归因缺陷→修复（归给触发时刻最新事件）。
- 2026-09-21 D2：合并分析 P0-1（submit 双步骤）→ 蒸馏器确定性去重；P0-2（占位符无消费方）→ 评测绕开密码流程，注入策略落 CLI 模块。
- 2026-09-21 D2（pilot 复盘三修复，`fix/pilot-infra`）：
  1. `ENABLE_UBLOCK_EXTENSION=false` 入 child env——GitHub releases 直连超时导致浏览器初始化 ConnectTimeout（pilot001 全军覆没根因）
  2. 模型 `openai/deepseek-chat` → `deepseek-v4-pro`——2026 年 DeepSeek API 已不支持旧型号名（400 明示支持 deepseek-flash / deepseek-v4-pro）；planner 需强模型先全角色单模型，成本超标再分角色
  3. 蒸馏器标题文件名安全清洗——origin 里的 `://` 进 JUnit 文件名变目录分隔符（阶段 1 分析 P1-4 应验）
- 2026-09-21 D2：pilot003 三格全过；token 实测 F3 复杂流约 42 万/次（planner 全量历史重 feed 所致，DeepSeek 前缀缓存会折扣实际成本）；cost 字段 cost_unavailable（DeepSeek 不在 litellm 价格表），以 token 计量汇报。
