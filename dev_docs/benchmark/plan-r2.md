# R2 改进计划 — miniwob-r2（规划稿，只写文档不改代码）

> 输入：`analysis-r1-failures.md`（桶量化与可恢复矩阵）、`analysis-r1-architecture.md`（H1–H9）、`round1-report.md`、`security-review-r1-pre/post.md`、`spec.md`（r1 现行规格）。源码事实均已逐处核对（见各条"实现位置"）。
> 目标（最高原则）：r2 官方口径（last-wins / 125）进入可比带 A 档中部（**50%+**），且每个数字诚实可披露。不为分数牺牲口径；不做无证据优化；视觉盲区如实声明。
> 预算硬约束：**key 余额已不足（r1 末期 402），全量运行被阻塞**——离线开发全部先行，"全量运行"是独立的最后一步，且运行前必须通过 1-token 余额预检（C1c）。
> 本文与 `spec-r2.md` 配套：本文是"做什么/为什么/预期"，spec-r2 是"改哪个文件哪个函数/行为定义/单测清单/验收"。

---

## 1. 采纳清单（C1–C7，每项：r1 证据 → 预期影响 → 实现位置 → 开关）

### C1 判分中立包（采纳，零争议，常开无开关）

**C1a 判分修复：页面奖励优先于引擎 infra 状态**（= 架构文档 H1）
- r1 证据：A 桶 5 格页面 raw>0 已到手却被判负——login-user-popup@27s、multi-layouts@83s、use-colorwheel-2@179s（raw=0.537）被 600s 引擎超时覆盖；click-pie@121s、click-collapsible-2-nodelay@188s 被重试行 402 崩溃 no_junit 覆盖（failures §1/§4-L1）。反例 email-inbox-delete（raw=1→重导航→−1）必须**仍判负**，作为修复正确性的对照锚。
- 预期影响：**+5 格 = +4.0pp（锁定值，非估计）**，43.2%→47.2%。
- 实现位置：`record2gherkin/benchmark/orchestrator.py::build_result_row`（L199-209）优先级重排 + 结果行新增 `runner_status` 字段（可审计）。`fetch_reward` 仍取 `/latest`（last-wins 语义不变，不新增洗白通道）。
- 验证：用 r1 数据固化的最小 fixture 回放——5 格翻正、email-inbox-delete 维持 failed、官方合计 54→59（单测锁定，见 spec-r2 §5-T1）。
- 开关：无（这是把 spec §0 口径 4"页面奖励为唯一权威"执行对，属正确性修复，保留旧行为无价值）。

**C1b 402/网络错误熔断器**（= H9 前半）
- r1 证据：3 行重试 4-5s 秒崩 no_junit 均因 402 Insufficient Balance，浪费重试预算且污染状态分母（post §7.2）。
- 预期影响：0pp（防污染与防烧 key）。
- 实现位置：`orchestrator.py`——attempt 级：`runner_status ∈ {no_junit, timeout}` 且 `duration_s < 30` 且 stdout 含 402/Insufficient Balance/连接错误标记 → 该 cell 不进重试池，结果行加 `infra_circuit_break` 字段；run 级：连续 2 格熔断 → 中止 stage（exit 2）。
- 开关：无（运维保护，常开）。

**C1c 运行前余额预检（必须实现，运行 gate）**
- 证据：key 已余额不足；r2 全量前无预检则第一格就烧超时。
- 行为：orchestrator 起服务前做一次 `max_tokens=1` 探测调用（litellm，复用 runner 的 model/base_url/key 常量），任何 402/余额/连接异常 → **exit 3，不启动任何执行**；探测文本经 `mask_secret` 纪律。`--dry-run` 不探测。
- 实现位置：新增 `record2gherkin/benchmark/preflight.py` + `orchestrator.main()` 接线。
- 开关：无（dry-run 天然豁免；不提供跳过开关）。

**C1d per-attempt 分目录日志**（= H9 后半，post §7.1 建议）
- 证据：5 个重试格 attempt1 的 stdout.log 被 attempt2 覆盖，盘上不可复核（post §4 附注）。
- 实现位置：`record2gherkin/evaluation/runner.py::run_feature` 增加可选 `stdout_log_path`（默认原路径，向后兼容）；orchestrator 传 `runs/<run_id>/attempt<N>/stdout.log`；结果行加 `attempt` 字段；`scan_cell_log` 扫本 attempt 文件。
- 开关：无。

### C2 终局信号回供（采纳，环境反馈通道差异，须披露）

