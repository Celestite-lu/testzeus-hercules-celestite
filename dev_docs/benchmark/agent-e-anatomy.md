# Agent-E 解剖与选择性吸收评估（r5 规划输入）

> 调研时间：2026-09-22 ~ 2026-09-24。方法：Agent-E 论文（arXiv 2407.13032，HTML v1）+ 仓库 HEAD 实克隆解剖（`f218c3c`，2025-05-12）+ 追查 81.6% 数字的真实出处；对照本仓库 `testzeus_hercules/core/` 现状（r4 后）。未验证处逐条标注。

## 0. 结论速览

1. **可比性判定：不可比，且锚点归属错误。** Agent-E 从未发表过 MiniWoB++ 数字；"125 challenging MiniWoB++ instances 上 81.6% Exact-Match"是 **HxAgent**（arXiv 2608.15491，Ho Chi Minh City Univ.，2026-08）的 Table 4 数字——GPT-4o 多模态 + 训练期经验注入 + 动作序列金标判分 + 5 个 flight/日历类任务 × 25 实例的偏科子集。`baselines.md` 把它记在 Agent-E 名下是错的。
2. Agent-E 论文真实数字：**WebVoyager 73.2%**（GPT-4-Turbo，643 任务，人工评判），与我们的 MiniWoB++ 口径没有交集。"距 Agent-E 20 个百分点"的叙事（round4-report.md §1）应废弃。
3. 组件层面：Hercules 是 Agent-E 的直系后裔，**Agent-E 的全部机制组件我方已继承或在改造后超越**（planner/browser-nav 分层、md/mmid DOM 蒸馏、mutation observer 变化观察、LTM、技能集超集）。仓库 HEAD 与论文 v1 中**不存在** Curious Reflector / 独立 self-evaluator / 独立 self-corrector 组件（`grep -ri "curious\|reflector"` 零命中；论文 v1 全文亦无）。
4. 真正值得吸收的只有 **1+1 项**：(a) HxAgent 的"DOM 失明时升级看截图"观察策略（射向我们 ~11 格视觉/画布盲区，需 GLM 视觉验证 + 口径披露裁决）；(b) 口径级决策——HxAgent 式跨 episode 经验库（与单 seed 零样本口径冲突，交总编排/用户，战略载体天然是 record2gherkin）。
5. 我方在判分完整性、反作弊、多域编排、LLM 路由、token/上下文治理上已明确超越 Agent-E（§5），这构成"我们不是在追一个更强的祖先，而是已经分化出更严的评测文化"的叙事基础。

---

## 1. 可比性核实（第一优先）

### 1.1 Agent-E 论文的实际数字（出处：arXiv 2407.13032v1 §3，HTML 全文）

| 项目 | Agent-E 论文实际设置 |
|---|---|
| 基准 | **仅 WebVoyager**（He et al. 2024），15 个真实网站 643 任务；MiniWoB++ **全篇未出现** |
| 任务修正 | 人工把静态日期任务平移 8 个月保可做 |
| 观察模态 | 纯文本 DOM（论文自述 "text-only web agent"，三档 content type：text_only / input_fields / all_fields） |
| 模型 | **GPT-4-Turbo**（planner 与 browser nav 同款），非 GPT-4o |
| 尝试次数/计时 | 论文未报告每任务尝试数与限时；实测均值成功 150s / 失败 220s，平均 25 次 LLM 调用/任务 |
| 判分 | 5 名人工标注员（IST 办公时间），"完整完成才算过"；ask-user 技能禁用 |
| 成绩 | **总 73.2%**（Table 3 为 73.1）；站点间 27.3%（Booking）~95.7%（WolframAlpha）；对 text SOTA +21%、多模态 SOTA +16% |
| 消融 | **无**（论文无任何 ablation 表） |

### 1.2 81.6% 的真实归属：HxAgent（出处：arXiv 2608.15491，HTML 全文；OpenReview 记录的 arXiv 号 2508.10833 与 2608.15491 不一致——未验证哪个是正式号）

