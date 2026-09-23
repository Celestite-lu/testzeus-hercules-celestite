# Benchmark 安全审查报告（R2 · 实验前）

- 日期：2026-09-22（r2 尚未开跑：`dev_runs/benchmark/` 仅存 miniwob-pilot / miniwob-r1；本报告全部实测用独立空闲端口 60080 + `/tmp/r2g_audit_r2/` 独立 rewards 文件，未触碰任何历史产物）
- 审查对象：r2 相对 r1 的全部变更（C1a/b/c/d、C2、C3、C4a–f、C5、C6、C7、`--smoke-cells`、预算 cap 144）+ 新 GLM provider（runner/orchestrator/preflight）；逐项对照 `plan-r2.md` / `spec-r2.md` / `test-report.md` §12–13
- 方法标注：凡写【实证】均有本机复现实验、单测运行或代码位级核对证据；写【推理】为机制级推断未端到端复现。
- 本报告只审查与记录，未改任何代码、未做 git 操作、未起常驻进程。

---

## 1. 结论：YELLOW（做完 §5 两项加固即可开跑，即"YELLOW-后 GREEN"）

**未发现任何新的判分作弊通道**：C1a 判分重排不可能把页面判负的格翻成通过；C2 终局标记实证为恒定中性文本且判分链路零改动；C3 单次开局拦截态实证"点击 START 无 reward、不重开"；r1 的 file:// 与沙箱扫描器仍在每个执行格的生效路径上。两个中风险均为**预算/密钥安全**面而非成绩有效性面，加固成本都很低。

| 等级 | 数量 | 向量 |
|---|---|---|
| 高 | 0 | — |
| 中 | 2 | W1 C1c 预检不探 GLM planner 模型 glm-5.3（headline 配置下"用了但没探"）；W2 `--extra-tools` 全量加载 extra_tools，附带**任意文件读/写**工具（recall/persist/augment_findings），现有扫描器不覆盖 |
| 低 | 3 | W3 重复 endEpisode 的陈旧值重发（r1 已知行为，C2 不新增记录）；W4 终局 cue 以最高 z-index 叠在页面左下（仅终局后存在，可能挡点击）；W5 预算 cap 144 是**单次调用**上限，跨调用无合计拦截（`--force`/重复 pilot 不计入） |
| 信息/无 | 7 | W6 C1a 判分修复实证无翻判通道（回放锁 +5、反例锁定）；W7 C1b 熔断偏差全在"少重试"保守向；W8 C2/C3/C1c key 闭环实测全过；W9 C4 全部为行为/能力变更（非作弊，须披露）；W10 GLM 外呼面收敛（child 仅 bigmodel.cn + 127.0.0.1）；W11 扫描器在 r2 新代码下未被绕过；W12 **runbook 陷阱**：test-report §12.5 的命令仍是 `--nav-model deepseek-flash`，在 `--provider glm` 下会把 nav 路由到 bigmodel 端点上的 deepseek 模型名（全量报废级操作失误源） |

**最关键一条：W1+W12 组合——GLM headline（`--provider glm --role-routing`）的 planner 模型 `glm-5.3` 从未被 C1c 预检探测【实证：probed=[glm-5.3-flash]，planner=glm-5.3 未探】，而文档里的现行命令还带着过时的 `--nav-model deepseek-flash`；二者叠加的失败模式是"预检绿灯 → 每格 planner 运行期报错 → 最多 130 格预算报废"。修复都是一行级：预检补探 planner 模型 + 换用本报告 §7 的命令（去掉 `--nav-model`）。**

---

## 2. 向量清单（等级 / 实证证据 / 缓解）

### W1 C1c 预检不覆盖 GLM planner 模型 —— 中（预算风险，非判分风险）

