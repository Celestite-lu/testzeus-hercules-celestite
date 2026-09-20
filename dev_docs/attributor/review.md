# 失败归因器 spec 独立审查（review.md）

> 审查对象：`dev_docs/attributor/plan.md`、`dev_docs/attributor/spec.md`
> 基准：`PLAN.md` §3.5、`dev_docs/README.md`；事实核对源码行逐条打开验证（见 §3 核对表）
> 日期：2026-09-20　审查方式：只读源码/文档核对，未改任何文件

## 结论：**REVISE**（必改项 2 条）

两条必改项都指向同一核心不变量——"规则层定类准、LLM 引用回查不误杀"：M1 让旗舰签名 S3 在真实运行里有系统性命中的可能但规格二义，M2 让带锚轮次的合法 LLM 引用被回查器必然误杀。其余部分（签名文本准确性、回查降级闭环、证据加载容错、离线可测性）核对通过。

---

## 1. 必改项

### M1 S3 主扫描语料范围二义，且与真实数据流脱节（连带 S12 同类问题）

- **问题**：spec §3.2 S3 行语料列写 "FAIL（主）+ THOUGHTS（次级）"，与 §3.1 的默认规则"每签名按 FAIL → SYSOUT → THOUGHTS 顺序扫描"不一致。实现者无法确定：主复合匹配（`max nav rounds (` 且 `reached before ##TERMINATE TASK##`）是只扫 FAIL，还是按默认三字段扫。
- **依据（源码数据流）**：`_run_nav_agent` 的返回串（含 nav 轮次耗尽串，`simple_hercules.py:1004-1007`）在 `_executor_node` 经 `messages.append(HumanMessage(content=f"[{agent_name}]: {helper_response}"))` 回灌 planner 会话（`simple_hercules.py:785, 805-806`），该会话经 `runner.py:93-96, 125-135` 存入 `agent_inner_thoughts.json`。即该串的**规范落点是 THOUGHTS**；进入 FAIL/SYSOUT 仅当 planner 恰好把它复述进 assert_summary/final_response（LLM 行为，不可靠）。若按"FAIL-only 主扫描"实现，S3（plan §7 明列**不可裁**、PLAN §3.5 点名的执行超限签名）在真实运行中大概率失配——而验收用例 14-16 全用合成 FAIL 文本，测不出此偏差，最终与 PLAN §3.5"规则签名命中即定类"的承诺背离。同类：S12（`[ERROR] <agent> LLM error:`，源码 `simple_hercules.py:917`）语料仅 FAIL∪SYSOUT，规范落点同样是 THOUGHTS 回灌。
- **改法**：spec §3.2 加一句钉死：S3 主复合匹配按 §3.1 默认顺序扫 FAIL→SYSOUT→THOUGHTS（同一字段内两子串同时命中方算命中，真实串中两子串相邻，天然同字段）；次级复扫仍仅对 THOUGHTS、按 (a) S8 → (b) S5∪S6∪S7 → (c) 兜底 agent_limit 的既定顺序（该次级逻辑本身闭环，无问题）。S12 语料并入 THOUGHTS，或注明"FAIL∪SYSOUT 仅覆盖 planner 复述形态，THOUGHTS-only 时经 fallback 走 LLM 层属设计内"。并在 §3.1 加一行数据流注记：引擎错误串经 HumanMessage 回灌 planner 会话后主要落在 THOUGHTS（`simple_hercules.py:805`）。

### M2 §4.2-2 轮次锚截图文件名"随该轮 excerpt 附文件名"与 §4.4-3 回查子串校验自相冲突

