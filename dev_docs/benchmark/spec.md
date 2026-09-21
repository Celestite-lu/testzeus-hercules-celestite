# 公开基准评测（MiniWoB++）spec — 实现规格

> 读者为实现者。读完本文不再需要做任何设计决策；未规定处按"最简单确定性行为"处理并记入实现说明。
> 契约来源：同目录 `plan.md`；执行器复用依据 `dev_docs/evaluation/spec.md` §4 与 `record2gherkin/evaluation/runner.py` 现状。
> 密钥红线：`LLM-Key.txt` 内容（单行，deepseek API key）**不得出现在任何文档、代码、日志、argv、测试固件中**，只能经 subprocess `env=` 注入。
> 事实基线：可行性探测已本机验证（勿重新设计）——`WOB_TASK_READY` 可等；页面不自动开局（EPISODE_ID=0），需 `Math.seedrandom(<seed>); core.EPISODE_MAX_TIME=<ms>; core.startEpisodeReal()`；指令 = `core.getUtterance()`（同 seed 确定性复现）；成功 = `WOB_RAW_REWARD_GLOBAL > 0`；原生计时器 10s（`core/core.js:50` 单点定义）。

## 0. 评测口径与目录（最高优先级）

**口径四条（报告必须原样披露，任何代码不得掩盖）**：

1. 本基准测**执行内核能力**（自然语言任务 → Gherkin → Hercules 语义化执行），不含录制/蒸馏（exp001 已覆盖）。
2. `EPISODE_MAX_TIME` 由原生 10s 放宽至 240s（默认，参数化），沿用 BrowserGym `episode_max_time` 可配的做法；若 pilot 后调整，报告须披露最终值与理由。
3. 每 task 单 seed 单次，非官方多 instance 均值口径。
4. **官方成败以页面原生奖励为唯一权威**（`reward_raw > 0`）；Hercules JUnit 结果与奖励不一致的行如实标记 disagreement，不改判。

与公开基线对比只引用可查证数字并注明设置差异（模型/观察空间/时间限制）。排行榜式图表、逐任务截图集、自动失败分析一律不做。

```
record2gherkin/benchmark/
  __init__.py
  vendor_miniwob.py    # 构建期（§1）
  build_task_table.py  # 构建期（§2）
  tasks.py             # 任务表加载 / seed 派生 / pilot 子集（§2.3）
  miniwob_server.py    # 伺服 + 补丁 + 收集端点（§3）
  goal_reader.py       # goal 预读 + 清洗 + Gherkin 渲染（§4、§5）
  orchestrator.py      # 编排 CLI（§7）
  metrics.py           # 指标（§8）
  miniwob_html/        # vendored html 树（生成后入库）
  tasks.json           # 静态任务表（生成后入库）
  PROVENANCE.md        # 来源与许可（生成后入库）
tests/record2gherkin/benchmark/
  test_tasks.py  test_server_patch.py  test_reward_endpoint.py
  test_goal_gherkin.py  test_results_metrics.py  test_orchestrator.py
  test_browser_miniwob.py          # 浏览器组：仅本机回环 + 本地 vendored 资产，无浏览器环境时整体 skip
dev_runs/benchmark/<exp_id>/       # 运行产物（gitignore，§7.4）
```

不新增第三方依赖：服务用 `http.server`；浏览器操作复用仓库已有 `playwright`（`sync_playwright`，与 `evaluation/baseline.py` 同模式）；XML/JSON 用标准库。构建期依赖（`miniwob`、`browsergym-miniwob`）只经 `uv run --no-project --with <pkg>` 临时安装，**不进 pyproject**。

## 1. vendoring：miniwob_html/

1.1 `vendor_miniwob.py`（构建期脚本，直接 `python` 运行）：

```bash
uv run --no-project --with miniwob python record2gherkin/benchmark/vendor_miniwob.py \
  --dest record2gherkin/benchmark/miniwob_html
```

- 定位包内 HTML 根：`import miniwob; Path(miniwob.__file__).parent / "html"`；**整树**复制到 `--dest`（含 `miniwob/*.html` 130 个任务页、`core/`、`flight/` 等 book-flight 同源 iframe 依赖，全套约 5.6MB）。目标已存在时清空后重建（幂等）。
- 生成 `PROVENANCE.md`：源包名与版本（`importlib.metadata.version("miniwob")`）、上游 `https://github.com/Farama-Foundation/miniwob-plusplus`、许可 **BSD-3-Clause**、复制命令、文件数与总字节数、全部文件按相对路径排序后的 sha256（漂移检测用）。
- 脚本不做任何内容改写（补丁在伺服期做，§3.2）。

