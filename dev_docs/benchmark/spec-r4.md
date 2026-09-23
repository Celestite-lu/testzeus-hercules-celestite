# R4 实现规格 — miniwob-r4（spec-r4）

> 读者为实现者。读完本文不再需要做任何设计决策；未规定处按"最简单确定性行为"处理并记入实现说明。
> 契约来源：`plan-r4.md`（采纳清单 R4-A/B/E/D/F/C 与拒绝理由）、`spec-r3.md`（r3 现行规格，本文件只写**增量**；未提及处沿用 r3/r2/r1）、`analysis-r3-failures.md`（死因证据）、`security-review-r3-post.md` §8（P1/P2 必办）。
> 现场锚点（行号为本规格订立时的代码位置）：`testzeus_hercules/core/tools/get_interactive_elements.py:44-89`（flatten 过滤器）、`testzeus_hercules/utils/get_detailed_accessibility_tree.py:255-266`（抓取属性表）、`testzeus_hercules/core/simple_hercules.py:956-987`（路由与建图）、`record2gherkin/benchmark/miniwob_server.py:238-249`（补丁拼装）、`record2gherkin/benchmark/orchestrator.py:447/537`（扫描器）。
> 总开关纪律：**r4 新增项独立 flag，默认 off = r3 行为逐字节复现**。唯一例外是 R4-E 的 harness 披露增强（不带 flag，判分零影响——r3 E2/E3/E4/E6 同一先例与理由）。headline 显式全开并在 manifest `flags` 记录。密钥红线沿用：key 只经 subprocess `env=` 注入，生成的 `agents_llm_config.json` 一律省略 `model_api_key`。
> 引擎接线点声明：r4 触碰 `testzeus_hercules/` 的文件为 `config.py`、`core/tools/get_interactive_elements.py`、`utils/get_detailed_accessibility_tree.py`、`core/simple_hercules.py`——均为 plan-r4 §1 采纳项的必要落点，**不触碰判分/奖励路径**（C1a 优先级、`fetch_reward`、`/latest`、miniwob_server 补丁的奖励 hook 零改动）。harness 触碰 `record2gherkin/benchmark/{orchestrator,miniwob_server,metrics}.py`，扫描器只做标注（`flagged`），永不改写 `status`/`official_passed`。

## 0. r4 运行前置（硬 gate）

1. C1c 余额预检、C1b 熔断器、429 熔断标记、`--max-cells` 配速、attempt 分目录日志——全部沿用 r3，零改动。
2. 预算：`R2_BUDGET_CAP = 144` 沿用。pilot+smoke 14 + headline 130 = 144 恰满；M4/M5 臂独立 exp-id/exp-root 另批另计（§6）。
3. headline 命令见 plan-r4 §3；默认命令（无 r4 flag）= r3 行为复现 + R4-E 披露增强。
4. **r3 遗留表述修正**：r4 headline 命令**不含** `--nav-max-tokens`（CLI 保留不删，兼容旧行为；安全复审 §5 实证 no-op）。凡 r4 报告引用 r2/r3 flags 表，`nav_max_tokens=768` 必须带"注入成功、provider 未生效"标注。

## 1. R4-A md 覆盖扩展（flag `--md-extended`，engine）

### 1.1 `config.py`

- `relevant_keys` 增 `"MD_INTERACTIVE_EXTENDED"`；`_finalize_defaults` 增 `setdefault("MD_INTERACTIVE_EXTENDED", "false")`；新增 getter（与 `get_sandbox_disabled` 同风格，config.py:1221-1223）：

```python
def get_md_interactive_extended(self) -> str:
    """Raw MD_INTERACTIVE_EXTENDED config value ("false" by default). Off = r3 parity."""
    return self._config.get("MD_INTERACTIVE_EXTENDED", "false")
```

### 1.2 `core/tools/get_interactive_elements.py`（死因修复点）

