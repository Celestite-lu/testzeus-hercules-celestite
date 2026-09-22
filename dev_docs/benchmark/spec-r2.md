# R2 实现规格 — miniwob-r2（spec-r2）

> 读者为实现者。读完本文不再需要做任何设计决策；未规定处按"最简单确定性行为"处理并记入实现说明。
> 契约来源：`plan-r2.md`（采纳清单 C1–C7）、`spec.md`（r1 现行规格，本文件只写**增量**；未提及处沿用 r1）、`analysis-r1-failures.md`、`analysis-r1-architecture.md`。
> 密钥红线沿用 r1：`LLM-Key.txt` 内容只能经 subprocess `env=` 注入；r2 新增约束——**生成的 `agents_llm_config.json` 一律省略 `model_api_key`**（执行侧经 env `MODEL_API_KEY` 回退，见 §6）。
> 总开关纪律：**C1 判分中立包常开（无开关）**；C2/C3/C4/C5/C6/C7 各自独立开关，**默认全部 off**。默认态 = r1 行为 **+ C1a 判分修复**（C1a 是正确性修复：把 spec.md §0 口径 4 执行对，flags 全 off 时判分口径即 59/125 回放口径，见 plan-r2.md §6.1 披露——不是纯 r1 的 54/125）；headline 运行显式全开并在 manifest `flags` 记录。

## 0. r2 运行前置条件（最高优先级，硬 gate）

1. **余额预检（C1c）不过 → 不开跑**：orchestrator 在启动 miniwob_server 之前执行预检；任何 402 / Insufficient Balance / 连接类异常 → **exit 3**、打印脱敏原因（含"需充值"提示），不启动服务、不执行任何 cell。`--dry-run` 不预检。
2. **预算硬拦**：`HERCULES_BUDGET_CAP` 由 142 语义细化为 `R2_BUDGET_CAP = 144`（冒烟 14 = pilot 10 + 重试 2 + drag 冒烟 2；headline 130，`hercules_budget` 按 stage 组合校验）；ablation 走独立 exp 根目录 + 独立预算参数（`--run-cap`，A1–A3=30、A4=130），**默认不存在、用户批准后才出现在运行命令里**。超限 `BenchmarkError`。
3. headline = 全开 flags 单次运行；ablation（若获批）在 headline 之后，同 exp_id `miniwob-r2`、`--exp-root` 不同目录（seed 派生不变，逐格可配对）。

## 1. C1a 判分修复（orchestrator）

### 1.1 `build_result_row` 优先级重排

`record2gherkin/benchmark/orchestrator.py` L199-209 的 status 组装替换为（确定性顺序）：

```python
raw = _reward_raw_value(reward)
if runner_status is None:
    status = STATUS_NO_GOAL                       # 未执行（goal 预读失败），不变
elif raw is not None and raw > 0:
    status = STATUS_OFFICIAL_PASSED               # 新：窗口内页面正奖励压倒 infra 状态（spec.md §0 口径 4）
elif runner_status in (runner_module.STATUS_TIMEOUT, runner_module.STATUS_NO_JUNIT):
    status = runner_status                        # infra 状态保留（仍可进重试池）
elif reward is None:
    status = STATUS_NO_REWARD
else:
    status = STATUS_OFFICIAL_FAILED               # raw<=0（含页面 timed out 的 raw=-1），不变
```

- `official_passed = (status == STATUS_OFFICIAL_PASSED)`；`disagreement` 语义不变（仅 passed/failed 两态计算）。
- `fetch_reward` 仍取 `/latest`（last-wins 跨 attempt 语义**不变**）：C3 开启时二次导航不产生新记录、自然保留首次终局；不新增任何洗白通道。
- 结果行新增键 `runner_status`（`"timeout"|"no_junit"|"no_reward"|"official_passed"|"official_failed"|None`，no_goal 为 None）——"引擎超时但页面已过"的救援格从此可审计。

### 1.2 结果行 schema 增量键（metrics.ROW_KEYS 同步更新）

```
runner_status: str | None      # §1.1
attempt: int                   # 本结果行是第几次 attempt（1 起；无重试恒 1）
infra_circuit_break: bool      # C1b 熔断位（默认 False）
```

三个新键与 r1 既有三个扫描键同样**不参与分母/判定的篡改**，仅披露与审计。

### 1.3 replay 锁（单测，见 §8-T1）