1.2 产物入库：`miniwob_html/`（整树）与 `PROVENANCE.md` 提交；验收时按 PROVENANCE 记录核对文件数与字节数。运行期伺服根 = 该目录。

## 2. 静态任务表 tasks.json

2.1 构建（构建期一次性，产物入库）：

```bash
uv run --no-project --with browsergym-miniwob python record2gherkin/benchmark/build_task_table.py \
  --html-root record2gherkin/benchmark/miniwob_html --dest record2gherkin/benchmark/tasks.json
```

- 枚举 `from browsergym.miniwob import ALL_MINIWOB_TASKS`（包级列表，125 个任务类），每类取 `get_task_id()`、`subdomain`、`desc`。**不可**用 `browsergym.miniwob.all`——它是子模块，迭代会直接 `TypeError: 'module' object is not iterable`（review 实测确认）。
- **校验硬失败**：task_id 数 ≠ 125、task_id 重复、或 `html_root/miniwob/<subdomain>.html` 不存在 → 非零退出，不写产物。
- 生成后自检 family/visual 派生（§2.2）并打印分布摘要。

2.2 schema（字段名固定；顶层对象 + `tasks` 数组，每任务一行）：

```json
{
  "generated_at": "ISO8601",
  "source": "browsergym-miniwob <version>, registry ALL_MINIWOB_TASKS (125 tasks)",
  "episode_max_time_ms_default": 240000,
  "tasks": [
    {
      "task_id": "miniwob.click-test",
      "subdomain": "click-test",
      "family": "click-test",
      "visual": false,
      "desc": "<browsergym 描述原文>",
      "html": "miniwob_html/miniwob/click-test.html"
    }
  ]
}
```

- `family`（报告聚合桶，不做语义声称）：`re.sub(r"-\d+$", "", subdomain)`，只剥离末段纯数字（`click-test-2` → `click-test`；`email-inbox-forward-nl-turk` 保持原样）。构建脚本落表，单测锁定。
- `visual` = `subdomain.startswith("visual-")`。仅用于报告披露分组，**不做任何豁免**（§11）。
- 数组按 `task_id` 字典序排序（确定性）。

2.3 `tasks.py`：

- `load_tasks(path=包内 tasks.json) -> list[dict]`（只读校验：125 行、id 唯一，否则抛 `BenchmarkError`）。
- `derive_seed(exp_id: str, task_id: str) -> int` = `int(sha1(f"{exp_id}:miniwob:{task_id}").hexdigest()[:8], 16)`。
- `make_run_id(subdomain: str, seed: int) -> str` = `f"miniwob__{subdomain}__s{seed}"`。
- `PILOT_SUBDOMAINS`（恰 10 个，硬编码于本模块，覆盖 ≥5 个 family 且含 ≥1 个 visual）：
  `click-test, click-button, click-checkboxes, enter-text, enter-password, focus-text, login-user, use-autocomplete, email-inbox-forward-nl, visual-addition`。任一不在全表内 → 启动即报错（上游改名时的显式失败，不静默缩量）。registry 中唯一的 visual 任务是 `visual-addition`（review 全量核对 125 个 id：不存在 `visual-maze`，pip 包 html 树中亦无该文件）。

## 3. 补丁式 HTTP 伺服器 miniwob_server.py

3.1 端口与生命周期

- `ThreadingHTTPServer`（标准库）绑定 `127.0.0.1:8462`（`DEFAULT_PORT`，模块级可配；启动前探测占用，被占报错退出——URL 是实验参数，不自动换端口）。
- 启动方式：orchestrator 以 `subprocess.Popen([sys.executable, "-m", "record2gherkin.benchmark.miniwob_server", "--root", <abs>, "--port", <port>, "--rewards-file", <abs>])` 启停（finally 兜底 terminate）；单测可线程内直起实例。独立命令同上，便于手动验证。
- 所有端点响应带 `Cache-Control: no-store`。

3.2 运行时补丁（核心机制，补丁文本固定如下）

