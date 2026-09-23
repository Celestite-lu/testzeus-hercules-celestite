# R4 改进计划 — miniwob-r4（plan-r4）

> 读者为 spec-r4.md 的契约来源与总编排代理的决策依据。数据基础：`analysis-r3-failures.md`（r3 的 66 未过格拆解，下称"分析"）、`round3-report.md` §4、`security-review-r3-post.md`（下称"安全复审"，YELLOW 两项 M1/M2 为 r4 必办输入）。
> **核心目标（最高原则）：r4 干净口径站上 50% 且口径不掺水。** 分析触达 37 格的 A（md 覆盖扩展）+ B（终止前校验）是两根主梁；弱证据候选（C 流式截断、F 独立 480s 臂）砍掉或降级不心疼；D（多 seed）只治测量、口径变更须用户批准，列为待批项。一切行为变更经独立 flag 披露，默认态（r4 flags 全 off）= r3 行为逐字节复现。
> 模型环境：GLM coding plan（planner=glm-5.3、nav/helper=glm-5.3-flash），5 小时窗口——延续分段执行 + 429 熔断 + `--max-cells` 配速；**r4 配速已翻倍（单段可 140 格级）**，预算表见 §5。

## 0. 法定输入采纳/拒绝总表

| 输入 | 采纳情况 |
|---|---|
| analysis-r3-failures.md | 全量采纳为证据基础：**A←§4.2**（email 家族 10 格两轮 0/20、死法唯一且稳定、非方差）+ Top-10 #1；**B←§3.1**（幻觉 24 格 junit=true 占官方失败 57% + NR 3 格同病根"TERMINATE 无页面证据校验"）+ Top-10 #2；**E←§4.5**（off-seed 1 格 + 6 格零 reward 事件无 verdict）；§2.2 480s 预期下调（+1±1）→ F 降级；§5 多 seed 性价比表 → D 待批设计；§6 可恢复矩阵 L3+L4=16 为最大单项 → A+B 组合依据；§4.4 视觉/画布族（~11 格）→ 不做（OoS） |
| round3-report.md §4 | 项 5（off-seed 防护）→ E；项 3（多 seed 或接受方差并披露）→ D；项 4（结构分析）已由 analysis-r3 完成；项 2（真补全上限=客户端流式截断）→ **拒绝立项**（见 R4-C 拒绝案）；项 1（480s 臂"+6~16 格"）→ **不采纳原预估**（分析 §2.2 校准为 +1±1），F 降级为对照臂、默认不做 |
| security-review-r3-post.md | M2/P1（planner 可重写任务 URL 的面）→ E 检测型防护；§8-2"nav=0 且 timeout 列 flagged"→ E 扫描器扩展；M1/P1（R3-1 no-op 的表述纪律）→ 采纳：**r4 headline 摘除 `--nav-max-tokens`**（实证 no-op，保留只会继续误导归因），报告 flag 表注明历史；P2（重试翻正/重导航通过格逐格披露）、P3（五件套维持原样 + 既有披露全单）→ 采纳为 r4 纪律 |
| 对照实现现场：`get_detailed_accessibility_tree.py` + `core/tools/get_interactive_elements.py` + miniwob email DOM | **死因定位（r4 A 的立项根据）**：md 注入层 `isInteractiveElement` 的 cursor:pointer 启发式已覆盖 `.email-thread` 行 / `.star`/`.trash` 图标 / find-greatest 的 `.card.hidden`（CSS 均 `cursor: pointer`，且任务在主文档非 iframe）；真正的过滤器在 `get_interactive_elements.flatten_elements`（get_interactive_elements.py:44-89）——只保留 role ∈ 白名单或 tag ∈ {a,button,input,select,textarea} 的节点，div/span 形态的行与图标（无 role）被整批丢弃，与 executor 自述"Interactive elements list is empty"吻合。**拒绝的替代修法**：click 工具选择器透传（`click_using_selector.py` 把一切无 `md=` 输入包成 `[md='…']`）——影响所有任务的每次点击、爆炸半径最大而证据弱，列 r5 候选；`isInteractiveElement` 大改——注入层已被现场证据覆盖，只授权最小修补（见 spec §2.3 降级路径） |
| `record2gherkin/benchmark/` flag 结构 | 独立开关、默认 off、manifest `flags`、`--smoke-cells`、retry 池、R2_BUDGET_CAP=144、C1b/429 熔断原样沿用；r4 只做增量 |
| spec-r2/spec-r3 | r3 全开包保留声明（§2）与披露纪律沿用；E 类"harness 披露增强不带 flag"先例沿用（r4 的 E 同理由） |