从 r1 数据（`dev_runs/benchmark/miniwob-r1/`）固化最小 fixture（无 key、无大文本，5 个救援格 + 1 个反例 + 3 个一致格 + 2 个 infra 格的判定输入字段）入库 `tests/record2gherkin/benchmark/fixtures/r1_replay.json`。断言：5 格翻正为 official_passed、**email-inbox-delete 维持非 official_passed**（r1 该行实为 `runner_status=timeout, raw=-1, nav=8`，按 §1.1 落 `status=timeout`——metrics 判 0，但不是 `official_failed` 字面值，review-r2 M5-1）、一致格不变；并断言"把该规则作用于 r1 全量等价输入时官方合计 54→59"（fixture 内附 125 行的 `(runner_status, raw)` 摘要数组，测试内重算通过数）。

## 2. C1b 熔断器 + C1d attempt 日志（orchestrator / runner）

### 2.1 attempt 日志

- `record2gherkin/evaluation/runner.py::run_feature` 增加可选参数 `stdout_log_path: Path | None = None`；`None` 时行为与现状逐字节一致（`plan.run_dir / "stdout.log"`）。
- orchestrator：`_execute_cell` 内计算 `attempt_no = 本 cell 在 results.jsonl 的既有行数 + 1`，传 `stdout_log_path = runs/<run_id>/attempt<attempt_no>/stdout.log`（目录自动创建）。结果行写 `attempt=attempt_no`。
- `scan_cell_log`（H2）只扫**本 attempt** 的日志文件；`scan_cell_rewards` 的窗口过滤（`_in_window`）不变（天然只算本轮 `received_at`）。

### 2.2 熔断器

orchestrator 模块级常量：

```python
CIRCUIT_BREAK_MARKERS = ("402", "Insufficient Balance", "insufficient balance",
                         "insufficient_user_balance", "insufficient_quota",
                         "Connection error", "APIConnectionError")
CIRCUIT_BREAK_DURATION_S = 30.0
CIRCUIT_BREAK_RUN_LIMIT = 2        # 连续熔断格数 → 中止整个 stage
```

- attempt 级：`_execute_cell` 收尾时，若 `runner_status ∈ {timeout, no_junit}` 且 `duration_s < CIRCUIT_BREAK_DURATION_S` 且本 attempt stdout.log 含任一 marker → 行内 `infra_circuit_break=True`、该 cell **不进入** `_retry_infrastructure_failures` 的重试池（重试筛选处显式排除）。
- run 级：orchestrator 维护 `consecutive_breaks` 计数（遇非熔断行清零）；`>= CIRCUIT_BREAK_RUN_LIMIT` → 抛 `BenchmarkError`，`run()` 的 finally 停服务、已写行保留、exit 2。
- 熔断不影响 `status`/`official_passed` 判定（仍按 §1.1 规则；熔断行多为 no_junit/timeout 负例）。

## 3. C1c 余额预检（新模块 preflight.py）

### 3.1 行为定义

新文件 `record2gherkin/benchmark/preflight.py`：

```python
@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str            # 已脱敏（经 runner_module.mask_secret）

def probe_llm(*, api_key: str, model: str, base_url: str,
              timeout_s: float = 30.0) -> ProbeResult:
    """1-token 探测，transport 与引擎同栈（review-r2 M4）：
    ChatOpenAI(model=model, api_key=api_key, base_url=base_url,
               max_tokens=1, timeout=timeout_s, max_retries=0
               ).invoke([HumanMessage(content="ping")])
    ——引擎实际栈即 langchain-openai ChatOpenAI 直连 base_url。
    禁用 litellm：自定义端点需 provider 前缀（openai/...）与 api_base，
    裸模型名会恒败（把可用 key 误判为失败），且调通也不构成对引擎路径的证明。
    任何异常 → ok=False，detail=mask_secret(str(exc))。"""
```

- orchestrator `main()`：非 dry-run 时，在 `start_miniwob_server` 之前调用；`ok=False` → `logger.error("preflight failed: %s", detail)` + 返回 **exit 3**。headline 需探测**两个模型**（planner 模型与 `--nav-model`，C5 off 时只探 planner 模型）。
- key 经 `runner_module.read_api_key()` 读取、仅作函数参数；detail 一律过 `mask_secret`；探测请求不写任何文件。
- 单测：monkeypatch `ChatOpenAI.invoke`，断言成功/402/连接错误三路 exit code 与脱敏；真实网络零调用。