仅对路径以 `/core/core.js` 结尾的 GET 生效：响应体 = vendored 文件原文 + **纯追加**两段补丁（不做文件内替换，避免锚点漂移；追加内容在原文件所有定义之后执行）。其余路径按字节原样伺服。补丁文本（实现时逐字嵌入 `miniwob_server.py` 常量，含标记注释供测试断言）：

补丁 A（auto-start）：

```js
/* __R2G_PATCH_START__ */
(function () {
  function q(name) {
    var m = new RegExp("[?&]" + name + "=([^&#]*)").exec(location.search);
    return m ? decodeURIComponent(m[1]) : null;
  }
  var seed = q("r2g_seed");
  if (seed === null) return;
  var ms = parseInt(q("r2g_ms") || "240000", 10);
  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    if (window.WOB_TASK_READY === true && document.readyState === "complete" && core.cover_div) {
      try {
        Math.seedrandom(seed);
        core.EPISODE_MAX_TIME = ms;
        core.startEpisodeReal();
        clearInterval(timer); /* 只在开局成功后停止轮询 */
      } catch (e) {
        if (tries > 200) { clearInterval(timer); window.__R2G_START_ERROR = String(e); }
      }
    } else if (tries > 200) {
      clearInterval(timer);
      window.__R2G_START_ERROR = "WOB_TASK_READY timeout";
    }
  }, 50);
})();
/* __R2G_PATCH_END__ */
```

补丁 B（reward hook）：

```js
/* __R2G_REWARD_HOOK__ */
(function () {
  var orig = core.endEpisode;
  if (typeof orig !== "function") return;
  core.endEpisode = function () {
    var ret = orig.apply(this, arguments);
    try {
      var m = new RegExp("[?&]r2g_seed=([^&#]*)").exec(location.search);
      var payload = {
        path: location.pathname,
        seed: m ? decodeURIComponent(m[1]) : null,
        reward: arguments.length > 0 ? arguments[0] : null,
        raw: window.WOB_RAW_REWARD_GLOBAL,
        done: window.WOB_DONE_GLOBAL,
        reason: arguments.length > 2 && arguments[2] != null ? String(arguments[2]) : "",
        ts: new Date().toISOString()
      };
      var x = new XMLHttpRequest();
      x.open("POST", "/__r2g_reward", false); /* 同步：确保页面销毁前送达 */
      x.setRequestHeader("Content-Type", "application/json");
      x.send(JSON.stringify(payload));
    } catch (e) { /* 绝不干扰 episode 本身 */ }
    return ret;
  };
})();
```

行为要点：

- `(task, seed)` 唯一确定开局；无 `r2g_seed` 的请求不自动开局（不干扰人工调试）。
- `r2g_ms` 缺省 240000（§0 口径第 2 条的参数化出口）。
- 轮询条件 = `WOB_TASK_READY === true && document.readyState === "complete" && core.cover_div`：只等 `WOB_TASK_READY` 不够（core.js:49 默认即 `true`），会在重页面抢在 `onload` 前调 `startEpisodeReal()`，依赖此时还未创建的 `cover_div`/`click-canvas`——实测 book-flight 抛 `ReferenceError: ui_utils is not defined` 后永久失去自动开局（review 必改 1.4）。`clearInterval` 只在开局成功后执行，try 失败可重试至 200 次（~10s）；重试中的重复 `Math.seedrandom(seed)` 同 seed 恒等，不破坏确定性。
- wrapper 先执行原 `endEpisode`（终局状态落定）再读全局并同步 POST；成功口径 = `raw > 0`（BrowserGym 同口径）。
- 页面 240s 计时到点，core 自身以 `core.endEpisode(-1, false, 'timed out')`（core.js:101）结束 → 也会 POST（`done=true, raw=-1, reason="timed out"`，review 实测收到），harness 无需自己判断 episode 超时。

3.3 端点契约

