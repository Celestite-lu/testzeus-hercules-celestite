# R2 Spec 独立审查 — review-r2

> 审查对象：`plan-r2.md`、`spec-r2.md`。法定输入：`analysis-r1-failures.md`、`analysis-r1-architecture.md`（另对照 r1 现行源码与 `dev_runs/benchmark/miniwob-r1/` 原始数据逐条复核）。
> 审查纪律：只拦"跑不通 / 数字不可信 / 作弊面 / 预算爆炸"。行号/接口错一处列一条。所有行号以当前工作树为准（2026-09-20）。
> 日期：2026-09-20。

---

## 0. 结论：**REVISE**（必改 5 项，其中 2 项属"跑不通"级、1 项属"作弊面"级）

r2 的总体设计是健全的：C1a 的 +5 经本审查用 r1 原始数据**独立复算成立**（54→59，含反例锁定）；行号引用绝大多数精确命中；开关矩阵默认态可回放 r1；离线测试 11 组均可不跑真 LLM 实现；预算 gate 与熔断的骨架在位。但存在 5 个必须在实现前修正的问题——其中 C5 的 key 断链会让 `--role-routing` 的 headline 在引擎启动即崩，C3 的被拦截加载面重新打开了 r1 安全审查已封死的 V2 re-roll 通道，冒烟 drag 格按现 spec 无法执行且预算算术自相矛盾。

### 必改项（REVISE 清单）

**M1（跑不通级）C5 planner key 断链：PlannerAgent 不经 `create_chat_model`，`MODEL_API_KEY` 对它不可见。**
- 事实链（已逐层核实）：`AgentsLLMConfigManager.initialize()`（`agents_llm_config_manager.py:119-175`）在文件加载成功后**直接 return，env provider 完全不注册**（File > Env 是源级覆盖，不是按键合并）；文件省略 `model_api_key` → `normalize_agent_config`（`agents_llm_config.py:75-94`）产出无 `api_key` 的 `model_config_params` → `SimpleHercules._initialize_agents` 把它交给 `PlannerAgent`，后者**直接 `ChatOpenAI(**filtered, **safe_llm_params)`**（`high_level_planner_agent.py:37-57`），不经过 `llm_helper.create_chat_model`。本仓库 `langchain-openai>=1.1.14` 的 ChatOpenAI 只从显式参数或 `OPENAI_API_KEY` env 解析 key，**不读 `MODEL_API_KEY`**。spec §6.2 引用的 `llm_helper.py:85` 回退只覆盖 nav/helper（`base_nav_agent.py:66`、`multimodal_base_nav_agent.py:61`、`llm_helper.py:170` 均走 `create_chat_model`），覆盖不了 planner。
- 后果：`--role-routing` 开启时 planner 构造/首调即抛缺 key 异常，每格 no_junit，headline 报废。
- 改法：C5 开启时 child env 增注 `OPENAI_API_KEY=<key>`（key 仍只走 subprocess env，红线不破）；同步修订 spec §6.2 注入键集合与 T7 断言（三键→四键）。r1 环境下 planner 之所以能跑，正是因为 env 路径把 `LLM_MODEL_API_KEY` 变成了 config 内的 `model_api_key`（`agent_config_types.py:82`、`config_env_loader.py`），C5 切到文件路径后该通道消失。

**M2（作弊面级）C3 被拦截的二次加载面重新暴露 V2 re-roll 通道。**
- 事实：`AUTO_START_PATCH` 的安全加固四行（HUD `display:none`、stub `core.updateDisplay`、stub `core.startEpisode`，`miniwob_server.py:90-93`）只在**开局成功分支**内执行。spec §4.2 的 single 变体在 `sessionStorage` 命中时**早退**，被拦截的加载页面上 `core.startEpisode` 未被 stub、`#sync-task-cover`（START 覆盖层）未失效——r1 安全审查对 V2 的结论原文（`miniwob_server.py:63-65` 注释）就是"该覆盖层离一次 plain DOM click 的重开只有一个 DOM click 之遥"。agent 有 click 工具且无 JS 执行面，点击 START 即可：(a) 在**无 `Math.seedrandom(seed)`** 的状态下重开一个随机实例（破坏 seed 可复现）；(b) 产生新 reward 记录，经 `/latest` last-wins 覆盖本格已有终局（正是 C3 声称封死的洗白通道）。
- 改法：`AUTO_START_PATCH_SINGLE` 的早退路径必须**同样应用加固**（早退前 stub `core.startEpisode`、隐藏 HUD，或把加固四行提到 sessionStorage 判断之前执行）；T6 增加断言"拦截态下点击 cover 不产生任何 reward 记录、`WOB_TASK_READY` 不翻转为已开局"。
- C3 其余面审查通过：agent 无 JS 执行能力、导航不清 sessionStorage（sessionStorage 按 tab+origin 持久，跨导航不清）、重试 attempt 是新进程新 context 不受影响、read_goal 预读独立 context 不受影响（`goal_reader.py:64` sync_playwright 确认）。

