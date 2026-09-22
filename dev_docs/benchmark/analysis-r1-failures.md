# R1 失败模式分析 — miniwob-r1 的 71 个未过格拆解

> 数据：`dev_runs/benchmark/miniwob-r1/`（results.jsonl 按任务去重 last-wins，取 125 任务口径；计时分解来自各格 `runs/*/stdout.log` 的时间戳行；页面事件来自 append-only 的 rewards.jsonl）。
> 分母一律为 **125 任务**。本文只给证据与估计，不给实现方案。所有"上界/保守"为两档独立标注；杠杆之间有重叠，合计时已做去重（§5）。

## 1. 桶量化总表（71 未过格）

| 桶 | 格数 | 占 125 | 定义 | 判定证据 |
|---|---:|---:|---|---|
| **A 判分瑕疵**（页面已过被判负） | **5** | 4.0% | 页面 `raw>0` 已到手，但被 600s 引擎超时/重试崩溃覆盖 | rewards.jsonl 页面通过事件 + status=no_junit/timeout |
| **B 引擎 600s 杀**（页面未过） | **23** | 18.4% | 进程组被杀；页面最终 raw≤0 | status=timeout(21) + click-collapsible-2/click-menu-2（终行 no_junit 崩溃，实质尝试为 600s 击杀） |
| **C 页面 240s 到点**（引擎正常收尾） | **25** | 20.0% | 页钟到期 WOB 回 −1，agent 收尾写 junit | status=official_failed 且 `reward_reason="timed out"` |
| **D 立即判负/答错** | **11** | 8.8% | agent 主动 submit/误点导致页面即时 −1 | status=official_failed 且 reason≠timed out |
| **E no_goal** | **4** | 3.2% | harness 未拿到目标：3 格 `empty utterance` + 1 格读 goal JS 报错 | click-color、email-inbox-{forward-nl, forward-nl-turk, nl-turk} |
| **F no_reward** | **3** | 2.4% | 全程 0 条 reward 事件（未判或未捕获） | drag-cube、draw-line、resize-textarea |
| 合计 | **71** | 56.8% | | |

**与 round1-report §2"超时 29"的对账**：29 是 130 行（含 5 个重试行）的 `status=timeout` 行数；按任务去重后实质发生 600s 击杀的格为 25（23 败 + book-flight 重试后过 + 2 格归入 A）。报告"no_goal/no_reward 6"实为 **7**（4+3），系笔误。真正的"超时现象"合计是 **B+C = 48 格（38.4%）**，是最大单一失分来源。

## 2. 超时桶深挖（B+C，48 格）——"延迟还是决策循环？"

**答：是"executor 微决策轮数 × 单轮 LLM 延迟"的乘积，且轮数膨胀是主因；工具执行和 planner 几乎不占时间。**

### 2.1 阶段计时分解（10 个引擎杀格逐格实测，时间戳行配对）

| 格 | span(s) | planner LLM | executor 轮数 | 轮间中位/均值(s) | 实测工具执行(s) |
|---|---:|---:|---:|---|---:|
| email-inbox-forward | 581 | 26 | **101** | 3.0 / 5.5 | 39.9 |
| email-inbox-star-reply | 585 | 24 | 78 | 9.0 / 8.9* | ~15 |
| email-inbox-delete | 567 | 16 | 71 | 5.0 / 7.8 | 14.4 |
| email-inbox | 573 | 20 | 72 | 4.0 / 7.7 | 23.3 |
| email-inbox-reply | 587 | 27 | 75 | — / 7.5 | ~15 |
| click-tab-2-easy | 578 | 23 | 59 | 6.0 / 8.5 | 13.2 |
| scroll-text-2 | 578 | 29 | 59 | 4.0 / 8.4 | 7.8 |
| hot-cold | 578 | 24 | 47 | 7.0 / 10.7 | 12.5 |
| click-pie-nodelay | 568 | 18 | 49 | 5.0 / 10.1 | 11.8 |
| tic-tac-toe | 577 | 35 | 36 | 10.5 / 15.4 | 10.2 |

*星号行为同法推算。10 格合计：planner LLM 242s（≈4%），实测工具执行 151s（≈3%），其余 **≈93% 是 executor 逐轮 LLM 延迟**（每轮 completion 仅 40-1000 token 的微决策：一次 hover/click/取 DOM）。
**对照基线**（12 个通过格均值）：executor 18.2 轮、planner 3.9 轮、span 158s。**被杀格平均 35-60+ 轮 vs 通过格 18 轮**——差距在轮数不在单轮延迟。
Planner 每格仅 2-5 轮、合计 16-58s；"planner 决策循环"不是瓶颈。**重导航浪费**：42/71 败格 nav≥2（通过格仅 16/54）；email-inbox-forward 16 次、email-inbox-important 11 次。

