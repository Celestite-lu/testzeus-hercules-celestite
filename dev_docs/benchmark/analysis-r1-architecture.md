# R1 架构与提示词反思 — 改进假设清单（供 round2 plan 采纳）

> 定位：第一轮 43.2%（官方 last-wins）/ 47.2%（首实例）之后的**架构与提示词层**反思。只做分析，不改代码、不动数据。
> 方法：只读 r1 产物（`dev_runs/benchmark/miniwob-r1/`：130 行 results / 227 条 rewards / 126 份 stdout.log）+ 引擎与 harness 源码；对 3 个超时格（multi-layouts、email-inbox-forward、use-colorwheel-2）做了逐日志抽样，并对全部 121 个有日志的格做了语料级统计。凡写【实证】均有可复核的脚本化证据；写【推断】为机制级推断。
> 日期：2026-09-20。对象 git_rev：manifest `a2800a1`。

---

## 0. 机制画像：r1 的延迟与失败到底发生在哪（先行证据）

r1 报告 §5 的候选方向里有两条被本轮数据**修正**了认识：

1. **"planner 轮次过多/空转"不成立**：超时格平均只有 **3.2 个 planner LLM 轮**（通过格 3.7、失败格 4.0）。planner 不是延迟源。
2. **真正的延迟源在 executor（nav agent）工具循环内部**：
   - 超时格平均 **51.3 个 executor LLM 轮**（通过格 19.9、失败格 26.8）；
   - 全语料 executor 相邻两轮间隔：**中位 5s、均值 9.0s、p90 23s**（n=2981）；
   - 超时格平均触发 **25.1 次 "changed browser state" 链断裂**（通过格 6.4、失败格 10.9）——每次断裂强制"停链→重感知→再来一轮 LLM"，把每个动作的成本从 1 轮变成 ≥2 轮；
   - 单步 nav 循环上限是 **50 轮**（`testzeus_hercules/core/runner.py:24` `browser_nav_max_chat_round=50`），没有按时间预算——一个 planner 步可以烧 5-8 分钟。

**三格抽样**【实证，日志时间戳与 TOKEN_COUNT 行】：

| 格 | 结局 | planner 轮 | executor 轮 | 状态刷新断裂 | get_interactive 执行 | 关键事实 |
|---|---|---|---|---|---|---|
| multi-layouts | 引擎 600s 超时；页面在 4.3 分钟即 raw=1（共 5 条 raw=1） | 3 | 61 | 25 | 12 | **64 轮中 46 轮发生在页面已通过之后**（HUD 被加固隐藏，agent 不知道自己已过） |
| email-inbox-forward | 引擎 600s 超时；4 条 raw=-1（=4 个 240s 页面计时窗全部耗尽） | 3 | 101 | 48 | 33 | nav=15：15 次同 seed 重导航，每次重开局重置 240s 并丢弃现场 |
| use-colorwheel-2 | 引擎 600s 超时；页面 raw=0.537 已过 | 3 | 62 | 5 | 7 | **65 轮中 56 轮发生在页面已通过之后**；单步内 executor prompt 涨到 27k tokens |

其他语料级事实【实证】：

- planner 首轮 plan 含显式"verify the task completed"收尾步的格：**58/121**（Then 子句 + planner prompt 的 closure/assertion 要求共同诱导）；
- 全部导航中 **249 次同 tab、18 次 force_new_tab**（93% 重导航走同 tab reload）；
- official_failed 中终局记录为页面 240s 超时（reason='timed out'）的格：**25 格**；
- 每个 executor 轮 completion token：中位 326、p90 1998、max 7521——输出冗长直接拉高每轮延迟。

---

## 1. 假设清单（按 预期影响/成本 排序）

