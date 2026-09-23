# R3 实现规格 — miniwob-r3（spec-r3）

> 读者为实现者。读完本文不再需要做任何设计决策；未规定处按"最简单确定性行为"处理并记入实现说明。
> 契约来源：`plan-r3.md`（采纳清单 R3-1…R3-6 与拒绝理由）、`spec-r2.md`（r2 现行规格，本文件只写**增量**；未提及处沿用 r2/r1）、`analysis-r2-failures.md`（行号证据）、`security-review-r2-post.md` §9。
> 总开关纪律：**r3 全部新增项独立 flag，默认 off = r2 行为逐字节复现**（唯一例外：E2/E3/E4/E6 是 harness/工具代码的披露增强，无 flag、对判分零影响，见 §5 前言）。headline 显式全开并在 manifest `flags` 记录。密钥红线沿用 r1/r2：key 只经 subprocess `env=` 注入，生成的 `agents_llm_config.json` 一律省略 `model_api_key`。
> 引擎接线点声明：r3 触碰 `testzeus_hercules/` 的文件为 `utils/llm_helper.py`、`core/simple_hercules.py`、`core/tools/open_url.py`、`core/tools/execute_python_sandbox.py`、`core/extra_tools/{__init__,drag_and_drop_tool,file_handler_tool}.py`、`core/agents/high_level_planner_agent.py`、`config.py`——均为 plan-r3 §1 采纳项的必要落点，不触碰判分/奖励路径（C1a 优先级、`fetch_reward`、`/latest`、miniwob_server 补丁层零改动）。

## 0. r3 运行前置（硬 gate）

1. C1c 余额预检、C1b 熔断器、429 熔断标记、`--max-cells` 配速、attempt 分目录日志——全部沿用 r2，零改动。
2. 预算：`R2_BUDGET_CAP = 144` 沿用。pilot+smoke 14 + headline 130 = 144；D-ablation 臂 135 独立 exp-id/exp-root 另批（§4）。
3. headline 命令见 plan-r3 §3；默认命令（无任何 r3 flag）= r2 默认行为（r2 七项 flags 同样默认 off）。

## 1. R3-1 nav 补全上限（flag `--nav-max-tokens`，engine）

### 1.1 `testzeus_hercules/utils/llm_helper.py`

- 新增函数（与 `get_llm_request_timeout_seconds` 同风格，直接读 env——r2 先例：`LLM_REQUEST_TIMEOUT` 亦不在 config relevant_keys 中、由 helper 直读）：

```python
def get_nav_max_completion_tokens() -> int:
    """Completion-token cap for nav/executor chat models. 0 (default) = uncapped (r2 behaviour)."""
    return max(0, _env_int("NAV_MAX_COMPLETION_TOKENS", 0))
```

- **运行时现状（r2 真实基线，review-r3 M1）**：benchmark 必经路径上 `adapt_llm_params_for_model` 被调用两次——`SimpleHercules.create` 对 planner/nav/helper 三个 config 原地 adapt（simple_hercules.py:152-160；benchmark 走 `SimpleHercules.create`，runner.py:71），`create_chat_model` 内部再 adapt 一次（llm_helper.py:83）。GLM 模型名落入 model_utils.py:69-70 的 "other models" 分支：`max_tokens` 缺失即注入 **4096**。即 r2 的 nav/planner 补全存在 4096 硬上限（实测肥尾 2000–3400 token 与之相容），且 **kwargs["max_tokens"] 在 benchmark 路径永不为 None**——注入逻辑必须是显式覆盖，`is None` 兜底是死代码。
- `create_chat_model`（L89-102）在 timeout/max_retries 兜底之后追加（**env > 0 时无条件覆盖** adapt 注入的 4096 与任何显式传入值；优先级写死为 env 最高——生成的 config 文件从不带 max_tokens，无实际冲突面）：

```python
nav_cap = get_nav_max_completion_tokens()
if nav_cap > 0:
    kwargs["max_tokens"] = nav_cap
```