**实证**：
- `orchestrator._run_preflight`（orchestrator.py:859-863）探测列表 = `[self.provider.model]` + role_routing 时的 `nav_model`（仅在 != provider.model 时追加）。四组合实测（本机实例化 Orchestrator 复算）：

  | provider | role_routing | probed | 实际使用 | 未探测 |
  |---|---|---|---|---|
  | deepseek | off | deepseek-v4-pro | deepseek-v4-pro | 无 |
  | deepseek | on | deepseek-v4-pro, deepseek-flash | 同左 | 无 |
  | glm | off | glm-5.3-flash | glm-5.3-flash（planner 也走 env 单模型） | 无 |
  | **glm** | **on（headline）** | **glm-5.3-flash** | planner=**glm-5.3**、nav/helper=glm-5.3-flash | **glm-5.3** |

- spec-r2 §3 明文要求"headline 需探测两个模型（planner 模型与 --nav-model）"；单测 `test_p_preflight_probes_glm_endpoint_and_models` 只断言了**显式传** `--nav-model glm-5.3` 的双探路径（test_provider_routing.py:107-109），默认路径漏网。
- 失败模式【推理】：glm-5.3 若不在 coding plan 可用清单 → 预检通过 → 每格 planner 首调报错。planner 报错通常产出 junit 失败（status=failed）或快崩——前者**不进**重试池也不触发熔断（C1b 只看 timeout/no_junit）→ 最多 125+5 格按序烧完（GLM coding plan 按量计费，损失=全量预算）。

**缓解（P0，一行级）**：`_run_preflight` 的探测列表在 role_routing 时并入 `self.planner_model`（即 `models = [provider.model, nav_model, planner_model]` 去重）；或运行前手工对 glm-5.3 做一次 1-token 探测并把输出粘贴进运行记录。二选一即可开跑。

### W2 `--extra-tools` 打开的是全量 extra_tools 工具面 —— 中（密钥/数据安全；自发概率低）

**实证**：
- `core/extra_tools/__init__.py:11` 以 `LOAD_EXTRA_TOOLS` 门控**整包动态 import**；orchestrator `--extra-tools` 只注入该键（orchestrator.py:886-887）。加载面（12 个工具，全部注册到 browser_nav_agent，部分含 api_nav_agent）：
  - `drag_and_drop`：**确认干净**——纯 Playwright 鼠标轨迹 down→move(20 步)→up（drag_and_drop_tool.py:119-137），无 page.evaluate、无文件、无网络。
  - `read_clipboard`：有两处 `page.evaluate`（clipboard_tools.py:49,61），但求值串是**硬编码常量**（`navigator.clipboard.readText()`/`.read()`），模型不可注入任意 JS——不构成任意 JS 执行面。MiniWoB 任务不使用剪贴板传奖励，读剪贴板无判分信息量。
  - `persist_findings` / `recall_findings` / `augment_findings`（file_handler_tool.py）：**任意路径文件写 / 读 / 追加**（.json/.yaml/.txt/.log，无路径白名单）。`recall_findings` 可把 `LLM-Key.txt`/`GLM-Key.txt`/`rewards.jsonl` 读进 LLM 上下文——与 V5（file://）同级的安全通道。
  - 其余：take_browser_screenshot、capture_the_screen、geo_location ×2、extract_text_from_pdf（按 agent 给的 URL 下载 PDF——与 open_url 同级的 agent 主动外联，非新升级面）、visual_skill ×2。
- **检测缺口**：`scan_cell_log` 的 V6 扫描只认沙箱调用标记（`SANDBOX_CALL_MARKERS`），而 file 工具**调用时无任何日志行**（persist/recall 函数体内无 logger 调用，成功路径静默）【实证：通读 file_handler_tool.py】——一次静默 `recall_findings` 在 stdout.log 不可见，r1 的三件套扫描（file:// / 沙箱 / 导航计数）全部探测不到。
- **判分面评估**：伪造成绩需要改伺服器**内存**（`fetch_reward` 走 `/latest` = 内存 last-wins）；`persist_findings` 只能改盘上 `rewards.jsonl`，只影响 H3 披露扫描、不影响官方判定【实证：miniwob_server.py:265-296（内存 authoritative）vs orchestrator.py:440-457（盘上仅扫描）】。故 W2 是**密钥/数据安全**风险，不是成绩灌水通道。
- 自发概率佐证【推理】：r1 全量 126 日志沙箱零调用；MiniWoB goal 文本无文件/剪贴板语义；工具面扩大后 flash 档模型误触率未知——这正是需要冒烟观测的原因。