| # | 假设 | 类别* | 预期影响（官方口径 43.2% 基准） | 成本 | 证据强度 |
|---|---|---|---|---|---|
| H1 | 判分修复：页面奖励优先于引擎 infra 状态 | **判分中立修复** | **+4.0pp（+5 格）**，43.2%→47.2% | 极低（orchestrator 一个函数内改优先级） | 强【实证】 |
| H2 | 重导航治理：补丁层单次开局（可选项 B）为主 + prompt 撤销"刷新教学"为辅 | B=环境硬化（披露）；A=能力增强 | 直接 +0.8pp（email-inbox-delete）+ 大量时间税回收（间接）；r1 复盘口径下官方数字几乎不变（0 洗白） | 低-中 | 强【实证】 |
| H3 | 延迟压缩：撤掉"动作后强制重感知"的双倍轮次 + 输出克制 + 收尾验证减负 | 能力增强（须 ablation） | 超时桶 24 格 ×（30-50% 轮次压缩）→ 估计 +3~8pp | 中（引擎 prompt + `simple_hercules` 小改） | 强【实证】+ 影响区间为推断 |
| H6 | 分角色模型路由：nav 用快模型、planner 用 v4-pro | 能力增强（须 ablation） | 与 H3 同桶叠加：超时格 executor 轮延迟若减半（9.0s→4.5s），51 轮 × 4.5s ≈ 省 230s/格 | 低（harness 侧 `agents_llm_config.json`，零引擎改动） | 机制【实证】，收益【推断】 |
| H4 | Gherkin 模板附加任务上下文提示 | 环境增强（披露 + ablation） | 针对 25 格页面 240s 死亡 + HUD 盲区误判；估计 +2~5pp | 低（`goal_reader.render_feature` 模板追加固定段） | 机制【实证】，收益【推断】 |
| H7 | 页面计时 240s→480s 对照 | 口径变更（仅 ablation，不作 headline） | 上界 25 格（20pp），现实转化估计 +4~10pp；须与 H3/H6 分开测量 | 中（重跑 125 格，token 预算约 ×1.3） | 桶大小【实证】，转化率【推断】 |
| H8 | （可选）引擎停在首个终局奖励（stop-at-terminal） | 口径变更（披露） | +0.8pp（email-inbox-delete）+ 平均 wall 时长显著下降 | 中（orchestrator 监视 rewards → SIGTERM 子进程） | 机制【实证】 |
| H9 | 运维：402 熔断 + per-attempt 日志目录 | 判分中立修复 | 0pp（防污染：r1 有 3 行 4-5s 的 402 no_junit 重试行） | 低 | 强【实证】（复审 §4 附注/§7） |

\* 类别定义：**判分中立修复** = 只把已有事实按既定口径算对（无争议）；**环境硬化/增强** = 改变 agent 所见环境或模板（须披露，建议 ablation）；**能力增强** = 改引擎行为/prompt/模型（须对照实验）；**口径变更** = 改计时/停止语义（必须披露且不作 headline）。

---

## 2. 各假设的证据与方案

### H1 判分修复：页面奖励优先于引擎超时（判分中立，无争议）

**机制**：`record2gherkin/benchmark/orchestrator.py` 的 `build_result_row`（L199-211）把 `runner_status == STATUS_TIMEOUT` 放在 reward 判定**之前**：引擎 600s 被杀 → 无条件 `status=timeout`（判负），即使页面已在 240s 窗口内投递 raw>0。而 §0 口径 4 规定页面原生奖励是**唯一**判分权威；`runner timeout` 只是进程级 infra 事件，不该覆盖页面终局。

**r1 证据**【实证，复审 §2 桥接】：59 首实例通过 − 6 反向自伤 + 1 重试翻正 = 54 官方。被 infra 状态覆盖的页面通过共 5 格：
- 600s 超时压过页面通过：login-user-popup、multi-layouts、use-colorwheel-2（后者页面 raw=0.537>0）；
- 重试行 402 崩溃 no_junit 压过 attempt1 页面通过：click-pie、click-collapsible-2-nodelay。
- 反例（**不该**被修复救回）：email-inbox-delete 首实例 raw=1 后重导航终局 raw=-1 —— `/latest` 是 last-wins，修复后仍判负，正确。

**精确方案**（orchestrator 侧，一处改动）：

```python
# build_result_row 内，替换 §7.2 优先级为：
raw = _reward_raw_value(reward)
if runner_status is None:
    status = STATUS_NO_GOAL                      # 未执行，不变
elif raw is not None and raw > 0:
    status = STATUS_OFFICIAL_PASSED              # 新：窗口内正奖励压倒 infra 状态（口径4）
elif runner_status in (STATUS_TIMEOUT, STATUS_NO_JUNIT):
    status = runner_status                       # infra 状态保留（仍可进入重试池）
elif reward is None:
    status = STATUS_NO_REWARD
else:
    status = STATUS_OFFICIAL_FAILED              # raw<=0 或 junit 与页面一致的负例，不变
```

