# R2 失败模式分析 — miniwob-r2 的 67 个未过格拆解（r3 规划输入）

> 数据：`dev_runs/benchmark/miniwob-r2/`（results.jsonl 136 行按任务 last-wins 去重 = 125 任务；rewards.jsonl 147 事件按 (task, seed) 且限 run 窗口内过滤——跨阶段陈旧事件已剔除，见 §7）。计时分解来自各格 `runs/*/attempt*/stdout.log` 时间戳行（本地 UTC+8，已换算）。
> 分母一律 **125 任务**。只给证据与两档估计（上界/保守），不给实现方案。与 r1 对照见 §5；模型更换混杂在每条归因中逐条标注。

## 1. 桶量化总表（67 未过格）

| 桶 | 格数 | 占 125 | 定义 | 判定证据 |
|---|---:|---:|---|---|
| **T 超时** | **41** | 32.8% | 页面 240s 时钟到期 WOB 回 −1，引擎继续跑满 600s 被杀 | status=timeout；40/41 格页面在 129–344s（中位 258s）已收 −1(timed out)，之后 zombie 中位 **339s**；1 格（use-slider-2）页面从未注册 episode（§2.4） |
| **O 官方失败** | **23** | 18.4% | 页面判负后引擎正常收尾 | status=official_failed；其中 15 格 junit_passed=true（完成幻觉，页面 raw=−1）、8 格诚实失败 |
| **G no_goal** | **1** | 0.8% | harness 未产出目标 | email-inbox-forward-nl（0.6s、无日志、无 runs 目录——crash 前零留痕） |
| **N no_reward** | **2** | 1.6% | planner 首 turn 90s 超时即判负，零执行 | click-shades、drag-shapes-2：90.0s、0 个 executor 轮、`final_response='planner_agent LLM call timed out after 90s'` |

**与 r1 的结构性差异**：r1 最大桶是"引擎 600s 击杀 + 判分瑕疵"（B+A=28）；r2 的 41 个超时格**全部是页面 240s 时钟先杀**（C1a 生效后页面通过不可能再被覆盖），引擎 600s 只是把 zombie 跑满。**r2 的约束从"引擎限时"移到了"页面 240s 时钟 × 单步延迟"**。

## 2. 超时 41 格深挖——"延迟还是决策循环？"

**答：三者并存，且出现了 r1 没有的第三个因子（长补全肥尾）。**

### 2.1 全量对照（125 格实测）

| 指标 | 58 通过格 | 41 超时格 |
|---|---|---|
| executor 轮数均值/中位 | 15.5 / **13** | 40.4 / **39**（3.0×） |
| executor 轮间耗时（TOKEN_COUNT 间隔−工具时间）中位 | 2–11s | 同分布，但 p90 22–140s |
| 页面 expiry | <240s 内完成 | 中位 258s |
| 600s 用途 | — | ≈90% 是 executor+planner LLM 等待；工具执行仅 0.7–20s（中位 ~6s） |

### 2.2 完补全 token → 延迟近线性（r2 特有的肥尾，n=3197 个 executor turn）

| completion tokens | n | 间隔中位 | 间隔 p90 |
|---:|---:|---:|---:|
| 0–50 | 1488 | 2.0s | 9s |
| 50–150 | 786 | 5.0s | 19s |
| 150–400 | 508 | 9.0s | 26s |
| 400–800 | 214 | 20.0s | 40s |
| >800 | 201 | **46.2s** | **80s** |

**10.3% 的 turn（>500 token）消耗了全部 executor 墙钟的 38%**（14426s / 37635s）。GLM-flash 在少数 turn 上生成 2000–3400 token 的长推理（如 drag-items att2 turn11 completion=2539 → 一次停顿 ~67s）。planner 侧：41 格平均 51s/格（中位 36s，最大 168s），且 **25/41 格发生 ≥1 次 planner 90s 超时**（draw-circle 10 次、highlight-text(-2)/draw-line 各 6 次），超时后 planner 重写步骤为 retry → 烧钟。

### 2.3 十格抽样逐格证据（attempt 取 results 对应 attempt；轮数=executor LLM 调用数）