## 1. 采纳清单（R4-A … R4-F）

### R4-A（主梁 1）md 覆盖扩展 = 交互元素终表过滤器扩展（engine flag `--md-extended`）——r1 以来首次动感知层

- **证据**：分析 §4.2——email 家族 10 格两轮全灭（0/20），死法一致且与 seed 无关：行/图标不在 md 表 → agent 退而尝试一切语法全部被包进 `[md='…']` not found；email-inbox-forward r3 36 点击 36 败。Top-10 #1：全网格最大单杠杆（10 格，~0.4 过 / ~0.7 能正确交互）。现场定位：`flatten_elements` 过滤器（§0 表）；md 注入层已覆盖目标元素（cursor:pointer 路径），find-greatest 的 `.card.hidden`（cursor:pointer + innerText 数字）同被终表丢弃。
- **改法（精确定义在 spec §2）**：flag 门控下 ① `flatten_elements` 增加 div/span/li/tr/td/th/img/label 形态节点的收编规则（须携带 name/title/description/text/aria-label/class/id 之一，杜绝无标识节点灌水）；② role 白名单扩 {row, cell, listitem, img}；③ `__fetch_dom_info` 的抓取属性表增 `class`（图标无文本，class="star"/"trash" 是唯一语义锚）；④ 终表硬上限 150 条（超限截断 + stdout `[R2G_MD_TRUNCATED]` 留痕）——上限只约束 md 表长度，不影响注入与判分。
- **判分立场**：感知层变更，判分中立（不触碰奖励/状态链），但**改变所有任务的 DOM 观察**——必须 flag 门控默认 off（off = r3 逐字节复现，含 `class` 属性与 role 集）。 headline 显式开启。
- **kill-switch（M1 出口判据）**：离线浏览器测试（T3，vendored email-inbox.html 真页断言行/图标入表）+ 冒烟 2 格（email-inbox-forward、email-inbox-star-reply）stdout 出现对 md 表内条目的成功点击；若 T3 证明注入层缺口（md 根本不在行/图标上），授权最小修补 `isInteractiveElement`（如 `element.ownerDocument.defaultView.getComputedStyle` 的 iframe 安全取值），修补后 T3 复验；仍失败 → headline 去掉 `--md-extended` 并固化放弃声明（R3-3 先例）。
- **预期救回**：保守 +4 / 上界 +8（email 10 格 ×0.35~0.5 + find-greatest ~0.5；hot-cold 的 `#touch-area` 依赖 jQuery 委托监听且无 cursor/tabindex，**A 大概率不覆盖**，如实记为不确定项，不进主张）。

### R4-B（主梁 2）终止前校验 = verify-before-done 路由层强制核验轮（engine flag `--verify-before-done`）