配套（同 commit）：结果行新增 `runner_status` 字段（当前行里只有 duration/junit 间接可推），使"引擎超时但页面已过"的救援格可审计；`fetch_reward` 仍取 `/latest`（last-wins 跨 attempt 语义不变，不新增任何洗白通道——重导航仍被 H2 扫描披露）。

**预期影响**：+5 格 = **+4.0pp**（43.2%→47.2%，官方口径与首实例口径重合）。附带收益：页面已过的格不再进 infra 重试池（r1 可省 2 次重试预算）。
**风险**：极低。语义变化是"把口径执行对"，官方 last-wins、分母 125、扫描披露位全部不变；对 r1 数据回放即可验证（54→59）。
**成本**：一个函数 + 单测；r2 跑前用 r1 数据做 replay 单测。

### H2 重导航治理（两方案对比）

**r1 证据**【实证】：61/125 格重导航 ≥2 次（最多 15 次）；格内洗白 0；净伤害 −4.0pp；1 格直接致败（email-inbox-delete nav=8，raw=1→−1）；email-inbox-forward 15 次导航烧掉 4 个完整 240s 计时窗。机制层面有两处共谋：
- **harness 侧机制**：补丁 A 对每次同 URL load 都自动开局（`miniwob_server.AUTO_START_PATCH`）——重导航 = 重置 240s + 丢弃失败提交，成本近乎为零（R1-pre V4 遗留）；
- **引擎 prompt 教学源**：`browser_nav_agent.py` L20 *"To refresh a page, open the same URL again using the appropriate navigation tool"*、L116 *"When a page refresh is needed, navigate to the current URL again using the appropriate tool"* ——prompt **主动教**了"刷新=重开 URL"。

**方案 A（引擎侧 prompt 约束）**：删除/改写上述两行，替换为："卡住时不要重开任务 URL——重开会重启任务并丢弃全部进度；改用其他感知工具（get_interactive_elements / get_page_text）重新审视页面，或如实报告受阻"。planner 侧同步加一句"不要将重开 URL 作为重试手段"。
- 优点：零披露负担、零环境变化；缺点：prompt 顺从性无保证（r1 已有 "After 3 repeated failures STOP"（L134）仍未拦住重导航）→ **单用不可靠**。

**方案 B（补丁层单次开局，R1-pre §4 可选项 B）**：补丁 A 开局成功后写 `sessionStorage["r2g_started"]`；同 tab 二次 load 检测到标记则**不再自动开局**——页面停在未开局状态，该格自然变成可检测的 no_reward/失败，而不是"可重置计时的免费重试"。
- **效力**【实证支撑】：r1 的 249/267 次导航是同 tab（93%），sessionStorage 恰好覆盖主要路径；force_new_tab 的 18 次会拿到新 sessionStorage，属残余缺口（可在实现中同时计数披露）。
- **对 r1 官方数字的影响 ≈ 0**：r1 重导航洗白为 0——没有任何一格靠二次开局翻正，因此封死该通道不改变任何已得成绩，只把"隐藏的时间税"变成"显式的失败"，并省掉 multi-window 空烧。这是它接近判分中立的论据。
- **代价/风险**：改变了任务环境（须在 spec §3.2/§12 回写 + 报告披露 + 重跑 pilot 验证）；误触 reload 的格会被"锁死"——但 r1 证据表明误触 reload 本来就在净伤害，锁死只是把伤害显式化。

**结论**：**B 为主（环境硬化，披露后采纳）、A 为辅（能力增强，随 H5 的 prompt commit 一并做并 ablation）**。两者不互斥：B 堵机制，A 纠行为。

### H3 延迟压缩（executor 循环是主战场）

**机制与证据**【实证】：见 §0 画像。三个可动手的点，按杠杆大小排序：