- 消费面：`create_chat_model` 的全部调用方 = 各 nav agent（`base_nav_agent.py:66` 及 multimodal 变体）与 helper 多模态单例——后者由 `_initialize_agents`（simple_hercules.py:190-193）在**每次 benchmark 都构造**（经 `create_chat_model`），故 helper 同样吃 768 cap（image-comparer 在文本 DOM benchmark 中不被调用，无实际影响，如实披露）。**planner 不经过此函数**（`high_level_planner_agent.py:37-57` 裸 ChatOpenAI，其 `max_tokens=4096` 来自 create() 的 adapt 注入），保持 4096 不变——planner 的延迟交给 §2。
- 语义口径：R3-1 = **把 4096 收紧到 768**，不是"从无上界到 768"。
- 截断语义：`finish_reason=length` 的截断由 LangChain 正常返回；断裂的工具调用参数会在执行层报错并作为 ToolMessage 错误回灌（现有 `_execute_tool_call` 路径），agent 下一轮自行缩短。不做截断重试、不做特殊提示（最简单确定性行为）。

### 1.2 orchestrator

- CLI `--nav-max-tokens`（type=int，default 0）；`Orchestrator.__init__(nav_max_tokens: int = 0)`；`_child_extra_env` 在 `nav_max_tokens > 0` 时注入 `NAV_MAX_COMPLETION_TOKENS=str(nav_max_tokens)`；`flags["nav_max_tokens"] = nav_max_tokens`（off 时记 0）。

## 2. R3-2 planner 专属请求超时（flag `--planner-timeout`，engine）

### 2.1 `llm_helper.py`

```python
def get_llm_planner_request_timeout_seconds() -> float:
    """Per-request timeout for the planner node. <=0 / unset → follow LLM_REQUEST_TIMEOUT (r2 parity)."""
    raw = os.getenv("LLM_PLANNER_REQUEST_TIMEOUT")
    if raw is None or raw == "":
        return get_llm_request_timeout_seconds()
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid LLM_PLANNER_REQUEST_TIMEOUT=%r; following LLM_REQUEST_TIMEOUT.", raw)
        return get_llm_request_timeout_seconds()
    return value if value > 0 else get_llm_request_timeout_seconds()
```

### 2.2 `testzeus_hercules/core/simple_hercules.py`

- import 处（L41）补 `get_llm_planner_request_timeout_seconds`。
- `_llm_ainvoke`（L280-285）首行改为：

```python
timeout = (
    get_llm_planner_request_timeout_seconds()
    if agent_name == "planner_agent"
    else get_llm_request_timeout_seconds()
)
```

- 超时消息格式与 `_planner_timeout_result`（L311-354）**逐字节不变**（`{timeout:g}s` 自然反映新值）；不做重试（plan-r3 R3-2 拒绝理由）。

### 2.3 `testzeus_hercules/core/agents/high_level_planner_agent.py`（provider 层 timeout 同源化——review-r3 M2）

- 现状：planner 的 ChatOpenAI 自身 `timeout` kwarg 兜底为 `get_llm_request_timeout_seconds()`（L53-54）。headline 的 `LLM_REQUEST_TIMEOUT=90` 同时喂外层 wait_for 与该 provider timeout——若只改 §2.2 的 wait_for，**>90s 的单次 planner 生成依然会被 provider 层在 90s 掐断**（随后进入 `LLM_MAX_RETRIES=2` 的 SDK 重试链），150s 外层窗口等不到完整返回。
- 改动：L54 兜底改为 `safe_llm_params["timeout"] = get_llm_planner_request_timeout_seconds()`（import 同步）。env 未设时两个 helper 同值返回 → **off = r2 逐字节一致**仍成立；headline 注入 150 后 planner 的 wait_for 与 provider timeout **同为 150s**，单次 >90s 的慢生成可完整返回。
- nav/helper 的 provider timeout **不变**（仍 `get_llm_request_timeout_seconds`，llm_helper.py:94-95）——executor 的 90s 由 R3-1 的 768 cap 兜住延迟，无需放宽。

### 2.4 orchestrator

- CLI `--planner-timeout`（type=int，default 0）；`> 0` 时 `_child_extra_env` 注入 `LLM_PLANNER_REQUEST_TIMEOUT=str(planner_timeout)`；`flags["planner_timeout"]`。headline 取 150。与 `--latency-env`（`LLM_REQUEST_TIMEOUT=90`，LATENCY_ENV_OVERRIDES **原值不动**）叠加：planner 的 wait_for 与 provider timeout **同为 150**（§2.2+§2.3 同源）、其余 agent 保持 90。

## 3. R3-3 drag_and_drop 选择器透传（`--extra-tools` 加载面内生效，无新 flag）

