# R4 Spec 独立审查 — review-r4

> 审查对象：`plan-r4.md`、`spec-r4.md`。法定输入：`analysis-r3-failures.md`。审查日期：2026-09-24。
> 审查纪律：只拦"跑不通 / 数字不可信 / 作弊面 / 预算爆炸"；本审查只读不改任何代码与既有文档，未做 git 操作。
> 源码核实均为本仓库当前 HEAD 实读；实测在本地以 vendored miniwob 页 + 真 Chromium（仓库 .venv Playwright）完成，脚本留档 `dev_runs/r4_review/live_probe*.py`（gitignore 区，未入库）。

## 结论：**REVISE**（必改 3 项，全部为 spec 局部修正；方向、架构、开关纪律、判分中立性均成立，修正后无需再审即可放行 M0）

---

## 1. 必改项（阻断 M0 出口或数字可信度，须先修 spec）

### MF-1 compact 剥离 `class`：T4 断言自相矛盾，star/trash 图标语义锚丢失（跑不通 + 削弱 A 的核心主张）

- `compact_interactive_node` 的 `allowed_keys`（get_interactive_elements.py:104-128）**不含 `class`/`id`**；spec §1.2 又要求"compact、json.dumps……逐字节保留"。两者合取的后果：extended 收录的图标节点在 compact 阶段被剥掉 `class`，终表条目退化为无语义形态。
- **实测证实**（email-inbox.html?r2g_seed=42，episode 启动后）：
  - star span 原始节点 `{"md":11,"tag":"span","name":"Caralie","class":"star"}`（name 系 flatten 父继承自行文本）；
  - 经现行 compact 后 → `{"md":11,"tag":"span","name":"Caralie"}`，与 trash 的 compact 结果**完全同形**——agent 无法区分 star 与 trash（每行 2 个 span 同名），star-reply 类任务只能盲试；
  - spec §5-T4 断言"≥2 个 class ∈ {star, trash} 的条目"按字面实现**必然失败**，与"读完本文不再需要做任何设计决策"矛盾。
- **必改**：extended 模式下 `allowed_keys` 增 `"class"`（实现上可无条件加入——off 模式抓取属性表无 `class`，任何 off 节点不带该键，off golden 逐字节不受影响，需在 T3a/T9 golden 中锁定验证）；T4 断言相应改写在 compact 之后的条目上。

### MF-2 预期区间数字与自述概率/去重规则不可推演（数字不可信）

- A 上界 "+8"：其自述构成为"email 10 格 ×0.35~0.5 + find-greatest ~0.5"→ 算术上界 **5.5**，不是 8。8 只能用分析 Top-10 #1 的"~0.7 **能正确交互**"凑出（10×0.7+0.5=7.5）——把"能交互"当"能通过"与本节"判分立场"不一致（法定输入给的通过概率是 ~0.4）。
- B 区间 "4~9（O-H 翻正 4~8 + NR 1~2）"：翻正上限 8/24=0.33，高于法定输入自述的翻正 ~0.2（→ ~5）；且 4~8 与 1~2 的和为 5~10，与"4~9"不闭合。
- §6 构成算术：上界 59+8+9=76 **未扣** A∩B（email-inbox-delete 同在 A 的 email 10 格与 B 的 NR 清单中）去重 1 格 → 至多 75；保守档 67=59+4+4 成立与否取决于"delete 只计入 A 保守档"这一未写明的假设。
- **必改**：给出逐格可推演的构成表（每格 × 概率来源行号），或把区间修正为自洽值。影响评估：修正后保守档 ~66（52.8%）仍高于 50% 主张线（63 格）且缓冲 ≥3 格——**50% 主张本身不动摇**，此改只涉数字诚实。

### MF-3 预算"144 恰满"账面与代码不符，且无全局累计强制（预算）

