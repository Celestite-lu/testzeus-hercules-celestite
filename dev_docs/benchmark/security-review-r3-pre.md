# R3 实验前安全审查报告（security-review-r3-pre）

- 日期：2026-09-23。审查对象：r3 相对 r2 的全部变更（commit `f321874` 引擎侧 9 文件 + `4b747c0` harness 侧，含 T1–T12）；法定输入：`plan-r3.md`、`spec-r3.md`、`review-r3.md`、`security-review-r2-post.md` §9。
- 方法：注入链源码实读 + 离线实测（`uv run pytest tests/record2gherkin`）+ **真浏览器实证**（本地 http.server :8799 + chromium：drag 三形态、open_url 六 scheme、沙箱关停）+ 子进程子集加载验证 + r2 数据回放 + 双臂 dry-run。
- **结论：YELLOW（1×P1 + 1×P2 + 3×P3；零 RED；无阻塞 M1 pilot 项；P1 建议在 M2 headline 前修，一行改动）。**

---

## 一、逐项核验结果（全部实证）

### R3-1 nav 补全上限 768 — PASS

- **注入链**：`create_chat_model`（`utils/llm_helper.py:116-121`）在 timeout/max_retries 兜底之后执行 `nav_cap = get_nav_max_completion_tokens(); if nav_cap > 0: kwargs["max_tokens"] = nav_cap`——env>0 **无条件覆盖** adapt 注入的 4096（review-r3 M1 的死代码缺陷已按修订落实）。`get_nav_max_completion_tokens` 直读 env（`max(0, _env_int(...))`），不经 config relevant_keys。
- **运行时实测**（glm-5.3-flash 名 + dummy key）：env=768 → `max_tokens=768`；env 未设 → `4096`（r2 真实基线复现）；env 未设 + 显式 `llm_config_params={"max_tokens": 256}` → `256`（env 不在场时现状不变）。
- **planner 隔离**：PlannerAgent 走裸 `ChatOpenAI`（`high_level_planner_agent.py`），不经 `create_chat_model`，其 `max_tokens=4096` 由 `SimpleHercules.create` 的 adapt 注入（T1 `test_t1_planner_path_never_carries_the_cap` 锁定）。env=768 下 planner 路径实测无 cap。helper 多模态单例同吃 768（spec 已如实披露，benchmark 文本 DOM 下不被调用）。

### R3-2 planner 双层 150s — PASS

- **两层同源**：外层 `_llm_ainvoke`（`simple_hercules.py:284`）对 `agent_name=="planner_agent"` 用 `get_llm_planner_request_timeout_seconds()`；provider 层兜底（`high_level_planner_agent.py:66-67`）同用该 helper。实测：`LLM_REQUEST_TIMEOUT=90` + `LLM_PLANNER_REQUEST_TIMEOUT=150` → planner 产物 `request_timeout=150`、nav 产物 `request_timeout=90`；headline 下两层同为 150，>90s 慢生成可完整返回。
- **off = r2 逐字节一致**：helper 在 unset/空串/非法值/≤0 时全部回退 `get_llm_request_timeout_seconds()`（实测回退不抛）。超时消息 `{timeout:g}s` 格式未动。
- `LATENCY_ENV_OVERRIDES` 五键与 r2 定稿 commit（`4ff016a`）**逐字节相同**（dict diff 为空）。

### R3-3 drag 透传 — PASS（解析面实证；附一条 P3 环境备注）

本地伺服（`dev_runs/r3pre_live/drag_page.html`）+ **真 chromium** 走真实工具函数与真实 `find_element`：

| 输入 | 实证 |
|---|---|
| `css=.drag-handle` | 原样透传（日志 `Found source element using selector: css=.drag-handle`，无 md 包装）；鼠标 down→21 move→up 全序列落在 drop-zone 中心，页面 `__DROP_OK__=true` |
| `42`（裸 md id） | 首候选 `[md='42']` 命中（r2 兜底语义保留） |
| `Kass`（未知形态） | 依序 `Kass`（未中）→ `[md='Kass']`（未中）→ `text='Kass'`（命中），落点为该元素中心 |
| 全候选失败 | `ValueError: Source element not found using any of these selectors: [...]` 列出全部候选 |
| `javascript:window.__PWNED__=true` | **零执行**：`Page.query_selector: Unexpected token "=" while parsing selector "javascript:..."` 报错被工具 except 捕获返回错误文本；页面 JS 标志位不变、零导航 |