**M3（跑不通级）冒烟 drag 2 格没有执行机制，且预算算术自相矛盾。**
- 事实：`PILOT_SUBDOMAINS`（`tasks.py:24-35`）**不含 drag-items / drag-box**（10 格已核实：click-test…visual-addition）；orchestrator CLI 没有任意 cell 子集入口（`main()` 只暴露 `--stage`，`plan_cells` 的 `tasks` 参数未接 CLI）。spec §9.5 要求"冒烟 12 次执行内 ≥1 drag 格日志出现 drag_and_drop 调用"——按现定义冒烟 stage 里**不存在 drag 格**，验收不可满足。同时 plan §4 自己写了"否则 +2"，但 `R2_BUDGET_CAP = 142 = 冒烟 12 + headline 130` 未含这 +2，实际最小执行数是 **144**。
- 改法（二选一，spec 必须钉死一种）：(a) orchestrator 增加受控的 `--extra-cells drag-items,drag-box`（或 `--tasks-file`）冒烟机制，cap 修正为 144；(b) 把 drag-items/drag-box 并入 pilot 集（`STAGE_RUNS["pilot"]` 随之 12，重试预算同步调整）并在 cap 内消化。不得让实现者临场发明机制。

**M4（跑不通风险级）C1c 预检的 transport 未钉死，照 spec 字面实现会恒败或探测了与引擎不同的路径。**
- 事实：spec §3.1 写 `litellm.acompletion(model, ...)`，而引擎实际栈是 **ChatOpenAI（langchain-openai）直连 `https://api.deepseek.com`**；litellm 对自定义端点需要 provider 前缀（`openai/deepseek-v4-pro` + `api_base`），裸模型名会被 litellm 当作未知 provider 抛错——预检将把"可用的 key"误判为失败并 exit 3，headline 被自己的 gate 挡死；反之若调通的是另一条路由，预检也不构成对引擎路径的证明。
- 改法：spec 钉死探测实现与引擎同栈：`ChatOpenAI(model=<model>, api_key=<key>, base_url=<LLM_MODEL_BASE_URL>)` + `max_tokens=1` 的最小 invoke（或明确写 litellm 方案必须带 `openai/` 前缀与 base_url，并把该行为写进 T4 的 monkeypatch 面）。

**M5（文字/断言修正，打包）三处低代价但不修会迫使实现者做设计决策：**
1. spec §1.3/T1 "email-inbox-delete 维持 **official_failed**" 与 §1.1 优先级矛盾：该格 r1 终态是 `runner_status=timeout`、`raw=-1`，按新规则落 `status=timeout`（metrics 上判 0，但**不是** `official_failed` 字面值）。断言应改为"维持非 official_passed（status=timeout）"。已用 r1 数据核实该行 `timeout / raw=-1.0 / task_url_navigations=8`。
2. spec 头部总开关纪律"默认全部 off（**默认行为 = r1**）"与 C1a 常开矛盾：flags 全 off 时判分已是 59 口径而非 r1 的 54。应改为"默认 = r1 行为 + C1a 判分修复（正确性修复，见 plan §6.1 披露）"，避免报告口径误述。
3. 行号勘误：`browser_nav_agent.py` 的第一处刷新教学实际在 **L55**（"20. To refresh a page, open the same URL again…"，plan/spec 写 L20；L20 实为 "interact with browser only using the tools provided."）。L116 正确。按引文文本搜索仍能唯一定位，但 spec 自称"已核对原文"却带错行号，须修正。

---

## 1. 行号/接口核对表（错一处列一条；✅=与声明一致）