- 单根因：**引擎不读页面终态**，同时解释 A 桶判分瑕疵与近半空转——23 格实质 600s 击杀前页面已终局、之后平均空转 273s（合计 ≈1.75h）；multi-layouts 页面通过后 46/64 轮、use-colorwheel-2 56/65 轮在空转（failures §2.3、architecture §0）。
- 方案：补丁层在 `WOB_DONE_GLOBAL` 翻转后向页面注入**中性**终局标记 `EPISODE ENDED`（固定文本，不含任何 reward/成败数值），agent 读到即知"本局已结束，应停止并报告"。判分仍 100% 按页面奖励，标记不改任何成绩——它只把 r1 的"全盲空转"变成"知情终止"。
- 可比性权衡（如实陈述）：原版 HUD 显示奖励数值（信息最多、但属 V1 泄漏已封）；r1 全盲（我们自己的安全加固所致，agent 找一个不存在的成功指示必然空转）；r2 中性标记介于两者——比 r1 好（有终止信号）、比原版裸露少（无数值）。
- 预期影响：直接救分有限（A 桶已由 C1a 计回），主收益是 zombie 时间回收（23 格 × 均值 273s）→ 更短 wall 时长 + 把残余时间转化为少数格的完成。估计 **+0~3pp**，另 wall 时长降 15-25%。
- 实现位置：`record2gherkin/benchmark/miniwob_server.py` 追加第三段补丁（`--terminal-cue` 启动参数门控，伺服器级 flag——URL 与 seed 语义完全不变）；与 C7 模板注释联动。
- 开关：`--terminal-cue`（默认 off；headline 显式开）。

### C3 单次开局补丁（采纳，P2 可选项 B 转正，环境硬化，须披露）

- r1 证据：补丁 A 对每次同 URL load 都自动开局 → 重导航 = 重置 240s 页钟 + 丢弃失败提交，成本近零。email-inbox-forward 16 次导航把页钟续到 **+584s**，email-inbox/-delete 553/581s（failures §2.2）；61/125 格 nav≥2；email-inbox-delete 首实例 raw=1 被 8 次重导航拖死。
- 方案：`sessionStorage["r2g_started"]` 限制每 tab 只自动开局一次；二次导航页面停在未开局状态（该次加载不产生新 reward 记录）。r1 实证 93% 重导航走同 tab（249/267），恰好被覆盖；force_new_tab 18 次是残余缺口，计数披露。
- 语义影响（如实陈述）：封死后官方 last-wins 退化为"每 tab 唯一一次开局的终局奖励"——重导航不再重置计时、也不再产生覆盖记录；r1 中被重导航毁掉的已到手成绩（email-inbox-delete 型）在 r2 会保留。这不是制造成功，而是移除"重开续命"通道；报告披露该语义。r1 格内洗白为 0，故封死通道对 r1 成绩回放无影响。
- 预期影响：直接 +0.8pp（email-inbox-delete 类）+ 大量时间税回收（间接）；r1 口径回放影响 ≈0。
- 实现位置：`miniwob_server.py` 补丁 A 增补两行 JS（`--single-start` 启动参数门控）；read_goal 预读用独立 browser context，sessionStorage 不跨 context，预读不受影响（单测锁定）。
- 开关：`--single-start`（默认 off；headline 显式开）。

### C4 延迟包（采纳，引擎侧，允许改 testzeus_hercules，独立 commit + 逐点开关）

r1 证据链：被杀格 35-101 executor 轮 vs 通过格 18.2 轮；轮间延迟均值 9.0s；93% 时间在 executor 逐轮 LLM；强制重感知断裂超时格平均 25.1 次（每断裂把 1 轮变 ≥2 轮）；completion p90 1998 tokens；单步无时间预算（一个 planner 步可烧 5-8 分钟）（architecture §0、failures §2.1）。

**C4a 状态刷新守卫克制化**（最大杠杆）
- 精确改法（已核对 `simple_hercules.py:548-570, 662-675, 940-991`）：现状 = 批内任一"成功状态变更工具"（`_BROWSER_STATE_CHANGING_TOOLS` 12 个，含 hover）即 break 跳过剩余 tool calls 并注入"Re-read the current page/DOM"强制下一轮重感知。改为模式化：`BROWSER_STATE_REFRESH_MODE`（config 注册新键，经 `get_global_conf()`，默认 `always` 保持 r1 行为）；
  - `always`：现状不变；
  - `markers_only`（headline 取值）：仅当工具结果命中 `_STATE_REFRESH_MARKERS`（工具自己声明"new elements have appeared"等）才打断；纯成功状态变更不打断——批内工具全部执行完，模型一轮看全结果；错误结果本就不打断，行为不变。