## 4. C2 终局信号 + C3 单次开局（miniwob_server 补丁层）

### 4.1 CLI 与补丁组装

- `miniwob_server` 模块新增启动参数：`--terminal-cue`（默认 off）、`--single-start`（默认 off）。
- `patch_core_js(source, *, terminal_cue: bool, single_start: bool)`：
  - `single_start=True` 时补丁 A 采用 `AUTO_START_PATCH_SINGLE`（§4.2 全文），否则用现状 `AUTO_START_PATCH`（逐字节不变）；
  - `terminal_cue=True` 时在 reward hook 之后追加 `TERMINAL_CUE_PATCH`（§4.3 全文）。
  - 标记注释：`__R2G_PATCH_SINGLE_START__`、`__R2G_TERMINAL_CUE__`（供测试断言）。
- orchestrator：CLI 增 `--terminal-cue` / `--single-start` / `--role-routing` / `--nav-model` / `--extra-tools` / `--template-notes` / `--latency-env`（均 store_true，`--nav-model` 默认 `deepseek-flash`）；另增 `--smoke-cells <subdomain[,subdomain…]>`（默认空，仅允许与 `--stage pilot` 组合）：向 pilot 计划**追加**命名 cell（C6 drag 冒烟专用——`PILOT_SUBDOMAINS` 不含 drag 格，无此机制冒烟验收不可满足，review-r2 M3），追加 cell 不进重试池、计入预算校验。`Orchestrator` 持有并传给 `start_miniwob_server` 与 child env；manifest 新增 `flags` 对象记录全部开关布尔、nav 模型名与 smoke-cells 清单。**URL、seed、episode_ms 语义零变化**（flag 是伺服器级，不进 URL）。

### 4.2 `AUTO_START_PATCH_SINGLE`（single_start 时的补丁 A，逐字嵌入）

在现状补丁 A 基础上**仅**增加两处（其余逐字节保持）：

```js
  var seed = q("r2g_seed");
  if (seed === null) return;
  try {
    if (sessionStorage.getItem("r2g_started") !== null) {
      /* 被拦截加载面同样应用 V2 加固（review-r2 M2）：拦截态下 START 覆盖层可点，
         onclick 在点击期经 core.startEpisodeReal() 动态取函数（core.js:78-80），
         会重开无 Math.seedrandom(seed) 的随机实例并 POST 新 reward 记录，
         经 /latest last-wins 覆盖本格已有终局——必须同层封死。
         stub 在补丁求值期执行（core 此刻已定义，无需等 cover_div）。 */
      core.startEpisodeReal = function () {};
      core.startEpisode = function () {};
      core.updateDisplay = function () {};
      var harden = document.createElement("style");
      harden.textContent = "#reward-display, #sync-task-cover { display: none !important; }";
      (document.head || document.documentElement).appendChild(harden);
      return;
    }
  } catch (e) {}
  /* ↑ 新增①：本 tab 已开局过 → 本次加载不自动开局（页面停在未开局状态，不产生 reward 记录），
     且拦截态与开局成功态受同等 V2 加固（stub + CSS 隐藏，双保险不依赖 onclick 绑定形态） */
```

```js
        clearInterval(timer); /* 只在开局成功后停止轮询 */
        try { sessionStorage.setItem("r2g_started", seed); } catch (e) {}
        /* ↑ 新增②：开局成功登记（同 tab 二次 load 被 新增① 拦截） */
```

- sessionStorage 按 tab 隔离：同 tab reload 被拦截（r1 的 93% 重导航路径）；`force_new_tab` 拿新 sessionStorage 仍开局——**残余缺口，报告披露并按既有 `task_url_navigations` 扫描计数**。
- read_goal 预读用独立 sync_playwright context（每次新开浏览器），sessionStorage 不跨 context，预读不受影响（单测 T6b 锁定）。
- 语义披露（写进 test-report）：开启后官方 last-wins 实际退化为"每 tab 唯一一次开局的页面终局"；r1 格内洗白为 0，故该通道封死不改变任何可回放成绩。

### 4.3 `TERMINAL_CUE_PATCH`（terminal_cue 时追加，逐字嵌入）