| 文档声明 | 实际核对 | 判定 |
|---|---|---|
| `simple_hercules.py:662-675` `_requires_state_refresh` | 662-675 逐行命中（agent 名检查→markers→成功状态变更+非错误） | ✅ |
| 守卫消费点（任务书 :963-991；plan 引 940-991） | 963 `if self._requires_state_refresh(...)` 起、至 991 提示语收尾；940-991 覆盖整批工具循环 | ✅ 两种引用均成立 |
| `simple_hercules.py:910` 轮循环 `for _turn in range(...)` | L910 命中 | ✅ |
| `simple_hercules.py:548-570`、`_BROWSER_STATE_CHANGING_TOOLS` 12 个含 hover、`drag_and_drop` 在 :556 | 集合 548-561 恰 12 项含 hover；drag_and_drop 在 L556；`_STATE_REFRESH_MARKERS` 563-570 | ✅ |
| `simple_hercules.py:31` 无条件 `from ...extra_tools import *` | L31 命中 | ✅ |
| `simple_hercules.py:170-199`（spec 引 176-199）nav_agent 配置驱动全部 nav/executor agents | `_initialize_agents` L170-208，nav_cfg 消费在 176-199（7 个 agent + helper） | ✅ |
| `browser_nav_agent.py` **L20** 刷新教学 | **L20 无此句**；原文在 **L55**；L116 命中 | ❌ → M5-3 |
| `browser_nav_agent.py:41` rule 8 "ALWAYS analyze ALL … FIRST" | L41 命中 | ✅ |
| `high_level_planner_agent.py` L198-204 Closure Nudge、L300 Critical Rule 5、322 行、L59-74 `_json_instruction`、待删章节（Platform Awareness L99 / Test Data Focus L234 / Executor Operation Detection ~L206） | 全部命中（`wc -l`=322） | ✅ |
| `llm_helper.py:85` `MODEL_API_KEY` 回退 | L85 逐字命中；nav/helper 经 `create_chat_model` 成立；**planner 不经此路径** | ✅ 引用属实，但覆盖面被 spec 高估 → M1 |
| `llm_helper.py:21-22` 默认 60s / retries 1 | L21-22 命中（`get_llm_request_timeout_seconds` 另回退 PORTKEY_TIMEOUT，env 显式注入优先，C4f 有效） | ✅ |
| `core/runner.py:24` `browser_nav_max_chat_round=50` | L24 命中（经 `SimpleHercules.create` 传入循环上限，C4b 改法可行） | ✅ |
| `core/runner.py:53-60` 读 planner/nav/helper 三配置 | 三读在 **L52-54**（53-60 覆盖 nav/helper 与配置存储，planner 越界 1 行） | ⚠ ±1，机制正确，不阻塞 |
| `extra_tools/__init__.py:11` `LOAD_EXTRA_TOOLS != "false"` 门控 | L11 命中；包 `__init__` 期动态 import → `@tool` 注册（`tool_registry.py:129`）→ browser_nav_agent 可取到；`config.py:701` 默认 "false"、`:1038` getter、`:556` env 映射 | ✅ C6 链路成立 |
| `config.py:166` `AGENTS_LLM_CONFIG_FILE` env 同名映射 | L166-167 命中；File>Env 为**源级覆盖**（加载成功即 return） | ✅（该语义正是 M1 的根据） |
| `orchestrator.py:199-209` `build_result_row` 优先级 | L199-209 逐行命中（architecture H1 的 199-211 为旧引用，plan/spec 已修正） | ✅ |
| `orchestrator.py:59` cap 142 / `assert_budget` | L59、L145 命中 | ✅（算术矛盾见 M3） |
| `evaluation/runner.py:41` `LLM_MODEL_NAME="deepseek-v4-pro"` | L41 命中；`LLM_MODEL_BASE_URL`/`LLM_MODEL_API_TYPE` 在 L42-43（§6.1 引用成立） | ✅ |
| `run_feature` 增 `stdout_log_path` / 经 `extra_env` 注入 | `extra_env` **已存在**（L251、264-265）；`stdout_log_path` 需新增（现 L306 硬编码），spec 已写明兼容语义 | ✅ |
| `miniwob_server` 补丁 A 两处插入点（seed 早退 / clearInterval） | L75-76 与 L94 命中；"安全加固四行"=L90-93 逐字保留可行 | ✅（拦截面缺口见 M2） |
| `patch_core_js(source)` 改签名 + REWARD_HOOK/TERMINAL_CUE 叠加顺序 | 现为 `patch_core_js(source)`（L155-158），追加式结构支持顺序扩展；endEpisode wrapper 链（REWARD_HOOK L107-131）与 T5(c) 断言一致 | ✅ |
| read_goal 预读独立 context | `goal_reader.py:64` 每次 `sync_playwright()` 新开，sessionStorage 不跨 context | ✅ |
| `render_feature` Then 前插注释段 | 模板 L101-114，注释行不破坏 gherkin 解析可行 | ✅ |
| `metrics.ROW_KEYS` 增量三键 | `metrics.py:21` 存在，T2 可行 | ✅ |