- **问题**：thoughts 类 EvidenceItem 的 excerpt 追加截图文件名后，不再是 `thoughts:<index>` referent（该轮 content_text）的子串；按 §4.4-3 该条必判 `excerpt_mismatch` 被 drop。
- **后果**：凡带轮次锚的轮次，LLM 对其的合法引用会被回查器系统性误杀 → 存活引用不足 → 走 `insufficient_evidence` 降级 inconclusive。恰好在最有归因价值的场景（not found 命中轮 + 对应截图）把本可定类的结论压成 inconclusive，违背 §2.4 自己的声明"锚只用于展示与报告附录，不改变任何逻辑"，也削弱"可信归类"核心目标。
- **依据**：§4.2-2"每轮一条 EvidenceItem（reference=`thoughts:<index>`，excerpt=content_text 截 400）；有轮次锚的截图随该轮 excerpt 附文件名" × §4.4-3"校验 norm(excerpt) 是 norm(referent) 的子串"（referent=该轮 content_text）。追加装饰文本必然破坏子串关系。
- **改法（三选一，推荐 b）**：(a) 锚截图文件名只进 LLM prompt 渲染层，不写入 excerpt 字段；(b) 带锚且非末状态对的截图单独打包为 kind=screenshot 的 EvidenceItem（回查走"存在即通过"分支，§4.2-3 同机制）；(c) §4.4-3 对 thoughts 类只校验追加前的前 400 字符。任选其一并在 §4.2-2/§4.4-3 同步措辞。

## 2. 签名与事实抽查核对表（源码逐条打开验证）

| 项 | spec/plan 声明 | 源码实况 | 判定 |
|---|---|---|---|
| S1 | ci 子串 `max planner rounds exceeded` | `simple_hercules.py:409` `"Max planner rounds exceeded."`；assert_summary :412-415 同含 | 一致 |
| S2 | `llm call timed out after` / `planner receives a timely model response` | :320 / :357-359，逐字一致 | 一致 |
| S3 | `max nav rounds (` 且 `reached before ##TERMINATE TASK##` | :1004-1007，两子串相邻同串 | 一致（主扫描范围见 M1） |
| S4 | `maximum context length` 等四模式 | 为通用 LLM 报错文本，litellm/openai/anthropic 固定串，合理 | 可实现 |
| S5/S6 | `net::ERR_[A-Z_]+`；`browser has been closed` 等 | Chromium/Playwright 固定串；用例 19 的 `Target page, context or browser has been closed` 由 S6 第二分支命中（第一分支 `Target (page, context or browser )?closed` 不匹配该形态，无害） | 一致 |
| S7/S13 | ci 子串 `[tool error]` / `[mcp tool error]` | `simple_hercules.py:682,697` `[TOOL ERROR] {tool_name}: ...`；`utils/mcp_help.py:113` `[MCP TOOL ERROR] {server}.{tool}: ...`，ci 均覆盖 | 一致 |
| S8 | `Element with selector ... not found` / `since the selector is invalid` | `click_using_selector.py:157,159` / `:306`，逐字一致 | 一致 |
| S11 | `##TERMINATE TASK##`+uncertain/incomplete/contradict | :110-124 failure_markers 的子集；其余标记（issue encountered 等）未入表，未命中走 LLM 层，设计内 | 一致 |
| S12 | `\[error\] [a-z_ ]+ llm error:` | :917 `[ERROR] {agent_name} LLM error: {e}`，agent_name 形如 browser_nav_agent 可匹配（语料范围见 M1） | 一致 |
| S10 前置 | S1/S2 assert_summary 含 EXPECTED/ACTUAL，须先于 S10 | :357-359、:412-415 确含两词；表序 S1/S2 < S10，载荷性成立 | 一致 |
| JUnit 属性键 | 9 键含 typo "netwrok"、前缀匹配 | `junit_helper.py:111-124` 逐字一致（:117 typo 属实） | 一致 |
| failure message 规则 | is_assert∧¬is_passed→assert_summary；否则 terminate=="no"→assert_summary or final_response | `junit_helper.py:88-106` 一致 | 一致 |
| suite 级属性 | total_execution_cost / total_token_used | `junit_helper.py:172-173` | 一致 |
| inner_thoughts | 顶层键规范值 `planner_agent`；content 经 json.loads，dict/多行字符串双形态 | `runner.py:38, 93-96`；`runner.py:109-123` | 一致 |
| 截图命名 | `{function_name}_start/_end_{ns}.png`；`latest_screenshot.png` 为不合形态例 | `playwright_manager.py:1097-1100`（`_{int(time.time_ns())}` + `.png`）；调用点 `click_using_selector.py:96,126` 等；`playwright_manager.py:1124` latest_screenshot | 一致（plan 写 1095-1098，实为 1097-1100，事实无误） |
| EXPECTED/ACTUAL 格式 | `high_level_planner_agent.py:183` | :183 `"EXPECTED RESULT: x\nACTUAL RESULT: y"`；引擎自拼变体 `EXPECTED:/ACTUAL:` 在 :413-414 | 一致 |
| LLM 接线 | `get_litellm_chat_model("attributor_analyze")` 与蒸馏器同机制；缺键抛错 | `litellm_helper.py:43`；`record2gherkin/distiller/polisher.py:97-99`（distiller_polish 同法）；`agents_llm_config_manager.py:291-306` 缺键抛 ValueError，可捕获转 AttributionError | 一致 |