- 预期：超时格减 10-30+ 断裂轮/格；每断裂省 1 整轮（均值 9s）。风险：stale md 留给下一轮报错自愈，错误恢复轮可能增加——靠 ablation 观测（§3-A3）。

**C4b 步级预算收紧**
- 精确改法：`core/runner.py::BaseRunner.__init__` 的 `browser_nav_max_chat_round=50`（纯轮数无时间维度）经 config 键 `BROWSER_NAV_MAX_CHAT_ROUND` 可覆写（headline 设 30）；`simple_hercules.py::_run_nav_agent` 轮循环（L910）顶部加 wall 预算检查 `NAV_STEP_TIME_BUDGET_S`（默认 0=off，headline 设 120）：到限带当前进展返回 planner（附 `[NAV_STEP_BUDGET_EXHAUSTED]` 可辨识标记），由 planner 决定继续/换路/终止。
- 配套：必须与 C4d（禁止重导航）同 commit——无此约束时 planner 会用"重派"消化被打断的步。

**C4c 输出克制（prompt 包）**：nav/planner system prompt 各加一句"响应简短：只列动作与结果，禁止长推理"（completion p90 1998 tokens 直拉每轮延迟）。

**C4d 撤销"刷新重试"教学（prompt 包，= H2 方案 A）**
- 已核对原文：`browser_nav_agent.py` L20 *"To refresh a page, open the same URL again…"*、L116 *"When a page refresh is needed, navigate to the current URL again…"*——引擎 prompt 在主动教重导航。删除两处，替换为："不要重开任务 URL——重开会重启任务并丢弃全部进度；卡住时改用 get_interactive_elements / get_page_text 重新审视，或如实报告受阻"。planner 侧同步一句"不要将重开 URL 作为重试手段"，并在 closure 规则中删"验证页面成功提示"类诱导（见 C4e）。

**C4e planner prompt 瘦身 + closure 规则修正（prompt 包）**
- 已核对：`high_level_planner_agent.py` Closure Nudge Examples（L198-204）与 Critical Rules #5 "Final step must always include an assertion"（L300）诱导独立验证步——HUD 被加固隐藏后验证步注定空转。改为"helper 回报动作+submit 完成即终止；单动作任务不追加独立验证步；本环境无页面成功提示"。系统提示瘦身（现 322 行：删 Salesforce/SAP Platform Awareness、Test Data Iteration 等 MiniWoB 无关节）。
- 预期：planner 首轮 token 与"数据驱动多轮迭代"式过度计划双降。

**C4f LLM 请求韧性（env 级，零代码）**
- 证据：60s helper 双超时级联直接杀死 7 格（ascending-numbers、click-menu、daily-calendar、drag-circle、find-greatest、order-food、use-spinner），71 败格中 32 格 planner 步含 "timed out, retry" 改写（failures §2.4）。已核对默认值：`LLM_REQUEST_TIMEOUT=60`、`LLM_MAX_RETRIES=1`（`utils/llm_helper.py:21-22`）。
- 改法：`build_child_env` 增注 `LLM_REQUEST_TIMEOUT=90`、`LLM_MAX_RETRIES=2`——延迟尖峰由请求级快速重试吸收，不再升级为 planner 层重试/重导航。

- 开关：`BROWSER_STATE_REFRESH_MODE`（engine env）、`BROWSER_NAV_MAX_CHAT_ROUND`、`NAV_STEP_TIME_BUDGET_S`（engine env）；C4c-e 为 prompt 包整体（独立 commit，逐 commit 可回退）；C4f 为 orchestrator env 注入 flag `--latency-env`。预期合计 **+2~6pp**（H3 的 +3~8 与 H6 去重后保守化）。

### C5 分角色路由（采纳，pilot 先行，独立成组归因）