**证据忠实性抽验**（法定输入 → plan 转译）：A 桶 5 格与反例已对照 `miniwob-r1/results.jsonl` 独立复算——130 行去重 125 任务、官方 54；5 条救援行恰为 plan 点名的 5 格（login-user-popup/multi-layouts/use-colorwheel-2 为 timeout+raw>0，click-pie/click-collapsible-2-nodelay 为 no_junit+raw>0；use-colorwheel-2 raw=0.537 逐位一致）；multi-layouts 页面通过时刻经 rewards.jsonl 差分 = **83s**，plan 采用的 failures 文档数字正确（architecture 文档"4.3 分钟"为分析文档内部矛盾，plan 未受染）。23 格 zombie/273s、93% 同 tab、61/125 格 nav≥2、9.0s 轮延迟、p90 1998 tokens 等转译均与两份分析一致，未见夸大预期（C2 的 +0~3pp、C4/C5 的 +2~6pp 均为分析区间去重后的保守化，来源可溯）。

---

## 2. 安全/作弊面预审表

| 项 | 预审结论 | 依据 |
|---|---|---|
| **C2 中性终局标记** | **通过**（设计级） | 注入恒定文本 `EPISODE ENDED`，不含 reward/done/成败数值（§4.3 逐字 JS + T5a 双 endEpisode 值一致性断言）；cue 不 POST、不改 `WOB_RAW_REWARD_GLOBAL`（T5c `/latest` 逐字节一致）。评估 agent 借 cue 在失败格提前终止**不影响判分**：页面 reward 仍是唯一权威，提前终止只是让 `raw=-1` 早些落定；A 桶收益由 C1a 计回，披露义务（§6.2）已写明。 |
| **C1a 判分修复** | **通过**，附 M5-1 文字修正 | 只有 `raw>0` 才压过 infra；页面判负（raw=-1，含 timed out）格不可能被误判通过（落到 infra 分支保持 timeout/no_junit，仍可重试，与 r1 同）；r1 格内洗白为 0、`fetch_reward` 保持 `/latest`，无新增洗白通道。**回放锁可机械验证**：本审查已用 results.jsonl 独立复算 5 救援 + 反例维持 + 54→59；fixture 所需 `(runner_status, raw)` 摘要可从 r1 行（status/official_passed/reward_raw）确定性导出。 |
| **C3 单次开局** | **有条件通过，M2 必改** | sessionStorage 按 tab+origin 持久、agent 无 JS 执行面、重试/预读/force_new_tab 缺口披露均成立；但被拦截加载面未加固，START 覆盖层 click 可重开无 seed 实例并写新 reward 记录——既是判分面（last-wins 覆盖）也是可复现性破口，见 M2。 |
| **C5 路由 key 只走 env** | **红线方向正确，M1 必改** | 文件省略 key + env 注入的方向不变式成立（生成文件无 `sk-`、T8 复核在位）；但 `MODEL_API_KEY` 只被 `create_chat_model` 消费，planner 走裸 ChatOpenAI，必须补 `OPENAI_API_KEY` 注入（仍只经 subprocess env），见 M1。 |
| **C1b/C1c 运维面** | 通过，附 M4 | 熔断三条件（infra 状态 + <30s + marker）保守、不改判分；run 级连续 2 格中止、exit 2、已写行保留，合理。 |
| **既有判分/披露面** | 通过 | `runner_status/attempt/infra_circuit_break` 三新键明确不进分母（§1.2、T2）；V3/V4/V5/V6 扫描与披露位全部保留；cap、manifest flags、git_rev 可追溯。 |

