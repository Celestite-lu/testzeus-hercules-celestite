# R3 Spec 独立审查报告（review-r3）

- 日期：2026-09-22。审查对象：`plan-r3.md` + `spec-r3.md`；法定输入：`analysis-r2-failures.md`、`security-review-r2-post.md`（§9）。
- 方法：逐条对照仓库源码（行号为当前工作树实读），只拦"跑不通 / 数字不可信 / 作弊面 / 预算爆炸 / 安全债"。
- **结论：REVISE（必改 3 项，均为文档/一处补丁级修改，不动 R3 六项立项结构与预算框架）。**

---

## 一、必改项

### M1（跑不通级）R3-1 的 spec 补丁是死代码，plan 立论"r2 无上界"不成立

**立论核实（不成立）**：plan R3-1 称"`LLM_MODEL_MAX_TOKENS=4096` 默认值在 benchmark 路径从未被消费……补全长度无上界"。源码实查否定该结论：

- `get_adapted_llm_params()` 确实仅 `mcp_server.py:102` 使用（plan 此半句对），但 `adapt_llm_params_for_model` 在 benchmark 必经路径被**直接调用两处**：`core/simple_hercules.py:152-160`（`SimpleHercules.create()` 对 planner/nav/helper 三个 config 逐一 adapt；`core/runner.py:71` 证实 benchmark 走 `SimpleHercules.create`）和 `utils/llm_helper.py:83`（`create_chat_model` 内部再 adapt 一次）。
- GLM 模型名落入 `model_utils.py:59-70` 的 "other models" 分支：`if "max_tokens" not in params: params["max_tokens"] = 4096`。即 **r2 的 nav/planner 补全存在 4096 硬上限**（实测肥尾 2000–3400 token 与该上限相容）。实查 `dev_runs/benchmark/miniwob-r2/agents_llm_config.json`：三个 agent 的 `llm_config_params` 均只有 `{temperature, cache_seed}`、无 max_tokens——但代码链在运行时注入 4096。
- **R3-1 的真实语义是"把 4096 收紧到 768"，不是"从无上界到 768"**。这不推翻立项价值（肥尾仍在 768 之上），但 plan 的"为什么 r2 没生效"整段证据链必须改写，否则 A 杠杆的归因叙事从根上失真。

**补丁核实（死代码）**：spec §1.1 的 `if kwargs.get("max_tokens") is None:` 在 benchmark 路径**恒为 False**——`create_chat_model` 先执行 `**adapted`（adapted 恒含 `max_tokens=4096`）再进兜底段，`kwargs["max_tokens"]` 永不为 None。按此实现，768 上限**永远不会生效**，M2 headline 会在 A 杠杆静默失效下跑完，且 T1 若按 spec 原文断言"`max_tokens` 为 None"要么测试失败、要么（换 gpt-5 模型名）测试绿而生产死。

**必改内容**：
1. spec §1.1 补丁改为 env>0 时**无条件覆盖**：`nav_cap = get_nav_max_completion_tokens(); if nav_cap > 0: kwargs["max_tokens"] = nav_cap`（或 `min` 语义，二选一写死）。
2. T1 断言改为：env 未设/`"0"` → `max_tokens == 4096`（r2 复现的真实值）；env=768 → `max_tokens == 768`；显式 `llm_config_params={"max_tokens": 256}` → 由第 1 点选定的优先级决定并加断言。
3. plan R3-1 证据段改写为"4096 → 768 收紧"；预期救回数字（T-B 9/3）不受影响（其依据是 >768 的实测尾部，与 4096 上限并存不矛盾）。
4. spec §1.1 消费面声明修正："helper 多模态单例 benchmark 不触发"不准——`_initialize_agents`（simple_hercules.py:190-193）每次 benchmark 都**构造**该单例（经 `create_chat_model`），helper 也会吃 768 cap（无害，但须如实披露）。

### M2（数字不可信级）R3-2 只改 wait_for 不改 provider 层 timeout，"150s 内完成即活"兑现不了