**缓解（按成本排序）**：
1. P0（零代码）：冒烟 14 格跑完后人工 `grep -c "recall_findings\|persist_findings\|augment_findings" dev_runs/benchmark/miniwob-r2/runs/*/attempt*/stdout.log`（注册行 `[TOOL_DEBUG] Processing tool '...'` 每格必有，需对比注册行数与总命中数；静默调用不可见是该方案的固有局限）；跑完后执行既有红线 `KEY=...; grep -rl` 双 key 零命中复核（key 若被读入并复述进 junit/results 会被它抓到）。
2. P1（产品代码，超出本次范围，记录建议）：给 file_handler/clipboard 增加调用日志行并加入 `SANDBOX_CALL_MARKERS` 同级扫描；或 orchestrator 改为子集加载（只 import drag_and_drop 模块）。
3. 报告必须披露：`--extra-tools` = 全量工具面（逐工具列名），非"仅拖拽"。

### W3 终局后重复 endEpisode 的陈旧值重发 —— 低（r1 已知，C2 未放大）

**实证**：本次隔离端口实测（click-test, seed=424242）：`endEpisode(1)` → 记录 raw=1；随后 `endEpisode(-1)` → vendored `EP_TIMER` 守卫使 orig early-return，REWARD_HOOK 重发**陈旧的 raw=1**（共 2 条记录，`/latest` 返回 raw=1）。与 r1-post §5 结论逐字一致（重复条目值恒定、结果中性）。TERMINAL_CUE 包装器不 POST、不改 `WOB_RAW_REWARD_GLOBAL`【实证：cue 注入后总记录数只由 endEpisode 次数决定，cue 本身零记录】。维持 r1 披露口径即可。

### W4 终局 cue 的点击遮挡面 —— 低

**实证**：cue div `position:fixed; left:8px; bottom:8px; z-index:2147483647`，实测可见（display:block，宽 ≈105.6px）。它**只在 endEpisode 之后被创建**【实证：回合内 `<no cue>`】，故不可能干扰终局前的任何任务交互；终局后 episode 已结束、页面交互本就无效。残余面仅是"终局后 agent 对被 cue 遮挡元素的点击会命中 cue"——无判分影响。无需处理，报告注明即可。

### W5 预算 cap 144 的语义边界 —— 低（运维纪律）

**实证**：`assert_budget(budget["total"])` 在 `run()` 入口按**单次调用**校验；pilot（10+2+2 smoke=14）与 full（125+5=130）各自 ≤144，两阶段合计 14+130=144 恰好贴 cap【实证：hercules_budget 复算】。但跨调用无合计：`--force` 重跑 pilot 又是 14 次、换 `--exp-id` 再跑同理。`--smoke-cells` 的旁路面已封死【实证：smoke 格显式排除出重试池（orchestrator.py:1132,1154-1155）、计入 `hercules_budget` 与 `_assert_can_run` 上限、仅限 pilot stage（:750-751）】。缓解：运行纪律（每阶段只跑一次；ablation 走独立 exp root + 独立批准），报告披露"cap 为单次调用上限"。

### W6 C1a 判分重排 —— 无翻判通道（重点核查项，结论：干净）