| 方法/路径 | 行为 |
|---|---|
| `GET /miniwob/<file>.html` 及 vendored 树内任意文件 | 原样伺服字节；不存在 → 404；拒绝目录穿越（规范化后必须仍在 root 内）；无目录列表 |
| `GET /core/core.js`（含任意 query） | vendored 原文 + 追加补丁 A、B；`Content-Type: text/javascript` |
| `POST /__r2g_reward` | body 为 JSON；**必须含非空 `path` 与非空 `seed`**，否则 400 不落盘；合法 → 追加一行到 `--rewards-file`（JSONL，逐行 flush），并更新内存 `(path, seed) -> record` last-wins，返回 `200 {"ok": true}` |
| `GET /__r2g_reward/latest?task=<subdomain>&seed=<n>` | 按 path 末段 `<subdomain>.html` 与 seed 匹配内存最新记录；命中 200 返回该记录，未命中 404 `{"error": "no_reward"}` |
| `GET /healthz` | `200 {"served_root": <abs>, "patched": true, "rewards": <已收条数>}` |

- 记录体即 §3.2 payload 原样（服务端不加工，另补 `received_at`）。
- harness 自身对该服务的 HTTP 调用必须显式禁用系统代理（`urllib.request.build_opener(urllib.request.ProxyHandler({}))`；exp001 test-report 已知问题 11）。

## 4. goal 预读器 goal_reader.py

4.1 `read_goal(subdomain: str, seed: int, *, port: int, episode_ms: int, timeout_s: float = 30) -> str`

- `sync_playwright` 起 chromium（headless），`page.goto(f"http://127.0.0.1:{port}/miniwob/{subdomain}.html?r2g_seed={seed}&r2g_ms={episode_ms}")`。
- `page.wait_for_function("() => window.WOB_TASK_READY === true", timeout=15000)`；auto-start 补丁随即开局；再 `page.wait_for_function("() => typeof core !== 'undefined' && !!core.getUtterance()", timeout=10000)`。
- goal = `page.evaluate("core.getUtterance()")`；**返回值有两种类型**：多数任务为 str，email-nl 类任务（实测 `email-inbox-forward-nl` 等）为 dict——非 str 时取 `["utterance"]` 键（与 browsergym `base.py::_get_goal` 同规则），得到 str 后经 `sanitize_goal` 返回；读毕立即关闭 browser。**不**触发 `endEpisode`，预读页不会产生 reward 记录。
- 同 seed 确定性 ⇒ 预读 utterance == Hercules 执行侧utterance（探测已验证，浏览器组单测再锁一次）。
- 失败（页面加载/utterance 为空）抛 `GoalReadError`：该 cell 记 `no_goal` 结果行（status 同 no_reward 族，official_passed=False），不重试、不阻塞后续 cell。

4.2 `sanitize_goal(text: str) -> str`：`"`→`'`；换行与连续空白折叠为单个空格；strip。纯函数。

## 5. Gherkin 模板与变量

5.1 模板（唯一形态，逐字）：

```gherkin
Feature: MiniWoB++ <task_id>
  Scenario: <subdomain> seed=<seed>
    Given I am on the page "http://127.0.0.1:<port>/miniwob/<subdomain>.html?r2g_seed=<seed>&r2g_ms=<episode_ms>"
    When <sanitized goal 原文>
    Then the task should be completed successfully
```

- 文件落 `<exp_dir>/features/<run_id>.feature`；`run_id = miniwob__<subdomain>__s<seed>`。
- 不含 `{{TEST_DATA:`、`<masked>`、多 Scenario。
- `Then` 行是 planner 的意图陈述；**官方成败不看它**（§0 第 4 条），disagreement 机制记录两者差异。

5.2 `render_feature(*, task_id, subdomain, seed, port, episode_ms, goal) -> str`：纯函数，产物可被 `testzeus_hercules.utils.gherkin_helper` 的解析入口解析（D1 同款校验）。

## 6. 执行器复用清单（不复制代码，直接 import）

来自 `record2gherkin/evaluation/runner.py`：

| 复用项 | 用途 |
|---|---|
| `build_run_plan(feature_path, project_root)` | 子进程命令/env/目录构造（key 只进 env；child env 已含 deepseek-v4-pro、`ENABLE_TELEMETRY=0`、`HEADLESS=true`、`ENABLE_UBLOCK_EXTENSION=false`） |
| `run_feature(..., timeout_s=600)` | 执行 + 超时杀进程组 + JUnit 定位；**benchmark 固定 `timeout_s=600`**（240s episode + planner 余量；orchestrator CLI 可覆盖） |
| `read_api_key()` / `mask_secret()` | key 读取与全部落盘文本脱敏 |
| `find_junit_xml()` / `parse_junit_xml()` | JUnit 定位与解析（cost/token 取数规则、failure 提取、`Terminate`/`final_response` 属性全部继承） |