### 2.2 重导航会复位 240s 页钟（新发现，报告未披露）

重导航 reload 任务页 → WOB episode 计时器重开。email-inbox 系列把页钟"续"到远超 240s：email-inbox-forward 页面最终在 **+584s** 才到期（16 次导航），email-inbox/-delete 在 553/581s。页级预算对这类格完全失效，也解释了为何它们能烧满 600s。

### 2.3 僵尸运行：页面终局后引擎不停车

- **23 次实质 600s 击杀中，页面在此之前就已终局（−1/timed out）**，之后引擎平均空转 **273s**（合计 6280s ≈ 1.75h）：click-collapsible-2/drag-shapes-2/click-pie-nodelay 等 17 格 zombie 330-349s；hot-cold 226s；tic-tac-toe 95s；email-inbox 四格 16-47s。
- A 桶 5 格同理反向：页面通过后继续空转 412-573s（login-user-popup pass@27s 后空转 573s）。
- 同一根因：**引擎不读页面 done/reward 终态**，同时解释了判分瑕疵（A）与近半超时空转。
- 25 个 C 格收尾均值 401s（页钟 240s + 平均 161s 的 wrap-up/续跑），drag-shapes、use-slider-2、social-media-some 收尾近 580s。

### 2.4 60s helper 超时级联（放大器）

failure_message 明确记载"helper LLM 60s 双超时→放弃"的格：**ascending-numbers、click-menu、daily-calendar、drag-circle、find-greatest、order-food、use-spinner（7 格）**。机制：单次 executor/helper 调用遇延迟尖峰 >60s 被掐 → planner 把下一步改写为"timed out, retry"→ 重试或重导航（清空页面进度）。71 个败格中 **32 格的 planner 步骤含"timed out 重试"改写**（stock-market、choose-date-nodelay、login-user-popup 各 2 次，其余 1 次）。

## 3. 能力桶深挖（真失败：C+D+E+F 的能力成分 + B 的非收敛格）

### 3.1 家族聚类（71 败格全覆盖，* = 兼属 A 判分瑕疵）

| 家族 | 格数 | 成员（桶） | 主要死因 |
|---|---:|---|---|
| 拖拽/绘制/选区（无 drag 工具，`core/tools/` 无 mouse down-move-up） | 14 | drag-box(B) drag-circle(C+helper超时) drag-cube(F) drag-items(C) drag-items-grid(C) drag-shapes(C) drag-shapes-2(B) drag-single-shape(C) drag-sort-numbers(C) draw-circle(C) draw-line(F) resize-textarea(F) highlight-text(C) highlight-text-2(C) | 硬能力缺口：agent 只能 click/hover/keys，用 press_key_combination 冒充拖拽失败 |
| 画布几何/视觉感知（文本 DOM 盲区） | 11 | bisect-angle(C) circle-center(C) right-angle(D) find-midpoint(B) grid-coordinate(D) count-shape(B) count-sides(C) visual-addition(B) click-shades(B) click-pie-nodelay(B) use-colorwheel-2*(A) | 画布/SVG 无可读节点，全程 hover 空转（count-sides 12 事件 9 个 hover） |
| email-inbox 多步记忆 | 10 | email-inbox(B) -delete(B) -forward(B) -important(B) -noscroll(B) -reply(B) -star-reply(B) + 3 个 no_goal(E) | 轮数爆炸（71-101 轮）+ 重导航清进度；**-delete 首实例 raw=1 证明可解，后被 8 次重导航拖死** |
| 日期/日历控件 | 4 | choose-date(D) choose-date-easy(D) choose-date-nodelay(D) daily-calendar(C+helper超时) | 输入未落位就 submit；同族 choose-date-medium 用同一策略（bulk_enter_text+click）通过 → 交互方式可对但缺校验 |
| social-media 条件多步 | 3 | social-media(C) -all(B) -some(C) | 条件筛选（只赞 @xxx）+ 重导航清进度 |
| 多表单流程 | 3 | book-flight-nodelay(C) order-food(C+helper超时) form-sequence(D) | 步骤多×每步延迟；book-flight 兄弟格重试即过 |
| 文本变换 | 2 | copy-paste(C) copy-paste-2(D) | 剪贴板 press_key 组合链路不稳 |
| 控件/选择杂项 | 18 | click-checkboxes(D,-0.33) click-checkboxes-soft(C) click-menu(C+helper超时) click-tab-2(B) click-tab-2-easy(B) click-tab-2-hard(C) click-tab-2-medium(D) number-checkboxes(C) use-slider-2(C) use-spinner(D+helper超时) ascending-numbers(C+helper超时) find-greatest(C+helper超时) hot-cold(B) scroll-text-2(B) text-editor(B) tic-tac-toe(B) multi-layouts*(A) click-collapsible-2(B) click-menu-2(B) stock-market(D) click-color(E) | 逐格见 §3.2 |
| **判分瑕疵（跨家族，§1-A）** | 5 | click-collapsible-2-nodelay* click-pie* login-user-popup* multi-layouts* use-colorwheel-2* | 页面已过，600s 杀/重试崩溃覆盖 |