### 3.1 `testzeus_hercules/core/extra_tools/drag_and_drop_tool.py`

- **source 解析替换**（现 L29-31 的 `if "md=" not in selector: selector = f"[md='{selector}']"` 整段删除，含子串误判缺陷）：

```python
_EXPLICIT_SELECTOR_PREFIXES = ("css=", "xpath=", "text=", "id=", "aria=", "role=")
raw = source_selector.strip()
if raw.startswith("md="):
    candidates = [f"[md='{raw[3:].strip()}']"]
elif raw.startswith(_EXPLICIT_SELECTOR_PREFIXES) or raw[:1] in ("#", ".", "[", "/", "(",):
    candidates = [raw]                                   # 任意显式 Playwright 选择器逐字透传
elif raw.isdigit():
    candidates = [f"[md='{raw}']", f"text='{raw}'"]      # 裸 md id（数字）——r2 兜底语义
else:
    candidates = [raw, f"[md='{raw}']", f"text='{raw}'"]  # 未知形态：原样 → md 兜底 → 文本兜底
```

- `find_element` 按 `candidates` 顺序尝试，取首个非 None；全部失败时错误消息列出全部候选（替换现 L50-51 的单候选消息）。
- target 侧逻辑（L53-97）**零改动**；拖拽鼠标序列（L117-137）零改动。
- 工具 description 更新为：`"Performs drag and drop from source to target. source_selector: any valid Playwright selector (CSS, XPath, 'text=…', '#id', '.class') or a bare md id; target_selector: any valid Playwright selector."`（参数 docstring 同步）。
- 定位兜底（plan-r3 R3-3 "源元素文本定位"）即 candidates 的 `text='…'` 尾项——不新增坐标参数。

### 3.2 生效面与冒烟

- 仅当 `--extra-tools` 加载该模块时行为变化；flags 全 off 时模块不加载，r2 复现成立。
- pilot 冒烟沿用 `--smoke-cells drag-items,drag-box` 机制（spec-r2 §4.1）。

### 3.3 kill-switch（M1 出口判据）

- 冒烟 2 格 stdout.log 中 drag_and_drop 调用的**选择器解析成功次数（进入鼠标序列）≥1** → 保留子集进 headline；
- 仍为 0 → headline 命令去掉 `--extra-tools`（整个模块移出），test-report 固化"修复后仍 0/2，放弃拖拽家族"的声明与两格日志摘要。判定与执行由总编排代理记录，不属于代码分支。

## 4. R3-4 D-ablation 臂（零代码）

- 命令形态：与 headline 完全相同的 flags + `--episode-ms 480000 --timeout-s 900 --exp-id miniwob-r3-d480 --exp-root <独立目录>`。orchestrator 的 `--episode-ms`/`--timeout-s` 已存在（main L1263-1264），`episode_max_time_ms` 已进 URL（`goal_reader.page_url`）与结果行/manifest。
- 预算：`hercules_budget("full")` = 125+5 = 130 ≤ 144 ✓；`_assert_can_run` 上限 `min(125+5, 144)` ✓。不改任何常量。
- 披露：ablation 产物的每个数字必须携带 `episode_max_time_ms=480000`/`timeout_s=900` 标注单独出现；与 headline 双列，禁止合并分母（plan-r3 §4）。默认不在运行序列，用户逐项批准后执行。

## 5. R3-5 安全必办包（E1–E6）

> E1/E5 的 env 由 orchestrator flag 注入（默认不注入）；E2/E3/E4/E6 是无 flag 的 harness/工具改动：E2/E3 只新增日志行与扫描标注（不改 `status`/`official_passed`），E4 只新增 metrics 披露字段（`overall` 口径不动），E6 只新增崩溃留痕文件。四者对任何运行（含 flags 全 off）的判分影响为零——这是它们不带 flag 的理由。

### 5.1 E1 子集加载（env `EXTRA_TOOLS_MODULES`）

- `config.py`：`relevant_keys` 增 `"EXTRA_TOOLS_MODULES"`；`_finalize_defaults` 增 `setdefault("EXTRA_TOOLS_MODULES", "")`；新增 getter `get_extra_tools_modules() -> str`。
- `core/extra_tools/__init__.py`：在现 LOAD_EXTRA_TOOLS 门控内增白名单过滤：