- 证据：r1 全角色单模型（`runner.py:41 LLM_MODEL_NAME="deepseek-v4-pro"` 注入全部角色）；executor 轮是延迟主体（均值 9.0s/轮 × 51 轮）；planner 每格仅 3-4 轮（强模型保留对质量影响面小）。
- 已核对的机制事实：
  - 引擎按三角色取配置：`core/runner.py:53-60` 读 `planner_agent`/`nav_agent`/`helper_agent`；`simple_hercules.py:170-199` 中 `nav_agent` 配置驱动**全部** nav/executor agents——路由 `nav_agent` 即覆盖执行循环。
  - 配置文件通道：`AGENTS_LLM_CONFIG_FILE`（`config.py:166`，env 同名映射）+ `AGENTS_LLM_CONFIG_FILE_REF_KEY`；File 优先于 Env。
  - **key 红线处理**：生成的 `agents_llm_config.json` 一律省略 `model_api_key`；`utils/llm_helper.py:85` 的 `create_chat_model` 会回退到环境变量 `MODEL_API_KEY`——故 child env 需增注 `MODEL_API_KEY=<key>`（key 仍只经 subprocess env，绝不落文件）。
- 方案：orchestrator 生成 `<exp_dir>/agents_llm_config.json`（gitignore）：planner=deepseek-v4-pro、helper=deepseek-v4-pro（benchmark 文本 DOM 下 image-comparer helper 不触发，零风险）、nav=deepseek-flash；child env 注入 `AGENTS_LLM_CONFIG_FILE` + `REF_KEY=litellm` + `MODEL_API_KEY`。
- 预期：executor 轮延迟若 2×（9.0→4.5s），超时格省 ≈230s/格；与 C4 叠加但有重叠，合计已并入 C4 的 +2~6pp 区间；成本同步下降。风险：flash 对长 system prompt 顺从性差，可能放大行为问题——**pilot 10 格先行**，flash 模型名在 C1c 预检中一并探测（不可用则中止并报告，不静默回退）。
- 开关：`--role-routing`（默认 off）+ `--nav-model`（默认 `deepseek-flash`）。

### C6 拖拽工具启用（采纳，先冒烟再算数）

- 已核对的机制事实：`core/extra_tools/drag_and_drop_tool.py` 现成（`@tool(name="drag_and_drop", agent_names=["browser_nav_agent"])`，md 选择器 + bounding-box 鼠标轨迹 down→move→up）；加载条件 = env `LOAD_EXTRA_TOOLS != "false"`（`core/extra_tools/__init__.py:11`，config 默认 `"false"`，`config.py:701`）；`simple_hercules.py:31` 无条件 `from ...extra_tools import *`；且 `_BROWSER_STATE_CHANGING_TOOLS` 已含 `"drag_and_drop"`（`simple_hercules.py:556`）——状态刷新守卫天然认识它。
- 证据：拖拽/绘制/选区家族 14 格全军覆没，agent 只能 `press_key_combination` 冒充拖拽（failures §3.1）。
- 改法：`--extra-tools` 时 `build_child_env` 增注 `LOAD_EXTRA_TOOLS=true`。**注意副作用**：该开关全量加载 extra_tools（clipboard/browser_assist/file_handler/geo/pdf/visual_skill）——clipboard 工具对 copy-paste×2 家族是意外潜在收益；H2③ 沙箱调用扫描在位，安全面不扩大（只开闸不加代码）。
- 预期：家族上界 14 格，但 drag-circle/draw-line/drag-cube/drag-cube 属 canvas 盲区不在射程；保守 **+1~3 格（+0.8~2.4pp）**。先 2 格冒烟（drag-items、drag-box）验证工具被调用、不崩、无异常工具面，再计入预期。
- 开关：`--extra-tools`（默认 off）。

### C7 Gherkin 模板上下文（采纳，环境增强，须披露 + 小对照）

- 证据：25 格页面 240s 死亡 + HUD 盲区下的验证空转；措辞陷阱家族（choose-date×3、book-flight×2、use-autocomplete 交互范式）；58/121 格首轮 plan 含显式 "verify the task completed" 步（architecture §0/H4）。
- 方案：`record2gherkin/benchmark/goal_reader.py::render_feature` 追加固定注释段（When 指令原文逐字不动）：① 指令同文显示在页顶 #query（消解重复困惑）；② 单页任务勿重载/离开（进度即失）；③ 页面无成功/失败提示，完成要求动作（含 submit）即报告终止；④（C2 开启时的变体）页面角落出现 `EPISODE ENDED` 即本局结束（无论成败），应立即停止；⑤ 日期/时间类以页面所示格式为准。C2 off 时④不出现（文本按 flags 组装，单测锁定两个变体）。
- 不做任务特定提示（日期示例值、autocomplete 玩法等会泄漏任务语义，违反判分中立精神）。
- 预期：**+1~3pp**（H4 区间 +2~5 与 C2 重叠去重后保守化）。
- 开关：`--template-notes`（默认 off；headline 显式开）。