1. **"动作后强制重感知"的双倍轮次**（最大杠杆）。`simple_hercules.py:548-570,662-675` 的 `_BROWSER_STATE_CHANGING_TOOLS` + `_requires_state_refresh`：每次成功的状态变更工具（click/entertext/open_url 等）都打断工具链、追加 "Re-read the current page/DOM before continuing"，强制下一轮 LLM 先重感知。multi-layouts 25 次、email-inbox-forward 48 次断裂——每个交互从 1 轮变 ≥2 轮。方案：动作结果里已含工具自带的回执（click 工具返回成功文本），把"强制重感知"降级为"仅在工具结果含模糊/错误标记时打断链"，或允许链内最后一个动作后直接终止本步。位置：**引擎代码**（非 prompt），属能力增强，须 ablation。
2. **nav prompt 的"先全量感知"教学**（`browser_nav_agent.py` L41 rule 8 *"ALWAYS analyze ALL page elements ... FIRST"* + Technical Guidelines STEP 1-5）：与 1 叠加后每步至少 1 次感知轮。方案：改为"若本步上下文已含可用 DOM 快照则直接行动；仅在状态变更后或数据缺失时重感知"。位置：**引擎 prompt**（H5 清单项）。
3. **输出克制**：completion p90 1998 tokens——nav/planner prompt 各加一句"响应必须简短：只列动作与结果，禁止长篇推理文本"，或评估关闭 reasoning 输出。每轮延迟近似正比于输出长度。
4. **单步轮上限与时间预算**：`browser_nav_max_chat_round=50` 无时间维度；一个步可烧 5-8 分钟（multi-layouts 单步平均 202s）。方案：给 `_run_nav_agent` 加步级 wall budget（如 120s 或 20 轮），到限即带当前进展返回 planner，由 planner 决定继续/换路/终止。风险：提前打断可能造成 planner 循环重派——须与 H2A 的"禁止重导航"约束同 commit 落地。

**Then 步与收尾验证**【实证 + 修正认识】：58/121 格的 planner 首轮 plan 含显式 "verify the task completed" 收尾步；且 planner prompt（`high_level_planner_agent.py` L198-204 Closure Nudge Examples、L300 rule 5 "Final step must always include an assertion"）要求每步带验证性 closure nudge。但 **HUD 被我们自己的安全加固隐藏**（`miniwob_server` 补丁 A stub 了 updateDisplay）——agent 在页面上**永远看不到成功指示**，验证步注定空转（multi-layouts 页面通过后 46/64 轮、use-colorwheel-2 56/65 轮都在"验证/修补一个已经结束的任务"）。处理：不删 Then 子句（口径模板），而是在 H4 的上下文提示里明示"页面无成功提示，完成要求动作即应报告并终止"，并按 H5 修 planner 的 closure 规则。

**预期影响**：超时格 executor 轮次压缩 30-50%（51.3→26-36 轮）× 每轮 9s ≈ 每格省 140-230s LLM 时间；24 个超时格中估计 3-8 格因此完成 → **+2.4~6.4pp**；同时对 25 个"页面 240s 死亡"格也有间接帮助（更快跑完动作序列）。

### H4 Gherkin 模板改进（附任务上下文提示）

**现状**【实证】：`goal_reader.render_feature` 输出 4 行模板，When 为指令原文（口径不变），无任何任务上下文。三个实测痛点：
1. **HUD 盲区是我们自造的**：安全加固隐藏了 reward HUD，页面无任何"已完成"信号，而模板 Then 却承诺"task should be completed successfully"——agent 会去找一个不存在的成功指示（见 H3 第 4 点证据）。
2. **措辞陷阱家族**：choose-date×3、book-flight×2 集中失败（r1 报告 §4.4）；use-autocomplete 的 "Enter an item that starts with 'Ni' and ends with 'ue'" 需要先理解"指令同文显示在页面 #query 区、需键入前缀触发候选"这类交互范式；日期类存在 MM/DD/YYYY 格式/locale 歧义。
3. **单页性未声明**：agent 偶发把任务当成"网站"去探索（重导航的间接诱因之一）。

**方案**（harness 侧 `render_feature` 追加固定注释段，When 指令原文保持逐字）：

```
  # Notes: The instruction above is also shown at the top of the page (#query).
  # This is a single-page task: do not reload or navigate away; progress is lost.
  # The page shows NO success/failure message. Once you have performed the
  # requested action (including any required submit), report completion immediately.
```

**位置/成本**：`record2gherkin/benchmark/goal_reader.py` 模板一个字符串段；所有格一致生效。
**分类与纪律**：这是环境增强——**不判分中立**，须披露（模板 diff 进 manifest 已由 git_rev 覆盖）并在 r2 内做 with/without 小对照（或与 r1 对照并注明多变量）。预期 +2~5pp（主要作用于 25 个页面超时格与验证空转格）。

