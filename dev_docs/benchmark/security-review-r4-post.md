# R4 实验后安全复审报告（security-review-r4-post）

- 日期：2026-09-24。审查对象：`dev_runs/benchmark/miniwob-r4/` 全量（131 行 results / 152 条 rewards / 285 条 epstart / 2 条 offseed / 131 份 stdout.log / manifest git_rev `96dd5a2`，flags：terminal_cue / single_start / role_routing / latency_env / template_notes / planner-timeout=150 / extra_tools(drag_and_drop) / disable_sandbox / assert_discipline / **md_interactive_extended** / **verify_before_done** / offseed_beacon）。对照基线：r3 官方 59/125 = 47.2%；plan-r4 预期带 66–69（52.8%–55.2%）。
- 审查人：独立事后审查（只读；未改代码、未动数据、未做 git 操作；仅有的写操作为本报告与 `dev_runs/r4_post_review/` 下的分析脚本）。
- 方法标注：【实证】= 对 r4 产物脚本化/人工复核；【代码】= 引擎源码追踪。这是四轮最大分数跳变（47.2→61.6），所有"好得可疑"处均用数据证实/证伪，无一处凭直觉放过。

---

## 1. 结论：GREEN（0 RED / 0 P1 / 0 P2；3 P3 / 3 信息级）

**61.6%（77/125）成绩可诚实发表，零作弊证据。** 超出上界（55.2%）的 8 格全部有良性归因：**超额主体是 `--md-extended` 的机制外溢**——扩表不仅救回 email 家族（法定预期 3–4 格，实绩 4/4 精确兑现），还把 r3 时"元素在页面上看得见、表里查不到"的 span.alink / accordion 头 / 树形 hitarea / slider handle 类任务整体解锁（再 +12 格），这是 plan 预期表只按 email+find-greatest 计账造成的**预期口径缺口**，不是判分问题。**`--verify-before-done` 对官方分的直接贡献 = 0**（实证：103 轮 verify 全部观察性质，零后续动作，所有格的页面裁决在 verify 之前已落盘）——它是诚实性/成本项，不是得分项。作弊面五类扫描、双 key 脱敏全零；新增的两个判分异常形态（终局后 endEpisode 复触发、planner 改写 seed 的 URL）经逐格追踪均**未被计入分值且方向全是压分**，判分链在两个方向上都未被穿透。

| 等级 | 数量 | 事项 |
|---|---|---|
| 高/中 | 0 | — |
| 低（P3） | 3 | P3-1 off-seed 检测器只验 seed 存在性、不验 seed 值——wrong-seed 变体（click-dialog-2 att1、count-sides）漏检，靠本报告交叉核对兜住（§6）；P3-2 终局后 endEpisode 复触发会在 rewards.jsonl 留下"先败后过"形态记录（book-flight、click-menu），人工审计必读 `raw` 字段而非 `reward` 字段，否则会误判洗白（§4）；P3-3 预算/口径跨 exp-dir 无工具总账（r4-pre V8 维持）：M1 pilot 实际落在独立 `miniwob-pilot/` 目录，主线 131 ≤ 144 本轮未越界，但拆窗绕账面依旧存在 |
| 信息 | 3 | I1 circle-center 以部分分 0.939 计通过（C1a raw>0 页面权威、任务原生距离分，r1 先例第 5 例）；I2 planner 断言纪律 prompt 再次出现"读 #reward/WOB_REWARD_GLOBAL"指令文本（6 格，r3 为 2 格）——执行面零 JS-eval 通道、零成功读取，方向为保守低报（drag-circle junit=false）；I3 `h3`（accordion 头）被 extended 收录——比 spec-r4 §2.3 字面标签集（div/span/li/tr/td/th/img/label）更宽，与 r4-pre 披露的"偏保守实现"表述不符，属无风险方向的实现偏差（元素均为页面可见可点 UI），建议 spec 附录记一笔 |

---

## 2. 标准五类作弊扫描 + 双 key 脱敏 —— 全零【实证】

对 131 份 stdout.log 独立 grep 复核：