```python
allow_raw = get_global_conf().get_extra_tools_modules() or ""
allow = {name.strip() for name in allow_raw.split(",") if name.strip()}
for _, module_name, _ in pkgutil.iter_modules([str(package_path)]):
    if allow and module_name not in allow:
        logger.info("[EXTRA_TOOLS] skipping module %s (not in EXTRA_TOOLS_MODULES allowlist)", module_name)
        continue
    ...  # 现有动态 import 逐字节保留
```

- allow 为空 = 全量加载 = 现行为（r2 复现通道）。
- orchestrator：CLI `--extra-tools-modules`（default `"drag_and_drop_tool"`，仅 `--extra-tools` 开启时消费）。`--extra-tools` 时注入 `LOAD_EXTRA_TOOLS=true`；子集值 `all`（大小写不敏感）→ **不注入** `EXTRA_TOOLS_MODULES`（全量，manifest 记 `["__all__"]`，报告必须披露）；值为空串 → `BenchmarkError`（防误开全量）；其余 → 原样注入 `EXTRA_TOOLS_MODULES=<值>`，manifest 记模块名列表。未开 `--extra-tools` → 两个 env 都不注入。

### 5.2 E2 文件工具调用日志行 + 扫描标记

- `core/extra_tools/file_handler_tool.py`：`persist_findings`/`recall_findings`/`augment_findings` 三函数体首行各加：

```python
logger.info("[EXTRA_TOOL_CALL] persist_findings path=%s", <其 path 类参数>)
```

（各自函数名与实参对应；不打印文件内容。）
- `orchestrator.py`：

```python
FILE_TOOL_CALL_MARKERS = ("[EXTRA_TOOL_CALL] persist_findings",
                          "[EXTRA_TOOL_CALL] recall_findings",
                          "[EXTRA_TOOL_CALL] augment_findings")
INVALID_REASON_FILE_TOOL = "file_tool_invoked"
```

`scan_cell_log` 增第四段：任一 marker 命中 → `reasons.append(INVALID_REASON_FILE_TOOL)`（与 file_url/sandbox 同路，`flagged` + `invalid_reason`；docstring 同步"④ file-tool call markers"）。

### 5.3 E3 open_url scheme 白名单

- `core/tools/open_url.py`：在 `special_browser_urls` 处理块之后、`ensure_protocol` 调用（L93）之前插入：

```python
scheme = (urlsplit(url).scheme or "").lower()
if scheme and scheme not in ("http", "https"):
    logger.warning("[OPEN_URL_BLOCKED] scheme=%s url=%s", scheme, url)
    add_event(EventType.INTERACTION, EventData(detail=f"open_url_blocked:{scheme}"))
    return f"Blocked URL scheme '{scheme}:' — only http:// and https:// are allowed. URL rejected: {url}"
```

- 无 scheme 的输入继续走 `ensure_protocol`（https 前缀现状保留）；`about:blank` 等既有 special 路径在其之前返回、不受影响；`javascript:`/`data:`/`file:`/`vbscript:`/未知 scheme 一律拒绝且**不发生任何导航调用**。
- orchestrator：`OPEN_URL_BLOCKED_MARKER = "[OPEN_URL_BLOCKED]"` → `scan_cell_log` 命中仅 `flagged=True`（披露，不 invalid——与安全复审 §7 对 3 格 javascript: 尝试"低危披露"的 r2 处理对齐）。

### 5.4 E4 metrics invalid 口径自动化

- `record2gherkin/benchmark/metrics.py`：
  - 新增 `clean_rate(cells, tasks) -> MetricValue`：以 latest 行为准，`invalid_reason` 非 null 的 cell **同时移出分子与分母**（`official_passed` 在剩余分母上统计；无行 cell 仍计 0 并列入 missing 语义不变）。
  - `Summary` 增字段 `clean: MetricValue` 与 `invalid_cells: list[dict]`（每项 `{"task_id", "seed", "invalid_reason"}`，按 task_id 排序，上限 50 条截断计数）；`as_dict()` 同步输出。
  - `overall`（`_rate`）**零改动**——官方口径必须继续把 invalid 格计 0 分母不动。
- 报告口径纪律：r3"干净口径"引用 `clean`（E1 生效时预期分母 125、invalid≈0）；若仍出现 invalid 格，`overall` 与 `clean` 双列披露。

### 5.5 E5 沙箱关停（env `SANDBOX_DISABLED`）