| 格 | span | 轮数 | exec耗时 | planner(超时次数) | 页面expiry | 僵尸期行为 | 分类 |
|---|---:|---:|---:|---|---:|---|---|
| ascending-numbers | 600 | 58 | 576s | 62s(≥1) | 257s | **继续正确执行升序点击**（Click×3+JS 循环至 433s+） | 操作数需求 |
| click-tab-2-hard | 600 | 69 | 567s | 36s(≥1) | 256s | 继续 tab 点击循环 | 操作数需求 |
| email-inbox-delete | 600 | 72 | 546s | 31s(≥1) | 264s；**nav=10 → 第二 episode 555s 再到期** | 继续删邮件 | 操作数需求+重导航续命 |
| use-slider | 600 | 59 | 572s | 49s(≥1) | 259s；第二 episode 552s | 继续设 slider | 操作数需求 |
| social-media-some | 600 | **91** | 568s | 68s(≥1) | 257s | Click+JS 循环伴 `ERR:Page.evaluate` 刷屏 | 决策膨胀+错误循环 |
| tic-tac-toe | 600 | 51 | 560s | 73s(≥1) | 271s | nav=5 局中重开局 | 决策膨胀 |
| highlight-text-2 | 600 | **8** | 316s | 28s(**6 次 90s 超时**) | 255s | 几乎无动作（600s≈6 次巨型 LLM 调用+超时链） | **纯延迟** |
| draw-circle | 600 | 10 | 253s | 34s(**10 次超时**) | 255s | 仅 get_page_text，无交互 | 纯延迟+画布盲区 |
| hot-cold | 600 | 39 | 496s | 37s(≥1) | 264s | hover 全部 `not found`（`#area > div:nth-child(N)` 选择器失配） | 工具/DOM 死端 |
| click-pie-nodelay | 600 | 44 | 565s | 33s(≥1) | 258s | hover 反复 `Element is outside of the viewport`（SVG 饼图） | 工具死端 |
| drag-items | 600 | 23 | 546s | 38s(4 次超时，单次 planner 停顿 95s) | 269s | drag_and_drop×2 全因选择器损坏失败（§3.1） | 工具死端 |
| use-slider-2 | 600 | 44 | 495s | 50s(2) | **无 episode**（活动页 URL 缺 `r2g_seed`，奖励事件零条） | — | 基建（种子透传） |

**三分法结论**：
- **决策循环膨胀**：存在（39 vs 13 轮），但它一半是任务真需要（多步机械任务），一半是失败重试；
- **单纯延迟**：r2 主因不是平均延迟（中位 2–5s，与 r1 deepseek 相当），而是**肥尾**（10% 长 turn 吃 38% 墙钟）+ **planner 90s 超时链**（25/41 格；2 格因此秒死，§1-N）；
- **任务需要 >240s**：机械多步类（ascending、click-tab-2(-hard)、email-inbox 系、use-slider、social-media-all、find-greatest、order-food、form-sequence、click-menu-2、click-color、circle-center、email-inbox）在当前延迟下需要 250–500s 纯执行时间，**结构性超出 240s**。

### 2.4 若 240s→480s 可救回几格（对照页面终局/僵尸期行为）

- **上界 16 格**：僵尸分类 still-trying 且僵产行为与目标一致、无工具死端的格子——ascending-numbers、click-tab-2、click-tab-2-hard、click-menu-2、click-color、circle-center、email-inbox、email-inbox-delete、email-inbox-forward、email-inbox-forward-nl-turk、email-inbox-nl-turk、find-greatest、order-food、social-media-all、use-slider、form-sequence。
- **保守 6 格**：有"正确执行中被钟杀"直接证据的——ascending-numbers、click-tab-2-hard、email-inbox-delete（r1 首实例 raw=1 实证可解）、use-slider、find-greatest、click-tab-2。
- **边界条件（r3 必须同提）**：① 引擎 600s 上限须同步抬高（480s 页钟 + wrap-up > 600s）；② 480s 只救 T-A 类，对肥尾格（highlight-text-2 型）与工具死端格（drag/hot-cold 型）无效；③ 成本：超时格墙钟翻倍（全轮 +~2.5h）。
- error-loop 11 格 + llm-stall 4 格 + no-episode 1 格在纯计时放宽下救回期望 ≈0。

## 3. 家族分析

### 3.1 拖拽/绘制/选区家族（14 格：drag-box、drag-circle、drag-cube、drag-items、drag-items-grid、drag-shapes、drag-shapes-2、drag-single-shape、drag-sort-numbers、draw-circle、draw-line、resize-textarea、highlight-text、highlight-text-2）

**drag_and_drop 工具被调用了：全 runs 合计 61 次，0 次成功。** 逐格：drag-box 7、drag-cube 6、drag-items 2、drag-items-grid 5、drag-shapes 9、drag-single-shape 18、drag-sort-numbers 11、resize-textarea 3；drag-circle / draw-circle / draw-line 为 0 次（连工具都没试，DOM 无可读节点）。