- **透传无新作弊面**：source 候选只进入三个 sink——`page.query_selector(selector)`、`page.wait_for_selector(selector)`（target 侧，零改动）、`page.evaluate_handle(js, selector)`（selector 作**函数实参**而非代码拼接，`playwright_manager.py:1633/1645`）。Playwright selector 引擎无任意 JS 执行通道；透传只扩大"可拖元素范围"（即修复目标），与既有 `click_using_selector` 等工具的 selector 面完全同权，非新增能力。
- **P3 备注（环境级，非 r3 回归）**：同一页面第 2 次起的鼠标拖拽序列在 ~1 个 move 后停摆（down 后事件不再到达页面）。用**零 Hercules 代码的裸 Playwright** 复现同一模式（drag1 事件 23/落点 OK，drag2/3 事件 2）——是 chromium headless 环境特性，与 R3-3 改动无关（鼠标序列 L117-137 零改动，r2 同样存在）。影响 M1 归因：drag 冒烟若出现"选择器已解析但 drop 未登记"，按 spec §3.3 的 kill-switch 判据（**解析成功进入鼠标序列**，非 drop 成功）仍应判"修复有效"；真实运行每格新页面，首拖即用，大概率掩盖此象。写入 test-report 披露即可。

### R3-5 安全包 — 五项全 PASS，两项遗留（见风险表）

- **E1 子集加载**（subprocess 三态实测，工具注册表取证）：`EXTRA_TOOLS_MODULES=drag_and_drop_tool` → 仅加载 drag 模块，`persist_findings`/`recall_findings`/`read_clipboard` **全部未注册**（43 tools vs 全量 54）；`all` 与空串 → 全量（r2 复现通道）。**绕过路径审计**：① `--extra-tools-modules all`（显式值，manifest 记 `["__all__"]`，报告必须披露）；② `--extra-tools-modules file_handler_tool`（显式值，manifest 记模块名）——两者均为显式 flag 输入且有披露载体，无静默绕过；env 只由 orchestrator flag 注入，agent 运行时无法触发新 import（import-time 门控）。`--extra-tools` + 空 modules → `BenchmarkError`（防误开全量）。**→ r2 W2 的"仓库根 agent 写文件"路径结构性关闭。**
- **E2 文件工具日志行**：三函数首行 `[EXTRA_TOOL_CALL] <name> path=...`（`file_handler_tool.py:32/89/131`）。合成日志过 `scan_cell_log`：三标记各自 → `flagged=True, invalid_reason='file_tool_invoked'`；多标记并存正确合并；干净日志（含工具注册行）全中性无误报。
- **E3 scheme 白名单**（真浏览器实测）：`javascript:` / `data:` / `file:` / `vbscript:` / `chrome:` 全拒，报错形态 `Blocked URL scheme 'javascript:' — only http:// and https:// are allowed. URL rejected: {url}`，`[OPEN_URL_BLOCKED]` 落日志，**goto 调用数=0**，页面标题/状态不变；`127.0.0.1:8799/x`（无 scheme）照走 ensure_protocol（https 前缀，r2 同）;`about:blank` special 分支前置不受影响。扫描器对该标记 flagged-only（仅披露，与 r2 javascript: 低危口径对齐）。`localhost:PORT` 边缘实测确拒（`urlsplit` 解析出 scheme=`localhost`），spec 勘误已在 `test-report.md:381` 落档：benchmark 任务 URL 恒带 `http://127.0.0.1:` scheme 不受影响。
- **E5 沙箱关停**：机制 = **env 层函数体首行拒绝**（工具仍注册，但任何 tenant 读取/日志标记之前即返回），非不加载。实测 `SANDBOX_DISABLED=true` → 返回 `Python sandbox is disabled in this environment ([SANDBOX_DISABLED]).`，无 `Using sandbox tenant` 行。`[SANDBOX_DISABLED]` 已入 `SANDBOX_CALL_MARKERS` → 合成日志实测映射 `sandbox_tool_invoked`——**尝试即无效，被拦截的尝试不逃逸扫描**（r2 契约保持）。
- **E4 clean 口径自动化**：`clean_rate`/`invalid_cells` 实现与 spec §5.4 一致（invalid 同时移出分子分母；`overall` 零改动——回放 r2 官方口径 58/125=46.4% 精确复现）。**与 r2 手工口径的一致性为部分成立**：见风险表 F1。
- **E6**：`GoalReadError` 分支写 `runs/<run_id>/goal_read_error.log`，写失败仅 warning（diff 审读确认）。

