# R4 实验前安全审查报告（security-review-r4-pre）

- 日期：2026-09-24。审查对象：r4 相对 r3 的全部变更（merge `e3ea331`；引擎 `c3f8988` + harness `b23ec40`）：R4-A `--md-extended`、R4-B `--verify-before-done`（重点）、R4-E 完整性信标、plan-r4 §5 预算全局护栏、flag 矩阵、r3 遗留复核。法定输入：`plan-r4.md`、`spec-r4.md`、`review-r4.md`、`security-review-r3-post.md` §9、`test-report.md` §15。
- 方法：r3..r4 全量 diff 实读 + 注入链/判分链源码追踪（simple_hercules 路由层、get_interactive_elements 终表过滤器、get_detailed_accessibility_tree 可见性门、vendored `miniwob_html/core/core.js` 奖励存储形态、open_url scheme 白名单、extra_tools 全目录 JS 能力审计）+ 离线实测复跑（`uv run pytest tests/record2gherkin` → **492 passed**，83.4s，本机独立复现 test-report §15.1）。
- **结论：GREEN（0 RED / 0 P1 / 0 P2；4 条 P3/信息级附注。无阻塞项，可进 M1 pilot+smoke；两条 P3 建议在 M2 headline 前后持续观察）。**

核心判定一句话：**r4-B 的禁读 reward 红线不是靠 prompt 自觉，而是结构性成立**——`WOB_REWARD_GLOBAL` 是纯 JS 全局量（`miniwob_html/core/core.js:44`），从不落 DOM；唯一可能渲染它的 HUD `#reward-display` 在 seeded 加载下被 r1 V2 加固置 `display:none` 且 `core.updateDisplay` 被 stub（`miniwob_server.py:116-118`）；browser 工具集 15 件全部固定用途、无 JS-eval 工具；`open_url` r3 起拦截非 http(s) scheme（`open_url.py:94-101`）；python 沙箱无页面访问且 headline 关停。**verify 轮没有引入任何一条机器可走的读全局路径。**

---

## 一、逐项核验结果

### R4-A `--md-extended` 终表过滤器扩展 — PASS

- **display:none 入表面 = 结构性为零**。可见性门在终表过滤器**上游**：a11y 树构建 JS 的 `processElement` 对隐藏元素直接 `return null` 且**整棵子树被剪掉**（`get_detailed_accessibility_tree.py:1043-1050` `isElementHidden`＝display:none ∨ visibility:hidden ∨ aria-hidden；`:1157-1212` 隐藏节点不递归子元素）。r4-A 只放宽**收录判定**（tag/role 白名单 + identity 门控，`get_interactive_elements.py:100-150`），不触碰可见性判定——extended 标签（div/span/li/tr/td/th/img/label）只能捞到 **r3 时已在树里的可见节点**。页面不该见的元素不可能因扩表变得"可见可点"。
- **奖励值不可经终表泄漏**（r4-A 特有的新担忧，已排除）：`#reward-display` HUD（含 `Last reward:` 数值模板，`core.js:171-239`）在 seeded 加载被补丁 A 置 `display:none` + `updateDisplay` 永久 stub（`miniwob_server.py:116-118`），`get_page_text` 走 innerText（跳过隐藏子树）、a11y 树走 isElementHidden——双通道均不可见，且数值文本根本不回写。r1 既有加固，r4 未触碰。
- **150 上限灌水风险 = 有效性风险，非作弊面**：截断只发生在 extended 模式、只砍**尾部**（文档序保序，T3d 实证 `md 1..150` 保留）、`[R2G_MD_TRUNCATED]` 只进服务端日志不进 agent JSON（设计如此）。丢元素只会让 agent 更瞎（漏点按钮 → 该格自败），**不可能造出奖励**。MiniWob 任务 DOM 小（T4 真浏览器实测 email-inbox 行/图标入表无截断），M1 kill-switch 判据含"截断计数=0"，遥测在位。
- **anonymous 灌水被封**：extended 标签**只经 `extra_hit` 收录**（须带 identity 键之一，`get_interactive_elements.py:136`）——这是对 spec §1.2 条件块的**偏保守实现偏差**（spec 字面会把裸 div 经 tag 集放进表），方向安全、已在 test-report §15.4-1 固化披露。附注备案即可。
- **off = r3 逐字节 golden 抽验**：T3a 与字面 golden JSON 逐字节比对（golden 由迁移前 r3 实现产出，提取脚本留档 `dev_runs/r4_impl/capture_golden.py`，非自证）；T8a 补丁层 off golden；T9 工具输出 golden。三项均在本次复跑的 492 passed 内。MF-1 的 `allowed_keys` 无条件加 `class` 对 off 惰性（off 模式抓取属性表不含 class，compact 按 `key in node` 过滤）——逻辑闭环，T3a 断言 `all("class" not in entry)` 锁定。
- **披露义务在位**：`class` 追加同样影响 `get_input_fields`（共用 `__fetch_dom_info`，`get_detailed_accessibility_tree.py:267-268`）——判分中立，test-report 已承诺披露。