```js
/* __R2G_TERMINAL_CUE__ */
(function () {
  var orig = core.endEpisode;
  if (typeof orig !== "function") return;
  core.endEpisode = function () {
    var ret = orig.apply(this, arguments);
    try {
      var cue = document.getElementById("r2g-terminal-cue");
      if (!cue) {
        cue = document.createElement("div");
        cue.id = "r2g-terminal-cue";
        cue.style.cssText =
          "position:fixed;left:8px;bottom:8px;font:12px monospace;color:#888;" +
          "background:#fff;padding:2px 6px;z-index:2147483647;";
        document.body.appendChild(cue);
      }
      cue.textContent = "EPISODE ENDED"; /* 中性：无数值、无成败、恒定文本 */
    } catch (e) { /* 绝不干扰 episode 本身 */ }
    return ret;
  };
})();
```

- 中性红线：文本恒为 `EPISODE ENDED`，禁止包含 `raw`/`reward`/`done`/成败词（单测 T5 断言 `endEpisode(1)` 与 `endEpisode(-1)` 后 `body.innerText` 的 cue 区域文本一致且仅此一词）。
- 与补丁 B 叠加顺序：补丁顺序为 原文 → 补丁 A(single) → REWARD_HOOK → TERMINAL_CUE；两个 wrapper 均执行，互不影响 POST。
- 可见性：cue 不用 `display:none`（`innerText` 必须可见）；fixed 角落定位不遮 `#query`；HUD 加固（`display:none` + stub）保持不变，`Last reward` 等文本仍零命中（单测 T5 复验）。
- 判分不受影响：cue 不 POST、不改 `WOB_RAW_REWARD_GLOBAL`；单测 T5 断言 `/latest` 与 r1 行为逐字节一致。

## 5. C4 延迟包（testzeus_hercules，两个独立 commit）

### 5.1 Commit 1：引擎代码（C4a + C4b）

**C4a** `testzeus_hercules/core/simple_hercules.py`：

- `config.py` 键表注册新键 `BROWSER_STATE_REFRESH_MODE`（env 同名，默认 `"always"`；经 `get_global_conf()` 读取，禁裸 `os.getenv`）。
- `_requires_state_refresh`（L662-675）改为：

```python
mode = (get_global_conf().get_config().get("BROWSER_STATE_REFRESH_MODE") or "always").strip().lower()
if mode not in {"always", "markers_only"}:
    mode = "always"
# markers 命中 → 永远打断（两个模式一致）
# mode == "always" → 现状逻辑逐字保留（成功状态变更工具打断，错误不打断）
# mode == "markers_only" → 成功状态变更工具不再打断（批内工具全部执行完）
```

- 执行循环（L940-991）不改结构；`markers_only` 下 `refresh_required` 仅由 markers 触发，跳批逻辑与提示语原样。

**C4b** 步级预算：

- `config.py` 注册 `BROWSER_NAV_MAX_CHAT_ROUND`（默认 `50`，即 r1 行为）与 `NAV_STEP_TIME_BUDGET_S`（默认 `0` = off）。
- `testzeus_hercules/core/runner.py::BaseRunner.__init__`：`browser_nav_max_chat_round` 缺省值改为从 config 读取（无配置时 50，行为不变）。
- `simple_hercules.py::_run_nav_agent` 轮循环（L910 `for _turn in range(...)`）前记 `step_started = time.monotonic()`；每轮顶部：预算 >0 且已超 → 返回 `f"{last_content}\n[NAV_STEP_BUDGET_EXHAUSTED] step budget {NAV_STEP_TIME_BUDGET_S}s reached"`（planner 可辨识、可决定继续/换路/终止；不是异常）。轮数上限触发的现有返回文本保持。

### 5.2 Commit 2：prompt 包（C4c + C4d + C4e）

逐点（全部 `testzeus_hercules/core/agents/` 内文本改动）：