- 代码事实：`RETRY_BUDGET = {"pilot": 2, "full": 5}`（orchestrator.py:101）——M1 的 10 个 pilot 格**可重试 2 次**（smoke 确不重试，orchestrator.py:1239-1270），M1 最坏 **16** 而非 14；plan §5"14+130=144 ✓"漏计。
- cap 强制是**按单次 orchestrator 调用**的（`self.hercules_runs = 0` 每次 run() 重置，orchestrator.py:884；上限 `min(STAGE_RUNS+RETRY+smoke, 144)` 按阶段计，orchestrator.py:1059-1061）——跨 M1/M2、跨断点续跑的 144 总盘**无工具强制**；且 M2 若拆 2 个窗口续跑，每次调用各带 5 次重试池，130 的账面同样可漂移。
- **必改**：预算表改为 M1 = 14–16（计提 pilot retry 余量）、M2 = 130/单次调用，并写明全局纪律条款：每次调用的实际执行数记入 manifest/报告，累计超过 144 即在报告披露；或声明接受最坏 146 的量化偏差。

---

## 2. 源码核实表（重点项逐条）

| # | plan/spec 主张 | 核实方式 | 结果 |
|---|---|---|---|
| V1 | "死分支"：`flatten_elements`（get_interactive_elements.py:44-89）的 role 白名单匹配 `r` 键，而 `rename_children` 未被调用、实际键是 `role`，role 匹配今天是死分支 | 实读 L13-17 import 含 `rename_children` 但调用在 L145-146 **被注释**；`do_get_accessibility_info` 的 JS 树产出 `node.role/tag/md`（get_detailed_accessibility_tree.py:1160-1166），`__fetch_dom_info` 亦写 `role/tag` 键 | **属实**。另核实：`clickable/focusable` 两分支同样为死键（整条管线无人产出），现行唯一活路径 = `tag ∈ {a,button,input,select,textarea}` 且带 md——比 spec 表述的"死得更多"，结论方向不变 |
| V2 | div/span 行与图标进不了终表（email 家族死因） | 实读 + 真浏览器实测：off 模式 flatten email-inbox 输出 **0 条**（复现 executor 自述 "Interactive elements list is empty"）；行/图标节点确实带 md（见 V3）但被 L80-88 过滤 | **属实** |
| V3 | 注入层 `isInteractiveElement` cursor:pointer 启发式已覆盖 `.email-thread`/`.star`/`.trash`/`.card.hidden`，任务在主文档非 iframe | 实读 vendored CSS（email-inbox.html:32,42,44；find-greatest.html:15 均 `cursor: pointer`）+ **实测 md 注入**：email-inbox 实测 `.email-thread`×7、`.star`×7、`.trash`×7 全部携带 md；find-greatest 实测 6 张卡（div.card.hidden + span.card-value）携带 md，页面无 iframe（agent 日志的"nested iframe"系误判）；star/trash 点击 handler 挂在主文档 `#main/#email`（email-inbox.html:282-300,407-425） | **属实**（plan-r2 风险 R2"注入层假设不成立"可解除，T4 大概率一次通过） |
| V4 | click 工具把一切无 `md=` 输入包成 `[md='…']`（拒绝选择器透传的依据） | click_using_selector.py:45-46：`if "md=" not in query_selector: query_selector = f"[md='{query_selector}']"` | **属实** |
| V5 | A 的 off = r3 逐字节（role_hit 仍只查 `r`、tag 集不变、extra_hit 恒 False） | 逐条比对 spec §1.2 条件式与现 L80-88 | **等价成立**；T3a golden + T9 parity 可锁定 |
| V6 | md 表爆炸风险与 150 上限合理性 | **实测 8 页**（episode 启动态）：email-inbox 47、email-inbox-noscroll 23、find-greatest 11、login-user 9、book-flight 8、multi-layouts 8、enter-text 6、social-media 4——全部远低于 150 | **上限充裕**（miniwob 页 400×400、元素量小）；截断遥测 `[R2G_MD_TRUNCATED]` 覆盖离群页。附注：截断按文档序，重页上任务区（页尾）可能先被截——已在建议 S3 |
| V7 | B 与 C2 终局信号交互不冲突 | 实读 TERMINAL_CUE_PATCH（miniwob_server.py:167-190）：中性常量文本、不 POST、不触碰 `WOB_RAW_REWARD_GLOBAL`；B 的核验轮只加一个 executor 轮，判分链（REWARD_HOOK_PATCH、`/latest`、last-wins、C1a）零改动；`_route_after_planner`/`_build_graph`/initial state 锚点（simple_hercules.py:956-964/970-987/1021-1042）与 spec 行号吻合，`verify_rounds` 经 LangGraph 状态持久、planner 节点不回写，硬上限 1 无循环面 | **成立** |
| V8 | B 不引入"读 reward 作弊面" | `_VERIFY_STEP` 只点名 `get_page_text` + `get_interactive_elements`（页面可见态）；`WOB_REWARD_GLOBAL` 是 core.js JS 变量（core.js:44-45），不出现在页面文本。**但 spec 无一句显式禁止**（见 S1）。关键事实：官方判分页面/服务端权威，agent 读 reward 不可能改写 official_passed，仅影响 junit 自报诚实度——即 B 至多把"读真值"用于自报，非新作弊面 | **基本成立，补一句显式禁令更稳**（S1） |
| V9 | E 判分中立、不带 flag 的理由 | 实读 CellScan 合并语义（orchestrator.py:431-444：flagged 或合并、invalid_reason 仅安全事件写入）与 build_result_row（status/official_passed 永不自 scan 回写，orchestrator.py:21-22,1169-1170）；E3 三条全部 flagged-only 与现有结构同构；E1/E2 为 append-only 服务端记录 + 页面 load 期一次 fetch POST，不触碰 episode；offseed/epstart/零事件对**任何已有格**的 status/reward 判定零影响 | **成立**；off-seed 1 格 + 零事件 6 格与分析 §4.5 一致，T7f 回放判据可行 |
| V10 | flag 矩阵：--md-extended/--verify-before-done 与 r2/r3 flags 兼容 | 实读 CLI（orchestrator.py:1354-1389）：r3 全部 flag 在位（含 `--nav-max-tokens` 保留不删）；`_child_extra_env`（969-991）结构支持两新键按 flag 注入；manifest `flags` dict（871-886）可增三键；`nav_max_tokens` 键现恒写入，r4 需条件省略（spec §4 已写，T2 未断言，见 S2） | **兼容** |
| V11 | 现场锚点行号（spec 头部 5 个锚点） | get_interactive_elements.py:44-89 ✓；get_detailed_accessibility_tree.py:255-266 ✓（attributes 表恰在 L255-266）；simple_hercules.py:956-987 ✓；miniwob_server.py:238-249 ✓（patch_core_js 恰在 L238-249）；orchestrator.py:447/537 ✓（scan_cell_log/scan_cell_rewards） | **全部命中** |
| V12 | config getter 风格与 AgentState | config.py:1215-1223 `get_sandbox_disabled` 同风格 ✓；relevant_keys（487 起，SANDBOX_DISABLED/PLANNER_ASSERT_DISCIPLINE 在 582-584）可增量 ✓；`AgentState(TypedDict, total=False)`（simple_hercules.py:52）增键零风险 ✓ | **可行** |

