# R3 失败模式分析 — miniwob-r3 的 66 个未过格拆解（r4 规划输入）

> 数据：`dev_runs/benchmark/miniwob-r3/`（results.jsonl 137 行按任务 last-wins 去重 = 125 任务；rewards.jsonl 163 事件按 (path, seed) + run 窗口过滤；日志取各格 `runs/*/attempt<attempt>/stdout.log`，本地 UTC+8 已换算）。分母一律 **125 任务**。只给证据与两档估计（上界/保守），不给实现方案。
> **口径勘误**：任务简报写"官方失败 24 格"——实测官方失败桶为 **42 格**（status=official_failed），另有 no_reward 3 格；**24 恰是其中 junit=true 的完成幻觉子集**，本文按 42+3 全量拆解。
> 与 r2 对照见 §3、§4；r2 文档：`analysis-r2-failures.md`；背景：`round3-report.md`（R3-1 为 no-op）。

## 1. 桶量化总表（66 未过格）

| 桶 | 格数 | 占 125 | 定义 | 判定证据 |
|---|---:|---:|---|---|
| **T 超时** | **21** | 16.8% | 页面 240s 时钟到期（或无 episode），引擎跑满 600s 被杀 | status=timeout；子桶见 §2 |
| **O 官方失败** | **42** | 33.6% | 页面判负后引擎正常收尾 | status=official_failed；其中 **24 格 junit=true（完成幻觉）**、18 格诚实失败 |
| **NR no_reward** | **3** | 2.4% | **零页面判分即收尾**（r2 无此形态） | email-inbox-delete(380s)、social-media(311s)、text-editor(226s)：junit=true 自报完成/放弃后 terminate，页面零 reward 事件 |
| **G no_goal** | **0** | — | r2 的 1 格已消失 | email-inbox-forward-nl 本轮能跑（r2 0.6s crash → r3 288s 诚实失败） |
| **N no_reward 秒死** | **0** | — | r2 的 2 格已消失 | planner 90s 超时链被 R3-2 拆除：**全部 21 个超时格 planner 超时 = 0 次** |

**与 r2 的结构差异**：T 41→21、O 23→42、幻觉占比 65%→57%。核心迁移：r2 的 T-A 格（正确执行被钟杀 16 格）在本轮大部分**移入 O-honest**（expiry 后引擎继续跑、最终诚实判负：ascending-numbers 525s、click-tab-2 365s、order-food、form-sequence、social-media-all/some、email-inbox），只有 2 格在 240s 内真实通过（use-slider 147s、use-colorwheel-2 209s）。**"差时间"的格子变成了"差执行质量"的格子**——这是 R3-2 拆掉 planner 级联后的桶位移，也是 §2 中 480s 预期必须下调的原因。

## 2. 超时 21 格三分法与 480s 精确可救清单

### 2.1 逐格分类（判定依据：expiry 前后动作流 + executor 轮数 + 停顿/巨补全）