1. `browser_nav_agent.py` L55 *"To refresh a page, open the same URL again using the appropriate navigation tool"* → *"Do not reopen the task URL to 'refresh': it restarts the task and discards all progress. When stuck, re-inspect with get_interactive_elements / get_page_text, or report the blocker honestly."*（按引文文本唯一定位；review-r2 M5-3 行号勘误 L20→L55）
2. `browser_nav_agent.py` L116 *"When a page refresh is needed, navigate to the current URL again using the appropriate tool"* → 删除，替换为 *"Never navigate to the current URL again as a retry; the page state must be repaired in place."*
3. `browser_nav_agent.py` L41 rule 8（ALWAYS analyze ALL … FIRST）放宽：*"Use the DOM snapshot already present in this step's context when it is sufficient; re-perceive only after state changes or when data is missing."*
4. nav 与 planner system prompt 末尾各加输出克制句：*"Keep responses short: list actions and results only; no long reasoning prose."*
5. `high_level_planner_agent.py`：Closure Nudge Examples（L198-204）与 Critical Rule 5（L300）改为——*"Terminate once the helper reports the requested action (including any required submit) is done; do not append a separate verification step for single-action tasks; do not ask the helper to look for on-page success indicators (this environment shows none)."*；系统提示删 Platform Awareness（Salesforce/SAP）、Test Data Focus/Iteration、Executor Operation Detection 章节与 `_json_instruction` 的重复 terminate 规则（保留通用句）。
6. planner 侧加一句：*"Do not use re-navigation to the task URL as a retry strategy."*

### 5.3 C4f 请求韧性（orchestrator env，flag `--latency-env`）

`build_child_env` 不改（它保持通用）；orchestrator 在 `--latency-env` 时经 `run_feature(..., extra_env=...)` 合并注入：

```
LLM_REQUEST_TIMEOUT=90    # 默认 60（llm_helper.py:21）；延迟尖峰不再 60s 即掐
LLM_MAX_RETRIES=2         # 默认 1（llm_helper.py:22）；请求级快速重试，替代 planner 层 "timed out, retry" 改写
BROWSER_STATE_REFRESH_MODE=markers_only
BROWSER_NAV_MAX_CHAT_ROUND=30
NAV_STEP_TIME_BUDGET_S=120
```

（engine 三键也由同一 flag 注入，保证"一个 flag = 延迟包整体可开关"；逐键仍在 engine 侧可独立覆写。）

## 6. C5 分角色路由（flag `--role-routing`，orchestrator）

### 6.1 配置文件生成

- 路径 `<exp_dir>/agents_llm_config.json`（gitignore 区）。结构（`ConfigFileLoader` 兼容形态，参考 `agents_llm_config-example.json.txt`）：

```json
{
  "litellm": {
    "planner_agent": {"model_name": "deepseek-v4-pro", "model_base_url": "<runner.LLM_MODEL_BASE_URL>",
                       "model_api_type": "<runner.LLM_MODEL_API_TYPE>",
                       "llm_config_params": {"temperature": 0.0, "cache_seed": null}},
    "nav_agent":     {"model_name": "<nav_model>",    "model_base_url": "…同上…", "model_api_type": "…", "llm_config_params": {"temperature": 0.0, "cache_seed": null}},
    "helper_agent":  {"model_name": "deepseek-v4-pro", "model_base_url": "…同上…", "model_api_type": "…", "llm_config_params": {"temperature": 0.0, "cache_seed": null}}
  }
}
```

- **`model_api_key` 一律省略**（红线）。`nav_model` = `--nav-model`（默认 `deepseek-flash`），预检（§3）未过即中止。
- 覆盖语义：File 优先于 Env（`agents_llm_config_manager.initialize`），故三角色模型名以文件为准；key 由 env 回退补齐。

### 6.2 child env 注入（extra_env 合并）

```
AGENTS_LLM_CONFIG_FILE=<abs agents_llm_config.json>
AGENTS_LLM_CONFIG_FILE_REF_KEY=litellm
MODEL_API_KEY=<key>            # create_chat_model 的回退键（llm_helper.py:85）；nav/helper 走此路径
OPENAI_API_KEY=<key>           # planner 裸 ChatOpenAI 专用（review-r2 M1）；与 MODEL_API_KEY 同值
```

- 引擎侧消费链已核对：`BaseRunner.initialize` 读 `planner_agent/nav_agent/helper_agent` 三配置 → `SimpleHercules._initialize_agents` 中 `nav_agent` 配置驱动全部 nav/executor agents（`simple_hercules.py:176-199`）→ 路由 nav 即覆盖执行循环；helper（image-comparer）在文本 DOM benchmark 中不触发。**planner 走裸构造 `ChatOpenAI`（`high_level_planner_agent.py:37-57`，不经 `create_chat_model`），只从显式参数或 `OPENAI_API_KEY` env 解析 key；且配置文件加载成功后 env provider 完全不注册（`agents_llm_config_manager.py:119-175` 直接 return，File > Env 是源级覆盖）**——`MODEL_API_KEY` 只救 nav/helper，缺 `OPENAI_API_KEY` 则 planner 构造/首调即抛缺 key，headline 报废（review-r2 M1）。
- 单测 T7：生成器输出的 JSON 不含 `sk-`/key 实值、三角色 model_name 正确、env 注入键集合精确（多一个键都不行）。

