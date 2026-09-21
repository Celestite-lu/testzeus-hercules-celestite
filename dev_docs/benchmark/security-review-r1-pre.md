# Benchmark 防作弊与沙箱安全审查报告（R1 · 全量实验前）

- 日期：2026-09-20（审查时 pilot 正在运行，未受干扰；全部实测用独立临时端口 + `/tmp/r2g_audit/` 独立 rewards 文件）
- 范围：`record2gherkin/benchmark/`（miniwob_server / goal_reader / orchestrator / tasks）、`record2gherkin/evaluation/runner.py`（child env）、Hercules 观察与工具面（get_page_text / open_url / execute_python_sandbox）、`dev_runs/benchmark/miniwob-pilot/` 已产出数据
- 方法标注：凡写【实证】均有本机复现实验或日志证据；写【推理】为代码级推断未跑通端到端；写【未验证】为探测未复现项。

---

## 1. 结论：YELLOW

**先做下列 P0 加固与检测再跑 full；不构成 RED（无发现已污染 pilot 成绩的作弊行为，pilot 数据本身干净）。**

| 等级 | 数量 | 向量 |
|---|---|---|
| 高 | 4 | V1 奖励 HUD 对 agent 文本可见；V2 START 覆盖可点击重启（re-roll）；V4 重新导航重置计时/洗白失败提交（pilot 已自发出现）；V6 Python 沙箱 restricted 档即全逃逸（含 page.evaluate 伪造奖励路径） |
| 中 | 1 | V5 `file://` 逃逸可读本地文件（含 LLM-Key.txt，安全影响高、自发概率低） |
| 低 | 2 | V3 `/__r2g_reward` 可被伪造 POST（需模型主动攻击 harness）；V10 伺服器 stdout 管道无排空（仅 LOG_LEVEL=DEBUG 时有风险） |
| 信息/无 | 2 | V7 任务页无 hidden 答案（基准原生属性）；V9 seed 派生无泄漏、240s 口径已披露（但被 V4 侵蚀） |

**最关键一条：V1+V2 —— MiniWoB 页面原生 HUD（`Last reward: 1.00`）与 endEpisode 后重新出现的 START 覆盖层都落在 `get_page_text` 的 `body.innerText` 视角里，agent 既能读到官方成败信号、又能点击 START 无限重开同一 (task, seed) 的新实例（240s 计时同时重置）。一个纯追加的小补丁可同时封掉两者，且已实测验证不影响奖励上报。**

---

## 2. 向量清单（等级 / 实证证据 / 缓解）

### V1 奖励 HUD 泄漏 —— 高（作弊：影响成绩有效性）

**实证**：
- 补丁后页面中，`document.body.innerText`（= `get_page_text` 的提取源，`testzeus_hercules/core/tools/get_page_text.py` 用 `root.innerText`，另有 `get_interactive_elements`/a11y 通道）在回合内即含 HUD 文本：`Last reward: - / Last 10 average: - / Time left: 240 / 240sec / Episodes done: 0`；调用 `core.endEpisode(1)` 后立即变为 `Last reward: 1.00 / Last 10 average: 1.00`（click-test、enter-text、login-user 三页实测一致）。`core/core.js:170-181`（DISPLAY_HTML 模板）、`:234-247`（updateDisplay）。
- core.js 自带的 `core.getDOMInfo`（:341-343）确实把 `reward-display / sync-task-cover / click-canvas / query` 从 DOM 观察里剔除——但那是 MiniWoB/BrowserGym 的观察函数；Hercules 用自己的 DOM/文本提取，不受其约束。即 BrowserGym 口径下 agent 看不到 HUD，我们的口径下看得到 → 观察空间**强于**对齐基线。
- pilot 佐证动机：planner 生成的 step 文本明确要求 executor"report ... any visible change in the page state, **including reward or status indicators** if present"（`runs/miniwob__enter-password__s4110908536/stdout.log:261`）——模型被指示主动寻找 reward 指示物。