## 3. 实测记录（供 M0-T4 复用）

- 环境：仓库 `.venv`（Playwright chromium），`http.server` 伺服 `record2gherkin/benchmark/miniwob_html/`，`PROJECT_SOURCE_ROOT` 指 tmp（do_get_accessibility_info 需写 json 日志目录）。
- 流程：打开 `<page>.html?r2g_seed=42` → 点击 `#sync-task-cover` 启动 episode（vendored 页无 auto-start 补丁，此步必要且可行，实测 7 封邮件生成）→ `do_get_accessibility_info(page, only_input_fields=False)` → 以 spec §1.2 收录判定（off/extended 两态）复算 flatten，另从 DOM 反查 `[md]` 元素的 class 模拟 §1.3 的 extended 抓取。
- 关键数字：off 模式 email-inbox=0 条 / find-greatest=1 条（r3 症状复现）；extended 模式 email-inbox 47 条（thread/star/trash 各 7 全部带 md 入表）、find-greatest 11 条（6 卡、name=数字文本）；8 页 extended 计数 4~47，无一触 150。
- 脚本：`dev_runs/r4_review/live_probe.py`（初版）、`live_probe3.py`（compact 前后对照）、`live_probe4.py`（8 页计数）。

## 4. 法定输入核对（analysis-r3-failures.md 转译忠实性）