- 现状死因（r4-A 立项根据，实现者须知）：`flatten_elements`（L44-89）只收录 `node.get("r","")` ∈ 白名单 role（**`r` 键在现行管线中不存在——`rename_children` 未被调用，实际键是 `role`，此 role 匹配今天是死分支**）或 `tag` ∈ {a,button,input,select,textarea} 的带 md 节点。email 家族的 `.email-thread` 行（div）、`.star`/`.trash` 图标（span）注入了 md 但在终表被整批丢弃。
- **重构（唯一目的：可测性；off 行为逐字节不变）**：把 L45-89 的 `flatten_elements`/`compact_value`/`compact_interactive_node` 原样提为模块级 `flatten_interactive_nodes(root, *, extended: bool, max_nodes: int = 150) -> list[dict]`，内部常量提为模块级 `INTERACTIVE_ROLES_BASE`（现 L47-64 字面量）、`INTERACTIVE_TAGS_BASE`（现 L82 集合）、`EXTENDED_TAGS = {"div","span","li","tr","td","th","img","label"}`、`EXTENDED_ROLES = {"row","cell","listitem","img"}`、`EXTENDED_IDENTITY_KEYS = ("name","title","description","text","aria-label","class","id")`。
- **收录判定**（按序短路，与现结构同形）：

```python
role_hit = node.get("r", "").lower() in roles or (extended and str(node.get("role") or "").lower() in roles)
tag_hit = node.get("tag", "").lower() in (tags_base | (EXTENDED_TAGS if extended else set()))
extra_hit = extended and node.get("tag", "").lower() in EXTENDED_TAGS and any(
    node.get(k) not in ("", None, [], {}) for k in EXTENDED_IDENTITY_KEYS
)
if "md" in node and (role_hit or tag_hit or node.get("clickable", False) or node.get("focusable", False) or extra_hit):
    ...收录...
```

  其中 `roles = INTERACTIVE_ROLES_BASE | (EXTENDED_ROLES if extended else set())`。**off 语义核对**：extended=False 时 `role_hit` 仍只查 `r` 键、tag 集不变、`extra_hit` 恒 False——与现 L80-88 条件完全等价（r3 复现）。
- **上限（只约束终表，不约束注入/判分）**：extended=True 时收录计数达 `max_nodes(150)` 即停止收录，并 `logger.warning("[R2G_MD_TRUNCATED] interactive table capped at %d (dropped >=%d)", max_nodes, <剩余数>)`；JSON 输出内**不加**哨兵节点。off 时上限不生效（现状无上限）。
- 工具函数体改为调用 `flatten_interactive_nodes(extracted_data, extended=<get_md_interactive_extended()=="true">, max_nodes=150)`；其余（compact、json.dumps、空表返回 "Its Empty, try something else"）逐字节保留。
- 父 name/title 向子继承的现有行为（L66-77，含对 child dict 的就地修改）原样保留。

### 1.3 `utils/get_detailed_accessibility_tree.py`（class 语义锚）

- `__fetch_dom_info` 的 `attributes` 列表（L255-266）改为：

```python
attributes = ["name", "aria-label", "placeholder", "md", "id", "for", "data-testid", "title", "aria-controls", "aria-describedby"]
if get_global_conf().get_md_interactive_extended().strip().lower() == "true":
    attributes.append("class")   # r4-A：图标无文本，class 是 star/trash 等的唯一语义锚
```

  （import `get_global_conf` 已在该文件头部存在。）off 时列表与 r3 逐字节一致。
- 披露义务：`class` 追加同样影响 `get_input_fields`（`only_input_fields=True` 共用 `__fetch_dom_info`）——判分中立，test-report 必须披露。
- `__inject_attributes`/`isInteractiveElement` **零改动**（现场证据：cursor:pointer 启发式已覆盖 `.email-thread`/`.star`/`.trash`/`.card.hidden`，任务在主文档）。**降级授权**：仅当 T4（真浏览器测试）证明目标元素未被注入 md 时，允许最小修补——cursor 检查改为 `element.ownerDocument.defaultView.getComputedStyle(element)`（iframe 安全取值），仍在同一 flag 语义内；修补后 T4 复验，仍失败走 kill-switch（plan-r4 §1-R4-A），headline 去掉 `--md-extended` 并固化放弃声明。

### 1.4 orchestrator

- CLI `--md-extended`（store_true，help 注明"R4-A: MD_INTERACTIVE_EXTENDED=true, extended interactive-element table (default off = r3 parity)"）；`_child_extra_env` 在开启时注入 `MD_INTERACTIVE_EXTENDED=true`；`flags["md_interactive_extended"] = bool`（off 记 false）。

## 2. R4-B 终止前校验（flag `--verify-before-done`，engine）

### 2.1 `config.py`

- `relevant_keys` 增 `"VERIFY_BEFORE_DONE"`；`setdefault("VERIFY_BEFORE_DONE", "false")`；getter `get_verify_before_done() -> str`（同 §1.1 风格）。

### 2.2 `core/simple_hercules.py`