### 不采纳/不在 r2 做（逐项理由）

| 项 | 理由 |
|---|---|
| 画布几何/视觉感知 11 格 | 射程外：文本 DOM agent 天然盲区，无视觉模型；明确声明，不掩饰（failures §3.1 家族③）。 |
| 480s 页面计时 | 口径变更，不进 headline；若预算允许仅作 ablation 单列（§3-A4），默认不跑。 |
| H8 stop-at-terminal（引擎停在首个终局奖励） | 与 C2 功能重叠（C2 让 agent 自己停）；且它是口径变更（last-wins→first-terminal-wins），披露负担重。C1a 已修判分、C2 已给终止信号后边际收益小。r3 若 zombie 仍严重再议。 |
| 视觉模型接入 / Agent-E 81.6% 追赶 | 同"画布视觉"，差距归因如实备注（round1-report §3）。 |
| planner 模型升级 | r1 数据：planner 非瓶颈（3.2 轮/格、4% 时间），无证据支持（failures §2.1）。 |

---

## 2. 组合策略

- **headline（`miniwob-r2`，全开组合）**：C1（常开）+ C2 + C3 + C4(a–f) + C5 + C6 + C7。全部 flag 在 manifest 记录（新增 `flags` 字段），报告逐项披露。
- **运行顺序（关键路径）**：C1c 预检 → 冒烟 pilot（10+2，验证 C2/C3/C5/C6/C7 全开不崩、拖拽工具被调用、flash 顺从性）→ 人工检查冒烟产物 → **全量 full（125+5，独立最后步骤，用户确认余额已充值的当天执行）**。
- **预期合计**：47.2%（C1a 后基线）+ C2/C3 ≈ +0.5~2 + C4/C5 ≈ +2~6 + C6 ≈ +0.8~2.4 + C7 ≈ +1~3（杠杆间重叠已去重口径）→ **官方口径 50~56%**（与 architecture 文档 §3 主推组合一致）。诚实备注：区间下沿意味着各项转化偏弱仍可能停在 48-50%，届时如实报告不粉饰。

## 3. Ablation 设计（另列预算，用户批准后才跑；默认不跑）

同 exp_id `miniwob-r2`（seed 派生不变，逐格可配对）、`--exp-root` 新目录、固定 10 格 ablation 集（= failures §5 Top-10 高价值格：login-user-popup、multi-layouts、click-pie、click-collapsible-2-nodelay、use-colorwheel-2、book-flight-nodelay、ascending-numbers、choose-date-easy、click-checkboxes、email-inbox-delete——比 PILOT_SUBDOMAINS 更贴近本轮杠杆）。每组 10 次执行、无重试：

| 组 | 配置 | 归因问题 |
|---|---|---|
| A0 | headline 全开（这 10 格的 headline 数据可直接复用，不重跑） | 基准 |
| A1 | 关 C5（单模型 v4-pro） | flash 顺从性代价 |
| A2 | 关 C2+C3（r1 式补丁层） | 环境硬化贡献 |
| A3 | 关 C4（r1 式守卫/轮数/预算，保留其余） | 延迟包贡献 |
| A4（可选） | episode-ms 480000 + timeout-s 840，其余同 headline（=H7 计时敏感性） | 480s 单列，绝不进 headline |

A1–A3 = 30 次执行；A4 = 130 次执行（全量级，默认不做）。

## 4. 预算表（写入 spec 的运行前置条件：超限即硬失败）

| 阶段 | Hercules 执行数 | 说明 |
|---|---:|---|
| 冒烟 pilot（全开 flags） | 10 + 2 重试 = 12 | 含拖拽冒烟 2 格（drag-items/drag-box 在集内则复用，否则 +2） |
| headline full | 125 + 5 重试 = 130 | 全开组合，单次 |
| **r2 主预算合计** | **≤142** | orchestrator `assert_budget` 以 `R2_BUDGET_CAP=142` 硬拦 |
| ablation A1–A3（另批） | 30 | 用户批准后执行 |
| ablation A4（另批，默认不做） | 130 | 用户批准后执行 |
| **r2 全项目上限** | **≤172（不含 A4）/ ≤302（含 A4）** | 逐项列出，逐项批准 |

成本锚点：r1 全量 12.9M token；r2 因步级预算+flash 预计 ≤ r1 × 1.2（A4 除外，×1.3-1.6）。

## 5. 里程碑（离线开发 1 天 + 运行 0.5 天）