### R3-6 断言纪律 prompt — PASS（内容审阅）

- 全文（`high_level_planner_agent.py:15`）只约束 `terminate=yes ∧ is_passed=true` 的**前提**：最后一条 helper 观察必须明确确认动作已执行，失败/超时/未提及/不确定 → `is_passed=false` 如实报告。
- **零泄露**：不含任何奖励机制信息（WOB_REWARD、/latest、判分链均未提及）、不含任何任务答案、不提供绕过判分的新能力；方向是让 agent 更保守地报告失败（反幻觉），属诚实性纪律而非奖励寻租面。实测：on → system_message 以该段开头且含 `is_passed=false`；off → 无该段（逐字节 r2）。注意 config 单例启动后改 env 不生效（T11 经 conf 注入锁定，运行顺序无此问题）。

### flag 矩阵终检 — PASS

- **headline 全开组合** dry-run：125 格计划、budget `{"full":125} retry 5 total 130 ≤ cap 144`、URL `r2g_ms=240000`、**零落盘**；env 键集恰为 r2 五键 + LOAD_EXTRA_TOOLS + 路由四键 + 新五 env 共 15 键（T10a 锁定，多一键即失败）；`LATENCY_ENV_OVERRIDES` 原值未动。
- **D-ablation 臂**：同 flags + `--episode-ms 480000 --timeout-s 900 --exp-id miniwob-r3-d480 --exp-root <独立目录>` dry-run：URL 携带 `r2g_ms=480000`、budget 130/144、零落盘、headline 目录零触碰——两臂互不影响，独立 exp-id/exp-root 成立，零代码差异。
- 默认命令（r3 flags 全 off）→ 新五 env 零出现（T10b），引擎侧 off 路径逐项实测同 r2。

### 测试基线

`uv run pytest tests/record2gherkin -q` → **456 passed**（0 failed，76.9s），与提交声明一致；T1–T12 全部在位。

---

## 二、风险向量表