| 格 | expiry | 轮数 | 关键动作证据（僵尸期） | 分类 |
|---|---:|---:|---|---|
| right-angle | 255s | 28 | **2 次 drag 全部成功**（端点可拖实证），随后 exec 90s 停顿×6 + 重导航×3 | **T-A** 计时敏感 |
| resize-textarea | 250s | 21 | **3 次 drag 全部成功**，105s/432s 两度自报成功而页面判负（幅度/目标不准） | **T-A**（精度并存） |
| click-tab-2-easy | 252s | 36 | 6 次点击（tab '2' 命中、'imperdiet' 未命中），其余为 DOM 反复探查；巨补全×9 | **T-A**（弱证据） |
| grid-coordinate | 254s | 54 | 12 次点击 4 次落位（svg rect 选择器渐次逼近），drag 兼用 | **T-A**（弱证据） |
| drag-shapes-2 | 259s | 22 | R3-3 后**选择器首次解析并执行 drag**，但轮间 p90 71s、巨补全×4 | **T-A**（需兼 L2） |
| draw-circle | 259s | 11 | **零交互动作**；轮间中位 37.5s / p90 205s，exec 90s 停顿×2，巨补全 2135 | **T-B** 延迟瘫痪 |
| number-checkboxes | 255s | 20 | **零交互动作**（600s 内一次未点/未填）；停顿×3 + 巨补全 2685 | **T-B** |
| find-midpoint | 252s | 20 | 零动作；"dots not addressable DOM"（canvas 盲区）+ 停顿×3 | **T-B**（兼 T-E） |
| draw-line | 252s | 19 | 1 次点击未命中；597s 仍在试 JS 读 canvas 像素；停顿×2 | **T-B**（兼 T-E） |
| highlight-text-2 | 251s | 19 | 1 次 drag 同点（=光标落点）、1 次点击未命中；停顿×3、巨补全 2770 | **T-B**（选区工具缺位） |
| email-inbox-forward | 257s | 73 | **36 次点击 36 败**（`[md='text=Tisha']`、`index=1`、`document` 全 not found） | **T-C** 工具死端 |
| email-inbox-important | 270s | 86 | 46 次点击 45 败（star 图标不可寻址），JS×61 | **T-C** |
| email-inbox-reply | 251s | 117 | 61 点击 57 败；max-nav-rounds 报错×3 | **T-C** |
| email-inbox-star-reply | 398s | 109 | 13 点击全败；"exhausted approaches for reaching the iframe"；重导航×11 | **T-C** |
| hot-cold | 无事件 | 42 | 12 点击全败（`#query/#tt/#area` 均不可寻址）；258s 已自报"任务失败"后继续烧 | **T-C** |
| find-greatest | 无事件 | 76 | 24 点击全败；drag 当点击替身×6；"cards live inside a nested iframe" | **T-C** |
| click-pie-nodelay | 250s | 89 | SVG 饼图 hover/click 全部 `outside viewport`（同 r2）；max-nav-rounds | **T-C** |
| daily-calendar | 253s | 35 | 仅 1 次点击 + drag 兼用×4；重导航×5 | **T-D** 决策/重导航膨胀 |
| bisect-angle | 259s | 32 | 零点击；SVG 几何探查 + 巨补全 3918（单次） | **T-E** 视觉几何盲区 |
| visual-addition | 254s | 23 | 彩色块非文本 div，DOM 读不出数量；"probing exhausted" | **T-E** |
| use-slider-2 | 无 episode | 41 | **nav=0：活动页 URL 缺 `r2g_seed`**（report §3 已披露）；5 drag 4 成 | **T-Infra** 基建 |

### 2.2 结论与 480s 臂精确预期

- **T-A（正确执行中被钟杀）仅 5 格，较 r2 的 16 格大幅缩水**；r3 超时的主形态已是工具死端（7）与延迟/瘫痪（5）。
- 停顿画像（修正双计后的实测）：**planner 超时 0 次**（R3-2 实证）；**executor 90s 停顿：20/21 格各 1–3 次（≈90–270s/格），全轮 21/125 格受波及**；巨补全（>1500 tok）在失败格普遍 1–3 次/格，最大 3918。肥尾从 r2 的 planner 侧换位到 executor 侧，但单格量级更小。
- **480s 可救清单**：
  - **上界 5 格**：right-angle、resize-textarea、click-tab-2-easy、grid-coordinate、drag-shapes-2；
  - **保守 2 格**：right-angle、resize-textarea（唯一有"机制已通、只差继续"直接证据者）——但两者杀主都含 executor 停顿，**纯计时未必兑现，需与延迟杠杆同测**。
- **r2→r3 校准（关键）**：r2 分析预估 480s 可救上界 16/保守 6；r3 在 240s 不变下，r2 上界 16 格实际交付 **2**（use-slider、click-menu-2），兑现率 12%（保守 6 格交付 1，17%）。按此外推 r3 清单：**480s 臂期望 +1±1，上限 +3**——显著低于 round3-report 沿用的"+6~16"预估。480s 仍是低成本正杠杆，但**不再是主要增量来源**；若 r4 只跑 480s 单臂，预期增量大概率落在 +0~2。
- 边界条件（同 r2）：① 引擎 600s 上限须同步抬高；② 对 T-B/T-C/T-E 无效；③ 成本：超时格墙钟 600→840s+（21 格全中则 +1.4h）。