- `AgentState` 增键 `verify_rounds: int`；`process_command` 的 initial state（L1021-1042）增 `"verify_rounds": 0`。
- 模块级常量（逐字）：

```python
_VERIFY_STEP = (
    "VERIFICATION REQUIRED BEFORE TERMINATION: call get_page_text and get_interactive_elements, "
    "then report whether the task goal is visibly satisfied on the current page (note any "
    "'EPISODE ENDED' terminal cue). Do not perform any other action."
)
```

- 新节点（打印/日志用 `logger`，不用 print）：

```python
def _verify_gate_node(self, state: AgentState) -> dict[str, Any]:
    rounds = int(state.get("verify_rounds", 0) or 0) + 1
    logger.warning("[R2G_VERIFY] forced pre-terminate verification round (verify_rounds=%d)", rounds)
    return {
        "next_step": _VERIFY_STEP,
        "target_helper": "browser",
        "terminate": "no",
        "is_assert": False,
        "verify_rounds": rounds,
    }
```

- `_route_after_planner`（L956-964）**最前**插入（`verify_flag = get_global_conf().get_verify_before_done().strip().lower() == "true"`，函数内现取现读）：

```python
if (
    verify_flag
    and terminate == "yes"
    and bool(state.get("is_passed", False))
    and int(state.get("verify_rounds", 0) or 0) < 1
):
    return "verify"
```

  其余逻辑逐字节不动：`is_passed=false` 的 terminate 直接 `"end"`；`verify_rounds ≥ 1` 后任何 terminate 直接放行（**硬上限 = 每场景至多 1 次核验**，无循环面）。
- `_build_graph`（L970-987）：`graph.add_node("verify", self._verify_gate_node)`；planner 条件边映射改为 `{"executor": "executor", "assertion": "assertion", "verify": "verify", "end": END}`；`graph.add_edge("verify", "executor")`。核验步经既有 executor 路径执行，观察以 `[browser_nav_agent]: …` 回灌 planner（现 L730 行为），planner 二次裁决。
- **planner prompt 零改动**（R3-6 断言纪律原样共存；r3-post 实证 prompt 层不足，r4 只做路由层）。**判分链零改动**：junit/官方判定路径不触碰；已落盘的正奖励不受核验轮影响（r3-post §4"反向桥接空"）。
- 与 C2 的交互（无代码，行为说明）：核验观察中 `EPISODE ENDED` cue 由 `--terminal-cue` 注入（headline 常开）；cue 缺席 = 页面未出裁决的直接反证。

### 2.3 orchestrator

- CLI `--verify-before-done`（store_true）→ env `VERIFY_BEFORE_DONE=true`；`flags["verify_before_done"] = bool`。

## 3. R4-E 完整性必办包（harness，判分中立，不带 flag）

> E1/E2 是 miniwob_server 补丁与服务端点（append-only 记录）；E3 是扫描器标注（只 `flagged`，永不 `invalid_reason`）；E4 是 metrics 披露字段。对任何运行（含 flags 全 off）的判分影响为零——不带 flag 的理由同 r3 E 类。

### 3.1 `miniwob_server.py`（E1 off-seed 探测 + E2 episode 启动信标）

- `patch_core_js(source, *, terminal_cue=False, single_start=False, offseed_beacon=False)` 增第三 kwarg；`offseed_beacon=True` 时追加补丁（追加序：auto_start → REWARD_HOOK → terminal_cue（如有）→ 本补丁，仍为纯 append）：

```js
/* r4 integrity beacons (harness-only): off-seed detection + seeded-load beacon */
(function () {
  var q = function (n) { var m = new RegExp("[?&]" + n + "=([^&#]*)").exec(location.search); return m ? m[1] : null; };
  var beep = function (ep, extra) {
    try { fetch("/__r2g_" + ep, { method: "POST", body: JSON.stringify(Object.assign({ path: location.pathname }, extra || {})) }); } catch (e) {}
  };
  if (!q("r2g_seed")) { beep("offseed"); } else { beep("epstart", { seed: q("r2g_seed") }); }
})();
```

  语义：`offseed` = 页面加载时 query 无 `r2g_seed`（安全复审 M2 的直接证据）；`epstart` = 带种子加载（auto-start 预期发生）。`offseed_beacon=False` 时输出字节与 r3 版 `patch_core_js` 完全一致（函数级回归锁定）。