### R4-B `--verify-before-done` verify 门控节点 — PASS（重点项，红线全部成立）

- **禁读红线三面核验**：
  1. **prompt 面**：`_VERIFY_STEP`（`simple_hercules.py:55-58`）白名单式指导——只许 `get_page_text` + `get_interactive_elements` + 报告可见状态 + "Do not perform any other action"；**全文零 reward 全局引用**，无任何引导读 `WOB_REWARD_GLOBAL`/`WOB_RAW_REWARD_GLOBAL` 的措辞。
  2. **代码面**：`_verify_gate_node`（`:969-979`）只写路由状态（`next_step`/`target_helper`/`terminate="no"`/`is_assert=False`/`verify_rounds`），不读不写任何奖励变量；verify 节点后经既有 executor 路径（`add_edge("verify","executor")`，`:1018`），判分链（junit/官方 `/latest`）零改动。
  3. **结构面（决定性）**：见结论段——DOM 读类工具在物理上够不到 JS 全局量；JS 执行通道全数封闭（无 eval 工具 + `javascript:` scheme 拒绝 + 沙箱无页面访问且 headline 关停）。"读全局的路径"在**任何一轮**都不存在，verify 轮没有新增路径。prompt 级红线（不读全局）因此不存在被违反的机器通道。
- **1 次硬上限**：路由条件 `verify_rounds < 1` 且 gate 节点恒 +1（`simple_hercules.py:987, 969-979`）——第二次 `terminate=yes ∧ is_passed=true` 直接 `"end"`（T5b 锁定）；verify→executor→planner 无回边到 verify（`is_assert=False` 使 assertion 分支也不可达），**无循环面**。T6 全链 stub 图跑实证执行序 `planner→verify→executor→planner→END`、`verify_rounds` 全程 ≤1。
- **与 C2 终局 cue 的交互 — 时序安全**：`TERMINAL_CUE_PATCH` 包裹在 `REWARD_HOOK_PATCH` **外层**（追加序 A→reward hook→cue），`endEpisode` 触发时**同步 XHR 的 reward POST 先完成、cue 文本后绘制**（`miniwob_server.py:180-200` 与 `:203-227` 的包裹次序）。verify 轮读到的 `EPISODE ENDED` 必然是裁决已落盘后的post-hoc 信号；cue 恒定文本、零数值零成败词（r2 审查定性维持）。cue 缺席 = 未出裁决的反证（spec-r4 §2 语义），核验不能改写官方判定。
- **触发面收敛**：只核验自报 pass（`is_passed=true`），失败终止直通 END（T5c）——判定权威始终在页面/服务端，核验只是反幻觉护栏；`is_passed` 由 planner LLM 输出解析（`simple_hercules.py:448,482`），verify 轮给 planner 二次观察机会，不存在"核验=放水"通道。
- **P3 附注**：verify 轮未做**机械工具限制**（executor 仍带全工具集，prompt 白名单无强制）——鉴于结构面已封死读全局通道，可接受；r4-post 若做 tool-level 白名单属加固非必改。另：核验轮消耗一次 planner+executor 回合（token 成本，`[R2G_VERIFY]` 日志可计），不影响判分。