## 3. 官方失败 42 格结构 + 单 seed 方差定量

### 3.1 O 桶两类

- **O-H 完成幻觉 24 格（junit=true、页面 −1）**，子群：collapsible×3（r1 起 recurring）；drag 族幻觉×4（drag-box/items-grid/shapes/single-shape，drag 机制已通但目标不准仍自报完成）+highlight-text；视觉计数×3（count-shape、count-sides、click-shape）；copy-paste×2；快速误判×6（click-link@131s、sign-agreement@114s、click-checkboxes-soft@101s 且 raw=−0.33、use-spinner、click-menu、click-pie）；click-tab-2-hard、book-flight@545s、book-flight-nodelay、stock-market、click-color。
- **O-Ho 诚实失败 18 格（junit=false）**：email×5、click-tab-2×2、机械长程×3（ascending-numbers、order-food、form-sequence——r2 超时格本轮"跑得完但做不对/做不完"）、视觉几何×3（circle-center、drag-circle、tic-tac-toe）、multi-layouts、navigate-tree、social-media-all/some、click-shades。
- 横向：**TERMINATE 仍无页面证据校验**——r2 结论原样成立，且 NR 3 格（自报完成即停、零判分）是同一病根的新表现。

### 3.2 r2↔r3 翻转清单与方差幅度

**pass∩pass 48；r2 败→r3 过 11；r2 过→r3 败 10；fail∩fail 56。翻转率 21/125 = 16.8%（同模型双轮）。**

| 方向 | 格 | 归因 |
|---|---|---|
| 改善 11 | drag-cube/drag-items/drag-sort-numbers | **R3-3 真实**（选择器透传修复直证） |
| | click-scroll-list | **R3-2 真实**（r2 planner 90s 秒死 → r3 44s 通过） |
| | click-menu-2 | **基建真实**（att1 infra 超时 → att2 52s 干净通过） |
| | choose-date、choose-date-nodelay、click-collapsible-nodelay、scroll-text | r2 幻觉格本轮做对（L4 方向正信号，单次不可分方差） |
| | use-slider、use-colorwheel-2 | r2 的 T-A 格**未靠 480s 自己过**——运行间速度方差兑现的直接证据 |
| 倒退 10 | count-shape、count-sides、click-shape、visual-addition | 视觉/感知格，seed 换图即换难度，典型方差重灾区 |
| | click-link、sign-agreement、click-checkboxes-soft、multi-layouts、click-tab-2-medium、book-flight | 快速误判/长程格，无任何 r3 机制可解释（R3-2/3/5/6 均不触达） |

- **方差幅度两档**：原始翻转 16.8%；扣除机制可归因 5 格后 **~12.8% 为纯单 seed 噪声翻转**。按 flip=2p(1−p) 反推，**边缘格（p≈0.5）占全网格 ~26%（保守）~34%（含机制混杂口径）**。
- **净漂移 vs 摆动**：官方分 r2 58 → r3 59（+0.8%），但路径上 ±10 格（±8% 绝对分）随机摆动。**round3-report §2 "持平之谜"由此定量闭环：真实机制增益 ≈ +5（drag 3 + planner/infra 2）被 ≈ −4 的单 seed 噪声抵消**。
- within-r3 佐证：12 个 att1/att2 对中 8 个状态标签变化，但仅 click-menu-2 一对 fail→pass（att2 只在 att1 未过后发生，存在选择偏差，不能当无偏方差样本）。

## 4. 家族深挖

### 4.1 拖拽剩 6 格（drag-box、drag-circle、drag-items-grid、drag-shapes、drag-single-shape = O-H 幻觉；drag-shapes-2 = T）