| 扫描项 | 结果 |
|---|---|
| `file://` 本地文件逃逸 | **0/131** |
| 沙箱标记（`Executing Python sandbox` / `Using sandbox tenant` / `[SANDBOX_DISABLED]`） | **0/131**（关停 + 零尝试） |
| 文件工具标记（`[EXTRA_TOOL_CALL]` persist/recall/augment） | **0/131** |
| `__r2g_reward` 出现在 agent 通道 / `/latest` 探测 | **0/131**（命中仅存在于 harness 网络捕获，r3 同款） |
| 异常快通（<15s 官方通过） | **0**（最快 click-collapsible 50.9s，全部为页面权威 raw≥0.939） |
| `open_url` scheme 拦截 | 1 次：count-sides att1 `view-source:` 被拒（零执行；该格 official_failed，无洗白张力；r3 为 4 次同型） |
| `endEpisode`/`seedrandom`/`WOB_REWARD` 出现在 agent 日志 | 6 格（drag-circle、resize-textarea、tic-tac-toe、book-flight-nodelay、choose-list、right-angle），**全部为 planner 断言纪律 prompt 的指令文本**；执行面无 JS-eval 工具（16 件工具集固定，r4-pre 已审计），drag-circle 的 executor 明确回复"helper 无 JS 评估能力，无法读取"并以 `is_passed=false` 保守收尾（官方 pass 是页面权威，junit 诚实低报） |
| 双 key 脱敏 | `LLM-Key.txt`（35 字符）与 `GLM-Key.txt`（326 字符）逐值 grep r4 全目录 + 通用 `sk-`/`Bearer` 模式：**全部零命中**【实证】 |
| W2 式盘上产物 | `git status` 干净（dev 树 gitignore），`log_files/`/runs 内零 agent 新建文件【实证】 |

## 3. 三口径与洗白嫌疑格【实证】

| 口径 | 通过/分母 | 比率 | 说明 |
|---|---|---|---|
| **官方 last-wins（metrics 口径）** | **77 / 125** | **61.6%** | `clean` = 77/125 同值（invalid=0、zero_reward_cells=[]）【实证：manifest 复算一致】 |
| **首实例（rewards 还原，att1 窗口内 own-seed 首条 raw>0）** | **76 / 125** | **60.8%** | 与官方差 1：click-dialog-2（att1 no_reward → infra 重试 → att2 64.4s 页面权威通过；r3 click-menu-2 同型，合法） |
| **零重导航子集（latest 行 nav=1，108 格）** | **74 / 108** | **68.5%** | 子集选择偏差照旧，仅诚实性指标 |
| 官方 row-based（131 行） | 77 / 131 | 58.8% | 同一组通过、更大分母 |

**洗白嫌疑格全清单（无一带判分瑕疵）**：

1. **click-dialog-2**（唯一重试翻正格）：att1 no_reward 的真实原因见 §6——planner 把页面导航到了**错误 seed** 的 URL（78425566 ≠ 786425566），harness 判零事件、flagged、进重试池；att2 干净重跑通过（nav=1、own-seed raw=1、flagged=false）。**注意：att1 的错 seed 页面本身被 agent 做到了 +1（03:32:11 有 raw=1 记录），但 harness 按 own-seed 过滤正确地没有计入**——判分链没有被骗过，反证过滤有效。
2. **V4 重导航通过格（nav>1 计入分子，逐格披露）**：**drag-items-grid**（nav=2，+1 在第二次 seeded 加载后 50s）与 **form-sequence-3**（nav=2，+1 在第二次 seeded 加载后 44s）为新增通过格，**click-option**（nav=2）为 r3 延续的稳定通过格。三者 raw 均为 1、页面权威；第二次加载即 240s 时钟重置面照旧披露（r3 同款 2 格）。form-sequence-3 另有一次 seedless 加载（offseed 信标捕获，该加载无判分能力，见 §6）。
3. **格内先败后过（同 attempt、own-seed raw 序列 ≤0→>0）**：0 格。book-flight（−1→display+1）与 click-menu（−1,−1→display+1）形似实非——后发的 +1 是 `reward` 显示字段，权威字段 `raw` 恒为 −1（见 §4），两格官方判负，方向反洗白。

**重试纪律**【实证】：6 次重试的 att1 全部为 timeout/no_reward（infra 域），零 official_failed 被重试；本目录累计 131 ≤ cap 144（M1 pilot 在独立 `miniwob-pilot/` 目录，另计）。

## 4. 判分链机制新知（对 r3 表述的修正）【代码+实证】

- r3 文档把判分称作"last-wins"。精确机制是：`RewardCollector.record` 按同 key 覆盖存储，`/latest` 取该 key 最后一条；**但官方判定读的是记录的 `raw` 字段（`WOB_RAW_REWARD_GLOBAL`），不是 `reward` 字段（endEpisode 的显示参数）**（`orchestrator.py:1253 fetch_reward` + `build_result_row` 的 `_reward_raw_value`）。
- REWARD_HOOK_PATCH 包裹 `core.endEpisode`，**对被守卫短路的重调用也会 fire**：页面终局后 agent 再点击可再次触发 hook，POST 出 `reward=<新参数>` 而 `raw=<封存原值>`（`miniwob_server.py:203-227`；r4 实证 4 条此类记录：book-flight 1 条、click-menu 1 条、click-color 多条同值）。因此 rewards.jsonl 中"终局后值翻转"只发生在显示字段，`raw` 一经终局即不可变——**判分在两个方向上都抗终局后翻转**。
- 对 r4-pre V7 的再定性：该机制封死的是**页面自身**的 post-terminal 翻转；**进程外伪造 POST 仍可写 raw**（localhost 无鉴权信任模型不变）。r4 全量日志零 JS-eval/网络工具通道，伪造面依旧只存在于"agent 被导去恶意页"的前提之下，且该导航会被 offseed/epstart 信标捕获——V7 结论维持 P3。