## 3. 交叉核对：phase1-merge-analysis P0/P1 失败形态覆盖

- **P0-1** submit 双步骤（click 落在跳转后页面，表单不存在）→ not found 形态，S8（test_rot 0.70 + 替代假设）及 S3 分支 (a) 覆盖；spec 用例 39 即该形态合成。覆盖。
- **P1-3** 静默点错（点错照样 success，最终 EXPECTED/ACTUAL 不匹配）→ S10 路由 needs_llm 三假设，用例 40 覆盖。覆盖。
- **P1-1** svg 垃圾步骤重放失败 → not found，S8 覆盖。覆盖。
- **P1-2** checkbox fill 报错（Playwright "cannot be filled" 类）→ 无签名命中，走 fallback→LLM 层。属设计内行为（未命中走 LLM 层），且 plan §6 已列为评测期补签名项，不拦。

## 4. 审查清单逐项结论

1. **签名可实现性与准确性**：13 条签名匹配串与源码固定文本逐条核实一致（§2 表）；S3 次级扫描三分支逻辑闭环（顺序复扫 + 兜底），但主扫描语料范围二义 → M1。
2. **"绝不瞎编"闭合**：定位符语法（§6.3）全部机械可解析（thoughts:index / feature:line / property:name / screenshot:filename）；四条降级路径（analyzer 缺配 / 异常×2 / 校验失败×2 / 回查后存活引用不足）完备且产物明确；非 inconclusive 强制 ≥1 条文本类存活引用；降级保留规则层种子证据。唯一漏洞是 M2 的误杀方向。
3. **证据加载器**：as-is → `run_dir/**/<filename>` glob 两段解析 + missing warning + 仅 JUnit 崩溃抛错，容错足以覆盖真实产物层级（`proofs/<stake>/<ts>/`、`log_files/<stake>/<ts>/`，`config.py:1064-1066, 1084-1089`）；截图 v1 对齐（形态正则 + 排序键 + 末状态对 + 字面锚）可机械落地，`latest_screenshot` 例外真实存在。
4. **可测试性**：42 条用例全离线成立——fixtures 为合成产物目录（手写最小 JUnit 树、双形态 thoughts、0 字节 png），LLM 一律 FakeAnalyzer 注入，测试清单不构造 DefaultAttributorAnalyzer、无网络路径；用例 27 的 grep 不变量、用例 41 的逐字节确定性均可机械执行。
5. **spec 完备性**：除 M1/M2 外无需要实现者拍板的设计决策；两处小歧义见 §5 建议项。

## 5. 建议不阻塞项（3 条）

1. **§2.1 junitparser 理由句失实**：`junitparser>=3.2.0,<4` 是已声明依赖（`pyproject.toml:30`，`junit_helper.py:7` 在用）且本身可解析 JUnit。"venv 无 junit-xml 解析库可用性保证"不成立；选标准库 ElementTree 仍可（更简单、零耦合），但建议把理由改为"避免与写入端库耦合/减依赖面"。
2. **rule_signature 取值未钉死**：§3.2 称"命中 id"（如 `planner_max_rounds`），§6.1 JSON 注释写 "S1…S13 id"。建议明确取 §3.2 id 列英文名（更可读且与 RuleOutcome 定义一致）。
3. **decided_by=degraded 时 `notes.alternative_hypotheses` 取值未规定**：建议一句钉死"降级时保留规则层 alt（若有，如 S3 分支 (a)/(b)、S7），否则空列表"。