**实证**：
- 优先级链（orchestrator.py:269-279）：`runner_status=None → no_goal`；`raw>0 → official_passed`；infra 状态保留；`reward=None → no_reward`；否则 official_failed。**进入 passed 的唯一路径是 `raw` 严格 > 0**——页面判负（raw=-1/0）在任何 infra 状态组合下都不可能变 passed；`_reward_raw_value` 对 bool/非数值显式排除。抽验边界（raw=0.537+timeout→passed；raw=-1+junit passed→official_failed+disagreement）由 `test_t1_boundary_cases_of_the_priority_chain` 锁定，本次实跑 PASSED。
- 回放锁实跑（本机）：`test_r1_replay.py` 5 例全 PASSED——5 救援格翻正、`email-inbox-delete` 反例维持非 official_passed、125 行摘要重算 54→59。
- `fetch_reward` 仍取 `/latest`（last-wins），未新增任何洗白通道；救援格经 `runner_status` 键可审计（审计式 = `status != runner_status`）。

### W7 C1b 熔断器偏差 —— 低（偏差方向全部保守）

**实证**（代码位核验）：attempt 级三条件（status∈{timeout,no_junit} ∧ <30s ∧ marker 命中）；绕过面=①>30s 的 402 死不熔断（烧 1 次重试，buffer 有限）；②"402"裸子串过宽（token 数 402 也命中）——只会**多熔断/少重试**，不改判分；③run 级"连续 2 格"可被 break/正常行交替绕过——但每条 break 行已单独逐出重试池，run 级只是兜底。无判分影响。维持现状。

### W8 C2 / C3 / GLM key 闭环 —— 实测通过（重点核查项，结论：干净）

**C2 终局信号（隔离端口 60080 实测）**：
- 回合内无 cue；`endEpisode(1)` 与 `endEpisode(-1)` 后 `#r2g-terminal-cue.textContent` **恒为 `EPISODE ENDED`**（两次一致，无数值/成败词）【实证】。
- HUD 加固回归不变：两种终局后 `body.innerText` 对 `Last reward / Last 10 average / Time left / Episodes done / START / 1.00 / -1.00 / reward` **零命中**【实证】。
- 奖励链路完好：POST 到达、`/latest` 返回 done=true（见 W3 的陈旧值语义）【实证】；单测 T5a-e 全绿。
- 评估影响判定：cue 出现的**同一 JS 调用链**上 REWARD_HOOK 已同步 POST 终局记录（wrapper 先 `orig.apply` 内含 POST，再写 cue 文本）【实证：miniwob_server.py:167-187 与 :190-214 的包装顺序】——agent 看到 cue 才停 = 终局记录必然已在，"借 cue 提前终止失败格"只能把官方 failed 格的空转时间缩短，**对判分零影响**（页面 raw 仍是唯一权威）。

**C3 单次开局（同端口同 context 实测）**：
- 首次加载正常开局（ept0 置位、utterance 可读）；同 tab 二次加载：`ept0=null`（未开局）、**0 条新 reward 记录**、`sessionStorage.r2g_started` 置位【实证】。
- 拦截态普通 DOM 点击 `#sync-task-cover`：Playwright 点击超时（元素不可达）+ `ept0` 保持 null + **0 条新记录**——START 重开路径在拦截态同样封死【实证；与单测 T6e 一致】。
- sessionStorage 绕过面确认：agent 工具清单无任何 JS 执行工具（浏览器工具全是 md 选择器级）；唯一 JS 通道是沙箱（V6），其调用被 H2③ 扫描器覆盖且 r1 全量零调用。**无 JS 能力 ⇒ 无 sessionStorage 伪造能力**【推理，机制闭环】。
- goal 预读不受影响：`read_goal` 每次独立 `sync_playwright` + `browser.new_page()`（goal_reader.py:63-79），sessionStorage 不跨进程/不跨 context【实证：代码位 + T6b】。
- 残余缺口（既有披露项）：`force_new_tab` 18 类新 tab 场景有全新 sessionStorage，仍可开局——由 `task_url_navigations`/`flagged` 计数披露，不封死不隐瞒。