| 时段 | 内容 | 出口条件 |
|---|---|---|
| D1 上午 | C1a-d（orchestrator/runner/preflight）+ 回放单测锁 +5、熔断/预检/attempt 日志单测 | `pytest tests/record2gherkin/benchmark -q` 全绿 |
| D1 下午前半 | C2/C3 补丁层 + `--single-start/--terminal-cue` + 浏览器组单测（标记中性可见、二次 load 不开局） | 同上 |
| D1 下午后半 | C4a-b 引擎改动（独立 commit）+ C4c-e prompt 包（独立 commit）+ C4f/C5/C6 env 注入 + C7 模板 + 全部单测；`make fmt` | 全绿 + 两个引擎 commit 各自可回退 |
| D2 运行（0.5 天） | C1c 预检（**前置：用户确认充值**）→ 冒烟 12 → 检查（拖拽调用日志、flash 输出纪律、单次开局生效、无 402）→ headline full 130 → 汇总与 test-report 固化 | 预算 ≤142；报告含 §6 披露清单 |
| D2+（另批） | ablation A1–A3（30）；A4 默认不做 | 批准后执行 |

## 6. 诚实性披露清单（报告口径，每条必须原文出现）

1. **C1a**：判分修复不是口径放宽——是执行 spec §0 口径 4 的既定权威（页面奖励）；附 r1 数据回放 54→59 的单测/脚本证据；新增 `runner_status` 字段使"引擎超时但页面已过"可审计。
2. **C2**：环境反馈通道差异声明——"原版 HUD 显示奖励数值；r1 我们的安全加固使 agent 全盲；r2 注入中性 `EPISODE ENDED` 终局标记（无数值、无成败）。判分完全不受影响。"
3. **C3**：单次开局语义 + force_new_tab 残余缺口计数；重导航扫描（`task_url_navigations`/`flagged`）继续保留并披露。
4. **C4/C5/C6**：能力增强（引擎行为/prompt/模型/工具面），逐项 flag + manifest `flags` 记录 + git_rev 可追溯；无 ablation 批准时明示"逐项不可单独归因"。
5. **C7**：环境增强，模板 diff 全文披露。
6. 保留 r1 全部既有披露：240s 放宽、单 seed 单次、Gherkin 中转、模型非 GPT-4、补丁纯追加、端点无防伪造。
7. 视觉盲区：11 格画布/几何家族如实声明为文本 DOM agent 射程外。

## 7. 风险表

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | **key 余额不足阻塞全量**（r1 末期已 402，最高优先） | 高 | C1c 1-token 预检为运行硬 gate；C1b 熔断防中途烧穿；全量设计为独立最后步骤，充值是用户侧依赖，spec 写明"预检不过不开跑" |
| R2 | deepseek-flash 可用性/顺从性回退（长 prompt 纪律差） | 中 | 预检探测模型名，不可用即中止不静默回退；pilot 10 格先行；ablation A1 归因；`--role-routing` 一键关 |
| R3 | C4a markers_only 后 stale md 错误恢复轮增多 | 中 | `_STATE_REFRESH_MARKERS` 保守保留（工具自报 DOM 变化仍打断）；ablation A3 观测；`BROWSER_STATE_REFRESH_MODE=always` 一键回 r1 行为 |
| R4 | C4b 步级预算打断诱发 planner 重派循环 | 中 | 与 C4d 禁重导航同 commit 落地；观察 nav≥2 率与 planner 轮数 |
| R5 | C2 中性标记被 agent 误读为干扰/误点 | 低-中 | 固定角落定位不遮 #query；文本恒为 `EPISODE ENDED`（单测锁定无数值）；C7 模板同步解释；冒烟观测 |
| R6 | C3 锁死误触 reload 的格（no_reward） | 低 | r1 实证 reload 是净伤害（0 洗白、−4pp），锁死只是把伤害显式化；no_reward 可检测且在重试池 |
| R7 | C6 extra_tools 全量加载面扩大（visual_skill 多模态等） | 低 | 只开闸不改代码；H2③ 沙箱扫描在位；冒烟检查注册面与日志；不触发即无影响 |
| R8 | 判分修复误救反例 | 极低 | email-inbox-delete 反例进 replay 单测永久锁定 |
| R9 | 预算超支 | 低 | `assert_budget` 常量 R2_BUDGET_CAP=142 硬拦；ablation 独立批准 |
| R10 | 改动叠加后与 r1 不可比 | 声明级 | headline 逐项披露（§6）；归因靠 ablation（另批）；无 ablation 时报告明示多变量 |