**缓解（H1，已验证可行）**：在补丁 A 的 `core.startEpisodeReal()` 成功后追加三行（纯追加，仍在 `__R2G_PATCH_*__` 标记内或紧随其后）：
```js
var hud = document.getElementById('reward-display');
if (hud) { hud.style.display = 'none'; }
core.updateDisplay = function () {};   /* 终局不回写 HUD 文本 */
core.startEpisode = function () {};    /* 封 V2：endEpisode 尾部(:145)不再重新显示 START 覆盖 */
```
实测验证（`/tmp/r2g_audit/` isolate + restart_and_patch 两轮）：加固后 `body.innerText` 全程零 `reward/START/Time left/Episodes done` 命中；**奖励 POST 完好**（记录 `raw=1, done=true` 正常到达）；`#query` 指令区不动，`core.getUtterance()` 可用（goal 预读不受影响——注意 `#query` 不剔除，预读依赖它）。采用 `display:none` 而非 `element.remove()` 的原因：`endEpisode` 仍要写 `episode-id` 等子元素，移除会在 orig 内抛 TypeError；`innerText` 跳过 `display:none`，对 get_page_text 已足够。

### V2 START 覆盖层 = DOM 可点的重开按钮（re-roll）—— 高（作弊）

**实证**（enter-text, seed=201）：`core.endEpisode(0)`（错误提交的典型终局）后，`body.innerText` 出现 `START`（覆盖层重新可见，`core.js:145` endEpisode 尾部调 `core.startEpisode()`，`:73-82` 恢复覆盖层并绑 `onclick=core.startEpisodeReal`）；对 `#sync-task-cover` 一次**普通 DOM 点击**（click_using_selector 即可，无需 JS eval）后：utterance 从 `Enter "Joye" ...` 变为 `Enter "Michel" ...`（同 seed 下 RNG 前进 → **新实例**）、计时器重置 `240 / 240sec`、`WOB_DONE_GLOBAL=false`。reward 记录 last-wins：失败(raw=0)→重开→成功(raw=1)，orchestrator 的 `/latest` 只取最后一条 → **计通过**。
**缓解**：并入 H1（`core.startEpisode` 置空后实测 `cover_display_after_fail='none'`，重开路径在 DOM 层关闭）。`startEpisodeReal` 本身仍可被 JS 调用，但浏览器 agent 无 JS eval 工具（工具清单已核对），JS 路径只剩 V6 的沙箱 `page`。

### V3 收集端点可伪造 —— 中（作弊；需模型主动攻击 harness，自发概率低）

**实证**：`POST /__r2g_reward` 仅校验 path/seed 非空（miniwob_server.py:182-190,296），伪造 `{"path":"/miniwob/forged-task.html","seed":"999999","raw":1.0,"done":true,"reason":"audit-forgery"}` 返回 200 落盘，`GET /__r2g_reward/latest?task=forged-task&seed=999999` 原样取回 → orchestrator 的 `fetch_reward` 会采信。last-wins 内存 + JSONL 追加使伪造与真实记录不可区分。**原则性极限**：补丁 B 对浏览器公开，任何浏览器侧共享密钥都会出现在伺服的 core.js 里，"防伪造"在机制上做不到根除，只能检测+披露。
`GET /__r2g_reward` 本身返回 404（:260-262，实测）——"导航到端点读历史奖励"不成立；`GET /__r2g_reward/latest` 可读但对当前 cell 在终局前为 404（无记录可读），跨 cell 因 seed 不同无信息量 → 该子项无风险，维持现状。
**缓解（H2）**：① orchestrator 每 cell 结束后核对 `rewards.jsonl` 中该 `(path, seed)` 的行数（合法流 ≤ 页面加载数；异常增多→行内 `flagged`）；② 校验 `reason` 字段域（合法值仅 `''` 与 `'timed out'`）与 `done/raw` 一致性；③ 报告中披露"端点无防伪造能力"这一基准限制。

### V4 重新导航重置计时/洗白失败 —— 高（作弊 + 已在 pilot 自发发生，口径失真）