**GLM provider key 闭环**：
- `GLM-Key.txt` 含 BASE_URL/KEY/MODEL 三行 KV【实证：仅字段名】；`read_api_key` KV 解析只返回 key 字符串（runner.py:150-172），空 `KEY=` 显式报错。
- 全部文件写点核验（orchestrator/runner/preflight）：路由配置（构造时即无 key + "model_api_key"/"sk-" 事前拦截，orchestrator.py:686-694）、结果行、脱敏 stdout——**无任何把 key 写盘的路径**；manifest 只含 `provider.name`/`provider.model`。
- key 只进：child `env=`（`LLM_MODEL_API_KEY` + role_routing 时 `MODEL_API_KEY`/`OPENAI_API_KEY` 同值四键，orchestrator.py:697-712）与 preflight 函数参数（detail 全过 `mask_secret`）。
- 脱敏红线复核（本机实跑）：`LLM-Key.txt` 与 `GLM-Key.txt` 的 KEY 值对 `dev_runs record2gherkin tests dev_docs` 双双零命中。
- 外呼面：bigmodel.cn 端点仅出现在 runner.py:49 常量；child 的 harness 侧外联 = bigmodel.cn（LLM）+ 127.0.0.1（任务伺服）；telemetry 关（ENABLE_TELEMETRY=0）、uBlock 下载关；agent 主动 open_url 外联与 r1 同面非新增【实证：grep 全仓】。

### W9 C4 引擎改动分类核查 —— 行为/能力变更，非作弊（重点核查项）

**实证**（逐项分类，全部须进 r2 报告披露清单，见 §6）：
- C4a `markers_only`（simple_hercules.py:603-619）：markers 恒打断（两模式一致）；markers_only 下成功状态变更不打断；默认 `always` 与非法值回落 = r1 逐字节。**分类：行为变更**（减少强制重感知），不改观察空间、不改判分。
- C4b 轮数 50→30 + 步级预算 120s（simple_hercules.py:814-829）：超限返回带 `[NAV_STEP_BUDGET_EXHAUSTED]` 的进展文本交 planner 决策，非异常。"agent 提前放弃某步"是**被披露的预算行为**：它把执行权交还 planner 而非伪造完成，official 判定仍只看页面 raw——**不构成作弊面，属能力/行为变更**。
- C4c/d/e prompt 包：nav rule 8 重感知放宽、L55/L116 刷新教学撤销替换（实证在位）、输出克制句、planner closure 修正与禁重导航句。**分类：行为教学变更**，且方向与 C3/C7 一致（都指向"少重导航、少找不存在的成功提示"）——这些是"把 r1 加固造成的盲区如实告知 agent"，报告按口径披露即可。附注（非阻塞）：spec §5.2 点名的 Platform Awareness/Test Data 章节删除后仍有残句（planner L90 "Acknowledge platform context (like Salesforce, SAP...)"、L275 "Available Test Data"），瘦身不彻底，不影响安全结论。
- C4f env 韧性三键：纯请求参数，无安全面。
- 默认 off 回归：`tests/test_simple_hercules_langgraph.py` 12 passed + T9 全绿（本机实跑）。

### W10–W11 其余核查项 —— 无发现

- **W10 GLM 外呼面**：见 W8；新增外联仅 bigmodel.cn 一处。
- **W11 r1 遗留扫描器仍在生效路径**：`_scan_cell`（orchestrator.py:1038,1069-1102）在每个执行格（含重试与 smoke 格）收尾调用，`scan_cell_log` 扫**本 attempt** 的日志（C1d 分目录后路径经 `_stdout_log_path(cell, attempt)` 传入），file:// 与沙箱标记逻辑与 r1 逐字一致；r2 新增代码（C2/C3 补丁、C5/C6/C7 env/模板、GLM provider）没有任何绕过 `scan_cell_log` 的路径【实证：调用图核验】。

### W12 runbook 陷阱 —— 低-中（操作失误源，随 W1 一并修）