不 import：`evaluation/sweep.py`（矩阵/seed 语义不同，只复制其编排模式：子进程服务启停、`assert_budget`、`--dry-run`、逐行追加 results.jsonl）；`evaluation/metrics.py`（坐标轴不同，benchmark 另写 §8 的小聚合）。

## 7. 结果行、编排与产物

7.1 结果行 schema（`results.jsonl` 每行，键名固定）：

```json
{
  "run_id": "miniwob__click-test__s1234567",
  "task_id": "miniwob.click-test",
  "subdomain": "click-test",
  "family": "click-test",
  "visual": false,
  "seed": 1234567,
  "goal": "Click on the 'button'.",
  "episode_max_time_ms": 240000,
  "status": "official_passed",
  "official_passed": true,
  "reward_raw": 1.0,
  "done": true,
  "reward_reason": "",
  "junit_passed": true,
  "junit_terminate": "##TERMINATE TASK##",
  "disagreement": false,
  "duration_s": 42.1,
  "total_tokens": 47000,
  "cost_usd": 0.05,
  "junit_xml": "<abs 路径或 null>",
  "failure_message": null,
  "started_at": "ISO8601",
  "finished_at": "ISO8601",
  "model": "deepseek-v4-pro"
}
```

7.2 status 组装规则（确定性顺序，`official_passed` 一律 false 当且仅当非 passed）：

1. `run_feature` 返回 `timeout` → `status="timeout"`（reward 记录有则照录，无则 reward 字段为 null）。注意 reward 字段允许负值：页面自身超时的记录为 `raw=-1, done=true, reason="timed out"`（实测非 `raw=0`），该值照录且仍按第 4 条 `raw > 0` 规则计官方失败。
2. JUnit 缺失/不可读（`no_junit`）→ `status="no_junit"`。
3. reward 记录缺失（`/latest` 404）→ `status="no_reward"`，`disagreement=null`。**无任何重开页面兜底**：`startEpisodeReal` 会重置状态，读到的不是终局值；只能如实记失败。
4. 其余 → `official_passed = (reward_raw > 0)`；`status = "official_passed" | "official_failed"`；`disagreement = (junit_passed is not None) and (junit_passed != official_passed)`。
5. goal 预读失败（§4.1）→ `status="no_goal"`，跳过执行，官方计失败。

7.3 `orchestrator.py` CLI：

```bash
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-pilot --stage pilot
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-full --stage full
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-full --stage full --dry-run
```

流程：读 `--exp-id` → 装载任务表 → 选 cells（pilot = `PILOT_SUBDOMAINS`；full = 全表 125）→ **断点跳过**：已有 results.jsonl 行的 `(task_id, seed)` 直接跳过（`--force` 关闭）→ 预算护栏 `assert_budget`（§7.5）→ 起 miniwob_server 子进程 + `/healthz` 探活 → 逐 cell：`derive_seed` → `read_goal`（no_goal 则记行跳过）→ `render_feature` 落文件 → `run_feature(feature, run_id, project_root=<exp>/runs/<run_id>/opt, timeout_s=600)` → `GET /__r2g_reward/latest` → 组装结果行追加 `results.jsonl` → 结束（finally）停服务、写 `manifest.json`。

- `--dry-run`：不进程、不预读、不写任何文件，仅打印每 cell 的 run_id/URL/feature 路径与预算合计。
- 每 cell 独立 `project_root`（`prepare_run_dir` 语义与 exp001 一致），`stdout.log` 已脱敏。

7.4 产物（`dev_runs/benchmark/<exp_id>/`，gitignore）：`results.jsonl`（追加式）、`rewards.jsonl`（服务端写）、`manifest.json`（`{exp_id, git_rev, started_at, stage, model_name, llm_base_url, server_port, episode_max_time_ms, timeout_s, budget, cells:[{task_id, seed}], metrics}`，不含 key 与 key 前缀）、`runs/<run_id>/`（opt + stdout.log）、`features/`。

7.5 预算护栏（数字写死，单测断言 + 运行时双拦截）：