| 项目 | HxAgent 的 "125 challenging MiniWoB++ instances" 设置 |
|---|---|
| 子集构成 | **5 个任务 × 25 实例 = 125**：choose-date、book-flight、flight.AA、flight.Alaska、flight.Alaska-auto（Table 4）——恰是其经验规则最密的 flight/日历系 |
| 成绩 | **81.6% Exact-Match（±34.6）**；同表基线：Li et al. 9.6、SeeAct 8.0、WALT **0.0**（差 70+ 点的"基线"说明子集高度偏科，不是全任务能力的度量） |
| 完整集 | 另有 39 任务 × 25 实例 = 975 实例上 **97.4% EM / 99% PM**（含 20 实例/任务训练 + 最多 8 条 few-shot 经验；消融：去经验 88%、去迭代规划 75%、去 SAM+经验 47%） |
| 观察 | **多模态**：(DOM 文本, 截图, 可交互元素) 三元组；文本先行，出现 ≥2 个候选歧义时才调截图；超大 DOM 时用截图生成文本摘要 |
| 模型 | GPT-4o，temperature=0 |
| 尝试/计时 | 每任务 50 实例，步数上限 MAX_t（值未披露），无时间限制表述 |
| 判分 | **Exact-Match = 生成的动作序列（op, element, value）与金标全对才算过**——是动作序列对齐，不是页面终态 reward |

### 1.3 与我方口径逐项对照

| 维度 | 我方（r4 headline，61.6%） | Agent-E（论文） | HxAgent（81.6% 那张表） | 差异方向 |
|---|---|---|---|---|
| 任务集 | 全 125 任务类 × 1 实例（browsergym registry） | 不适用（WebVoyager 真实网站） | 5 任务类 × 25 实例，flight/日历偏科 | 对 HxAgent 大幅有利 |
| 观察 | 零样本文本/DOM（md 表，≈a11y 档） | 文本 DOM | DOM+截图+元素，多模态 | 对 HxAgent 有利 |
| 模型 | GLM（240s/格） | GPT-4-Turbo | GPT-4o | 对两个对手有利 |
| 经验/演示 | 无（零样本） | 无 | **20 实例/任务训练 + 8 few-shot 经验规则** | 对 HxAgent 大幅有利（消融 −9.4 点） |
| 尝试 | 单 seed 单次 | 未披露 | 训练-评测分离 = 同任务族的变相多 attempt | 对 HxAgent 有利 |
| 判分 | 页面原生 `WOB_RAW_REWARD_GLOBAL > 0` | 人工评判 | 动作序列金标 Exact-Match | 不可互译：序列全对≠终态成功，反之亦然 |

**判定**：三个数字处在三个不可互换的口径里。我方 61.6% 的同 regime 公开可比带仍是 baselines.md 的 A 档（零样本文本-DOM GPT-4 级 47-60%），**已越带上沿**；"冲刺 81.6%"的目标本身建立在错误归属上。建议 r5 报告口径改为："零样本文本-DOM 全 125 类单次口径下，无同口径公开 SOTA 可比；A 档带已越上沿；81.6%（HxAgent）与 73.2%（Agent-E/WebVoyager）均不同口径，仅作背景。"

### 1.4 衍生修正项（供总编排采纳，本文不代改）

- `baselines.md` A 档行 "Agent-E（125 challenging instances）81.6%" → 更正为 HxAgent 并标注四重口径差。
- `round4-report.md` §1 "距同源架构锚点 Agent-E（81.6%）差 20 个百分点" → 历史文档不改，r5 报告修正表述。

---

## 2. 组件级解剖（Agent-E 机制 → 我方现状 → 差距）

代码依据：`/tmp/agent-e-anatomy`（Agent-E HEAD `f218c3c`）vs `testzeus_hercules/`。文件名同源关系明显（dom_helper/js_helper/dom_mutation_observer/detect_llm_loops/response_parser 等均为继承后分化，已逐文件 diff 确认全部 DIFFERS）。