## 7. C6 拖拽工具 + C7 模板上下文

### 7.1 C6（flag `--extra-tools`）

- `extra_env` 增注 `LOAD_EXTRA_TOOLS=true`。已核对事实：`core/extra_tools/__init__.py:11` 以该键门控全量动态 import（含 `drag_and_drop`/`read_clipboard` 等）；`simple_hercules.py:31` 无条件 `from ...extra_tools import *`；`drag_and_drop` 已在 `_BROWSER_STATE_CHANGING_TOOLS`（`simple_hercules.py:556`）。不改 extra_tools 任何代码。
- 冒烟验收（运行期）：经 `--smoke-cells drag-items,drag-box` 执行（§4.1 机制），两格 stdout.log 出现 drag_and_drop 调用且无异常注册；出现异常 → 关 flag 重跑该两格并把工具面异常记入报告。

### 7.2 C7（flag `--template-notes`）

- `goal_reader.render_feature` 增加关键字参 `notes_terminal_cue: bool = False`；在 `Then` 行之前追加注释段（注释行不影响 gherkin 解析；单测复验可解析）：

```
  # Notes: The instruction above is also displayed at the top of the page (#query).
  # This is a single-page task: do not reload or navigate away; all progress would be lost.
  # The page shows no success or failure message. Once you have performed the requested
  # action (including any required submit), report completion immediately.
```

- `notes_terminal_cue=True`（且 `--terminal-cue`）时追加第 5 行：`  # A small "EPISODE ENDED" note in the corner means this episode is over (pass or fail alike): stop and report immediately.`——C2 off 时该行**不得**出现（虚假信息；单测 T8 锁定两个变体）。
- When 指令原文逐字不动；不做任务特定提示。

## 8. 离线单测用例清单（`tests/record2gherkin/benchmark/`，不跑真 LLM；浏览器组仅回环）

**T1 判分回放锁 +5（test_results_metrics.py 或新 test_r1_replay.py）**
- fixture `r1_replay.json` 输入 → 5 救援格 official_passed；email-inbox-delete 维持**非 official_passed**（按 §1.1 落 `status=timeout`，metrics 判 0——非 `official_failed` 字面值）；一致格/infra 格不变；125 行摘要重算官方合计 **59**；`runner_status` 键落位。
- 边界：`raw=0.537>0` 且 runner timeout → passed；`raw=-1` + junit passed → official_failed + disagreement；`runner_status=None` → no_goal（优先级最高）；`reward=None` 且无 infra 状态 → no_reward。

**T2 结果行 schema**：行键集合 == `metrics.ROW_KEYS`（更新后含 `runner_status`/`attempt`/`infra_circuit_break`）；指标函数对新键零依赖（不进分母）。

**T3 熔断器**：duration<30 + marker 命中 + no_junit → `infra_circuit_break=True` 且不进重试池；marker 不命中 → 正常重试；连续 2 格熔断 → `BenchmarkError`；熔断行 status 仍按 §1.1（不被改写）。

**T4 预检**：monkeypatch `ChatOpenAI.invoke`（§3.1 同栈 transport 面），断言成功/402/连接错误 → orchestrator exit 0/3/3；detail 含 key 实值时被 mask；dry-run 不触发探测。

**T5 终局标记（浏览器组）**：`--terminal-cue` 伺服器下——(a) `endEpisode(1)` 与 `endEpisode(-1)` 后 `#r2g-terminal-cue` 文本恒 `EPISODE ENDED` 且两次一致；(b) `body.innerText` 零 `Last reward|Time left|Episodes done|START`（r1 加固回归）；(c) `/latest` 记录与不加 cue 的伺服器逐字段一致（POST 不受影响）；(d) cue 幂等（重复 endEpisode 仍单实例单文本）；(e) flag off 时 core.js 不含 `__R2G_TERMINAL_CUE__`。