**是工具实现问题，不是（只是）agent 用法问题**——证据链（drag-items att2 / drag-box / drag-shapes 日志 + `core/extra_tools/drag_and_drop_tool.py` 契约）：
1. **md 包装损坏（主因）**：工具把"非 md= 前缀"的 source 整个包进 `[md='...']`。agent 传 CSS（`#draggable-list .draggable:nth-child(1) .drag-handle`）→ 变成 `[md='#draggable-list ...']` → `ValueError: Source element not found`。agent 换 `css=` 前缀同样被包坏；传 xpath/text 同理（`[md='//div[...]']`、`[md='text=Kass']`、`[md='1']`→目标 `'2'` not found）。
2. **契约错配**：工具要求 source 是 md ID，但拖拽源（drag-handle、SVG shape、canvas 元素）**不在 md 交互元素表里**——agent 无正确答案可传。drag-shapes 里 agent 已用截图做对视觉推理（"red ≈(68,103) size 27×23"），却没有任何工具能表达坐标拖拽。
3. **拖拽机制本身可行**：工具是 mouse down→20 步插值→up（≈3.5–4s/次），MiniWoB 用鼠标事件，选择器解析一旦通过即有真实拖拽机会（drag-shapes 唯一一解析成功的调用拖到了 `css=body`，目标错，无页面反应）。
4. **页面反应**：60/61 次调用在元素查找阶段即抛错，页面零反应；无一次 HTML5 drag 事件或鼠标序列到达页面。高亮/选区家族无对应工具，highlight-text 靠 54 次 Click+11 次 JS 合成尝试全部失败。

**净效果（对照 r1）**：r1 无拖拽工具 14 格全灭；r2 加了工具仍 13 败 + drag-shapes-2 秒死（planner 超时）= **C6 直接救回 0 格**，副作用：61 次失败调用各烧 1–2 个 executor 轮（肥尾下每次 5–70s），且 drag-box/social-media-all/drag-items-grid 3 格因此越出面尝试 `javascript:` URI（被 https 前缀偶然化解，安全复审已披露）。

### 3.2 click 变体家族（14 格）

| 子族 | 格 | 失败模式（日志证据） |
|---|---|---|
| collapsible ×4 | click-collapsible / -2 / -2-nodelay / -nodelay | 全部 **junit=true + 页面即时 −1(reason='')**：agent 点了 Section 头部（`[md='text=Section #1']`、`.collapsible-header` 等多 selector 尝试均命中）即宣告完成；页面要求点开后的内层目标。其中 2 格是 r1 通过格倒退（click-collapsible、-nodelay）。类型：理解错指令+完成幻觉 |
| pie ×2 | click-pie / -nodelay | hover 反复 `outside of the viewport`（SVG 饼图定位失败），click 切片未果，"pie menu still collapsed"（click-pie junit msg）。类型：交互方式错（hover 展开不可用） |
| tab-2 ×3 | click-tab-2 / -easy / -hard | 机械点击循环正确但每轮 7–10s，40–70 轮 >240s；-easy junit=true 在 expiry 后仍自报成功。类型：操作数×延迟 |
| menu ×2 | click-menu / -2 | 多级菜单逐层 hover/click，junit=true 而 raw=−1(-menu)；-2 到期。类型：理解错+延迟 |
| 其他 ×3 | click-color、click-shades、click-scroll-list | color：r1 no_goal → r2 能跑但超时（进展）；shades：planner 首轮 90s 超时秒死（no_reward）；scroll-list：planner 90s 超时后诚实判负（**r1 通过格倒退**）。类型：基建延迟 |

## 4. 官方失败 23 格错误类型分布

| 类型 | 格数 | 成员 | 代表证据 |
|---|---:|---|---|
| **完成幻觉**（junit=true，页面 −1） | 15 | collapsible×4、choose-date、choose-date-nodelay、click-menu、click-tab-2-easy、copy-paste、copy-paste-2、email-inbox-important、email-inbox-noscroll、scroll-text、text-editor、use-spinner | choose-date：写入 01/20/2016 并 Submit 后，executor 的收尾动作是"读 div#reward 确认"——**在读到 −1 的情况下 junit 仍报 pass**；use-spinner@56.6s 即时 −1（填错数值）；copy-paste 剪贴板链路（read_clipboard 14 次调用）内容不符仍自报成功 |
| **planner 90s 超时终止**（诚实） | 2 | click-scroll-list、stock-market | `final_response='planner_agent LLM call timed out after 90s'`，junit 如实记 fail |
| **多步未完成**（诚实超时） | 6 | book-flight-nodelay、click-pie、daily-calendar、navigate-tree、email-inbox-reply、email-inbox-star-reply | navigate-tree 57 次 Click 未命中 'Secrets'；daily-calendar junit msg 自述 "0 clicks performed"（12 次 Click 全部未落位）；book-flight-nodelay 15 Click+13 按键后到点（兄弟格 book-flight 在 r2 通过@272s，实证可解） |