### 3.2 代表格定性证据（stdout 工具轨迹 + 终态 DOM + planner 记录）

| 格 | 错误类型 | 证据链 |
|---|---|---|
| **choose-date-easy**（D） | 交互方式错+零校验：`bulk_enter_text` 未落位，7 次盲点后对**空字段** submit → −1；planner 仍标全步"(Completed)" terminate=yes | 终态 text_only_dom：`Date:` 字段为空、Submit 在；轨迹 bulk_enter_text→click×7 |
| **click-checkboxes**（D） | 找错元素（多选边界）：3 目标点了 4 个 → 部分分 −0.33；随后**格内重导航清页**，丧失纠错机会 | 轨迹 click×4 → open_url → click×5；reward −0.333 |
| **drag-items**（C） | 交互方式缺失：无 drag 工具，用 click+press_key_combination 冒充 → 失败 → 2 次重导航 → 放弃 | 轨迹 open_url→click→press_key_combination；`core/tools/` 无 drag 实现 |
| **count-sides**（C） | 视觉盲区：SVG 多边形无可读文本，12 事件中 9 个 hover，0 次有效作答 | 轨迹 hover×9 + 1 次重导航 |
| **social-media-some**（C） | 漏步骤/清进度：hover+press_key+click 一次后立即 open_url 重载，丢失已完成状态 | 轨迹 6 工具 → open_url；reason=timed out |
| **stock-market**（D） | 理解错+误操作：页面渲染 `$undefined0`（未读出价格即动作），随后 6 次重导航 | junitmsg + 轨迹 open_url×6 |
| **use-spinner**（D） | 基建放大：helper 60s 双超时 → planner 步骤改写"(Failed - browser helper timed out twice)"→ terminate 失败，任务本身只是设 −7 | planner final_response 原文 |
| **highlight-text**（C） | 选区能力缺失：hover+click 无法做文本选区，helper 超时后仅剩 hover 空转 | 轨迹 hover/click×5，无选区工具 |
| **email-inbox-delete**（B） | **可解但自杀**：首实例 raw=1（安全复审实证），随后 8 次重导航 → 终局 −1 → 600s 杀 | security-review-r1-post §3 + rewards 序列 |
| **grid-coordinate**（D） | 视觉盲区+超时级联：click 超时 → 3 次重导航，从未完成一次有效点击 | 轨迹 open_url×4 |

**横向模式**：29/36 个 official_failed 格 junit_passed=true——**agent 自报成功而页面判负**（完成幻觉）。TERMINATE 依据是 helper 的"##TERMINATE TASK##"，不含页面 reward 校验。

## 4. 可恢复性矩阵（败格桶 × 杠杆；每格只计入一个"主杠杆"防止重复计数）

杠杆：L1 判分修复（页面奖励优先于进程超时）｜L2 延迟/轮次压缩｜L3 重导航治理（含页钟复位问题）｜L4 prompt/验证改进（submit 前字段校验、TERMINATE 须页面证据）｜L5 基建熔断（60s helper 熔断+快速重试、no_goal/no_reward harness 修复）｜L6 模型升级（视觉+规划）

| 桶（格数） | L1 | L2 | L3 | L4 | L5 | L6 | 桶小计 上界/保守 |
|---|---|---|---|---|---|---|---|
| A 判分瑕疵（5） | **5 / 4** | — | — | — | — | — | 5 / 4 |
| B 引擎杀（23） | — | 7 / 3 | 6 / 2 | — | 2 / 1 | 3 / 1 | **18 / 7** |
| C 页面到点（25） | — | 4 / 2 | — | — | 5 / 2 | 8 / 3 | **17 / 7** |
| D 立即判负（11） | — | — | — | 6 / 3 | — | 2 / 1 | **8 / 4** |
| E no_goal（4） | — | — | — | — | 1 / 0 | — | 1 / 0 |
| F no_reward（3） | — | — | — | — | 0 / 0（修复后仅"可判"，仍需 L6 能力才能过） | （随 L6 计） | 0 / 0 |
| **合计（去重）** | | | | | | | **上界 49 / 保守 22** |