---

## 3. 审查清单逐项结论

1. **证据忠实性**：通过（§1 抽验；预期区间均有分析文档出处，无夸大）。
2. **行号/接口**：1 处实质错误（L20→L55）、1 处 ±1（runner.py 52-54 vs 53-60）、其余全部命中（§1 表）。
3. **开关矩阵**：默认态可回放 r1 **但须按 M5-2 修正措辞**（C1a 常开使默认判分=59 口径）；headline 全开组合无相互冲突——C3 与重试/冒烟重跑不冲突（每 attempt 新进程新 browser，sessionStorage 不跨进程）；C4b 与 C4d 同 commit 约束、C5 pilot 先行、C2/C7 文本联动（T8 两变体）均已写明。**冒烟 drag 格机制缺失 → M3**。
4. **离线测试设计**：11 组全部可不跑真 LLM——T1-T4/T7-T11 纯离线；T5/T6 走本地回环浏览器（`tests/record2gherkin/benchmark/` 已有 test_browser_miniwob/test_server_patch 先例）；T9 用 stub self 可测守卫与预算文本；T10 monkeypatch 子进程命令即可。T1 断言按 M5-1 修正、T7 键集合按 M1 修正后闭合。
5. **预算与运行前置**：骨架在位（预检 gate→exit 3、熔断、cap 硬拦、ablation 独立批准），但 **cap 算术矛盾 → M3**；C1c 探测 transport → M4。`LLM-Key.txt` 脱敏复核链（§9.8）与 `env_redacted` 对新增 env 键的掩码（值等于 key 时被 `mask_secret` 覆盖）均成立。
6. **spec 完备性**：主体达到"读完无需设计决策"，但 M1-M4 各留了一个实现者必须替 spec 做的决定；ablation 的 10 格选择与禁重试机制未定义（见不阻塞建议 a）。

---

## 4. 不阻塞建议（≤3 条）

- **a) ablation 运行机制未定义**：spec §0.2 只写了 `--run-cap`，但 orchestrator 没有任意 10 格子集入口、也没有"无重试"开关（RETRY_BUDGET 按 stage 固定）。ablation 属另批事项，不拦本轮；批准 ablation 前需补 `--tasks-file`/`--cell-list` 与 retry=0 语义。
- **b) 两份分析文档间小矛盾备案**：multi-layouts 页面通过时刻 failures 写 83s（经 rewards.jsonl 复核**正确**）vs architecture 写 4.3 分钟；email-inbox-forward 导航次数 failures 写 16 vs architecture 写 15。plan 采信的数字均与原始数据一致，无需改 plan；仅防后续引用时踩错源。
- **c) 两个实现提示**（写代码时的坑，非设计问题）：C4b 的 `browser_nav_max_chat_round` 缺省值改读 config 时须在 `__init__` 函数体内取（Python 默认参数在定义期求值）；`_requires_state_refresh` 读 config 每工具调用一次，注意 `get_global_conf().get_config()` 返回的是内部 dict（`config.py:806`），非法值回落 `always` 的 T9 分支要覆盖空字符串。

---

## 5. 验证证据清单（本审查执行的独立复算）

- r1 数据复算：`results.jsonl` 130 行 → 125 任务、官方 54；救援行恰 5（与 plan 点名一致，use-colorwheel-2 raw=0.5372...）；email-inbox-delete 末行 `timeout / raw=-1.0 / task_url_navigations=8`；multi-layouts 首条 raw=1 与 started_at 差分 = 83.0s。
- 引擎侧行号逐条打开核对（simple_hercules.py 4 处、browser_nav_agent.py 3 处、high_level_planner_agent.py 5 处、llm_helper.py 3 处、core/runner.py、config.py、extra_tools/__init__.py、drag_and_drop_tool.py、tool_registry.py）。
- C5 key 链四层源码追踪（agents_llm_config_manager → config_file_loader → agents_llm_config.normalize_agent_config → PlannerAgent/ChatOpenAI v1.1.14 key 解析），并确认 `langchain-openai>=1.1.14` 只认 `OPENAI_API_KEY`。
- harness 侧接口核对（orchestrator 全文、evaluation/runner 全文、miniwob_server 全文、goal_reader、metrics、tasks）。