| 阶段 | Hercules 执行数上限 |
|---|---|
| pilot | 10 + 2 重试 = 12 |
| full | 125 + 5 重试 = 130 |
| **累计红线** | **142**（成本红线 $20，按 $0.13/次上限；`manifest.budget` 记录已用/上限） |

重试仅限 timeout/no_junit/no_reward（基础设施类），每 cell 至多 1 次；用例失败（official_failed）绝不重跑。

## 8. 指标 metrics.py

输入 `results.jsonl` 行列表（`load_rows` 容错复用 evaluation/metrics 的容错语义，函数自含）：

1. **总体通过率** `Overall = Σ official_passed / |tasks|`；分母含全部 125 行（timeout/no_junit/no_reward/no_goal/official_failed 均按 0 计）；缺行 cell 计 0 并列入 `missing`。
2. **分家族通过率** `FamilyRate(f) = Σ official_passed(f) / |tasks(f)|`，输出 `{family: {value, passed, total}}`；另单列 `visual` 组通过率（披露用，不豁免）。
3. **成本/时长**：`AvgTokens/TotalTokens`、`AvgDuration/TotalDuration`、`AvgCost/TotalCost`；None 排除出均值并计数（`tokens_missing_count` 等），禁止记 0。
4. **disagreement**：计数 + 清单（run_id、junit_passed、official_passed）。
5. **JUnit 侧通过率**：一并输出（透明对照，非头条指标）。
6. `summarize(rows) -> Summary`（dataclass，`as_dict()` 直接进 `manifest.metrics` 与 test-report）。

## 9. 离线单测用例清单（不跑真 LLM、不跑真 Hercules；浏览器组仅本机回环 + 本地 vendored 资产）

**A 组 任务表（test_tasks.py）**
1. tasks.json 恰 125 行；task_id 唯一且 == `"miniwob." + subdomain`；数组按 task_id 字典序
2. 每行 `html` 文件真实存在且位于 `miniwob_html/miniwob/<subdomain>.html`
3. family 派生：`click-test-2`→`click-test`、`click-tab-2`→`click-tab`、`email-inbox-forward-nl-turk`→原样（规则锁定）；`visual` = 前缀判定且表中 visual 数 > 0
4. `derive_seed`：同 (exp_id, task_id) 多次恒等；对全表枚举，任意两任务 seed 互异
5. `PILOT_SUBDOMAINS`：恰 10 个、⊆ 全表、覆盖 ≥5 family、含 ≥1 visual

**B 组 伺服与补丁（test_server_patch.py，线程内起服务 + 禁代理 urllib）**
6. `GET /miniwob/click-test.html` → 200 且与 vendored 文件字节一致；响应头 `Cache-Control: no-store`
7. `GET /core/core.js` → 前缀与 vendored 文件逐字节一致，且含 `__R2G_PATCH_START__`、`__R2G_REWARD_HOOK__`、`startEpisodeReal`、`/__r2g_reward` 字面量（补丁 = 纯追加）
8. `GET /missing.html`、`GET /`、`GET /` 目录名 → 404；`/../etc/passwd` 形态 → 404（穿越防护）
9. 端口被占 → 启动硬失败（参照 exp001 demo_server 行为）

**C 组 收集端点（test_reward_endpoint.py，tmp_path 作 rewards-file）**
10. 合法 payload POST → 200；`/latest?task=click-test&seed=1` 取回一致（含 `received_at`）
11. 同 (task, seed) 两次 POST → last-wins；不同 seed 互不串
12. 每次 POST 向 rewards.jsonl 追加恰一行（文件与内存一致）
13. 非法 JSON / 缺 seed / 缺 path → 400 且不落盘

**D 组 goal 预读与 Gherkin（test_goal_gherkin.py）**
14. `sanitize_goal`：`"`→`'`、换行/多空白折叠、strip（纯函数表驱动）
15. `render_feature`：三段式 + URL 含 `r2g_seed` 与 `r2g_ms`；产物经 gherkin 解析入口通过；恰 1 Feature/1 Scenario
16. **浏览器组（test_browser_miniwob.py，无浏览器环境 skip）**：真实伺服器 + 真实 vendored 页——(a) `?r2g_seed=` 打开后 auto-start 生效，证据 = `core.ept0 != null`、utterance 非空、`core.EPISODE_MAX_TIME === 240000`、无 `__R2G_START_ERROR`（**不得**用 `EPISODE_ID > 0`：开局成功后 `WOB_EPISODE_ID` 仍为 0，仅在 `core.endEpisode` 内自增，review 实测确认）；(b) 同 URL 两次打开 utterance 相同（同 seed 确定性）；(c) 页内 `core.endEpisode(1)` 后 `/latest` 取到 `raw>0, done=true`