| # | Agent-E 组件 | 机制（论文+仓库实证） | 我方对应物 | 差距评估 |
|---|---|---|---|---|
| 1 | 分层 planner（nested chat） | AutoGen 双 agent：PlannerAgent（50 轮）→ 每步 fresh 实例化 BrowserNavAgent（10 轮/步）；planner 输出 JSON `{plan, next_step, terminate, final_response}`（`ae/core/prompts.py` PLANNER_AGENT_PROMPT） | `simple_hercules.py` LangGraph：planner 节点（500 轮上限）→ executor 节点每步 fresh 调 nav agent（10 轮）→ assertion/verify 节点 | **无差距，我方更结构化**（+target_helper 多域路由、+is_assert 判分语义、+token/耗时核算） |
| 2 | 子目标管理 | planner 的 next_step 单元素粒度 + "helper 无状态、每步带全上下文"约定 | 同源：next_step + EXPLICIT CLOSURE NUDGES + helper 无状态约定（high_level_planner_agent.py §Helper Direction） | **无差距** |
| 3 | DOM processor（三档蒸馏 + mmid） | `get_dom_with_content_type`：text_only / input_fields / all_fields；a11y 树注入 `mmid`+`aria-keyshortcuts`（`get_detailed_accessibility_tree.py`） | 同源文件：`get_page_text` / `get_input_fields` / `get_interactive_elements`（md 标识）；**r4 `--md-extended` 把 div/span/label 等"可见但不可寻址"元素纳入表**（+17 格实证） | **我方超越**（extended 覆盖是 Agent-E all_fields 没有的） |
| 4 | 变化观察（change observation） | 页面级 MutationObserver + click 技能回包附"new elements have appeared... needs further interaction"（`click_using_selector.py` L45-58） | 同源继承 + `_STATE_REFRESH_MARKERS`（simple_hercules.py L528）触发状态刷新 | **无差距** |
| 5 | planner 验证纪律（"每步后验证、终止前验证、勿信 helper"） | PLANNER_AGENT_PROMPT Guideline 5：逐步验证 + 终止前验证 | **我方按 regime 反向裁制**：MiniWoB 无成功指示器，prompt 明令"勿加独立验证步、勿找页面成功指示"；诚实性由 `--assert-discipline`（r4 headline 已开）+ `--verify-before-done`（r4-B，判分完整性 0 分组件）承担 | **口径性差异，非缺失**。回吸会重蹈 r2 前的验证空转（240s 预算下每步验证 = 烧钟） |
| 6 | 持久性/计划修订（"very very persistent planner…revise plan…绝不轻言终止"） | 同 prompt Guideline 7 + "Complexities of web navigation" 7 条 | 我方 planner 有 fallback/替代动作指令但整体更收敛（"MULTIPLE ATTEMPTS 后报告并终止"） | 我方有意收紧。r3 §2 实证：9 个超时格死因是 canvas 盲区+停顿，不是过早放弃。**无收益证据** |
| 7 | URL 回溯（error recovery/backtracking，论文 §3.5） | planner 在步骤里显式带返回 URL；GO_BACK prompt 存在但 HEAD 未注册技能 | 我方**明确禁止** re-navigation（任务 URL 重开=重置任务，r3 专项修掉的 churn） | **regime 不适用**。MiniWoB 单页任务下回溯=自毁 |
| 8 | self-evaluator / self-corrector / Curious Reflector | **仓库 HEAD 与论文 v1 均不存在**（grep 零命中）。实际机制 = 组件 5+4+6 的组合（planner 自查 + 变化观察 + 修订） | 见上各行 | **传说组件，无实体可吸**（未验证：EmergenceAI 博客/其他分支是否有后续版本） |
| 9 | 技能集 | click、entertext、bulk_enter_text、press_key_combination、openurl、geturl、get_dom、pdf_extractor、pause_flow、get_user_input、动态加载（ADDITIONAL_SKILL_DIRS） | `core/tools/` 22 个 + `core/extra_tools/` 7 个（含 drag_and_drop、dropdown、slider、date-time、upload、geo、captcha、sandbox、clipboard） | **我方超集**；pdf_extractor 在 extra_tools 亦有 |
| 10 | 用户 LTM（user_preferences.txt 注入 prompt） | `static_ltm.py` → `$basic_user_information` | 同源：`basic_test_information`（测试数据 LTM） | **已继承改用** |
| 11 | ask-user 技能 | 登录/captcha 时交人工 | 无（自动评测下 = 终止报告） | **评测口径下不应有** |
| 12 | 编排基建 | AutoGen wrapper，无 token 核算、无压缩、无超时兜底、无重试语义 | LangGraph + context 超限压缩回退 + planner timeout 兜底 + cost 汇总 + Portkey 多模型路由 | **我方超越** |
| 13 | 评测 harness | WebArena/WebVoyager 式：JSON 任务 + 字符串判分（exact/fuzzy/must_include/ua_match，`test/evaluators.py`） | record2gherkin/benchmark：页面原生 reward + own-seed 过滤 + off-seed 信标 + 五类作弊扫描 + 超额归因实证 | **我方超越（更严）** |