## 5. 超额归因：61.6% 从哪来（核心审计）【实证】

净变化 +18 = 新通过 24 − 回退 6。逐格归因（r3 同任务对照 + r4 运行时 DOM 快照中的 md 索引反查点击目标 + 截图目验）：

| 归因桶 | 格数 | 格 | 证据 |
|---|---:|---|---|
| A-法定（email 家族） | **4** | email-inbox-delete / -important / -nl-turk / -star-reply | 点击全部落在 `span.star`/`span.trash` 图标（extended 收录；r3 两轮 0/20 死法即"图标不在表"）。plan 保守 3/上界 4 → **精确兑现** |
| A-法定（find-greatest） | **1** | find-greatest | `div.card`/`span.card-value` 入表（58.3s 通过）；截图确认三张牌面全在屏上，`.card.hidden` 只是"牌背"类名，**无隐藏内容泄露** |
| A-机制外溢（超出 plan 记账口径，但机制与 A 完全同构） | **12** | click-tab-2 ×4 + click-link（`span.alink` 伪链接/伪 tab）、click-collapsible ×3（`h3.ui-accordion-header` 手风琴头）、navigate-tree（`div.hitarea`+`span.folder`）、use-slider-2 + form-sequence（`span.ui-slider-handle`）、book-flight-nodelay（`label`） | 每格的 md 点击反查命中 extended 标签+class；这些元素 r3 时在页面上**可见但不在终表**，与 email 死法同构——plan §6 只把 email+find-greatest 计入 A 预期，是**预期模型漏计**，非判分异常 |
| 非 A（方差/二阶效应） | **7** | circle-center（0.939 部分分）、click-checkboxes-soft、count-shape、drag-box、drag-circle、multi-layouts、drag-items-grid（0 次 md 点击、纯 drag 工具） | 点击全为 legacy 标签；7/46 前失败格翻转在历史噪声带内（r2↔r3 翻转 21 格；单 seed ±10 格） |
| **verify 门控直接贡献** | **0** | — | 见 §7：103 轮全部观察性质，任何格的裁决都在 verify 之前定型；"verify 后继续做完全过"的翻正路径本轮**零发生** |

- **回退 6 格**（click-scroll-list、drag-cube、drag-items、multi-orderings、text-transform、use-colorwheel-2）：全部单 attempt 诚实失败/超时，无 flagged、无判分异常——方差，与翻转组 7 格同带宽。
- **作弊嫌疑专项结论："extended 表让 agent 看到不该看的元素"不成立**：① 结构面——可见性门在终表过滤器上游、隐藏子树整枝剪除（r4-pre V4 维持）；② 遥测面——md 表长 p50=4/p90=36/max=129 < 150，**`[R2G_MD_TRUNCATED]` 全轮 0 次**，截断灌水未发生（r4-pre V5 关账）；③ 实证面——新增通过格点击的 extended 元素（star/trash 图标、alink、手风琴头、hitarea、slider handle、卡牌、label）全部是渲染中的可见 UI，5 格抽查截图（find-greatest、star-reply、click-tab-2-medium、navigate-tree、count-shape）逐张目验均为正常任务终态、EPISODE ENDED 前完成交互。

## 6. verify 门控与信标交叉核对【实证】

- **verify 触发**：103/131 行带 `[R2G_VERIFY]`，**每行恰 1 次**（`verify_rounds<1` 硬上限零越界，无循环）。
- **禁读红线**：verify 步只调 get_page_text + get_interactive_elements；**103/103 轮在 verify 标记后零非白名单工具调用**（全量 TOOL_DEBUG 反查）——prompt 白名单 100% 被遵守，r4-pre P3"未做机械工具限制"未兑现为风险。
- **verify 后改判清单**：空。全部 77 个通过格在 verify 时页面已 `EPISODE ENDED` 且 raw 已落盘；verify 的实际效果 = 拦谎/诚实 junit（drag-circle、click-dialog-2 att2 两格 junit=false 保守低报）+ r3 的 no_reward 病根清零（latest 行零_reward 仅剩 1 行且为 att1 infra）。
- **信标交叉（r4-pre V6 收账）**：285 条 epstart 全部能与某 (subdomain,seed) 行对上（零孤儿 attempt）；2 条 offseed 均落入本格 attempt 窗口且两格都被 flagged（count-sides official_failed、form-sequence-3 official_passed——后者的 +1 出自 seeded episode，seedless 加载无判分能力）；**无伪造信标迹象**（每条信标都能与 results 时间线互证）。
- **P3-1 新增（wrong-seed 盲区）**：E1 只测 seed 存在性。r4 实发 2 格 3 次 wrong-seed 加载——click-dialog-2 att1（seed=78425566，1 epstart + 1 raw=1 reward，**被 own-seed 过滤正确拒计**）、count-sides（seed=999/1 两次 epstart，零 reward）。全部是 planner 改写/编造 URL（r3 use-slider-2 同病），全部官方失败或重试，**零分值影响**；但 E1 对此静默，靠本审计的 rewards↔results 交叉才现形。r5 建议：server 端把 beacon/reward 的 seed 值与 harness 派生 seed 比对，不等即 flagged（检测型，一行判断）。