**E 组 结果组装与指标（test_results_metrics.py，手写 fixture）**
17. runner 超时 + 有 reward → status=timeout 且照录 reward；JUnit 缺失 → no_junit
18. reward 缺失 → no_reward 且 disagreement=null；goal 失败 → no_goal 不执行
19. `reward_raw=0` + junit_passed=True → official_failed + disagreement=True；`reward_raw>0` + junit_passed=False → official_passed + disagreement=True；一致 → disagreement=False
20. 指标：Overall 分母含全部失败族；缺行计 0 并入 missing；FamilyRate 聚合正确；token/cost 均值排除 None 且 missing 计数正确；visual 组与 disagreement 清单正确

**F 组 编排与护栏（test_orchestrator.py，dry-run / monkeypatch）**
21. pilot/full cell 计划数 = 10 / 125；`assert_budget`(12, 130) ≤ 142 通过，超限抛错
22. dry-run：无进程、无文件写入；打印项含 run_id、`?r2g_seed=` URL、feature 路径、预算合计
23. 断点跳过：预置含某 cell 的 results.jsonl 后 dry-run 计划数递减；`--force` 恢复

## 10. 验收标准

1. `uv run pytest tests/record2gherkin/benchmark -q` 全绿（离线：无 key、无外网；浏览器组仅回环，无浏览器时 skip 并提示）。`make fmt` 后 `--check` 通过。
2. 构建产物入库且可复现：`miniwob_html/` 与 PROVENANCE 记录的文件数/字节数一致；`tasks.json` 125 行、A 组全绿。
3. 伺服器一条命令启动，`/healthz` 与 `/__r2g_reward` 契约与 §3.3 一致（B/C 组 + 手动 curl 双证）。
4. pilot（`--exp-id miniwob-pilot --stage pilot`）：10 行结果；≥1 行 official_passed 且 `total_tokens` 非 None；§12 验证点全部回写 spec 后才允许开 full；Hercules 执行 ≤12。
5. full（`--exp-id miniwob-full --stage full`）：125 行（失败也是行）、manifest 完整（含 metrics）、Hercules 累计 ≤130（两阶段合计红线 142）。
6. 脱敏：对 `dev_runs/benchmark/`、`record2gherkin/`、`tests/`、`dev_docs/` 以 key 实值 grep 零命中（命令模板 `KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" <目录>`，key 不落文档）。
7. `dev_docs/benchmark/test-report.md` 固化：总体/分家族（含 visual 组）通过率、AvgTokens/AvgDuration、disagreement 清单、JUnit 侧对照通过率、失败清单（run_id + 摘要）、**§0 四条口径披露原文**、与公开基线数字的引用及设置差异声明。

## 11. Out of Scope

WebArena / WebVoyager / Mind2Web 任何形式的接入或复现；attributor 接入；多 seed 重复与方差报告；视觉任务豁免（125 个全跑，visual 失败就是失败）；排行榜式图表、逐任务截图集、自动失败分析；Hercules 内核任何改动；录制/蒸馏链路参与本基准；公开基线的本地复现实验（只引用可查证文献数字 + 设置差异声明）；断点续跑之外的并发/分布式编排；reward POST 丢失时的"重开同 seed 页"兜底（机制上不可行，见 §7.2.3）；CI 集成。

## 12. 遗留决策点（pilot 暴露后回写本节，不预先设计）

- `open_url` 对 `?r2g_seed=&r2g_ms=` query 的实际行为；不可用则切换为无 query 的 path 段映射方案并回写 §3/§5。
- 240s episode 窗口内单步延迟实测分布；全量 `episode_max_time_ms` 是否上调（上调必须披露）。
- 预读 utterance 与执行侧一致性抽查（≥3 个任务人工比对）。
- JUnit cost/token 属性在 deepseek 下回传情况（沿用 exp001 结论核对，变化则记 test-report）。
- pilot 实测单 run 时长/token → full 墙钟与预算外推数字回写 §7.5。