- **证据**：分析 §3.1——完成幻觉 24 格（junit=true、页面 −1，占官方失败 57%）+ NR 3 格（自报完成/放弃即停、页面零判分）同病根"TERMINATE 无页面证据校验"，r2 结论原样成立；Top-10 #2（拦截 ~0.7 / 翻正 ~0.2）。**prompt 层已被 r3 实证不足**：R3-6 断言纪律上线后假阳性自评 17→24 不降反升——故 r4 做工具/路由层（r3 plan 明示"机制版留 r4"）。
- **改法（精确定义在 spec §3）**：flag 开启时，planner 产出 `terminate=yes ∧ is_passed=true` 且本场景未做过核验（state `verify_rounds < 1`）→ 路由到新 `verify` 节点（不进 END）：下发固定核验步（"调 get_page_text + get_interactive_elements，对照任务目标报告页面当前状态，不做其他动作"）→ 走既有 executor 路径 → 观察回灌 planner，planner 二次裁决。**每场景至多 1 次**（`verify_rounds` 硬上限），第二次 terminate 直接放行——无循环风险、有界成本。`is_passed=false` 的终止不触发核验。
- **与 C2 终局信号的交互**：核验观察里若页面已 endEpisode，agent 可见 `EPISODE ENDED` cue（C2 注入的中性文本）——有 cue = 页面已出裁决（官方判分本就页面权威）；无 cue = "尚未有页面裁决"的直接反证，planner 据此继续执行或改 `is_passed=false`。已 +1 的格不被核验轮损害（奖励先落盘，超时不回判，r3-post §4"反向桥接空"实证）。
- **判分立场**：不改判分链（C1a、fetch /latest、last-wins 全不动），只改 agent 行为与 junit 诚实度；预期收益一半在"翻正"（agent 继续做完真过）一半在"拦谎"（junit 翻诚、NR 格要么继续做要么如实报败）。
- **成本**：通过格（59）每格 +1 核验轮 ≈ +10–20s/格、+~0.3M token/轮全量——接受并披露。
- **预期救回**：保守 +4 / 上界 +9（O-H 翻正 4~8 + NR 1~2，email-inbox-delete NR 格与 A 重叠只计一次）。

### R4-E（安全必办，判分中立，不带 flag——r3 E 类先例）off-seed 与零事件完整性包

- **E1 off-seed 检测（补丁层）**：auto-start 补丁追加探测行——页面 query 无 `r2g_seed` → `POST /__r2g_offseed`；miniwob_server 记 `offseed.jsonl`（ts, path）。只检测不阻断（阻断/自动补齐 = 掩盖 planner 缺陷且需向引擎逐格传 seed，拒绝，见 §1 拒绝表）。
- **E2 episode 启动信标**：auto-start 补丁追加 `POST /__r2g_epstart`（带 seed）→ `epstart.jsonl`——区分零事件格的两种形态："episode 从未启动"（注册缺口）vs "已启动无奖励"（上报缺口）；分析 §4.5 的 6 格零事件盲区就此可判。
- **E3 扫描器扩展（只 flagged 披露，永不 invalid）**：① `nav==0 ∧ status=timeout` → flagged（安全复审 §8-2 原文，r3 中 off-seed 格唯一可见信号目前是中性值）；② offseed 记录落入该格 attempt 窗口 → flagged `off_seed_navigation`；③ 终态行 reward_records==0 → flagged `zero_reward_events`；④ epstart 缺失细分 `episode_never_started`。
- **E4 metrics 披露字段**：`zero_reward_cells` 清单（≤50 条截断），复用 `invalid_cells` 样式。
- **定位**：本轮对格分期望 **+0**（检测不修复；r3 的 use-slider-2 零分值影响不重演即可）——价值在计分完整性与 r4 报告的可归因性（66 未过格里不再有"无 verdict"格）。

### R4-D（待批附加实验）多 seed 口径——只治测量，headline 不动