| # | 等级 | 风险 | 实证 | 缓解 / 建议 |
|---|---|---|---|---|
| F1 | **P1**（calibre 诚实性，不阻塞 M1） | **E4 clean 口径的 last-wins 语义在"无效格被重试"场景下弱于 r2 手工口径**：无效事件发生在 att1、格因 infra 状态进入 retry、att2 干净 → latest 行 `invalid_reason=None` → 该格从 `clean` 的剔除中消失。回放实证：r2 rows 人工补注 W2 后 `clean = 55/117`（手工口径 55/115）；差额两格 ascending-numbers（att1 写文件失败→att2 timeout 重试）与 click-pie（att1 静默成功→att2 干净重试）。风险方向 = **多计**：r3 中一个"沙箱尝试 + timeout"格若被干净重试且通过，将按通过计入 clean（≤1–2 格量级，恰在 50% 主张的缓冲带内）。另：r2 数据**原样**回放 `clean = 57/124 ≠ 55/115`——r2 的 10 格 W2 是磁盘取证发现，r2 日志/行中无任何可自动检测的标记（正是 E2 堵的缺口），**r2 手工口径不可由 r3 自动口径在 r2 数据上复现**，引用时不得声称"自动复算一致"。 | 本报告 §一/E4 + 回放数据（官方 58/125 精确复现，clean 差 2 格已定位到行级） | M2 headline 前一行修复（orchestrator `_retry_infrastructure_failures` 选格循环加 `if row.get("invalid_reason"): continue`）：无效格不再烧 infra 重试预算，latest 行保持 invalid → clean 自动正确；顺带与"尝试即无效"契约对齐（披露：重试行为相对 r2 有微小收紧）。不改 M1 pilot 亦可先行（E1 下文件标记结构性不可能；沙箱尝试已在 att 行+flagged 披露） |
| F2 | **P2**（卫生/任务前提不符） | **r2 W2 实物清理未完成**："persist_findings 实物已清理"的前提不成立——10 个产物中 5 个仍在仓库：`blocker_click_shape_H.log`、`click_menu_state.log`、`click_pie_attempt.log`、`reward_probe.log`（仓库根）、`log_files/inbox_task_progress.log`；已清理 5 个（debug_log.txt、episode_context.json、miniwob_reward_verification.txt、reward_report.txt、task_state.json）。全窗口扫描无其他残留（`opt/`、run_data/ 干净）。五文件均 gitignore/untracked（零 git 历史风险），r2-post 已逐文件通读确认无密钥。 | mtime 全部落在 r2 窗口（09-22 23:20–09-23 17:30），内容与 r2-post §2.1 清单一一对应 | 删除 5 个残留文件（或总编排代理在 test-report 明示"保留为取证样本"的例外声明）；此后该路径已被 E1 结构性关闭（本报告 §一/E1） |
| F3 | P3（备注） | headless chromium 同页第 2+ 次拖拽的鼠标事件流停摆（裸 Playwright 复现，零 Hercules 代码参与）——环境特性，r2 起即存在，非 R3-3 回归 | 本报告 §一/R3-3 | M1 冒烟归因时以"选择器解析成功"为准；test-report 披露一段 |
| F4 | P3（备注，已有勘误） | E3 对 `localhost:PORT` 形态的新拒绝（r2 会被 https 前缀升级后导航）——行为收紧非漏洞 | 实测 `localhost:8799/x` 被拒；`127.0.0.1:PORT` 不受影响；任务 URL 恒带 scheme | 勘误已在 `test-report.md:381`；r3 报告若出现该形态被拒格按 `[OPEN_URL_BLOCKED]` 披露口径呈现 |
| F5 | P3（备注） | planner 的 4096 上限依赖 `SimpleHercules.create` 的 adapt 预注入——绕过 create 直接构造 PlannerAgent 时 `max_tokens=None`（本审查直构时观察到的现象，非生产路径） | benchmark 必经 `SimpleHercules.create`（runner.py:71），T1 以 adapt 步锁定 4096 | 无需动作；实现说明里保持"planner 4096 由 adapt 注入"的表述即可 |

风险计数：**P1×1、P2×1、P3×3，RED×0。**

## 三、建议运行命令（批准后执行）

M1 pilot+smoke（14 执行，全开 flags，drag kill-switch 判据 = stdout.log 中 `Found source element using selector` ≥1 次）：

```bash
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r3 --stage pilot --provider glm --exp-root dev_runs/benchmark \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-max-tokens 768 --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline \
  --smoke-cells drag-items,drag-box
```

M2 headline（分段，`--max-cells` 配速，断点续跑同命令；**建议 F1 一行修复落地后启动**）：

```bash
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r3 --stage full --provider glm --exp-root dev_runs/benchmark \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-max-tokens 768 --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline \
  --max-cells <按5h窗口配速>
```

M4 D-ablation 臂（另批、独立 exp-id/exp-root，数字必须带 `episode_max_time_ms=480000` 标注单独出现）：

```bash
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r3-d480 --stage full --provider glm --exp-root dev_runs/benchmark-d480 \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-max-tokens 768 --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline \
  --episode-ms 480000 --timeout-s 900 \
  --max-cells <按5h窗口配速>
```

## 四、结论

**YELLOW。** R3 六项变更的引擎行为、flag 门控、off 复现与注入精确性全部实证通过，无新增作弊面（drag 透传仅进入 Playwright selector 引擎，scheme 白名单/沙箱关停/文件工具标记三道扫描闭环，断言纪律 prompt 零泄露），headline 与 D 臂互不干扰。唯一 P1 是 E4 clean 口径在"无效格被重试"场景下相对 r2 手工口径的 last-wins 弱化（一行可修，不阻塞 M1）；唯一 P2 是 r2 W2 实物清理尚有 5 个残留文件。两者处理完毕即可升级 GREEN 进入 M2 headline。