- `config.py`：`relevant_keys` 增 `"SANDBOX_DISABLED"`；`setdefault("SANDBOX_DISABLED", "false")`；getter `get_sandbox_disabled() -> str`。
- `core/tools/execute_python_sandbox.py`：函数体最前（tenant 读取与任何日志标记之前）插入：

```python
if get_global_conf().get_sandbox_disabled().strip().lower() == "true":
    logger.warning("[SANDBOX_DISABLED] execute_python_sandbox refused (disabled for this environment)")
    return "Python sandbox is disabled in this environment ([SANDBOX_DISABLED])."
```

- orchestrator：CLI `--disable-sandbox`（store_true）→ env `SANDBOX_DISABLED=true`；`SANDBOX_CALL_MARKERS` 元组追加 `"[SANDBOX_DISABLED]"` → 命中仍落 `INVALID_REASON_SANDBOX`（r2 契约不变：**尝试即无效，被拦截的尝试同样无效**，杜绝"拦截成功却静默逃脱扫描"）。

### 5.6 E6 极早崩格留痕

- orchestrator `_run_cell` 的 `GoalReadError` 分支（no_goal 路径）：append row 之前向 `self.runs_dir / cell.run_id / "goal_read_error.log"` 写入异常文本（`mkdir(parents=True, exist_ok=True)`；写失败仅 warning 不抛）。结果行 `failure_message` 语义不变。

## 6. R3-6 planner 断言纪律（flag `--assert-discipline`，engine）

- `config.py`：`relevant_keys` 增 `"PLANNER_ASSERT_DISCIPLINE"`；`setdefault("PLANNER_ASSERT_DISCIPLINE", "false")`。
- `core/agents/high_level_planner_agent.py`：模块级常量（逐字）：

```
_ASSERT_DISCIPLINE_INSTRUCTION = """ASSERTION DISCIPLINE: Before responding with terminate=yes and is_passed=true, the LAST helper observation must explicitly confirm that the requested action (including any required submit) was actually performed. If the last observation reports a failure, a timeout, does not mention the action, or is inconclusive, you must set is_passed=false and describe what is missing. Never base is_passed on your instruction alone.

"""
```

- `__init__` 中 `self.system_message = self._json_instruction + self.system_message` 之后追加：

```python
if str(get_global_conf().get_config().get("PLANNER_ASSERT_DISCIPLINE") or "").strip().lower() == "true":
    self.system_message = _ASSERT_DISCIPLINE_INSTRUCTION + self.system_message
```

（import `get_global_conf`；off 时 system_message 与 r2 逐字节一致。）
- orchestrator：CLI `--assert-discipline`（store_true）→ env `PLANNER_ASSERT_DISCIPLINE=true`；flags 记录。

## 7. orchestrator 汇总（r2 → r3 增量）

- 新 CLI：`--nav-max-tokens <int>=0`、`--planner-timeout <int>=0`、`--extra-tools-modules <csv>="drag_and_drop_tool"`、`--disable-sandbox`、`--assert-discipline`（r2 的七个开关与 `--provider/--smoke-cells/--max-cells` 原样）。
- `_child_extra_env` 键集（headline 全开时）：r2 的 `LATENCY_ENV_OVERRIDES`(5 键) + `LOAD_EXTRA_TOOLS` + 路由 4 键，加 `NAV_MAX_COMPLETION_TOKENS`、`LLM_PLANNER_REQUEST_TIMEOUT`、`EXTRA_TOOLS_MODULES`、`SANDBOX_DISABLED`、`PLANNER_ASSERT_DISCIPLINE`。注入精确性受 T10 锁定。
- `flags` dict 新增键：`nav_max_tokens`、`planner_timeout`、`extra_tools_modules`（列表或 `["__all__"]`）、`disable_sandbox`、`assert_discipline`。默认运行全部为 off/空语义。
- `LATENCY_ENV_OVERRIDES` 的五个键值**逐字节不动**。

## 8. 离线单测清单（`tests/record2gherkin/`，不跑真 LLM；零真实网络；env 用 monkeypatch.setenv 隔离）