源码实查：planner 的 ChatOpenAI 自身 `timeout` kwarg 兜底为 `get_llm_request_timeout_seconds()` = 90s（`high_level_planner_agent.py:49-51`，`safe_llm_params["timeout"]`；headline 的 `LATENCY_ENV_OVERRIDES` 注入 `LLM_REQUEST_TIMEOUT=90`，orchestrator.py:119）。provider 层在 90s 掐断单次 HTTP 请求并触发 `max_retries=2` 的 SDK 内部重试。因此：

- spec §2.2 只把 `_llm_ainvoke` 的 wait_for 提到 150s，**>90s 的单次 planner 生成依然永远无法完成**（90s 被 client 掐断→重试→重试须在 90–150s 残余窗口内恰好跑完才活）。N 格（click-shades / drag-shapes-2，确定性慢生成）的救回机制与 spec 声称的"150s 内完成即活"不符，R3-2 的 +2~3 预期存在系统性高估风险。
- plan R3-2 机制诊断自相矛盾：#1 称"provider 重试永远来不及发生"，#3 又引"实测单次 planner 停顿 95–168s"——在 wait_for=90 下 >90s 的"单次停顿"只能是"90s 掐断 + provider 重试链"的间隔测量，恰恰证明 provider 级 timeout/重试在起作用。诊断段须按真实机制改写。

**必改内容**：spec §2 增一行必要改动——`PlannerAgent.__init__` 的 timeout 兜底改用 `get_llm_planner_request_timeout_seconds()`（env 未设时两 helper 同值返回，"off = r2 逐字节一致"仍成立）；T2 增加对 planner 产物 `timeout kwarg` 的断言（150 注入后 == 150）。plan 诊断 #1/#3 段同步改写。

### M3（数字不可信级）plan §6 保守档构成与总数不 reconcile

表称构成 "58 + A/B +5~12（T-B 9/3、N+O-B +2~3）+ C +1~3 + F +1~4"：保守档 58+5+1+1 = **65/125 = 52.0%**，表写 **63/125 = 50.4%**（上界 58+12+3+4=77 ✓ 无矛盾）。−2 格无任何注释解释。核心主张"贴线达标 50.4%"与 60–62 fallback 复跑政策都悬在这个数上，构成列必须能被读者复算。

**必改内容**：补记口径（例如"T-B 保守 3 中 2 格需 C 联测、已移入 C 计"）或改数字为 65/125=52.0%；D 臂两行（69/93）随 headline 基数联动核对。

---

## 二、源码核实表