| 转译点 | 核对 |
|---|---|
| A←§4.2（email 10 格两轮 0/20、死法唯一、Top-10 #1 的 ~0.4 过/~0.7 交互） | 忠实；但 plan 把 0.7 交互率用于 A 上界推导 → MF-2 |
| B←§3.1（幻觉 24 = junit=true 子集、占官方失败 57%、NR 3 同病根；Top-10 #2 拦截 0.7/翻正 0.2） | 忠实；翻正上限 8 超出 0.2 → MF-2 |
| E←§4.5（off-seed 1 + 零事件 6） | 忠实（T7f"恰好 6 形态"判据与分析一致） |
| §2.2 480s +1±1 → F 降级 | 忠实（上界 5/保守 2、杀主含 executor 停顿均如实转译） |
| §5 多 seed → D（majority-of-3 边缘集、拒绝 best-of、42 执行/3.5h/4.3M） | 忠实，口径纪律（独立 exp、双列、headline 永单 seed）与分析口径警告一致 |
| §6 可恢复矩阵 L3+L4=16 最大单项 → A+B 组合 | 忠实引用；§7 战略项 37 格（#1+#2）转译一致 |
| 53.6%/60.8% 有无夸大 | 区间算术本身成立（67/76 ÷ 125），但构成推导不可复算 → MF-2；修正后保守 ~66（52.8%）仍支撑 50% 主张，**结论数字不翻盘** |

## 5. 不阻塞建议（≤3 条）

- **S1**：spec §2.2 补一句显式禁令："核验轮只读页面可见状态，禁止以任何工具（含 execute_js）读取 `window.WOB_REWARD_GLOBAL`/`WOB_RAW_REWARD_GLOBAL`"。现文本仅靠措辞暗示；虽然官方判分页面权威、读 reward 不构成判分作弊，但一句话可消除审计歧义。
- **S2**：T2 补断言 (e)：headline manifest `flags` **无** `nav_max_tokens` 键（§4 只有文字要求，未入测试锁定）；顺带在 §0.3 披露措辞中把"默认命令 = r3 行为逐字节复现"限定为"引擎与判分行为"——E1/E2 信标恒开使默认运行的 core.js 字节与 r3 不同（无判分影响，但字面"逐字节"不成立，T8a 的 offseed_beacon=False golden 分支在真实运行序列中是死代码）。
- **S3**：E3-① 的 `nav==0 ∧ timeout` 在 stdout.log 不可读时（scan 返回中性 0）会误标 flagged——建议仅在 log 可读且确无导航时标注，或接受并作为披露噪声写进 M3 报告口径；另建议冒烟时记录截断发生在表内第几条（验证文档序截断是否伤及页尾任务区）。

## 6. 审查范围附注（未处理，仅记录）

- 未复审 r2/r3 既有 spec 条文的继承正确性（spec-r4 声明沿用，超出本轮范围）。
- M4/M5 为待批附加臂，仅核对其"零 M0 代码、独立 exp、双列呈现"设计与主线的隔离性（成立），未审其获批后实现细节。