- **证据**：分析 §3.2/§5——单 seed 纯噪声翻转 ~12.8%，边缘格占 ~26–34%，r3 官方分 ±10 格（±8%）摆动；majority-of-3 限边缘格 ΔE≈0、方差 ±10→±3；best-of-3 的 +4~7 是口径红利（等效每格三次机会），**headline 不可与历史单 seed 直接比**。
- **裁定**：作为 r4 附加 ablation 设计好、**默认不跑**，触发门控见 §4-M4；任何口径变更标注**须用户批准**。
- **采纳方案 = majority-of-3 限边缘集**（r2↔r3 翻转 21 格 ∪ r4↔r3 新翻转格；每格补 2 个派生 seed ≈ 42–50 执行，~3.5h 墙钟 / ~4.3M token）；**拒绝 best-of-3**（掺水）与全网格 ×3（~2/3 成本浪费，分析 §5）。
- **口径纪律**：独立 exp-id `miniwob-r4-ms3`/独立 exp-root；产出只作"测量稳定化"列（majority ≥2/3 计过），与 headline 双列、禁止合并分母；headline 主张永远引用单 seed 240s 口径。

### R4-C（拒绝立项，给性价比判断）executor 延迟残尾的客户端流式截断

- **证据面**：分析 §2.1/§2.2——executor 90s 停顿 20/21 超时格各 1–3 次、T-B 延迟瘫痪 5 格、巨补全最大 3918 tok；安全复审 §5——GLM 端点忽略 max_tokens，真上限需客户端流式截断。
- **拒绝理由（性价比）**：① 爆炸半径/证据比失衡——流式改造改变**每个任务每一轮**的传输路径（工具调用增量组装、断裂重试），而直接证据只触达 5 格（T-B）+ T-A 兼及；② 收益上界小——90s 墙已把单次停顿封顶，肥尾最大 3918 tok ≈ 100s，T-A/T-B 合计保守兑现 ~1 格（r2 兑现率 12–17% 校准）；③ prompt 极简纪律已被 r2 C4d 实证无效，不再重复。**结论：r4 不做**；r5 候选保留，前置条件 = 换 provider 时先做 max_tokens 尊重性 live 预检（安全复审 §5 方法学）。T-B 5 格在 r4 继续诚实记为盲区。

### R4-F（降级，默认不做）480s 计时臂 = 与 C 捆绑的对照臂

- 分析 §2.2 已把 480s 预期校准为 **+1±1**（上界 5/保守 2，且保守 2 格 right-angle/resize-textarea 的杀主含 executor 停顿——C 拒绝后其兑现进一步存疑）。r4 不设独立 480s 臂；若用户要对照证据，按 spec §6 以同 flags + `--episode-ms 480000 --timeout-s 900`（零代码）另批另计，数字永带 `episode_max_time_ms=480000` 标注单独出现。

### 明确不做（本轮砍掉，不心疼）

| 项 | 理由 |
|---|---|
| 视觉/画布/几何族（T-E ~11 格：count-shape、bisect-angle、draw-* 等，L6 强模型） | 射程外（任务指定不做）；r4 报告继续记盲区 |
| click 工具选择器透传（修 `[md='text=Tisha']` 式包装） | 爆炸半径 = 所有任务每次点击，A 落地后证据转弱；列 r5 候选 |
| hot-cold `#touch-area` 专项 | jQuery 委托监听 + 坐标点击，A 覆盖不确定；不为其加引擎面 |
| off-seed 自动补齐/阻断型防护 | 掩盖 planner 缺陷、需逐格 seed 接线；E 选检测披露 |
| planner prompt"URL 逐字使用"行 | 无强制力；E 的补丁层检测已覆盖同一风险面 |
| `--nav-max-tokens` 续用 | 安全复审 §5 实证 no-op；保留误导归因，摘除并注明 |
| 判分/口径链任何改动（`_rate`/overall、C1a、last-wins） | 口径不掺水是最高原则 |

## 2. r3 全开包保留声明

`--terminal-cue / --single-start / --role-routing / --nav-model glm-5.3-flash / --latency-env / --template-notes / --planner-timeout 150 / --extra-tools / --disable-sandbox / --assert-discipline / --provider glm` 原样保留进 r4 headline（R3-2 实证生效故保留 planner-timeout；R3-1 no-op 故摘除 nav-max-tokens）。r4 新增 flags 仅两个：`--md-extended`、`--verify-before-done`，独立、默认 off。默认命令（无 r4 flags）= r3 行为逐字节复现 + E 的 harness 披露增强（无判分影响，r3 E 类先例）。