**实证**：`test-report.md` §12.5 的 r2 命令（GLM 切换**之前**写入）显式携带 `--nav-model deepseek-flash`；在 `--provider glm` 下该值会原样进入路由配置的 `nav_agent.model_name`（orchestrator.py:784 不做 provider 校验）→ bigmodel 端点上请求 deepseek 模型 → 每格 nav 调用失败。§13 未更新该命令。缓解：使用 §7 的命令（省略 `--nav-model`，GLM 默认即 glm-5.3-flash）；或在 orchestrator 对"nav_model 属于另一 provider 命名"时告警（P1）。

---

## 3. 测试与脱敏复核（本机实跑记录）

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/record2gherkin -q` | **427 passed**（59.4s，与 test-report §13.2 一致） |
| `uv run pytest tests/test_simple_hercules_langgraph.py -q` | **12 passed**（引擎默认 off 回归） |
| `uv run pytest tests/record2gherkin/benchmark/test_r1_replay.py test_provider_routing.py -v` | **14/14 PASSED**（回放锁 +5 / 反例锁定 / 边界 / GLM 路由与预检） |
| `KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs record2gherkin tests dev_docs` | **0 命中** |
| `GLM KEY 同上扫描` | **0 命中** |
| 隔离端口实测（60080，`--terminal-cue --single-start`） | §2.W8 全部断言通过；脚本存于 `/tmp/r2g_audit_r2/verify_c2_c3.py` |

---

## 4. 对 r2 报告的口径披露清单建议（每条须原文出现）

1. **Provider 切换（最高优先新增）**：r2 全量由 deepseek-v4-pro 切换为 GLM coding plan（planner glm-5.3 / nav、helper glm-5.3-flash）。**r1→r2 的任何数字变化不能单一归因于 C1–C7 改进包——provider 本身也是变量**；归因依赖 ablation（另批），无 ablation 时报告必须明示多变量混杂。
2. **C1a**：判分修复是执行"页面奖励为唯一权威"的正确性修复而非放宽；附回放锁 54→59 的单测证据与 `runner_status` 审计口径（救援格 = `status != runner_status`）。
3. **C2**：环境反馈通道差异声明（原版 HUD 数值 → r1 全盲 → r2 中性 `EPISODE ENDED` 无数值无成败）；判分 100% 页面 raw；cue 不 POST。
4. **C3**：单次开局语义（last-wins 实际退化为"每 tab 唯一一次开局的终局"）；force_new_tab 残余缺口 + 导航计数披露保留；拦截态加固（stub+CSS）说明。
5. **C4**：逐项行为变更披露——refresh 守卫 markers_only、轮数 50→30、步级预算 120s（步内提前让出 = 可观测行为，不影响官方判定）、prompt 教学/瘦身 diff（含 §2.W9 的瘦身残句备注）、C4f 请求参数。
6. **C5/C6**：GLM 分角色路由模型名；**`--extra-tools` = 全量 extra_tools 加载（12 工具逐项列名，含文件读写/剪贴板/截图/geo/PDF），非仅拖拽**——这是 W2 的披露义务。
7. **C7**：模板注释两变体 diff 全文；cue 行与 `--terminal-cue` 联动（C2 off 时不得出现）。
8. **预算口径**：cap 144 为单次调用上限（pilot 14 + full 130 = 144 贴线）；r2 未设 $ 上限（成本仍逐行记录）。
9. **沿用 r1 全部既有披露**：240s 放宽、单 seed 单次、Gherkin 中转、补丁纯追加、奖励端点无防伪造能力、重导航扫描与 flagged 披露。
10. **视觉盲区**：11 格画布/几何家族为文本 DOM agent 射程外，如实声明。

---

## 5. 开跑前加固清单

**P0（headline 前必须，均为一行级或零代码）**
1. **W1**：`_run_preflight` 探测列表并入 `self.planner_model`（role_routing 时）；或运行前手工 1-token 探测 glm-5.3 并留痕。验收：GLM+role_routing 组合下 UNPROBED=none（可用本报告 §2.W1 的复算脚本形态复核）。
2. **W12**：运行命令以本报告 §7 为准（GLM 下不带 `--nav-model deepseek-flash`）；同步勘误 test-report §12.5（注明该命令为 deepseek 口径）。
3. **W2-1**：冒烟 14 格跑完后执行 file 工具人工扫描 + 双 key 脱敏红线复核，异常即关 `--extra-tools` 重跑受影响格并按安全事件处理。

**P1（不阻塞，记入 backlog）**
4. W2-2：file_handler/clipboard 调用日志行 + 扫描标记（或 orchestrator 子集加载 extra_tools）。
5. W12：orchestrator 对 nav_model 与 provider 命名不匹配时告警。
6. 上游建议维持 r1-pre P1-5（open_url scheme 白名单、沙箱环境开关）。

**验收红线**：加固后 `uv run pytest tests/record2gherkin -q` 全绿（若改 orchestrator 需补一条"glm+role_routing 探测含 glm-5.3"的单测）；`make fmt` 相关目录 `--check` 通过；双 key grep 零命中。

---

## 6. 建议的 r2 运行命令（W1/W12 加固完成后）

```bash
# 0) 预演（dry-run：不预检、不进程、不写文件）
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-r2 --stage pilot --dry-run \
  --provider glm --terminal-cue --single-start --role-routing --extra-tools --template-notes --latency-env \
  --smoke-cells drag-items,drag-box