- 服务端两个新端点：`POST /__r2g_offseed` → 向 `<exp_root>/offseed.jsonl` 追加一行 `{"ts": <iso8601>, "path": <body.path 或 "">}`；`POST /__r2g_epstart` → `epstart.jsonl` 一行 `{"ts": …, "path": …, "seed": …}`。body 尽力 JSON 解析、失败取空串；响应一律 `204`；文件 flush per line（同 rewards）。两文件不存在时首写创建。`/healthz` 形状不变。
- orchestrator 启动 server 时恒传 `offseed_beacon=True`（headline 与默认运行皆然）；`flags["offseed_beacon"] = true` 记录。

### 3.2 `orchestrator.py`（E3 扫描器扩展）

- 新常量：`REASON_OFF_SEED = "off_seed_navigation"`、`REASON_ZERO_REWARD = "zero_reward_events"`、`REASON_EP_NEVER_STARTED = "episode_never_started"`——全部 **flagged-only**（`CellScan(flagged=True)`，`invalid_reason` 恒 None；经现有 `CellScan` 合并语义叠加）。
- ① **nav=0 且 timeout**：在持有该格终态 status 的组装处，`status == "timeout" and scan.task_url_navigations == 0` → 合并 `CellScan(flagged=True)`（安全复审 §8-2 原文；r3 中该形态是 off-seed 格唯一可见信号）。
- ② **offseed 归因**：读 `offseed.jsonl`，新 helper `match_records(path_prefix: str, window: tuple[float, float], records: list[dict]) -> bool`（时间窗过滤，与 rewards 的 run 窗口过滤同法；`path` 与该格任务页前缀匹配）；命中 → 合并 `REASON_OFF_SEED` 的 flagged。
- ③ **零事件**：该格终态 `reward_records == 0`（`scan_cell_rewards` 现有产出）→ 合并 `REASON_ZERO_REWARD`；若同时 epstart 窗口内零命中 → 改并 `REASON_EP_NEVER_STARTED`（细分"从未启动"vs"已启动无奖励"，对应分析 §4.5 的两种缺口）。
- 一格可同时携带多个 flagged 原因（合并语义已支持）；全部只披露，不改判。

### 3.3 `metrics.py`（E4 披露字段）

- `Summary` 增 `zero_reward_cells: list[dict]`：latest 行 `reward_records == 0` 的格，每项 `{"task_id", "seed", "status"}`，按 task_id 排序，>50 条截断并计数（复用 `invalid_cells` 样式）；`as_dict()` 同步输出。`overall`/`clean`/`invalid_cells` 零改动。

## 4. orchestrator 汇总（r3 → r4 增量）

- 新 CLI：`--md-extended`、`--verify-before-done`（均 store_true，默认 off）。r3 全部 CLI 原样保留（含闲置的 `--nav-max-tokens`，headline 不再传）。
- `_child_extra_env` 键集（headline 全开）：r3 全集（`LATENCY_ENV_OVERRIDES` 五键、`LOAD_EXTRA_TOOLS`、路由 4 键、`LLM_PLANNER_REQUEST_TIMEOUT`、`EXTRA_TOOLS_MODULES`、`SANDBOX_DISABLED`、`PLANNER_ASSERT_DISCIPLINE`）加 `MD_INTERACTIVE_EXTENDED`、`VERIFY_BEFORE_DONE`。**不含** `NAV_MAX_COMPLETION_TOKENS`。注入精确性受 T2 锁定。
- `flags` dict 新增键：`md_interactive_extended`、`verify_before_done`、`offseed_beacon`（恒 true）；`nav_max_tokens` 键在 r4 headline 的 manifest 中**缺省**（不写 false 也不写 768——避免"声明在位"的 r3 式失真）。
- `LATENCY_ENV_OVERRIDES` 五键值逐字节不动。

## 5. 离线单测清单（`tests/record2gherkin/`；零真实 LLM、零外网；env 用 monkeypatch.setenv 隔离）

**T1 config 开关**：两新键默认 `"false"`；getter 返回原值；monkeypatch 置 true 后 getter 跟随；relevant_keys 含两键（否则 env 不入 config）。

**T2 orchestrator 注入与 flags**：(a) r4 全开 → `_child_extra_env` = r3 headline 键集 + `MD_INTERACTIVE_EXTENDED`+`VERIFY_BEFORE_DONE`，**多一个键即失败**，且无 `NAV_MAX_COMPLETION_TOKENS`；(b) 默认（无 r4 flags）→ 两新 env 零出现、`flags` 中两键为 false；(c) manifest `flags` 含三个新键（`offseed_beacon` 恒 true）；(d) `LATENCY_ENV_OVERRIDES` 五键值不变（回归）。