**小结**：组件清单上 Agent-E 没有一项"我方缺了导致丢分"的机制。我方剩余 48 格的结构（r4：官方失败 ~39 = 完成幻觉存量 + 能力失败，超时 9）在 Agent-E 的机制箱里找不到对应钥匙——它的 WebVoyager 成绩恰恰依赖长程多页导航与人工判分的宽容度，这两者都不在我方口径内。

## 2b. HxAgent 补充解剖（真正持有 81.6% 的系统）

| HxAgent 组件 | 机制 | 我方现状 | 差距 |
|---|---|---|---|
| 迭代规划 + 逐步 State Evaluator（"proactive correction"，消融 −23 点） | 每步后由 G 判 success/stop 触发重规划 | 我方 planner 循环本身就是逐步重评估；r4 verify gate 是终局版 | **等价** |
| 短期记忆 SAM（末 k 个 state-action 对；消融去 SAM+经验 −50 点） | 观察巨大时的历史截断 | 我方全历史 + 压缩回退（context 超限才压） | 无证据我方受损；r3 L2 已单独治理停顿/巨补全 |
| 长期经验库（训练期轨迹 + LLM 抽规则 K，去重入库；消融 −9 点） | 跨 episode 的可复用启发式 | **无** | 有，但属口径问题（§4-B） |
| 多模态升级观察 o^img | DOM 文本优先；≥2 候选歧义 / 超大 DOM 摘要时调截图 | **无**（截图仅作 proof 留档，`get_latest_screenshot_stream()` 存在但不入模型；工具回包管线 `_execute_tool_call` 仅 str） | 有，唯一硬差距（§4-A） |
| SoA→Selenium 脚本生成（77.3-100% 可重放） | 成功轨迹固化为测试脚本 | record2gherkin 蒸馏器（轨迹→Gherkin 用例） | **战略同构**，非 benchmark 补丁 |

---

## 3. 吸收评估矩阵

评级：收益依据 = 对剩余 48 格的映射；成本 = 实现+验证工作量；文化冲突 = 与确定性/单次口径/防作弊文化的张力；作弊面 = 引入判分污染的风险。

| 候选 | 预期收益依据 | 实现成本 | 文化冲突 | 作弊面 | 结论 |
|---|---|---|---|---|---|
| **A. 截图升级观察**（HxAgent o^img；Agent-E 无此物） | 视觉/画布/几何族 ~11 格（r3 §4.4：count-shape、count-sides、click-shape、circle-center、drag-circle、bisect-angle、find-midpoint、draw-* 等）+ "不可寻址元素"残量的兜底观察；r3 恢复矩阵给视觉几何族保守 2/1 格、视觉计数 4 格属方差重灾区 | **中**：新观察工具（走 `EXTRA_TOOLS_MODULES` 注入，不动 testzeus_hercules 核心）+ 突破 `_execute_tool_call -> str` 的管线让 ToolMessage 携带 image content（MultimodalBaseNavAgent 已是视觉模型接线，GLM 视觉支持度**未验证**）+ browser_nav prompt 加升级规则（"md 表与 page text 双查无果 → 截图判读"） | 中：截图是合法观察非判分信号，但使我方脱离"纯文本-DOM regime"，A 档归属需重新披露 | 无（观察输入，非判分旁路） | **改造后吸收，先 3-5 格试射**（r3 §7-6 清单），GLM 视觉不行即弃 |
| **B. 跨 episode 经验库**（HxAgent 长期经验） | 其消融 −9 点；对我方≈把每类任务从零样本变 few-shot | 高（轨迹采集+规则抽取+去重+注入管线） | **高：直接破坏零样本单次口径**，成绩增益属协议增益非能力增益 | 无，但可比性掺水 | **不擅自吸收；口径决策上交总编排/用户**。若未来采纳，战略载体 = record2gherkin（经验→Gherkin 用例库），与项目主目标同构 |
| C. planner 验证纪律回吸 | r3/r4 实证完成幻觉是存量失败源，但 r4-B verify + assert-discipline（headline 已开）已按我方环境改造过 | 0 | 回吸会恢复验证空转（240s 预算） | 无 | **不吸收**（已以更适配形式存在） |
| D. 持久性/"绝不终止"纪律回吸 | 超时 9 格死因非过早放弃（r3 §2） | 低 | 与 240s 预算直接冲突 | 无 | **不吸收** |
| E. URL 回溯/GO_BACK | MiniWoB 单页，回溯=任务重置 | 低 | 与防 churn 成果冲突 | 无 | **不吸收**（regime 不适用） |
| F. get_user_input / pause_flow / pdf_extractor | 评测禁人工；pause 已有 time_keeper；pdf 在 extra_tools 已有 | — | ask-user 破坏自动评测 | — | **不吸收**（已有/不适用） |
| G. SAM 历史截断 | 无证据我方全历史+压缩受损 | 低 | 低 | 无 | **不吸收**（无收益依据） |
| H. enter_text_and_click 复合技能 | Agent-E 自己都注释掉了；我方 bulk 工具 + submit 约定覆盖 | 低 | 低 | 无 | **不吸收** |