**T1 nav 补全上限**（断言对象 = adapt 注入后的运行时值）：env 未设或 `"0"` → `create_chat_model` 产物 `max_tokens == 4096`（r2 真实基线复现，model_utils 兜底生效）；env `NAV_MAX_COMPLETION_TOKENS=768` → `max_tokens == 768`（无条件覆盖 4096）；显式 `llm_config_params={"max_tokens": 256}` + env 768 → `768`（锁定"env 最高优先级"语义）；显式 256 + env 未设 → `256`（无 env 时现状不变）；planner 路径（`PlannerAgent` 的 stub llm 捕获 kwargs）保持 `max_tokens == 4096`、永不含该 cap。

**T2 planner 超时分档（双层）**：`SimpleHercules.__new__` 绕过 init 后调 `_llm_ainvoke`，stub llm 的 `ainvoke` sleep 超限：env `LLM_PLANNER_REQUEST_TIMEOUT=5` + `LLM_REQUEST_TIMEOUT=1` → `agent_name="planner_agent"` 在 ~5s 内不抛、`"browser_nav_agent"` 在 ~1s 抛 TimeoutError 且消息含 `1s`；env 未设 → 两者同为 `LLM_REQUEST_TIMEOUT` 值；非法值回退不抛异常。**provider 层同源**（stub ChatOpenAI 捕获构造 kwargs）：env `LLM_PLANNER_REQUEST_TIMEOUT=150` → `PlannerAgent` 产物 `timeout == 150` 且 `max_tokens == 4096`（不受 R3-1 影响），nav agent（`create_chat_model`）产物 `timeout == 90`；env 未设 → 两者 `timeout` 同为 90（r2 复现）。

**T3 extra_tools 子集**（subprocess 隔离，避免 config 单例污染）：子进程 env `LOAD_EXTRA_TOOLS=true` + `EXTRA_TOOLS_MODULES=drag_and_drop_tool` → import 包后 `drag_and_drop` 在命名空间、`persist_findings`/`read_clipboard` 不在；无 `EXTRA_TOOLS_MODULES` → 两者均在（r2 全量复现）；`EXTRA_TOOLS_MODULES=` 空串 → 全量。

**T4 文件工具日志行**（caplog）：调 `persist_findings` 写 tmp 路径 → 记录含 `[EXTRA_TOOL_CALL] persist_findings` 与路径；`recall_findings`/`augment_findings` 同理。

**T5 scheme 白名单**（monkeypatch `PlaywrightManager.get_current_page` → stub page 记录 goto/evaluate）：
(a) `open_url("javascript:void(0)")` → 返回文本含 `Blocked URL scheme 'javascript:'`，stub page 零导航调用；
(b) `open_url("data:text/html,x")`、`open_url("file:///etc/passwd")` → 同拒；
(c) `open_url("127.0.0.1:PORT/x")`（无 scheme）→ 正常走 ensure_protocol 并导航；
(d) `open_url("about:blank")` → 走既有 special 分支（行为回归）；
(e) 日志含 `[OPEN_URL_BLOCKED]`。

**T6 沙箱关停**：env `SANDBOX_DISABLED=true` → `execute_python_sandbox` 返回文本含 `[SANDBOX_DISABLED]`，不出现 `Using sandbox tenant` 行；env off → 走原路径（mock 执行器，断言原日志序列第一行为现 r2 行为）。

**T7 扫描标记**：`scan_cell_log` 对含 `[EXTRA_TOOL_CALL] persist_findings` 的日志 → `flagged=True, invalid_reason="file_tool_invoked"`；含 `[OPEN_URL_BLOCKED]` → `flagged=True, invalid_reason=None`；含 `[SANDBOX_DISABLED]` → `invalid_reason="sandbox_tool_invoked"`；干净日志 → 全中性（回归 r2 T 套件不变）。

**T8 metrics clean 口径**：构造 3 task rows（1 个 latest 行 `invalid_reason` 非空且 `official_passed=True`、1 个 invalid 失败格、1 个普通通过格）→ `overall = 1/3`（不变）；`clean = 1/1`；`invalid_cells` 恰含两格且带 reason；>50 个 invalid 时截断计数正确。

**T9 drag 选择器解析**（stub PlaywrightManager.find_element 捕获实参 + stub page.wait_for_selector/bounding_box/mouse）：
(a) `("css=.drag-handle", "css=.drop-zone")` → find_element 恰收到 `"css=.drag-handle"`（不包 md），鼠标 down→20 move→up 序列执行，返回成功文案；
(b) `("5", …)` → 首候选 `[md='5']`；
(c) `("Kass", …)` → 依次尝试 `Kass`、`[md='Kass']`、`text='Kass'`，find_element 前两个返回 None、第三个命中 → 成功；
(d) 全候选失败 → 错误消息列出全部候选；
(e) `("md=7", …)` → `[md='7']`；target 侧行为零变化（既有调用形态回归）。