**T3 flatten 过滤器（纯单元）**：(a) off 模式 golden——固定合成树（含 role=button 节点、裸 md div、无 md 节点）输出与**现实现逐字节一致**（迁移前先固化 golden JSON）；(b) extended 收录：`{md, tag:'div', name:'Tisha'}`、`{md, tag:'span', class:'star'}`（无 name）、`{md, tag:'li', id:'x'}` 均收录；无任何 identity 键的 `{md, tag:'div'}` 不收录；(c) extended 的 role 白名单：`role:'row'/'cell'/'listitem'/'img'` 节点收录；(d) 上限：>150 收录截断 + caplog 含 `[R2G_MD_TRUNCATED]`，前 150 条保序；(e) off 模式 151+ 节点不截断（现状）。

**T4 真浏览器注入/终表验证（Playwright；chromium 不可用时 pytest.skip，不失败）**：`http.server` 起临时端口服 `record2gherkin/benchmark/miniwob_html/`；打开 `miniwob/email-inbox.html`；点击 START 遮罩启动 genProblem（选择器以 vendored core.js 的 cover 结构为准，实现说明记录）；config 日志目录指 tmp；flag on 调 `do_get_accessibility_info(page, only_input_fields=False)` 后经 `flatten_interactive_nodes(extended=True)`：含 ≥1 个 class 含 `email-thread` 的条目、≥2 个 class ∈ {star, trash} 的条目、全部带 md；flag off：这些条目零出现（r3 复现）。同法对 `find-greatest.html`：flag on ≥4 个 `.card` 条目（name 为数字）。**本测试同时是 A 的注入层假设验证器（§1.3 降级授权的触发判据）**。

**T5 verify 路由**（`SimpleHercules.__new__` 绕 init + stub）：(a) flag on + terminate=yes + is_passed=True + verify_rounds=0 → `"verify"`；(b) `_verify_gate_node` 返回值恰为 spec §2.2 字典（next_step == `_VERIFY_STEP`、verify_rounds=1、terminate="no"）且 caplog 含 `[R2G_VERIFY]`；(c) verify_rounds=1 + 同条件 → `"end"`（硬上限）；(d) is_passed=False + terminate=yes → `"end"`（不核验）；(e) flag off + is_passed=True → `"end"`（r3 复现）。

**T6 verify 全链（stub planner/executor 序列）**：flag on，planner stub 首轮 terminate=yes/is_passed=true、二轮同值 → 执行序 = planner→verify→executor→planner→END，executor 恰执行 2 次、`_VERIFY_STEP` 出现在第二次执行的状态里；planner 二轮改 is_passed=false → END；全程 verify_rounds 从不超过 1。

**T7 扫描器与 metrics**：(a) 合成 timeout 行 + nav==0 → flagged；(b) offseed 记录入窗 → `off_seed_navigation`、出窗 → 不标；(c) reward_records==0 → `zero_reward_events`；epstart 零命中 → `episode_never_started`（替代而非叠加）；(d) 干净行零标注（r3 回归）；(e) `zero_reward_cells` 清单与 >50 截断；(f) **r3 数据形态回放**：按分析 §4.5 构造 6 格 fixtures（NR3 + 3 零事件 T），断言恰好这 6 形态命中零事件标注、其余 119 形态零命中。

**T8 补丁与服务端点**：(a) `patch_core_js(..., offseed_beacon=False)` 输出 == r3 版函数输出（golden 字节）；(b) True → 追加段含 `/__r2g_offseed` 与 `epstart` 且仍为纯 append（前缀字节不变）；(c) handler：POST 两端点 → jsonl 各一行、字段齐、204；坏 body 不 500；(d) 奖励端点 `/__r2g_reward` 行为零变化（回归）。

**T9 工具输出 parity**：monkeypatch `do_get_accessibility_info` 返回固定树 → `get_interactive_elements` off 模式 JSON 与 r3 golden 一致（含空表文案分支）；extended 模式含新条目。`get_input_fields` off 不变、extended 产物含 class 字段（§1.3 披露的测试锚）。

**T10 回归**：`tests/record2gherkin/` 全量绿；r3 的 T1–T12 语义不受影响（凡涉及 flatten 内部结构的既有用例按 T3 的模块级入口适配并在实现说明记录）。