### R4-E 完整性信标（E1/E2 端点 + BeaconLog + E3/E4 披露）— PASS

- **暴露面 = 恒 204 零信息**：`/__r2g_offseed`、`/__r2g_epstart`（`miniwob_server.py:486-501`）尽力解析 body、失败兜底空串，**恒 `204 No Content` 且无 body/Content-Length**（`_send_no_content`，`:410-414`，RFC 7230 合规）——页面侧拿不到任何回读信息，无探测/枚举价值。信标 JS（`:233-239`）load 期一次 fire-and-forget fetch，异常全吞，不触碰 episode/reward 链；补丁纯 append（T8b 前缀字节不变），off=T3 r3 字节 golden。
- **BeaconLog 数据非敏感**：仅 `{ts, path, seed}`（`path`=location.pathname，`seed`=harness 派生的确定性开局参数）——无页面内容、无 env、无 key；文件落 `<exp_dir>/offseed.jsonl|epstart.jsonl`（= rewards 文件同目录，`miniwob_server.py:522`），`dev_runs/` gitignore（.gitignore:177）覆盖。E3 扫描只产 `flagged` 注记（`flag_reasons` 恒伴 `invalid_reason=None`，T7a-d 锁定"只披露不改判"）；E4 `zero_reward_cells`/`reward_records` 均披露键、零进分母（metrics.py diff 核对）。
- **P3 附注（r1 面的顺带再定性，非 r4 新增）**：两个信标端点与 r1 的 `/__r2g_reward` 同一信任模型——绑 127.0.0.1:8462、无鉴权、无 Origin 校验，本机任意进程或 agent 误导航到的任意页面可 POST 伪造信标行（跨域 simple POST 可达、读被 CORS 挡）。伪造信标**只能污染披露注记**（flagged-only，不进 invalid_reason、不进分母），无判分影响；伪造 `/__r2g_reward` 理论上可 last-wins 覆盖 `/latest`——**该面 r1 既存、超出本次变更集**，且利用前提（知道端口+路径+seed 并把浏览器导去恶意页）恰是 E1 offseed 现在能侦测到的形态。r4-post 复核时以 offseed.jsonl 反向核对即可。`_serve_beacon` 的 Content-Length 无上限读取与 reward 端点同款（localhost 限内，信息级）。

### 预算全局护栏（plan-r4 §5）— PASS，如实分级：**披露机制 + 纪律约定，非强制机制**

- **机制部分（单 exp-dir 内成立）**：`cumulative_hercules_used` 由 `results.jsonl` 现存行实时计算（`orchestrator.py:1450-1470`），断点续跑同 exp-id 时文件累积、跨调用自动入账，Σ>144 触发 warning 日志 + `cumulative_over_cap=true` 入 manifest——orchestrator 无法"忘记"记账，T2e 锁定。执行行口径 = 带 `goal` 的行（no_goal 行不计）。
- **约定部分（跨 exp-id/exp-root 不设防）**：换 `--exp-id`（或 `--exp-root`）即从零计数，**无跨目录总账**；`results.jsonl` 为无签名 JSONL，手改行数即改读数。拆 exp-id 绕账在工具层**可行且不可被工具发现**，只能人工盘点 `dev_runs/` 目录。这与 plan-r4 §5 的自我定性一致（"量化披露（纪律条款，不阻断）"、"禁拆窗"是纪律语言）——**设计如此，如实分级：护栏强度 = 单 dir 自动化审计 + 跨 dir 人工纪律**。
- 结构性最坏 146 = M1 16 + M2 130（对 cap 144 偏差 +2 全为 pilot 域重试）已在代码注释量化披露，无隐瞒。**风险定级 P3**：这是成本纪律风险，不是分数完整性风险——记账绕过不影响任何一格的官方判定。