# 1) 冒烟 pilot + drag 冒烟（14 次执行 = 10 + 重试 2 + smoke 2；非 dry-run 启动即 C1c 预检）
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-r2 --stage pilot \
  --provider glm --terminal-cue --single-start --role-routing --extra-tools --template-notes --latency-env \
  --smoke-cells drag-items,drag-box

# 2) headline full（125 + 重试 5 = 130；cap 144 硬拦；用户确认余额后执行）
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-r2 --stage full \
  --provider glm --terminal-cue --single-start --role-routing --extra-tools --template-notes --latency-env
```

注意：GLM 下**不要**传 `--nav-model`（默认 glm-5.3-flash 即正确值）；若坚持显式传参必须是 `--nav-model glm-5.3-flash`。

---

## 7. 证据与复现索引

- 隔离实测脚本：`/tmp/r2g_audit_r2/verify_c2_c3.py`（独立端口 60080 + `/tmp/r2g_audit_r2/rewards.jsonl`，未触碰 8462 与历史产物）；关键输出：cue 恒中性、leak 零命中、拦截态点击 `TimeoutError`/0 记录、`/latest` done=true。
- 关键代码位：`record2gherkin/benchmark/miniwob_server.py:82-187`（补丁 A/single/cue/hook 原文与包装顺序）、`:238-250`（patch 组装顺序）；`record2gherkin/benchmark/orchestrator.py:269-279`（C1a 优先级链）、`:356-372`（C1b）、`:852-868`（C1c 预检模型列表 = W1 现场）、`:697-712`（四键 env）、`:881-890`（extra/latency env）、`:1122-1170`（重试池排除 smoke 与熔断行）；`record2gherkin/evaluation/runner.py:150-172`（KV 解析）、`:192-215`（child env）；`record2gherkin/benchmark/preflight.py:40-63`（同栈探测）；`record2gherkin/benchmark/goal_reader.py:104-145`（C7 两变体）。
- 引擎位：`testzeus_hercules/core/simple_hercules.py:603-619`（C4a）、`:814-829`（C4b）；`testzeus_hercules/core/runner.py:24-40`（轮数 config 化）；`testzeus_hercules/core/agents/browser_nav_agent.py:55,116,148`；`testzeus_hercules/core/agents/high_level_planner_agent.py:90,97,191,261,275,277`；`testzeus_hercules/core/extra_tools/{__init__,drag_and_drop_tool,file_handler_tool,clipboard_tools}.py`（W2 工具面）。
- 测试：`tests/record2gherkin/benchmark/`（test_r1_replay / test_provider_routing / test_engine_guards / test_browser_miniwob T5/T6）+ `tests/record2gherkin/evaluation/test_llm_provider.py`。