### H5 引擎侧 prompt 调优点清单（独立 commit、判分保持中立）

允许修改 `testzeus_hercules/` 的 prompt（这是本 fork SOTA 冲刺的核心手段），但每个点独立 commit、harness 判分零改动、manifest 依 git_rev 可追溯。值得改的点（按优先级）：

1. `browser_nav_agent.py` L20 + L116：删除两处"刷新=重开 URL"教学，替换为 H2A 文案（重导航治理的 prompt 半边）。
2. `browser_nav_agent.py` L41（rule 8）+ Technical Guidelines STEP 1-5：放宽"每步必须先全量感知"，允许利用本步已有 DOM 快照直接行动（H3 第 2 点）。
3. `high_level_planner_agent.py` 系统提示瘦身（现 322 行/首轮 ~3.3k tokens）：删 Platform Awareness（Salesforce/SAP）、Test Data Focus/Iteration、Executor Operation Detection 等 MiniWoB 无关章节（可保留通用句）；预期 planner 首轮延迟与每轮 token 双降，且减少 planner 生成"数据驱动多轮迭代"式过度计划。
4. `high_level_planner_agent.py` closure/assertion 规则（L198-204、L300 rule 5）：改为"helper 已回报动作+submit 完成即终止；单动作任务不要追加独立验证步；不要要求 helper 检查页面上的成功提示（本环境无成功提示）"。
5. 两个 nav/planner prompt 各加输出克制句（H3 第 3 点）。
6. planner `_json_instruction`（L59-74）与 prompt 正文存在重复的 terminate 规则——合并为一处，降低自相矛盾风险。

**纪律**：全部属能力增强；除第 1 点（与 H2 配套、有强行为证据）外，建议在 r2 内按"prompt 包"整体 ablate 一次，而不是逐点跑 125 格（预算不允许逐点对照）。

### H6 分角色模型路由（deepseek-flash nav + v4-pro planner）

**现状**【实证】：harness 用单一 `LLM_MODEL_NAME=deepseek-v4-pro` 注入全部角色（`record2gherkin/evaluation/runner.py:41,150` env），引擎内 planner 与 nav 走同一模型。executor 轮是延迟主体（§0：均值 9.0s/轮、超时格 51 轮）。
**方案**：harness 侧生成 `agents_llm_config.json`（引擎已有 `AgentsLLMConfigManager` 按 agent 路由的机制，AGENTS.md 记载），把 `browser_nav_agent`（及 api/sec/sql 等 nav 角色）指向 deepseek-flash，planner 保留 v4-pro。**零引擎代码改动**。
**预期收益**【推断】：executor 轮若提速 2×（9.0s→4.5s），超时格平均省 51×4.5≈230s，与 H3 叠加直接压缩延迟桶；成本同步下降（executor 轮 token 占比高）。planner 轮仅 3-4 次/格，保留强模型对规划质量影响面小。
**风险**：nav 的 DOM grounding（md 定位）与工具链纪律可能回退——flash 模型对长 system prompt 的顺从性更差，可能放大 H2/H3 的行为问题。**必须 ablation**（pilot 10 格先行，再决定全量），并单独成组以便归因。

### H7 页面计时口径 240s→480s 对照实验

**设计**：
- 同 exp_id（`miniwob-r1-t480` 作为 exp_id 会改变 seed——**注意** `derive_seed(exp_id, task_id)` 依赖 exp_id 字符串；若要保持 125 格 seed 与 r1 完全一致以便逐格配对，须**复用 exp_id=`miniwob-r1` 但换 `--exp-root`** 到新目录）；
- `--episode-ms 480000`（CLI 已支持，`tasks.py` 默认 240000）+ 引擎 timeout 同步放大（`--timeout-s 840`，保持"页面 + planner head-room"比例）；
- 判分逻辑不变（H1 修复后版本）；先跑 pilot 10 格，全量仅在预算允许时跑；
- **披露要求**：作为 ablation 章节呈现（"计时敏感性"），绝不进 headline；与 H3/H6/H4 的效果分开归因（不同实验组）。

**为什么值得跑**【实证】：official_failed 中 25 格终局记录是页面 240s 超时（含 ascending-numbers、daily-calendar、highlight-text×2、order-food 等多步任务）；240s 对 10s 原版已是 24×放宽，但对"每步 5-23s LLM 延迟 × 多步"的 agent 仍是主要失败桶。上界 20pp，现实转化估计 +4~10pp（drag/draw 等视觉盲区家族不会因时间转化）。
**成本**：125 格重跑，token 预算约为 r1 的 1.3-1.6×（墙钟时间上升）。