横向：15/23 的完成幻觉率较 r1（29/36）绝对数下降但占比仍 65%——TERMINATE 依旧无页面证据校验（C2 的 cue 只告知 episode 结束，未接入 terminate 判据）。

## 5. r2 改进包实际效果归因（r1→r2 同任务对照；模型混杂已标注）

任务级迁移：pass∩pass 48，**r1fail→r2pass 10**，**r1pass→r2fail 6**，fail∩fail 61 → 官方净 +4。

| 改进项 | 实际效果（实证） | 净分贡献 |
|---|---|---|
| **C1a 判分中立** | 2 个 timeout 审计格翻正：click-shape（页面 raw=1@92s）、odd-or-even（raw=1@62s）——r1 规则下均判负，r2 计通过；另有 4 格 junit 不再否决页面（本就通过）。r1 的 5 个 A 桶格中 2 格本轮真实通过、3 格页面权威判负 | **+2**（click-shape 该分按安全契约定无效，干净口径不计） |
| **C2 终局 cue** | 零提前放弃（300 处 cue 全部晚于终局，复审一致）；**但引擎不停车问题原样保留**：40 超时格 zombie 中位 339s，click-shape 在页面通过后看到 cue → planner 选择"重载页面再做一个 fresh episode"，多烧 ~500s（C1a 兜底成 through）。cue 未接入 terminate 判据，完成幻觉照旧 | 0（防恶化有功，无得分） |
| **C3 单次开局** | 多导航败格 61(r1)→16(r2)；零重导航子集 60→102。但 16 个 nav≥2 败格**无一通过重导航翻正**（11 次重试维持原判；唯一重试翻正格 use-autocomplete 是 att1 无 episode 的基建失败后 att2 通过）——重导航在 r2 不再产生分数，只产生墙钟 | 0 直接（诚实性 +） |
| **C4 延迟包** | 未抗住 GLM-flash 肥尾：10% 长 turn 吃 38% 墙钟；planner 90s 超时波及 25/41 超时格 + 2 格秒死。r1 的 helper 60s 级联换成了 r2 的 planner 90s 级联（同构放大器换位） | ≈0（被新模型延迟抵消） |
| **C6 extra_tools（drag）** | 61 调用 0 成功，0 格因此通过；+W2 暴露（persist_findings 10 格）+3 次 javascript: 尝试 | **−0（净负风险）** |
| **C7/goal 预读** | no_goal 4→1；r1 的 3 个 no_goal 格本轮都能跑（2 timeout、1 click-color timeout） | 0–1（可跑但未过） |
| **模型更换（混杂）** | r1fail→r2pass 10 格中 3 格是 r1 的 D 桶理解错误（choose-date-easy、click-checkboxes、click-tab-2-medium）——C 包无对应杠杆，指向 GLM 能力；倒退 6 格（collapsible×2、scroll-list、navigate-tree、scroll-text、use-slider）多为 r1 靠 deepseek 速度险过的格 | 不可分，双向都在 |

## 6. 可恢复性矩阵（67 格 × 杠杆；每格记一个主杠杆）

杠杆：**L1** 计时放宽（页钟 480s+引擎上限同步）｜**L2** 延迟压缩（长补全抑制/planner 提速/超时快速重试）｜**L3** 工具修复（drag 选择器透传+坐标拖拽、hover viewport、选区、hot-cold DOM）｜**L4** prompt/验证（terminate 前读页面 reward、目标分解）｜**L5** 基建（planner 90s 熔断、no_goal、种子透传）｜**L6** 模型档位（hard 格上主模型）