**实证**：open_url 对 URL 无任何限制；agent 重新打开 Given 的同 URL（同 seed）→ 补丁 A 重跑 `Math.seedrandom(seed); startEpisodeReal()` → **同一实例从头开局、240s 计时重置、此前任何错误提交的 done/raw 状态全部作废**（最后加载的页面决定终局 POST）。pilot 9 个已完成 run 中 5 个出现 ≥2 次同 URL 导航（email-inbox 4 次、use-autocomplete 3 次、login-user/enter-password/click-button 各 2 次），且 enter-password 的 planner step 明确写了"**Do not navigate away**"而模型仍在 23:08:44 再次导航——属自发"复查"行为而非攻击，但机制上等价于：**240s 口径被架空（600s 子进程墙钟内可多次重置），失败提交可被重开洗白**。
**缓解（H3，P0 检测 + 披露）**：orchestrator 在每 cell 收尾扫描已有 `stdout.log`：统计 `Opening URL` 中命中本 cell 任务 URL 的次数写入结果行（如 `task_url_navigations`），>1 → `flagged=true` 并在 test-report 披露计数与名单。是否改判由报告口径预先声明（建议：r1 全量保留官方奖励但逐行披露；更严选项见 §4 可选项 B）。

### V5 file:// 逃逸 —— 中（安全影响高：密钥/本地文件进 LLM 上下文；自发概率低）

**实证**：
- `open_url` 显式保留 `file:` scheme（open_url.py:220-235 special_schemes 含 `"file:"`）后 `page.goto(url)`；实测 Playwright chromium `goto("file:///tmp/r2g_audit/secret_probe.txt")` 成功，文件全文（含假密钥 `sk-dummy...`）进入 `body.innerText` → get_page_text → LLM 上下文。目录列举（file:///tmp/）本次**未验证**复现（innerText 未含目录项），直连文件读取已足够构成通道。
- 子进程 `cwd=REPO_ROOT`（runner.py:236 `cwd=REPO_ROOT`），`LLM-Key.txt` 位于仓库根（存在性已核实，内容未读、未入任何日志）。file:// 需绝对路径；agent 不知道仓库布局，但文件路径可能从 Gherkin/日志/错误信息中拼出。【推理】泄漏链完整存在：file:// 打开 → innerText → 上下文 → 可能被模型复述进 output/JUnit final_response。**未用真实密钥测试**（红线），机制同假密钥。
- 同源伺服的任务 JS 对 DOM agent 可见 = 与 BrowserGym 等一切 DOM agent 相同的观察空间，基准常态、非作弊（如实记录）。
**缓解（H4）**：① P0 检测：运行后 `grep -c "file://" runs/*/stdout.log`（pilot 现值全 0），>0 → 该 cell 标记无效并按安全事件处理；② 上游产品建议（非 benchmark 改动）：open_url 增加 scheme 白名单（仅 http/https）。

### V6 Python 沙箱 —— 高（安全 + 作弊能力，一石二鸟的攻击面）