**T10 orchestrator 注入与 flags**：
(a) 全开 → `_child_extra_env` 键集恰为 r2 五键 + LOAD_EXTRA_TOOLS + 路由 4 键 + 新五 env（`EXTRA_TOOLS_MODULES="drag_and_drop_tool"`），多一个键即失败；
(b) 默认（r3 flags 全 off）→ 新五 env 零出现；
(c) `--extra-tools --extra-tools-modules all` → 无 `EXTRA_TOOLS_MODULES` 键，flags 记 `["__all__"]`；
(d) `--extra-tools --extra-tools-modules ""` → `BenchmarkError`；
(e) manifest `flags` 含五个新键及取值；`--nav-max-tokens 0 --planner-timeout 0` → 不注入对应 env。

**T11 断言纪律开关**（stub ChatOpenAI + stub get_user_ltm）：conf true → PlannerAgent.system_message 以 `_ASSERT_DISCIPLINE_INSTRUCTION` 开头且含 `is_passed=false`；false/未设 → 与 r2 逐字节一致（快照锁定）。

**T12 D 臂 dry-run**：`--stage full --dry-run --episode-ms 480000 --timeout-s 900` → 计划 125 格、budget 130/cap 144、零执行零落盘；打印的 URL 含 480000 episode 参数；不触发预检。

**回归**：`tests/record2gherkin/` 全量绿（r2 的 T1–T11 语义不受影响；spec-r2 §8 中与 extra_tools 全量加载相关的用例如涉及本 T3 的 env，按 T3 隔离方式适配并在实现说明记录）。

## 9. 验收标准

1. `uv run pytest tests/record2gherkin -q` 全绿；`make fmt` 后 `make lint`（black --check）通过。
2. T1–T12 全绿；新增引擎改动不触碰 `build_result_row` 判分链与 miniwob_server 补丁层（diff 审计为零改动）。
3. M1 pilot+smoke（14 执行，全开 flags）：drag kill-switch 有明确判定记录（§3.3 二选一）；completion 无异常长尾（>768 token 的 executor turn 应为 0——以 stdout.log TOKEN_COUNT 直方图核验）；无 402；单次开局/cue 行为与 r2 一致。
4. M2 headline：125 任务全覆盖、总执行 ≤130、manifest `flags` 记录 plan-r3 §3 全集；`clean` 口径由 metrics 产出（预期 invalid≈0；若非 0，`overall`/`clean` 双列）。
5. 披露完整性（test-report.md 必须逐项出现）：① A 的截断语义与直方图证据；② B 的 planner 超时计数对照（r2 25/41 → r3）与 150s 取值理由；③ C 的修复效果或放弃声明；④ E 的五个新标记/口径及其判分中立说明；⑤ F 为 prompt 行为变更的声明；⑥ r2 既有披露全单沿用（provider 混杂、240s/600s、单 seed 等）。
6. 脱敏红线复核：`KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs record2gherkin tests dev_docs` 零命中。
7. （若获批）D 臂：135 执行、独立 exp root、所有数字带 `episode_max_time_ms=480000` 标注；逐格配对归因表入 analysis-r3。

## 10. Out of Scope

r1 spec §11 与 spec-r2 §10 全部沿用，另加：

- plan-r3 §1"明确不做"全表：视觉/画布与坐标拖拽（drag-shapes/draw-*）；click-pie hover viewport、hot-cold DOM 等 L3 残余；模型档位调换与 planner 升级；全 agent 超时重试与 planner max_tokens；TERMINATE 机制化 reward 校验（L4 强版，留 r4）；LATENCY_ENV_OVERRIDES 键值调整。
- `extra_tools` 其余模块的行为改动（clipboard/pdf/geo/visual_skill 只受子集门控，函数体不动；file_handler 仅加日志行）。
- `_rate`/`overall` 官方口径的任何语义改动（E4 只增 `clean`/`invalid_cells` 披露）。
- D-ablation 的自动执行与 headline 合并口径（须用户逐项批准、双列呈现）。
- r2 数据的重新判分或 results.jsonl 的任何回写。