## 6. ablation 臂（M4/M5，零 M0 代码；获批后实现/执行）

- **M5 480s 对照臂（零代码）**：headline 同 flags + `--episode-ms 480000 --timeout-s 900 --exp-id miniwob-r4-d480 --exp-root <独立目录>`；135 执行；所有数字带 `episode_max_time_ms=480000` 标注单独出现，与 headline 双列、禁止合并分母。
- **M4 多 seed majority-of-3（待批后实现两小 CLI）**：`--cells <csv 任务名>`（plan 过滤）+ `--seed-variant <int>`（seed 派生偏移：`seed_i = derive(base_seed, i)`，i=0 即 headline 主 seed）；命令 = headline 同 flags + 两 CLI + `--exp-id miniwob-r4-ms3 --exp-root <独立目录>`；只跑边缘集（r2↔r3 翻转 21 格 ∪ r4↔r3 新翻转格）× variant ∈ {1,2}（42–50 执行）。产出口径 = 每格 3 seed 的 majority（≥2/3 计过），**仅作测量稳定化列**；headline 官方/clean 口径永不引用。best-of-3 不实现（plan-r4 §1-R4-D 拒绝）。触发门控与批准流程见 plan-r4 §4-M4。

## 7. 验收标准

1. `uv run pytest tests/record2gherkin -q` 全绿；`make fmt` 后 `make lint`（black --check）通过。
2. T1–T10 全绿；diff 审计：判分链（`build_result_row` 的 status/official_passed 语义、miniwob_server 奖励 hook、`/latest`、C1a 优先级）零改动；扫描器只增 flagged 标注。
3. M1（14 执行，全开 flags）：**A kill-switch 有明确二选一判定记录**——T4 通过 + 冒烟 2 格 stdout 中出现对 md 表内条目（email-thread/star/trash 类）的成功 `Executing ClickElement with "[md='…']"`（同 md 不落 not-found）→ A 进 headline；否则去 `--md-extended` 并固化放弃声明。B：每格 `[R2G_VERIFY]` ≤1 次、无 planner 循环；email-inbox-delete 冒烟的 junit 诚实化方向被记录。表长遥测：冒烟格 `[R2G_MD_TRUNCATED]` 出现次数 = 0；无 402。
4. M2 headline：125 任务全覆盖、总执行 ≤130；manifest `flags` 记录 plan-r4 §3 全集且无 `nav_max_tokens` 键；`clean` 口径由 metrics 产出（预期 invalid=0，与 official 同值；若非 0 双列）。
5. 披露完整性（test-report.md 必须逐项出现）：① A 感知层变更声明 + 扩表前后 md 表长度统计与截断计数 + `class` 字段对 get_input_fields 的影响；② B 核验轮触发数、junit↔官方不一致数对照（r3 的 24 假阳性 → r4 实测值）；③ E 的 offseed/epstart/零事件清单及"只披露不改判"说明；④ `--nav-max-tokens` 摘除理由（安全复审 §5 引用）；⑤ r2/r3 既有披露全单沿用（provider 混杂、240s/600s、单 seed 单次、Gherkin 中转、奖励端点无防伪造、视觉盲区 11 格）。
6. 脱敏红线复核：`KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs record2gherkin tests dev_docs` 零命中。
7. （若获批）M4/M5：独立 exp-id/root；所有数字带口径标注（`seed_variant` / `episode_max_time_ms`）双列呈现；逐格配对归因表入 analysis-r4。

## 8. Out of Scope

spec-r3 §10 与 spec-r2 §10 全部沿用，另加：

- plan-r4 §1"明确不做"全表：视觉/画布/几何族（L6）；click 工具选择器透传（r5 候选）；hot-cold `#touch-area` 专项；off-seed 自动补齐/阻断型防护；planner prompt"URL 逐字"行；`--nav-max-tokens` 的任何修复型实现（r5 前置 = provider max_tokens 尊重性预检）。
- executor 客户端流式截断与任何传输层改造（R4-C 拒绝案）。
- `isInteractiveElement` 的启发式重构（仅授权 §1.3 的最小修补，且以 T4 失败为前提）。
- 判分/口径链任何语义改动（`_rate`/`overall`/`clean`、C1a、last-wins、奖励 hook）；`flags` 历史键的回写。
- M4/M5 的自动执行与 headline 合并口径（须用户逐项批准）。
- r2/r3 数据的重新判分或 results.jsonl 的任何回写。