| # | 审查项 | 结论 | 证据（当前工作树实读） |
|---|---|---|---|
| 1 | R3-1 立论"r2 max_tokens 从未生效" | **不成立**（见 M1） | simple_hercules.py:152-160；runner.py:71；llm_helper.py:83、89-102；model_utils.py:59-70（other→4096 兜底）；dev_runs/benchmark/miniwob-r2/agents_llm_config.json 实查（文件无 max_tokens，运行时注入 4096） |
| 2 | R3-1 注入点选 `create_chat_model` 兜底段 | 位置对、条件死（见 M1） | 该函数确为全部 nav agent 构造点：base_nav_agent.py:66、multimodal_base_nav_agent.py:61；mcp_server.py:102 非 benchmark 面 |
| 3 | "planner 走裸 ChatOpenAI、不经 create_chat_model" | 成立（但 planner 也带 4096 cap） | high_level_planner_agent.py:38-57；其 `llm_config_params` 已被 simple_hercules.py:160 注入 max_tokens=4096 |
| 4 | R3-2 "90s 硬墙"现场 | 成立 | simple_hercules.py:280-285 `asyncio.wait_for(…, timeout=get_llm_request_timeout_seconds())`；planner 节点传 `agent_name="planner_agent"`（L393-397）→ spec 的分档选择器可行；`_planner_timeout_result` L311-354 立即 terminate=yes ✓；executor 超时重写路径 L831/839 存在 ✓ |
| 5 | `LLM_PLANNER_REQUEST_TIMEOUT` 需 engine 改动、默认 off=r2 | 成立但欠一处（见 M2） | 新 helper 直读 env 合规（先例：`get_llm_request_timeout_seconds` 即 os.getenv 直读，llm_helper.py:47-52，不入 relevant_keys）✓；遗漏 PlannerAgent 的 provider timeout |
| 6 | R3-3 缺陷现场与修法匹配 | 成立 | drag_and_drop_tool.py:29-31 `if "md=" not in selector: f"[md='{selector}']"`（含子串误判）逐字确认；target 多策略 L54-97、鼠标序列 L117-137 与 spec"零改动"范围吻合；spec 片段对空串/`(`开头等形态处理自洽 |
| 7 | E1 落点（extra_tools 子集） | 可行 | extra_tools/__init__.py:11-22 现有 LOAD_EXTRA_TOOLS 门控，白名单过滤插在其内可行；默认 `LOAD_EXTRA_TOOLS="false"`（config.py:668）→ 默认不加载 = r2 ✓；模块清单 7 个，subset 剔除 file_handler/clipboard ✓（R4 风险指向 read_clipboard 属 clipboard_tools.py ✓） |
| 8 | E1 "干净分母 115→125"机制 | 成立 | file 工具不可加载 → W2 类 invalid 结构性消失；E4 `clean` 口径把 invalid 格移出分子分母（metrics.py `_rate` L203 / Summary L109-133，`overall` 不动的约束可实现）；残留：E5 下沙箱**尝试**仍 invalid（spec 已有"invalid≈0、否则双列"的兜底表述，自洽） |
| 9 | E3 落点（scheme 白名单） | 可行（注意边缘，见 S2） | open_url.py：special 块 L42-48、`ensure_protocol` 调用 L93、https 前缀 L207-241 与安全复审引用一致；插入点正确；`urlsplit` 已 import（L5），`add_event/EventData/EventType` **未** import（spec 片段需补）；`ensure_protocol` 对 data:/file: 的"原样放行"证实白名单前移的必要性 |
| 10 | E5 落点 | 可行 | execute_python_sandbox.py 入口（L66 起 tenant 读取）之前插入检查可行；`SANDBOX_CALL_MARKERS`（orchestrator.py:371）追加 `[SANDBOX_DISABLED]` 后"尝试即 invalid"契约保持 ✓ |
| 11 | E2/E4/E6 落点 | 可行 | file_handler_tool.py:24/81/121 三函数 ✓；orchestrator GoalReadError 分支 L982-1003、`runs_dir` L776、`cell.run_id` L153-154 ✓；invalid 契约（CellScan/INVALID_REASON_*，orchestrator.py:346-373）与 spec 引用语义一致 |
| 12 | 开关矩阵自洽性 | 自洽（一处措辞冲突，见 S3） | 新五 flag 默认 off → env 不注入 → engine 不变（`_child_extra_env` L899-908 现状确认）；`LATENCY_ENV_OVERRIDES` 5 键（L118-124）"原值不动"可执行 ✓；E2/E4/E6 确为判分中立披露增强；**E3 是 engine 行为变更且无 flag、对 flags-off 运行也生效**——与 plan 开头"逐字节复现"字面冲突（plan §2 与 spec §5 前言的"唯一例外"表述为准） |
| 13 | 与 r2 现有 flags 兼容 | 成立 | r2 七 CLI 开关原样存在（orchestrator.py:1266-1280）；r3 增量全部独立参数；`R2_BUDGET_CAP=144`（L82）沿用 |
| 14 | 预算数学 | 正确 | pilot 10+2 + smoke 2 = 14；headline 125+5 = 130；14+130 = 144 = cap ✓（`hercules_budget`/`_assert_can_run` L821/974 现状可容纳）；D 臂 135 ≤ 144 独立 exp 另批 ✓；合计 279 = 144+135 ✓；D 臂零代码成立（`--episode-ms/--timeout-s` L1263-1264、默认 240000/600（tasks.py:21、L78）、`episode_max_time_ms` 已入 URL 与结果行 L312/L956/L981） |
| 15 | T1–T12 离线可验证 | 基本成立（T1 随 M1 修，stub 深度见 S3） | T2（`__new__` 绕过 init + stub llm）、T3（subprocess 隔离 config 单例）、T5/T6/T9（monkeypatch）、T7/T8（纯函数）、T10（env 注入）、T11（stub `get_user_ltm`，注意 PlannerAgent 现有 `print` 不影响）、T12（`--dry-run` L741/824 已存在，跳过 C1c 预检）全部不需要真 LLM/真网 ✓ |