## 4. 吸收组合建议（供 r5 规划采纳）

**唯一机制类吸收：A（截图升级观察），按试射-裁决两步走**
1. 试射臂（独立 exp-id，不进 headline）：3-5 个视觉格（建议 count-shape、count-sides、circle-center、drag-circle、visual-addition——最后一个是 r2 曾过的"非永久盲区"校准点）。
2. 实现落点（不改 testzeus_hercules 的合法路径）：`EXTRA_TOOLS_MODULES` 注入一个 `observe_screenshot` 工具（复用 `PlaywrightManager.get_latest_screenshot_stream()`，`core/extra_tools/visual_skill.py` L47 已有先例）；管线缺口在 `simple_hercules.py` 的 `_execute_tool_call`（返回 str）与 base_nav_agent 工具回包——若试点通过，把"工具回包可携带 image content"作为 PLAN.md 接线点申报。
3. 前置验证（一行成本）：先手工确认 GLM 端点接受 image_url content（litellm passthrough）——**未验证，是整条建议的 go/no-go**。
4. 口径披露：若进 headline，报告需新增"文本-DOM+截图升级"regime 标注并同步修 baselines.md 分层；若 GLM 视觉弱导致试射 0 增益，直接关闭并归档。

**口径类决策上交（不代决）**
- B（经验库）与多 seed majority-of-3（r4 报告 §4-2 已列）同属"测量口径变化"，建议总编排合并为一个"r5 口径包"请用户裁决：维持纯零样本单次（则只做 A 的试射）vs 开经验/few-shot 臂（另立 exp-id 与档位标注）。

## 5. 我方已超越 Agent-E 的部分（增强叙事）

1. **判分与完整性**：页面原生 reward、own-seed 过滤、off-seed 信标、五类作弊扫描、超额归因逐格实证——Agent-E 用人工/LLM 字符串判分，无此闭环。
2. **多域编排**：api/sec/sql/mcp/time_keeper/executor 七个 helper + target_helper 路由；Agent-E 只有 browser。
3. **基建**：LangGraph 状态机 + assertion/verify 节点、context 超限压缩回退、planner timeout 兜底、token/cost 全程核算、Portkey 多模型 fallback；Agent-E 的 AutoGen wrapper 均无。
4. **DOM 覆盖**：r4 `--md-extended` 的"可见但不可寻址"元素纳入（+17 格实证）超出 Agent-E all_fields 的交互元素白名单。
5. **工具超集**：drag/dropdown/slider/date-time/upload/geo/captcha/sandbox/clipboard 等。
6. **任务表示**：Gherkin 中转（Given=URL/When=指令）是双刃剑（转译损耗），但它是我方"录制即用例"主目标的载体，属战略分化而非落后。

## 6. 来源与未验证清单

- Agent-E 论文：arXiv 2407.13032（HTML v1，§2 架构 / §3 评测）；仓库 github.com/EmergenceAI/Agent-E @ f218c3c（2025-05-12）本地解剖。
- HxAgent：arXiv 2608.15491（HTML v1，§3 架构、Table 4/6、附录 C）；其 OpenReview 页引用 arXiv:2508.10833，两号不一致——**未验证**。
- 未验证项：GLM 的图像输入支持；EmergenceAI 是否在博客/其他分支发布过 MiniWoB 数字；HxAgent MAX_t 具体值与 GPT-4o 快照版本。
- 我方数字：`dev_docs/benchmark/round4-report.md`、`analysis-r3-failures.md`、`test-report.md` §15.5。