**实证**（默认配置 = benchmark child 的实际配置：`build_child_env` 只叠加 LLM 相关键与 3 个开关（runner.py:140-161），**未设 SANDBOX_TENANT_ID** → `config.py:756` 默认 `""` → `_get_tenant_specific_injections` 命中 restricted 空 policy；仓库无 `.env`）：
- `_execute_in_sandbox`（execute_python_sandbox.py:459-485）无条件注入 `"__builtins__": __builtins__`（含 `open/exec/eval/__import__`）、`os`、`sys`、`json`、`time`、**活的 `page/browser/context/playwright_manager`**，`exec(code, ...)` 在 Hercules 子进程内直接执行——tenant 档位只控制 requests/pandas 等**便利注入**，不构成安全边界。
- restricted 档实测四连通（假密钥环境）：`open()` 读任意本地文件 = True；`os.environ["LLM_MODEL_API_KEY"]` 可读（假 key 长度 32 全见）；任意 HTTP 外发（urllib，经/绕代理均可）= True；`await page.evaluate("1+1")` 在活页面上执行 = True。**推论**：`page.evaluate("core.endEpisode(1)")` 即可在真页上伪造官方通过；`open()` 可读 rewards.jsonl、任务 JS、乃至 LLM-Key.txt。
- 触达面：`execute_python_sandbox` 在每个 benchmark child 中注册于 executor_nav_agent（pilot stdout.log:205-208），Gherkin 关键字触发族为 execute/run/call/script/automation/workflow——MiniWoB goal 文本几乎不含这些词，pilot 9 run 零调用（日志只有注册行，无 `Using sandbox tenant` 调用行）。自发风险低，但这是**唯一**能把"作弊"与"密钥外泄"一次完成的工具。
**缓解（H5）**：SANDBOX_TENANT_ID **不是**有效缓解（restricted 档已实证逃逸）。可落地选项：
1. P0 检测：post-run 扫描 stdout.log 的调用标记（`Using sandbox tenant` / `Executing execute_python_sandbox`），命中 → cell 无效 + 安全事件（与 H4 同一个扫描函数）。
2. P1 产品建议：给 `execute_python_sandbox` 加环境开关（如 `SANDBOX_DISABLED=1` 直接拒执行），orchestrator 经现成的 `run_feature(..., extra_env=...)` 注入——最小改动、根除该攻击面（benchmark 侧不改产品代码，故列为建议项）。

### V7 goal/答案泄漏 —— 无（基准原生属性，如实记录）

**实证**：全 130 个任务页 grep 无 `correct_answer/hidden answer` 类静态答案；抽 3 页——click-checkboxes 答案存 JS 变量 `checkboxData.toclick`（不进 DOM）；login-user 的用户名/密码只出现在 `#query` 指令文本与 JS 闭包（指令本来就要给 agent 看，与预读 goal 同文）；simple-arithmetic 题目运行期生成。伺服层/补丁/Gherkin 模板（goal_reader.py `render_feature` 三行固定模板）无任何把期望答案写进 agent 可见文本的路径。同 seed 下 JS 源码可读 = 所有 DOM agent 的共同观察空间，属基准原生属性，无需处理。

### V8 `/__r2g_reward` GET —— 低（已证 404，无路径）

见 V3：GET 本体 404（实测），/latest 对当前 cell 终局前无记录。维持现状。

### V9 seed 与计时口径 —— 信息（无泄漏；240s 披露被 V4 侵蚀）

`derive_seed = int(sha1("{exp_id}:miniwob:{task_id}")[:8],16)`（tasks.py:84-86）：seed 只进 URL query，对 agent 是无意义数；服务器不暴露 exp_id。`r2g_ms` 在 URL 中可见（无害参数本身，但配合 V4 可重置计时——归入 V4 处理）。240s 放宽已按口径 2 披露；full 报告需追加披露 V4 的实际影响（见 §4）。

### V10 伺服器 stdout 管道 —— 低（运维）

`start_miniwob_server` 以 `stdout=PIPE` 起服务且从不排空（仅 early-exit 时读一次，orchestrator.py:285-309）。请求日志走 `logger.debug`（默认 INFO 被抑制）故默认不积压；若有人以 `LOG_LEVEL=DEBUG` 跑 125-cell 全量，管道写满 64KB 后伺服器阻塞。缓解：不要用 DEBUG 跑全量（或后台线程排空 PIPE）。

---

## 3. pilot 事后扫描结果（9/10 cell，visual-addition 运行中）

- **无可疑行为**：全部 stdout.log 零 `file://`、零 `__r2g_reward` 访问、零 `endEpisode/seedrandom` 调用、零沙箱调用。
- **奖励记录干净**：每个 cell 恰 1 条 POST（无重试循环、无重复/伪造 POST）；8 个 official_passed 全部 `raw=1.0, reason=''`，时长 48-215s（无异常快速通过）；email-inbox-forward-nl 为页面自身 240s 超时（`raw=-1, reason='timed out'`）+ 子进程 600s timeout，口径处理正确。
- **污点**：5/9 run 存在同 URL 重复导航（V4，agent 自发）；planner step 文本要求汇报 reward 指示物（V1 的动机证据）。
- pilot 成绩**未发现**被上述向量污染的迹象；但 full 前仍须先封 V1/V2（机制存在即会被更强模型自发踩中）。