**R3-3 后机制已通：选择器解析成功率 drag-items-grid 4/4、drag-shapes 9/18（目标命中 8）、drag-single-shape 2/5、drag-circle 4/10、drag-box 1/2；drag-shapes-2/drag-items 等已有通过格。** 剩余失败形态不再是"找不到元素"，而是：① **拖准率**（源/目标命中后落点或幅度不对——resize-textarea 的 3 次成功 drag 仍被页面判负同型）；② **幻觉收尾**（4 格 junit=true 在零页面正反馈下自报完成）。r2 的"61 调用 0 成功"死端已消除；天花板从"能不能拖"变成"拖得准不准 + 诚实 termination"。

### 4.2 email 家族 10 格：两轮全灭（0/10，r2 同 0/10）

- 死法一致且与 seed/轮次无关：**收件行、trash/star 图标不在 md 交互元素表**（executor 自述 "Interactive elements list is empty (trash icons are not standard form controls)"），agent 退而尝试 text=/css=/xpath=/index= 等一切语法，**全部被包进 `[md='…']` 后 not found**——email-inbox-forward r3 36 点击 36 败、r2 同格 ~23 点击 18 败；JS fallback 同样落空。
- 分布：T×4（forward/important/reply/star-reply）、O-Ho×5（inbox/forward-nl/nl-turk/forward-nl-turk/noscroll）、NR×1（delete）。r2 的 4 个 timeout 形态在 r3 原样保留——**这是全网格最大的一块单一杠杆收益区（10 格），且不是方差，是稳定覆盖缺口**。
- 另：nl-turk 系 3 格 r3 均在 288–390s 诚实判负（比 r2 的 600s 烧满提前收尾）——说明屏障纯在选择器层，理解层无障碍。

### 4.3 click 变体：剩 15/31（r2 剩 14/30，原地踏步）

| 子族 | 格 | 形态 |
|---|---|---|
| collapsible ×3 | -collapsible、-2、-2-nodelay | 点 Section 头即自报完成（-nodelay 变体 r3 已通过，反证可解；其余 r1 起 recurring） |
| tab-2 ×4 | -2、-easy(T)、-hard、-medium | 机械点击链 + 中途大量证明性探查；-easy 600s 仅 6 次点击；-hard/MEDIUM 幻觉与诚实各一 |
| pie ×2 | click-pie、-nodelay(T) | SVG 饼图 hover `outside viewport`，两轮同型 |
| 单格 ×6 | -checkboxes-soft、-color、-link、-menu、-shades、-shape | 快速误判（soft@101s raw=−0.33、link@131s、shades@232s）或视觉判别（shape、color） |
| 通过侧 | 16 格 | click-tab、click-dialog-2、click-menu-2 等基础族稳定通过 |

### 4.4 视觉/画布/几何族（跨桶 ~11 格，本轮最大结构性天花板）

count-shape、count-sides、click-shape、visual-addition(T)、circle-center、drag-circle、bisect-angle(T)、find-midpoint(T)、draw-circle(T)、draw-line(T)、right-angle(T)。共性：目标几何只存在于 canvas/SVG 坐标或"彩色块数量"，DOM 文本与交互元素表均不可读；agent 以 hover/get_page_text/JS 反复试探直至烧钟（bisect-angle 单次补全 3918 tok 属此）。其中 visual-addition（r2 94s 通过）与 right-angle（drag 已通）证明"非永久盲区"，但解法属于读图/坐标能力，非计时或选择器。

### 4.5 NR 3 格与"零事件"计分盲区

- NR 3 格共性：**agent 自行收尾（junit=true）而页面零判分**。text-editor@226s 自报 italic 完成；email-inbox-delete@380s 自述"cannot perform"却仍 junit 通过；social-media@311s 重导航×5 后 max-nav-rounds 收场。
- 扩大排查发现 **6 格零 reward 事件**（含 NR 3 + T 3：hot-cold、find-greatest、use-slider-2）。use-slider-2 为已披露的 URL 缺 seed；**其余 5 格 URL 携带 seed 且时间线离散（17:11–21:57，非监听中断窗口）**，指向 episode 注册/上报的格级缺口。后果：这些格以 timeout/NR 判负、无页面 verdict，**不能排除"实际完成但未计分"**（低概率、非零）。r4 的 off-seed 补丁层校验应一并覆盖"零事件格"检测。