**T6 单次开局（浏览器组）**：(a) `--single-start` 下同 context 二次 `goto(同 URL)` → 第二次不开局（`WOB_TASK_READY` 后无 `core.ept0` 更新、无新 reward 记录）；(b) 预读路径（独立 context）不受影响：utterance 正常读取；(c) flag off 时二次 load 行为与 r1 一致（自动重开局）；(d) `patch_core_js` 字节断言：single 变体仅在指定三处与现状补丁 A 不同，开局成功分支的安全加固四行逐字保留；(e) **拦截态加固（review-r2 M2）**：二次 load 后普通 DOM 点击 `#sync-task-cover` → 无任何新 reward 记录、页面保持未开局（`core.ept0` 不更新）；拦截态 `body.innerText` 零 `START|Last reward|Time left|Episodes done` 命中。

**T7 路由配置构造**：生成器产物 JSON 合法、无 `model_api_key`/`sk-`、三角色模型名与 `--nav-model` 一致；child env 注入恰含 `AGENTS_LLM_CONFIG_FILE`/`AGENTS_LLM_CONFIG_FILE_REF_KEY`/`MODEL_API_KEY`/`OPENAI_API_KEY` 四键（后两者同值；多一个键都不行）；flag off 时不生成文件、不注入。

**T8 模板变体**：`render_feature(notes_terminal_cue=False/True)` 两变体快照锁定；gherkin 解析入口通过；When 原文逐字不变；C2 off + notes on 时无 EPISODE ENDED 行。

**T9 引擎守卫（simple_hercules 单测）**：`markers_only` 下成功 click 不打断批内（`refresh_required=False`、剩余 tool calls 全执行）；markers 命中仍打断；`always`（默认）行为与现状一致（回归）；非法模式值回落 `always`。步级预算：超限返回文本含 `[NAV_STEP_BUDGET_EXHAUSTED]`；预算 0 时不触发。

**T10 attempt 日志**：`run_feature(stdout_log_path=...)` 落盘指定路径、缺省路径回归不变；重试第二次 attempt 写 `attempt2/`；`scan_cell_log` 对 `attempt2/` 文件计数、结果行 `attempt=2`。

**T11 manifest flags 与冒烟计划**：全开运行命令 → manifest `flags` 记录全部布尔、nav 模型与 smoke-cells 清单；默认运行 → flags 全 false；`--smoke-cells drag-items,drag-box` 时 pilot 计划恰 12 格（10 + 2 追加）、追加 cell 不进重试池、`hercules_budget` 合计按 `R2_BUDGET_CAP=144` 校验通过且超限仍硬拦。

## 9. 验收标准

1. `uv run pytest tests/record2gherkin/benchmark -q` 全绿；`make fmt` 后 `--check` 通过。
2. 两个引擎 commit（§5.1/§5.2）独立存在、各自可 revert 而不破坏 harness 测试；harness 改动不触碰引擎判分路径。
3. T1 回放锁 +5 与反例锁定通过（判分逻辑改动的可回放性证明）。
4. 预检 gate：无 key / 402 环境（可用假 key 模拟 402 路径的单测 + 手动一次真实预检）下 exit 3、零执行。
5. 冒烟 pilot + drag 冒烟（14 次执行 = pilot 10 + 重试 2 + `--smoke-cells` drag 2，全开 flags）：12 行结果；drag 冒烟 2 格日志出现 drag_and_drop 调用；flash 输出无失控（completion 无异常长文）；单次开局生效（重导航格 `task_url_navigations>1` 但无新 reward 记录）；无 402。
6. headline full（130 次执行，用户确认充值后）：125 行、manifest 含 `flags` 与 `metrics`；总执行 ≤144。
7. test-report.md 固化：r1 三口径对照 + r2 官方口径 + §6 披露清单（plan-r2.md §6）原文全量出现；`runner_status` 救援格名单、`flagged` 计数、attempt 覆盖情况披露。
8. 脱敏红线复核：`KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs record2gherkin tests dev_docs` 零命中（含新生成的 agents_llm_config.json）。

## 10. Out of Scope

r1 spec §11 全部沿用，另加：

- 画布几何/视觉感知 11 格的任何能力补齐（视觉模型、截图感知）；H8 stop-at-terminal；480s 计时进 headline（仅 A4 ablation 位，默认不跑）；任务特定 Gherkin 提示（日期示例值、控件玩法教学）；planner 模型升级；`fetch_reward` 语义改动（last-wins 保持）；engine 侧 `_requires_state_refresh` 以外的执行器循环重构；ablation 的自动执行（必须用户逐项批准）。