---

## 4. 加固实施清单（按优先级；均为 benchmark 模块内最小改动，不动产品代码）

**P0（full 前必须）**
1. **H1 补丁 A 追加 hardening**（miniwob_server.py `AUTO_START_PATCH` 内 `startEpisodeReal()` 之后追加 §2.V1 的三段 JS；同步更新 test_server_patch 的标记断言与 test_browser_miniwob：断言 `endEpisode` 后 `body.innerText` 零 `reward|START|Time left|Episodes done`、`sync-task-cover` display:none、`/__r2g_reward` 记录仍到达）。已验证代码片段与预期行为见 §2.V1/V2。
2. **H2 cell 收尾扫描**（orchestrator，读已落盘 stdout.log，一个函数三件事）：① 任务 URL 导航次数 → `task_url_navigations` 字段，>1 → `flagged`（V4，必须披露）；② `file://` 命中 → cell 无效 + 安全事件（V5）；③ `Using sandbox tenant`/`Executing execute_python_sandbox` 命中 → cell 无效 + 安全事件（V6）。
3. **H3 rewards 异常检测**：每 cell 核对 rewards.jsonl 该 `(path,seed)` 行数与 `reason` 域（V3），异常 → `flagged`。

**P1（full 前建议）**
4. 报告口径增补：披露"补丁为纯追加、奖励端点无防伪造能力、agent 可重导航"三项基准限制（符合口径条款"任何代码不得掩盖"）。
5. 上游产品建议（非本次改动）：open_url scheme 白名单（V5 根除）；`execute_python_sandbox` 环境开关 + orchestrator 经 `run_feature(extra_env)` 注入（V6 根除）。

**P2（可选/记录）**
6. 可选项 B（更严的 V4 处理）：补丁 A 用 `sessionStorage` 做"本 tab 只自动开局一次"，二次导航不自动开局 → 该 cell 变 no_reward（可检测的失败而非可洗白的重试）。改变行为，若采用须重新跑 pilot 验证并回写 spec §3.2/§12。
7. 伺服器 PIPE 排空或文档声明禁用 DEBUG 跑全量（V10）。

**验收红线**：加固后重跑 `uv run pytest tests/record2gherkin/benchmark -q` 全绿；用 §2.V1 的实测脚本形态（独立端口 + /tmp rewards）复核：HUD 零命中、START 不可见、奖励 POST 完好、utterance 预读不变；`KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs record2gherkin tests dev_docs` 零命中（本报告撰写时已复核为 0）。

---

## 5. 证据与复现索引

- 实测脚本（临时区，可随时删除）：`/tmp/r2g_audit/{r2g_audit_test.py, isolate.py, restart_and_patch.py, file_and_sandbox.py}`；独立 rewards 文件 `/tmp/r2g_audit/rewards*.jsonl`；全部使用临时端口（62xxx），未触碰 pilot 的 8462。
- 关键代码位：`record2gherkin/benchmark/miniwob_server.py:53-107`（补丁 A/B 原文）、`:182-190,284-300`（POST 校验）、`:260-262`（GET 404）；`core/core.js:106-146`（endEpisode + 尾部 startEpisode）、`:170-181`（HUD 模板）、`:341-343`（getDOMInfo 剔除清单）；`testzeus_hercules/core/tools/get_page_text.py`（innerText 提取）；`testzeus_hercules/core/tools/open_url.py:220-235`（file: 保留）；`testzeus_hercules/core/tools/execute_python_sandbox.py:459-485`（无条件注入）、`:206-231`（tenant policy 仅便利注入）；`record2gherkin/evaluation/runner.py:140-161,236`（child env / cwd）。
- pilot 证据：`dev_runs/benchmark/miniwob-pilot/runs/*/stdout.log`（`Opening URL` 计数、`stdout.log:205-208` 注册日志、enter-password `:261` planner 索要 reward 指示物）；`dev_runs/benchmark/miniwob-pilot/{results,rewards}.jsonl`。