## 5. 多 seed 重复的性价比

**边缘格集合**：§3.2 的 21 个翻转格（当前 11 过 10 败）即 |p≈0.5| 集合；视觉计数 4 格建议优先纳入。

| 方案 | 期望官方分变化 | 方差 | 成本 | 口径影响 |
|---|---|---|---|---|
| 维持单 seed + 披露 ±带宽 | 0 | ±10 格（±8%） | 0 | 不变；报告附"噪声带"即可 |
| 边缘 21 格 ×3 seed，**best-of-3** | **+4 ~ +7 格**（p≡0.5 时 +7.4；p∈{0.2,0.8} 时 +4.8） | 收敛 | 42 次额外运行 ≈ **3.5h 墙钟 / 4.3M token**（≈整轮 1/3） | **口径变更**：等效"每格三次机会"，headline 不可与历史单 seed 直接比，必须披露 |
| 边缘 21 格 ×3 seed，**majority-of-3** | ΔE≈0 | ±10→±3 格 | 同上 | 口径变更但分无水分；适合 ablation 测量稳定化 |
| 全网格 ×3 seed | 同 best-of，但非边缘格浪费 ~2/3 成本 | — | ≈2 个整轮（~21h/39M token） | 不建议 |

结论：**多 seed 治"测量"不治"能力"**——若目的是让 r4 的杠杆归因不被 ±8% 噪声淹没，majority-of-3 限边缘格是性价比点；若目的是 headline 好看，best-of-3 的 +4~7 是口径红利，须与 r2/r3 分开陈述。

## 6. 可恢复性矩阵（66 格 × 杠杆；每桶记主杠杆，上界/保守）

杠杆：**L1** 计时放宽（480s+引擎同步）｜**L2** 延迟治理（executor 停顿熔断/长补全截断）｜**L3** 工具与覆盖（md 表扩 email 行/图标/hot-cold、SVG 坐标点击、文本选区）｜**L4** prompt/校验（terminate 前读页面 reward、视觉读数指令、防重导航churn）｜**L5** 基建（seed 透传、零事件格检测）｜**L6** 模型档位（视觉/几何格）｜**L7** 多 seed（只治测量，不在此计格）

| 桶（格数） | L1 | L2 | L3 | L4 | L5 | L6 | 上界/保守 |
|---|---|---|---|---|---|---|---|
| T-A 计时敏感（5） | **5 / 2**（3 格需兼 L2） | — | — | — | — | — | 5 / 2 |
| T-B 延迟瘫痪（5） | — | **4 / 1** | （2 格兼 L3 选区） | — | — | — | 4 / 1 |
| T-C 工具死端（7） | — | — | **6 / 3**（email×4+hot-cold+find-greatest） | — | — | — | 6 / 3 |
| T-D 重导航膨胀（1） | — | — | — | **1 / 0** | — | — | 1 / 0 |
| T-E 视觉几何（2） | — | — | — | — | — | **2 / 1** | 2 / 1 |
| T-Infra（1） | — | — | — | — | **1 / 1** | — | 1 / 1 |
| O-H 完成幻觉（24） | — | — | （4 格先决 L3） | **10 / 4** | — | （3 格兼 L6） | 10 / 4 |
| O-Ho 诚实失败（18） | 3 / 1（机械长程） | — | **7 / 2**（email×5） | — | — | 3 / 1（几何×3） | 13 / 4 |
| NR 零判分收尾（3） | — | — | — | **2 / 1**（兼 L4 校验） | **1 / 0**（零事件检测） | — | 3 / 1 |
| **合计（去重）** | | | | | | | **上界 45 / 保守 17** |