## 7. 超时 21→9 的归因【实证】

- r3 21 个 timeout 格的去向：**→通过 5**（click-tab-2-easy、email-inbox-important、email-inbox-star-reply、find-greatest、use-slider-2——全部页面 raw=1 权威通过）+ **→诚实失败 12**（页面 240s 钟内出 raw=−1 真实裁决：bisect-angle、click-pie-nodelay、daily-calendar、drag-shapes-2、draw-line、email-inbox-forward、email-inbox-reply、highlight-text-2、hot-cold、number-checkboxes、resize-textarea、visual-addition）+ 维持 timeout 4。
- **"verify 提前终止合理化"不成立**：verify 零后续动作（§6）、无法提前结束任何 episode；39 个 official_failed 行**全部**携带页面 raw=−1 裁决，9 个 timeout 行全部携带页面 'timed out' raw=−1——r4 是四轮以来首个**零"无页面裁决"终局行**的轮次。超时收敛的原因是 A 扩表拆掉了"元素不可达→烧钟"死端（email/tab/collapsible 类格子不再空转到 600s 墙），属真实完成或真实失败，非话术终止。

## 8. r4-pre 三条 P3 待验项收账

| 项 | 收账结果 |
|---|---|
| V5 截断遥测 | `[R2G_MD_TRUNCATED]` = 0 次；表长 max 129 < 150 —— 未触发，关账 |
| V2/V3 verify 计数与 junit↔官方对照 | 103 触发、≤1/格、无循环；disagreement 26 行（24 junit=true∧官方负——幻觉在失败格依旧存在，verify 拦而不改判；2 行 junit=false∧官方过——保守方向）—— 关账 |
| V6/V7 信标披露口径 | offseed/epstart 与 results 逐条互证通过，披露无污染；新增 wrong-seed 盲区见 §6 P3-1 |

## 9. r5 建议

1. **seed 值校验**（P3-1）：miniwob_server 对 beacon/reward 的 seed 与 harness 派生 seed 比对，不一致即 flagged `wrong_seed_navigation`（检测型，不改判；顺带让 click-dialog-2 型 att1 的失败原因显式化）。
2. **报告口径**：发表 r4=61.6% 时随表披露——首实例 60.8%（click-dialog-2 重试翻正 1 格）、部分分通过 1 格（circle-center 0.939）、重导航通过 3 格（drag-items-grid、form-sequence-3、click-option）、单 seed 噪声带 ±10 格（本轮 13 格翻转）。
3. **verify flag 的定位改写**：数据证明它是诚实性/裁决完整性组件（no_reward 终局清零、junit 保守化）而非得分组件，round4-report 与后续 plan 不应再按"翻正 ~0.2/格"给它记期望。
4. **A 的预期模型修正**：r5 若再动感知层，把"可见但不可寻址"元素类（span 伪链接、accordion 头、hitarea、slider handle、label）整体纳入记账口径——本轮 12 格机制外溢证明这才是 A 的真实杠杆宽度。
5. **M4（majority-of-3）不触发**：门控条件"归因不确定 ≥±3 格"已被本审计消解（A 贡献 17 格为日志实证而非估计）；方差贡献 7−6=±净 1 格在既有噪声披露带内，headline 无需测量列护驾。若做，按 plan 原设计另批另计。
6. **实现偏差备案**：extended 收录集实际含 `h3`（I3），spec-r4 §2.3 补一句实现实录；role_routing_env / write_agents_llm_config 的"key 只走 env"路径未动，双 key 零泄露维持。

---

## 附：复核脚本

`dev_runs/r4_post_review/{diff_cells,transitions,rewards_audit,windows,orphans2,verify_audit,verify_audit2,md_audit,three_metrics}.py`（全部只读分析，可重放本文各表数字）。