### flag 矩阵 — PASS，无冲突

- **headline**（test-report §15.5 cmd 2）：`--stage full` + r3 十项（**不含** `--nav-max-tokens`，r3-post §5 实证 no-op 后摘除）+ `--md-extended` + `--verify-before-done`。无 `--smoke-cells`——该参数是 pilot 专属，`stage != "pilot"` 直接 `BenchmarkError`（`orchestrator.py:896-897`），full+smoke 组合会**响亮报错**而非静默错跑。
- **冒烟**（§15.5 cmd 1）：`--stage pilot` + 4 个 smoke cells（A 两格 + B 两格）+ 全开 flags——两新 flag 与 stage/smoke/budget 正交；smoke 格永不重试（`RETRY_BUDGET` 口径不变）；预算 16 ≤ cap。B 判据（每格 `[R2G_VERIFY]` ≤1、无 planner 循环）与 A 判据（截断=0、md 点击命中）可同运行分别取证，互不污染。
- **`nav_max_tokens` 键缺省语义**：flags dict 仅在实传时写该键（`orchestrator.py:971-973`）——r4 headline manifest 无此键，杜绝 r3 式"声明在位但 no-op"失真；子进程 env 相应不含 `NAV_MAX_COMPLETION_TOKENS`（T2a 键集恰等断言）。GLM 端点忽略 max_tokens（r3 实证），行为与 r3 等价。**P3 附注**：读取 `flags.nav_max_tokens` 的旧 ad-hoc 分析脚本会遇到键缺失（KeyError），无既有下游工具受损证据，r4 报告引用历史 flags 表时按 spec-r4 §0.4 带"注入成功、provider 未生效"标注即可。

### r3 遗留复核 — PASS

- **F1 无效格不进重试池**：在位（`orchestrator.py:1395-1398`，`invalid_reason` 行显式排除 + 日志留痕）——重试"洗白"安全事件的口径弱化路径已封。
- **r2/r3 flag 零回归**：全部 CLI 保留；本次复跑 492 passed 含全部 r2/r3 套件，r3 既有 4 处断言适配（manifest flags 键集 + nav_max_tokens 缺省断言）均为 spec-r4 §4 强制的 schema 适配、语义断言一字未改（test-report §15.4-3）；T2d `LATENCY_ENV_OVERRIDES` 五键回归绿。判分链（`build_result_row` 语义、`/latest`、`REWARD_HOOK_PATCH`、`overall`/`clean`）diff 审计零语义改动（test-report §15.1 表第 5 行，本次 diff 复读一致）。
- **脱敏**：两新 flag 与信标不读写 key 文件；`role_routing_env`/`write_agents_llm_config` 的"key 只走 env"代码未动；test-report §15.6 已做全仓 key 扫描零命中，本次 diff 复核无 key 字面量。

---

## 二、向量表（作弊面/风险全清单）