依据摘注（每格清单见 §1/§3.1）：
- **L1**：5 格页面 raw>0 是 rewards.jsonl 实证（click-pie@121s、click-collapsible-2-nodelay@188s、login-user-popup@27s、multi-layouts@83s、use-colorwheel-2@179s）。保守档扣 1 格给 use-colorwheel-2（raw=0.537 部分分，依赖阈值口径）。
- **L2 上界 7**（B 桶：count-shape、drag-box、drag-shapes-2、find-midpoint、text-editor、visual-addition、click-tab-2——nav≤3 且 executor≤45 轮，压缩到通过格的 18 轮水平即可进 240s）+ **4**（C 桶：book-flight-nodelay——兄弟格实证可过、copy-paste、click-tab-2-hard、number-checkboxes）。保守 5：只算家族有通过实证或差距最小的。
- **L3 上界 6**（email-inbox、email-inbox-delete、email-inbox-star-reply、email-inbox-noscroll 之外的主受害者 hot-cold、tic-tac-toe、click-pie-nodelay；email-inbox-delete 有首实例通过实证）、保守 2。
- **L4 上界 6**（choose-date、choose-date-easy、choose-date-nodelay、click-checkboxes、copy-paste-2、form-sequence；机制=submit 前校验字段非空/选中数正确，29/36 完成幻觉的格里最接近的一批）、保守 3。
- **L5 上界 6**（C 桶 helper 双超时 5 格中行为已近完成者：ascending-numbers、click-menu 直接受益；daily-calendar、find-greatest、order-food 还叠任务理解，计入上界）+ E 桶 1（click-color harness JS 修复即可跑；3 个 NL 邮件格所在家族 0/7 通过率，保守不计）。保守 3。
- **L6 上界 18**（B3+C8+D2：画布/拖拽/视觉家族在视觉模型下部分可解，Agent-E 81.6% 为同族锚点）、保守 6。
- **两档合计的含义**：保守 22 → 54+22=**76/125 = 60.8%**（A 档带上沿）；上界 49 → 54+49=**103/125 = 82.4%**（≈Agent-E 锚点）。**杠杆不可加总兑现，此为数学上界**；同一格常需 2 个杠杆同时到位（如 email 家族 = L2+L3）。

## 5. Top-10 高价值目标任务（救回概率 × 实现成本排序）

| # | 任务 | 主杠杆 | 救回概率 | 成本 | 依据 |
|---|---|---|---|---|---|
| 1 | login-user-popup | L1 | ~0.95 | ≈0 | pass@27s，空转 573s 后被判负 |
| 2 | multi-layouts | L1 | ~0.95 | ≈0 | pass@83s |
| 3 | click-pie | L1 | ~0.95 | ≈0 | pass@121s |
| 4 | click-collapsible-2-nodelay | L1 | ~0.95 | ≈0 | pass@188s |
| 5 | use-colorwheel-2 | L1 | ~0.9 | ≈0 | raw=0.537>0@179s |
| 6 | book-flight-nodelay | L2 | ~0.6 | 中 | 同族 book-flight 重试即过；34 轮×延迟的纯超时 |
| 7 | ascending-numbers | L5 | ~0.55 | 低 | 5 步点击任务死于 helper 60s×2 双超时 |
| 8 | choose-date-easy | L4 | ~0.5 | 低 | 空字段 submit；同族 medium 同策略已通过 |
| 9 | click-checkboxes | L4 | ~0.4 | 低 | 仅差 1 个标签（−0.33）；勿重导航即有纠错机会 |
| 10 | email-inbox-delete | L3 | ~0.35 | 中 | 首实例已通过（raw=1），被 8 次重导航拖死 |

（次选：click-tab-2-easy ~0.4/中、click-menu ~0.4/低、copy-paste ~0.35/低、find-greatest ~0.35/低。）

## 6. 数据口径备注

- 5 个重试格的 attempt1 stdout.log 被 attempt2 覆盖（复审 §4 同结论）；本文计时均取"实质尝试"（duration 最长）的留存日志。click-menu-2 的 600s 击杀行无留存日志（NO LOG），其归属依 results/rewards 交叉判定。
- `reward_reason`、"页面到期时刻"、zombie 时长由 rewards.jsonl 时间戳与 run 起止差分得出，秒级精度（stdout 时间戳粒度 1s）。
- "实测工具执行"只累加日志显式打印 `Command executed in Xs` 的工具（get_page_text/get_interactive_elements 等），click/hover 的执行耗时未单独打印，故 3% 是下界；即使全部算作工具耗时，executor 延迟占比仍 >85%。
- E/F 桶 7 格与报告"6 格"的差异见 §1 对账。