| 桶（格数） | L1 | L2 | L3 | L4 | L5 | L6 | 小计 上界/保守 |
|---|---|---|---|---|---|---|---|
| T-A 计时敏感（16） | **16 / 6** | — | — | — | — | — | 16 / 6 |
| T-B 肥尾/基建主导（9） | — | **9 / 3** | （3 格需兼 L3） | — | — | 2 / 1 | 9 / 3 |
| T-C 工具死端（15） | — | — | **10 / 4**（修复后仍受 240s 限制） | — | 1 / 1（use-slider-2 种子透传） | 3 / 1 | 10 / 4 |
| O-A 完成幻觉（15） | — | — | — | **9 / 4** | — | 2 / 1 | 9 / 4 |
| O-B planner 超时终止（2） | — | 2 / 1 | — | — | 2 / 1 | — | 2 / 1 |
| O-C 多步未完成（6） | 2 / 1 | — | — | — | — | 4 / 1 | 4 / 1 |
| G no_goal（1） | — | — | — | — | 1 / 1 | — | 1 / 1 |
| N no_reward（2） | — | — | — | — | 2 / 1 | — | 2 / 1 |
| **合计（去重）** | | | | | | | **上界 53 / 保守 21** |

- 保守 21 → 58+21=**79/125 = 63.2%**；上界 53 → 111/125 = 88.8%（数学上界，多杠杆叠加才可兑现，同 r1 口径）。
- 若 r3 跑"480s 单列 ablation"：预期增量落在 T-A 上界 16 / 保守 6（§2.4），这是**成本最低的单杠杆验证**。
- L3 若只修 drag 选择器透传（不动坐标拖拽），受益格 ≈ drag-box/cube/items(-grid)/single-shape/sort-numbers 中选择器可表达的 3–5 格，且过钟风险仍在——需与 L1 同测。

## 7. Top-10 高价值目标（救回概率 × 成本）

| # | 目标 | 杠杆 | 救回概率 | 成本 | 依据 |
|---|---|---|---|---|---|
| 1 | click-shades + drag-shapes-2（秒死对） | L5 | ~0.8 | ≈0 | planner 90s 超时即判负、0 轮执行；任何重试/降档都能让它们真正开跑 |
| 2 | email-inbox-forward-nl（no_goal） | L5 | ~0.6（跑起来）/ ~0.25（过） | 低 | 0.6s 无日志 crash；同族 r2 已能跑但 0/7 过，分开记账 |
| 3 | use-slider-2（无 episode） | L5 | ~0.55 | 低 | 活动页 URL 缺 seed（对照 use-autocomplete att1 同病灶、重试即过） |
| 4 | ascending-numbers | L1 | ~0.6 | 中 | 僵尸期仍正确执行升序点击，纯粹差时间 |
| 5 | use-spinner | L4 | ~0.55 | 低 | 56s 即时 −1，任务本身一步设数； terminate 前读 reward 即可拦住错误自报 |
| 6 | click-tab-2-hard / click-tab-2 | L1 | ~0.5 | 中 | 机械循环正确，40–70 轮 × 7–10s |
| 7 | stock-market + click-scroll-list | L2/L5 | ~0.45 | 低 | planner 90s 终止；scroll-list 是 r1 通过格（速度敏感） |
| 8 | email-inbox-delete | L1(+L3 nav) | ~0.4 | 中 | r1 首实例 raw=1 实证可解；r2 两 episode 分别死于 264s/555s |
| 9 | choose-date-nodelay | L4 | ~0.4 | 低 | 136s 即时 −1；同族 choose-date-medium/ easy r2 均通过，交互模式已有正例 |
| 10 | drag-single-shape | L3 | ~0.35 | 中 | 18 次 drag 调用全因 md 包装损坏；任务语义最简单（单形状），修选择器透传后首个受益格 |

（战略项：drag_and_drop 选择器透传修复本身价值高于任何单格——解锁 8 格天花板，但需与 L1 联测。）

## 8. 数据口径备注

- rewards.jsonl 含跨阶段陈旧事件（如 click-collapsible 出现 t=−54325s 的事件）：本文所有"页面事件"均按 (task, seed) + run 窗口 [−10s, duration+10s] 过滤后统计；147 事件中窗口内 128。
- executor 轮间耗时 = 相邻 executor TOKEN_COUNT 时间差 − 其间"Command executed"工具时间；跨 planner 的间隔含 planner LLM 时间（计入 planner 侧不重复计）。被 600s 击杀时的在途调用无法计量（highlight-text-2 型格子约 200–300s 属此类）。
- "页码 expiry"取窗口内首个 reward<0 事件；drag-box 等多 episode 格取首个。
- r1 状态映射使用 r1 results 原始 status（含 no_junit）；r1 的 A/B/C/D 桶归属见 analysis-r1-failures.md。
- 干净口径 55/115 的 10 个 W2 格中 9 格在本表 T/O 桶内按原状态统计（安全复审已单列）；click-shape 的通过依赖 C1a 翻正（§5）。