## 三、法定输入转译核对（无夸大）

- A←分析 §2.2：10.3% turn 吃 38% 墙钟、>800 token n=201 中位 46.2s/p90 80s、0–150 token 2274/3197——逐项与原文一致 ✓。
- B←25/41 planner 超时、N 两格 90.0s/0 executor 轮、O-B 两格（click-scroll-list/stock-market）——与 §1/§4 一致 ✓；Top-10 #1 ~0.8、#7 ~0.45 的救回概率转译忠实 ✓。
- C←§3.1：61 调 0 中、L29-31 主因、拖拽机制可行、"透传受益 3–5 格且需与 L1 联测"→plan"headline 保守只兑现 1–3"**未夸大**（方向为折减）✓。
- D←§2.4：上界 16/保守 6、边界①引擎上限同步抬高——原样转译 ✓；"ablation 不进 headline"的口径纪律与 §6 建议一致 ✓。
- F←§4：15 格完成幻觉、§6 L4 9/4——一致 ✓；plan 自评"证据中等、不承诺"属如实降级 ✓。
- 安全复审 §9 → E1–E6 映射逐条准确（P0 子集+日志+invalid 入 metrics、P1 白名单、P1 沙箱、P2 早崩留痕、P2 维持项）；click 家族盲区"拒绝立项、降级为报告复盘段"符合 round2 §4-5 的实际要求 ✓。
- 唯一数字问题 = M3（保守档 −2 不 reconcile），其余区间相对法定输入**未发现夸大**。

## 四、不阻塞建议（≤3 条）

- **S1** kill-switch 判定信号落成具体 grep：现码"进入鼠标序列"无专属日志行；可机械判定的代理信号是 drag_and_drop_tool.py:88 `Found target element using selector: …`（源+目标均已解析）或 L142 成功文案。spec §3.3 直接写明日志 pattern，避免执行时现场裁量。
- **S2** scheme 白名单的 `localhost:PORT` 边缘：`urlsplit("localhost:5000/x").scheme == "localhost"`（字母开头被解析为 scheme）→ r3 将拒绝，而 r2 现状经 https 前缀可正常导航；T5(c) 只覆盖数字开头的 `127.0.0.1`。要么在 spec 明示接受该行为变更并补一条 T 用例/披露，要么放行该形态；同时补记 open_url.py 需新增 `add_event/EventType/EventData` import。
- **S3** 统一两处口径 + 补全 stub 清单：① plan 开头"默认态 = r2 行为逐字节复现"改为与 plan §2/spec §5 前言一致的"判分行为复现（E2/E3/E4/E6 披露与安全增强除外，其中 E3 对 flags-off 运行也是 engine 行为变更）"；② T5(c)/T6 的成功路径还需 stub `browser_logger.log_browser_interaction` 与 `take_screenshots`（open_url L93 之后必然调用），spec 只写了 stub page，实现者会踩坑。

## 五、结论

**REVISE。** 三项必改中 M1 属"按 spec 实现 R3-1 必然静默失效"的跑不通级缺陷，M2 属"救回机制与代码现实不符"的数字不可信级缺陷，M3 属核心主张表不可复算；三者均为局部修改，R3 的六项立项结构、预算框架（14+130=144 / ablation 135 另计）、安全必办包与披露纪律经核实全部成立，改完即可 PASS 复审。