## 3. 组合策略（headline 单次运行，全开披露）

```
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r4 --stage full --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-model glm-5.3-flash --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline \
  --md-extended --verify-before-done \
  --max-cells <按5h窗口配速，单段可达140>   # 断点续跑同命令
```

headline flags 全集（manifest `flags` 记录）：r3 十项（去 nav_max_tokens）+ `md_interactive_extended=true` + `verify_before_done=true`；harness 披露常量 `offseed_detector/epstart_beacon=true` 随 manifest 记录。

叠加关系：A 主张 email 10 + find-greatest（L3 桶 T-C 6/3 + O-Ho email 7/2 的去重子集）；B 主张 O-H 24 + NR 3（L4 桶）；A∩B 仅 email-inbox-delete 一格（去重计一次）；E 不计格。B 的核验轮在 A 扩表后观察更准（行/图标可见）——两flag互补无冲突。

## 4. 里程碑（5h 窗口分段，单段配速上限 140 + 429 熔断）

| 里程碑 | 内容 | 出口判据 | 预估 |
|---|---|---|---|
| M0 实现+单测 | spec-r4 全部改动 + 离线单测 T1–T10（含 T3 真 Playwright 浏览器测试）+ `make fmt`/`lint` 绿 + 既有 tests/record2gherkin 全绿 | spec §9 验收 1–2 | 0.5–1 天 |
| M1 pilot+smoke | pilot 10 + A 冒烟 2（`--smoke-cells email-inbox-forward,email-inbox-star-reply`）+ B 冒烟 2（`--smoke-cells click-link,email-inbox-delete`，全开 flags） | A kill-switch 判定（§1-R4-A）；B 核验轮 ≤1/格、无 planner 循环；无 402 | 0.5 天（1 窗口） |
| M2 headline full | 125 格 + retry 5；配速翻倍下预期 1 个 5h 窗口收口（兜底 2 窗口） | 130 执行 ≤ 上限；manifest flags 完整；125 任务全覆盖 | 1–2 天 |
| M3 复盘固化 | analysis-r4-failures.md + round4-report.md + security-review-r4-post.md 固化 | clean 口径数字 + 披露清单（A 表长遥测、B verify 轮计数与 junit 幻觉对照、E offseed/epstart/零事件清单）落档 | 0.5–1 天 |
| M4 多 seed ablation（待批） | 门控：`(r4_clean − r3_clean) ≤ +6` 或 A/B 任一单杠杆归因不确定度 ≥±3 格时，由总编排提请用户批准后跑边缘集 majority-of-3（42–50 执行，独立 exp root） | 逐格 majority 表 + 方差带 ±3 对照；只作测量列 | +1 天 |
| M5 480s 对照臂（待批，默认不做） | 用户逐项批准后按 spec §6 跑 | 双列标注呈现 | 另计 |

**headline 路径（M0–M3）合计 ~2.5–4 天；含 M4 共 ~4–5 天。**

## 5. 预算表（执行数 = Hercules 子进程数）

| 阶段 | 执行数 | 构成 | 上限校验 |
|---|---:|---|---|
| M1 pilot+smoke | 14 | 10 + smoke 4（smoke 永不重试） | R2_BUDGET_CAP=144 沿用 |
| M2 headline full | 130 | 125 + retry 5 | **满足"r4 headline ≤130"约束（不含 pilot）**；144 总盘内 14+130=144 ✓ |
| M4 ms3 ablation | 42–50 | 边缘集 ×2 派生 seed，独立 exp-id/root，**另计** | 须用户批准 |
| M5 480s 对照臂 | 135 | 同 flags + 480s/900s，**另计** | 须用户批准，默认不做 |
| **合计（headline 主线）** | **144** | | 配速翻倍后 M2 预计 1–2 窗口；429 熔断兜底 |