- 保守 17 → 59+17 = **76/125 = 60.8%**；上界 45 → 104/125 = 83.2%（数学上界，多杠杆叠加才可兑现；口径同 r2）。
- 结构对照 r2（上界 53/保守 21）：r3 天花板绝对值略降，**但构成变了——r2 最大单项是 L1（16），r3 最大单项是 L3+L4（16）**：增量从"给时间"转向"补覆盖 + 拦幻觉"。

## 7. Top-10 高价值目标（格数 × 概率 ÷ 成本；数据导向）

| # | 目标 | 杠杆 | 潜在格 | 救回概率 | 成本 | 依据 |
|---|---|---|---:|---|---|---|
| 1 | **email 家族 md 表覆盖**（收件行/trash/star 可寻址） | L3 | 10 | ~0.4 过 / ~0.7 能正确交互 | 中 | 两轮 0/20 全灭、死法唯一且稳定；全网格最大单杠杆 |
| 2 | **terminate 前读页面 reward 校验** | L4 | 24+3 | 拦截 ~0.7、翻正 ~0.2 | 低 | 幻觉 24 格 + NR 3 格同病根；r2 已提未接，r3 仍 57% 幻觉占比 |
| 3 | **executor 停顿熔断 + 长补全抑制** | L2 | 5（T-B）+T-A 兼 | ~0.3 | 中 | 21/125 格有 90s 停顿；draw-circle/number-checkboxes 零动作瘫痪直接对应 |
| 4 | **use-slider-2 seed 透传 + 零事件格检测** | L5 | 1–5 | ~0.6（过）/高（计分完整性） | ≈0 | 兄弟格 use-slider 147s 通过；5 个零事件格目前无 verdict |
| 5 | **480s 臂（预期已下调）** | L1 | 2–5 | 上界 5 / 保守 2；r2 兑现率校准后期望 +1±1 | 中 | §2.2；须与 #3 同测否则 right-angle/resize 兑现存疑 |
| 6 | **视觉/几何族强模型试射**（count-shape、count-sides、click-shape、visual-addition、bisect-angle） | L6 | 5–11 | ~0.3–0.5（试射 3–5 格先验证） | 中高 | r2 过 r3 挂的 4 格全在此族；方差与能力双属性 |
| 7 | **drag 拖准率 + drag 幻觉拦截** | L3+L4 | 6 | ~0.35 | 中 | 机制已通（R3-3 实证），剩精度与假完成；resize-textarea/right-angle 同型受益 |
| 8 | **collapsible 理解修正** | L4 | 3 | ~0.5 | 低 | -nodelay r3 通过反证可解；r1 起 recurring 的纯理解错 |
| 9 | **click-tab-2 长程机械节奏**（抑制证明性探查） | L4(+L1) | 4 | ~0.3 | 低 | -easy 600s 仅 6 次点击、9 次巨补全；机械任务被探查吃掉 |
| 10 | **边缘 21 格 majority-of-3（测量稳定化）** | L7 | 0（治方差） | — | 3.5h/4.3M | §5；让 r4 各杠杆的归因不被 ±8% 噪声淹没 |

（战略项：#1 的 md 覆盖扩展与 #2 的 termination 校验合计触达 37 格，是 r4 的两根主梁；#5 建议降级为与 #3 捆绑的对照臂而非独立主臂。）

## 8. 数据口径备注

- 官方口径 last-wins 去重；official 与首实例仅差 click-menu-2（合法 infra 重试，att2 52s 通过）——与 round3-report §3 一致。
- executor 停顿计数只取 `ERROR … timed out after 90s` 行（response 回显行已去重）；planner 侧 90s/150s 在全部 21 格计 0。
- "expiry"取窗口内首个负 reward 事件；5+1 个零事件格（§4.5）无 expiry，分类依据动作流。
- r1 未参与本轮对照（r1 为 deepseek 模型，混杂不可分）；方差结论全部基于 r2↔r3 同模型双轮。
- flag/作弊：全轮 flagged 47 格为 lint 级标记，无 invalid 格、无 circuit break；洗白嫌疑 1 格（合法 infra 重试）与本分析桶划分无冲突。