### H8（可选）引擎停在首个终局奖励

**机制**：r1 里引擎对页面 endEpisode 完全无感，通过后的格平均空转 5+ 分钟（三格抽样 46/64、56/65 轮）。orchestrator 可在 cell 运行中监视 `rewards.jsonl` 对本格的首条记录，SIGTERM 子进程并直接按该奖励判定。
**口径影响**：把"last-wins"实质变为"first-terminal-wins"——这是**口径变更**，必须披露。r1 回放：官方 54→55（救回 email-inbox-delete），并大幅降低平均 wall 时长（r1 全程 wall≈336s/格，估计可降 20-30%）；同时从机制上消灭 V4 重导航重试通道（与 H2B 二选一或并用）。
**风险**：JUnit 缺失（no_junit）需在判定路径豁免；与"披露的 infra 重试"语义交互要在 spec 写清。**若 H2B 采纳，H8 的边际收益主要是省时而非防作弊**——列为可选。

### H9 运维修复（判分中立，随手做）

- 402/余额熔断：orchestrator 检测重试 attempt 秒崩（4-5s no_junit + 402 字样）即熔断，不再消耗重试预算（r1 浪费 3 行）。
- per-attempt 日志目录：`runs/<run_id>/attempt<N>/stdout.log`（复审 §7.1），消除 attempt1 证据被覆盖的问题。

---

## 3. 建议纳入 r2 的组合

### 主推组合（SOTA 冲刺，全部带披露与归因设计）

1. **H1 判分修复 + H9**（判分中立，先行落地，r1 数据 replay 单测）→ 基线直接 47.2%；
2. **H2 方案 B**（sessionStorage 单次开局，pilot 验证后全量）+ **H2 方案 A/H5-1**（prompt 撤销刷新教学，独立 commit）；
3. **H3 延迟包**（状态刷新链降级 + nav prompt 感知放宽 + 输出克制 + 步级预算，作为一个"prompt+小代码包"整体 ablate）；
4. **H6 路由**（flash nav / v4-pro planner，pilot 先行，独立成组）；
5. **H4 模板上下文提示**（with/without 小对照或与 r1 对照并披露）；
6. **H7 480s 对照**作为计时敏感性 ablation（预算允许时）。

实验矩阵建议：核心组 = 1+2（对外 headline，声称最干净）；增强组 = 核心 + 3 + 4 + 5（SOTA 数字）；ablation = 6 与 3/4/5 的拆组（pilot 级）。全部结果同表披露三口径（官方/首实例/零重导航子集——H2B 后子集=全量，指标自然退化，报告须注明）。

### 保守组合（最小披露负担，数字可防御）

仅 H1 + H9 + H2B（pilot 验证后）+ per-attempt 日志：官方口径 47.2%→约 **47.2-48%**，全部改动属"判分中立修复 + 环境硬化（防灌水方向）"，无能力增强声明，可比性最强。

### 预期合计影响区间

- 保守组合：**47.2%~48.2%**（+4.0pp 判分修复 + ≤1pp H2B 时间税间接效应）；
- 主推组合（不含 H7）：**50%~56%**（47.2% 基线 + H3/H4/H5/H6 合计 +3~9pp，进入可比带 A 档 47-60% 中部）；
- 主推 + H7（480s ablation 单列）：ablation 口径 **54%~62%**（不进 headline）。
- 触及 Agent-E 81.6% 仍需视觉/几何能力补齐（r1 报告 §3 归因③），不在本轮假设清单射程内——只作差距备注。

---

## 4. 附注（范围外发现，仅记录不处理）

- `PlannerAgent.__init__` 与 `simple_hercules` 里有多处裸 `print`（L24-26、L489-494、L729-732），违反仓库 T20 规则；benchmark 子进程里它们混入 stdout.log 增大日志体积，建议随 H5 commit 顺手清理。
- `use-colorwheel-2` 的重复 POST 机制（EP_TIMER 守卫 + hook 重发陈旧值）是良性的，但 r2 若采纳 H8 需把"首条记录"与"陈旧重发"区分（按 raw/done 相同即视为同终局即可）。