token 预算：r3 全程 12.9M；r4 预估 13–15M（B 核验轮 +~0.5–1M；A 扩表使 DOM 读取略增——上限 150 条封顶）。5h 窗口配速按"单段 140 格级"规划，中断即断点续跑。

## 6. 预期影响区间（对照 r3：官方 = clean 59/125 = 47.2%）

| 口径 | 保守 | 上界 | 构成 |
|---|---|---|---|
| **r4 headline 干净口径**（125 分母，E 类预期 invalid=0） | **67/125 = 53.6%** | **76/125 = 60.8%** | r3 基线 59 + A +4~8（email 10 格 0.35~0.5 + find-greatest）+ B +4~9（O-H 翻正 + NR，email-inbox-delete 与 A 去重）；E +0（完整性收益） |
| 50% 主张线 | 需 ≥63 格 | — | 保守档 67 留 4 格缓冲；单 seed 噪声带 ±10 格（±8%）须随表披露 |

诚实备注：①主张押在 A+B 的证据强度上——email 家族是全网格唯一"两轮 0/20、死法唯一、非方差"的稳定缺口，幻觉/NR 是"r2 提出未接、r3 仍 57%"的病根项；②若 headline 落在 60–62 格（48.0–49.6%），以 M3 归因 + M4 门控决策（majority-of-3 把归因噪声压到 ±3）补证，**绝不改判分、不上 best-of**；③A 是感知层变更，若冒烟显示 md 表爆炸/长尾失控（cap 频繁触发、DOM 读取延迟显著上升），headline 前回退该 flag 并更新 plan 附录。

## 7. 风险表

| # | 风险 | 概率/影响 | 缓解 |
|---|---|---|---|
| R1 | A 扩表后 md 表膨胀（非 email 页行的 div/span 收编过多）→ token/延迟上升 | 中/中 | 150 条硬上限 + `[R2G_MD_TRUNCATED]` 留痕；M1 冒烟对比表长遥测；flag 一键回退（默认 off） |
| R2 | A 的注入层假设不成立（md 根本不在行/图标上——cursor 启发式现场误判） | 低/高 | T3 真浏览器测试在 M0 先证；授权最小修补 `isInteractiveElement`；修补失败走 kill-switch 放弃声明 |
| R3 | A 使 agent 在非 email 页被新条目分心（行为面全体变化） | 中/中 | 收编规则要求可读标识（name/class 等）；冒烟含非 email 对照格（pilot 10）；M3 分桶归因可识别 |
| R4 | B 核验轮把接近 240s 页钟的通过格推过线 | 低/低 | 已 +1 奖励先落盘、超时不回判（r3-post §4 实证"反向桥接空"）；最坏浪费一轮 |
| R5 | B 核验后 planner 循环（terminate=no → 再 terminate=yes 再核验） | 低/中 | `verify_rounds` 硬上限 1，第二次 terminate 直接放行；冒烟验证 |
| R6 | B 核验观察上下文膨胀 | 低/低 | 核验步要求"报告"而非回灌原始表；executor 返回摘要文本 |
| R7 | E3 扫描器误伤（合法格被 flagged 噪声淹没） | 低/低 | 全部 flagged-only 不进 invalid；`zero_reward_events` 在 r3 数据上回放应恰好命中 6 格（T7 回放校验） |
| R8 | GLM provider 行为漂移使 A/B 预期失准 | 中/中 | M1 冒烟即校验（表长 + 点击解析 + 核验行为）；失准则 M2 前回退对应 flag 并记录 |
| R9 | D 双口径被误读为刷分 / M4 越批 | 低/高 | majority-of-3 只作测量列、双列披露、headline 永远单 seed 口径；未批准不进运行序列 |
| R10 | 429/5h 窗口中断 | 中/中 | `--max-cells` 断点续跑 + 熔断原样沿用；配速翻倍后单段 140 格级 |