| # | 向量 | 入口 | 结构屏障 | 判定 |
|---|---|---|---|---|
| V1 | verify 轮读 `WOB_REWARD_GLOBAL` 影响终止决策 | executor 全工具集 | JS 全局不落 DOM；HUD display:none+stub；无 eval 工具；`javascript:` 拒绝；沙箱无页面访问 | **关闭**（结构性，非仅 prompt） |
| V2 | verify 轮循环（核验→再核验） | `_route_after_planner` | `verify_rounds < 1` 硬上限 + 无回边；T5b/T6 锁定 | **关闭** |
| V3 | C2 cue 被当奖励信号预判分 | TERMINAL_CUE_PATCH | reward POST（同步）先于 cue 绘制；cue 恒定零值文本；官方判定服务端权威 | **关闭** |
| V4 | display:none 元素经扩表可点 | extended 终表 | 可见性门在过滤器上游，隐藏子树整枝剪除（r1 既有） | **关闭** |
| V5 | 150 上限截断灌水 | extended 终表 | 只伤有效性不造奖励；`[R2G_MD_TRUNCATED]` 遥测 + M1 kill-switch 判据 | **P3**（有效性盲区，agent 不知被截断） |
| V6 | 信标端点伪造记录污染披露 | 127.0.0.1:8462 POST | flagged-only 永不 invalid/进分母；恒 204 零回读 | **P3**（披露可污染、判分不可污染） |
| V7 | `/__r2g_reward` 伪造 last-wins 覆盖 | 同上（r1 既存面） | 需知端口+path+seed 并导入恶意导航；E1 offseed 使该导航可侦测 | **P3**（超出 r4 变更集，r4-post 以 offseed.jsonl 反查） |
| V8 | 拆 exp-id 绕预算总账 | orchestrator CLI | 单 dir 自动入账；跨 dir 无总账（设计即纪律条款） | **P3**（成本纪律风险，非分数完整性风险） |
| V9 | manifest `nav_max_tokens` 键缺省破坏旧脚本 | flags schema | spec-r4 §0.4 有意为之；无既有下游工具 | **信息级** |
| V10 | off 模式混入 extended 行为 | config/compact/golden | 属性表 flag 门控 + T3a/T8a/T9 字面 golden | **关闭**（492 passed 独立复现） |

## 三、风险计数与建议

- **计数：0 RED / 0 P1 / 0 P2 / 3 P3 / 1 信息级**（V5、V6、V8、V9；V7 为 r1 既存面顺带再定性，不计入 r4 新增）。
- 最关键一条：**verify 红线靠结构成立（V1 关闭）是本次最强结论；最值得持续观察的是 V6/V7——localhost 无鉴权 POST 信任模型下，信标披露可被伪造污染，r4-post 必须用 offseed.jsonl/epstart.jsonl 与 results.jsonl 交叉核对披露口径**。
- 无阻塞项；r4-post 审查落档义务：① `[R2G_MD_TRUNCATED]` 计数与表长遥测（V5）；② verify 轮触发数与 junit↔官方对照（V2/V3 实测确认）；③ offseed/epstart/零事件清单与"只披露不改判"核对（V6/V7）。

## 四、建议命令（与 test-report §15.5 一致，本审查验证过其组合合法性）

```bash
# 全量回归（本审查复跑通过：492 passed）
uv run pytest tests/record2gherkin -q

# 0) 预演
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r4 --stage full --dry-run --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --planner-timeout 150 --extra-tools --disable-sandbox --assert-discipline \
  --md-extended --verify-before-done

# 1) M1 pilot+smoke（--stage pilot 与 --smoke-cells 组合合法；full+smoke-cells 会被 BenchmarkError 拒绝）
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r4 --stage pilot --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --planner-timeout 150 --extra-tools --disable-sandbox --assert-discipline \
  --md-extended --verify-before-done \
  --smoke-cells email-inbox-forward,email-inbox-star-reply,click-link,email-inbox-delete \
  --max-cells <按5h窗口配速>

# 2) M2 headline full（130 ≤ 单次调用上限；累计目标 ≤144，超限自动 warning 入 manifest）
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r4 --stage full --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --planner-timeout 150 --extra-tools --disable-sandbox --assert-discipline \
  --md-extended --verify-before-done \
  --max-cells <按5h窗口配速，单段可达140>
```

断点续跑 = 重跑同命令（同 exp-id，护栏累计口径才有效）；**禁拆 exp-id 分窗**（V8，纪律条款）。
