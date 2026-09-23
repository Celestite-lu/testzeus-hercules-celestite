# R3 改进计划 — miniwob-r3（plan-r3）

> 读者为 spec-r3.md 的契约来源与总编排代理的决策依据。数据基础：`analysis-r2-failures.md`（r2 的 67 未过格拆解，下称"分析"）、`round2-report.md` §4、`security-review-r2-post.md`（下称"安全复审"，其 §9 即 r3 安全必办清单）。
> **核心目标（最高原则）：r3 干净口径站上 50% 且零安全债。** 证据弱的候选砍掉不心疼；一切行为变更经独立 flag 披露，默认态（flags 全 off）= r2 行为逐字节复现。
> 模型环境：GLM coding plan（planner=glm-5.3、nav/helper=glm-5.3-flash），5 小时窗口限额——延续 r2 的分段执行 + 429 熔断 + `--max-cells` 配速，预算表见 §5。

## 0. 法定输入采纳/拒绝总表

| 输入 | 采纳情况 |
|---|---|
| analysis-r2-failures.md | 全量采纳为证据基础：A←§2.2 肥尾；B←§2.2/§1-N/O-B；C←§3.1（61 调 0 中 + 选择器损坏证据链）；D←§2.4（16/6）与 §6 L1；F←§4 完成幻觉 15 格、§6 L4 |
| round2-report.md §4 | 采纳 1（超时延迟）、2（extra_tools 治理）、3（scheme 白名单）；4（拖拽专项）并入 C；5（click 家族盲区分析）**拒绝立项**（无工具/杠杆可做，列 Out of Scope，只做 r3 报告中的家族复盘段落） |
| security-review-r2-post.md §9 | P0（extra_tools 子集 + 调用日志行 + invalid 剔除进 metrics）→ E1/E2/E4；P1（scheme 白名单）→ E3；P1（沙箱关停）→ E5；P2（极早崩格日志）→ E6；P2（C2/C3/C1a/C4 维持）→ 采纳为"r2 全开包原样保留" |
| spec-r2.md / orchestrator.py | flag 结构（独立开关、默认 off、manifest `flags`、LATENCY_ENV_OVERRIDES、smoke-cells、retry 池）原样沿用；r3 只做增量 |
| drag_and_drop_tool.py 现场 | md 包装 bug（L29-31）、target 多策略（L55-97）、拖拽机制可行（分析 §3.1-3）逐条采纳进 C 的修法 |
| 模型环境事实（GLM 5h 窗口） | 预算 ≤130 headline 执行、分段 `--max-cells`、429 熔断常开；ablation 臂另批另计 |

## 1. 采纳清单（R3-1 … R3-6）

### R3-1（A）完补全肥尾治理 = nav 侧 max_tokens 上限（engine flag）——prompt 纪律不再加码

- **证据**：分析 §2.2——10.3% turn（>500 token）吃 38% executor 墙钟；>800 token turn（n=201）间隔中位 46.2s / p90 80s；GLM-flash 少数 turn 生成 2000–3400 token。
- **为什么是 max_tokens 而不是 prompt**：r2 的 C4d 已落地"Keep responses short"输出克制句，肥尾照旧（分析 §2.2 全量 n=3197）——prompt 纪律对 GLM-flash 已被实证无效，不再重复加文本。max_tokens 是引擎侧硬上限。
- **r2 真实基线（review-r3 M1 修正，原"r2 无上界"立论不成立）**：benchmark 必经路径上 `adapt_llm_params_for_model` 被调用两次——`SimpleHercules.create` 对 planner/nav/helper 三个 config 原地 adapt（simple_hercules.py:152-160；benchmark 走 `SimpleHercules.create`，runner.py:71），`create_chat_model` 内部再 adapt 一次（llm_helper.py:83）。GLM 模型名落入 model_utils.py:69-70 的 "other models" 分支被注入 `max_tokens=4096`——**r2 的 nav/planner 补全存在 4096 硬上限**（生成的 config 文件本身不含 max_tokens，4096 是运行时兜底；config.py:762 的 `LLM_MODEL_MAX_TOKENS=4096` 仅经 `get_adapted_llm_params` 被 mcp_server 消费，与 benchmark 无关、数值上恰与该兜底同值）。实测肥尾 2000–3400 token 全部落在 4096 之内。
- **改法**：新 env `NAV_MAX_COMPLETION_TOKENS`（默认 0=off），`create_chat_model`（llm_helper.py:89-102）兜底段之后应用——覆盖全部 nav agents（base_nav_agent.py:66 及 multimodal 变体），**不含 planner**（planner 走裸 ChatOpenAI，high_level_planner_agent.py:37-57；对 planner 截断有 JSON 断裂风险，planner 交给 R3-2）。**注入必须是 env>0 时无条件覆盖**（而非 `is None` 兜底）：benchmark 路径 kwargs 恒带 adapt 注入的 4096，`is None` 条件永不成立、768 会静默失效（review-r3 M1）。语义口径 = **把 4096 收紧到 768**；T-B 预期（9/3）不变，其依据是 >768 的实测尾部，与 4096 上限并存不矛盾。
- **取值 768**：合法动作 turn 0–150 token 占 2274/3197；768 在 ~38 token/s 下把单 turn 延迟天花板压到 ~20s，只切除 >768 的病理尾部（n≤201）。截断的失败模式 = 工具调用参数 JSON 断裂 → 该次调用报错 → agent 下轮缩短重试（一次浪费 2–5s），可接受且须在 test-report 披露。
- **开关**：orchestrator `--nav-max-tokens <int>`（默认 0=off）→ env 注入。预期救回：T-B 上界 9 / 保守 3（分析 §6 L2 与 B 合并计）。

### R3-2（B）planner 90s 超时链 = planner 专属超时上限（150s）

- **证据**：25/41 超时格发生 ≥1 次 planner 90s 超时；2 格秒死（N：click-shades、drag-shapes-2，90.0s、0 executor 轮）；2 格 planner 超时诚实终止（O-B）。
- **机制诊断（读 simple_hercules/llm_helper/high_level_planner_agent 现状后的回答："是 90s 太紧还是无重试？"）——90s 对 planner 是**双层**硬墙，改法是双层同源放宽**：
  1. **双层 90s**：外层 `_llm_ainvoke`（simple_hercules.py:280-285）`asyncio.wait_for(LLM_REQUEST_TIMEOUT)` 硬切所有 agent 调用；内层 planner 的 ChatOpenAI 自身 `timeout` kwarg 兜底同为 90s（high_level_planner_agent.py:53-54，headline 的 `LLM_REQUEST_TIMEOUT=90` 同时喂两层）。provider 层在 90s 掐断单次 HTTP 请求后触发 `LLM_MAX_RETRIES=2` 的 SDK 重试——**慢生成的单次请求在 r2 结构上不可能超过 90s 完成**，重试只能在残余窗口内碰运气。
  2. planner 超时 → `_planner_timeout_result`（L311-354）**立即 terminate=yes**，一格只有一次机会；executor 超时 → 步中止 + planner 重写 retry（L833-836），烧一轮 planner LLM（分析 §2.2"超时链"）。
  3. planner 是 glm-5.3（非 flash，更慢），实测 planner 停顿达 95–168s——在 90s 双层墙下这类"单次停顿"测量只能是"90s 掐断 + provider 重试链"的间隔，恰证内层 timeout 在起作用；90s 对它是确定性偏紧，r1 的 helper 60s 级联在 r2 换位成 planner 90s 级联（分析 §5-C4）。
- **拒绝的替代案（记录理由）**：
  - *全 agent 超时重试（wait_for 后重发）*：temperature=0 下重发同一 prompt 大概率同样慢，救不了慢生成，只把最坏暂停翻倍到 180s，直接吃掉 240s 页钟；429/连接类瞬断已由 provider 级 `LLM_MAX_RETRIES=2` 覆盖——冗余。
  - *planner max_tokens 截断*：planner 输出是单块 JSON，截断 → parse 失败 → 空 next_step → planner↔executor 空转循环（simple_hercules.py:685-698、_route_after_planner），风险不对称，不做。
- **改法**：新 env `LLM_PLANNER_REQUEST_TIMEOUT`（默认 0=跟随 `LLM_REQUEST_TIMEOUT`，即 r2 的 90s），**同源喂两层**——外层 `_llm_ainvoke` 对 `agent_name=="planner_agent"` 使用之，且 planner 的 ChatOpenAI provider 层 timeout 兜底同改用该 helper（只改 wait_for 一层则 >90s 单次生成仍被 provider 层 90s 掐断，150s 兑现不了——review-r3 M2）。headline 注入 150s：覆盖实测长尾（168s 中的 p99.9 例外接受），N/O-B 四格从"90s 即死"变为"150s 内完成即活"（这些格引擎余量 510s、任务本身 <60s）。
- **开关**：orchestrator `--planner-timeout <int>`（默认 0=off）→ env 注入。预期救回：N 2 格 ~0.8 + O-B 2 格 ~0.45（分析 Top-10 #1/#7），并削减 25 格中的 planner 重写循环（T-B 保守 3 的另一部分）。

### R3-3（C）drag 工具修复（选择器透传 + 文本兜底），带 pilot kill-switch；坐标拖拽不做

- **证据**：分析 §3.1——61 调 0 中全因 `[md='...']` 包装损坏（drag_and_drop_tool.py:29-31 把一切非 md= 前缀的 source 包进 md）；拖拽源（drag-handle/SVG shape）根本不在 md 交互元素表里，agent 无正确答案可传；拖拽鼠标序列机制本身可行。
- **两案对比结论——先修，修不好就弃**：
  - *修复案*：source 选择器透传（`css=`/`xpath=`/`text=`/`#`/`.`/`//` 等合法 Playwright 形态原样传给 find_element，仅裸 md 值才包 `[md='…']`——与现状优先级相反）+ 工具 description 更新（agent 可读）+ target 侧多策略保留。成本 ~40 行；天花板 = drag 家族 8 格（分析 §6 L3：纯透传受益 3–5 格且"需与 L1 联测"——r3 headline 无 D 时保守只兑现 1–3 格）。
  - *放弃案*：从 extra_tools 移除 drag 并在报告声明——省下 61 次失败调用烧掉的 1–2 executor 轮/格（肥尾下每次 5–70s），也移除 3 格 `javascript:` 越面的诱因；但放弃 = 8 格天花板永久归零。
  - **裁定**：修复成本远低于天花板价值 → r3 做修复；**kill-switch**：pilot 的 drag 冒烟 2 格若仍 0 成功调用解析，则 headline 前把 `drag_and_drop_tool` 移出子集并在 test-report 声明放弃（触发条件与记录义务见 spec §3.4）。
- **坐标拖拽（drag-shapes/draw-* 家族）不做**：与视觉/画布家族同射程（canvas/SVG 无 DOM 锚点），列 Out of Scope；这 6+ 格在 r3 报告中继续诚实记为盲区。
- **开关**：无新 flag（修复只在 `--extra-tools` 加载时生效；默认态不加载 extra_tools = r2 复现）。预期救回：保守 +1~3（drag-single-shape/drag-box/drag-sort-numbers 类选择器可表达格）。

### R3-4（D）计时放宽 = 单列 ablation 臂，不进 headline

- **证据**：分析 §2.4——240s→480s 上界救 16 格 / 保守 6 格（T-A 桶，全部是"正确执行中被页钟杀"）；边界条件①引擎上限须同步抬高（480 页钟 + wrap-up > 600s）。
- **裁定：ablation 臂，不进 headline**。理由：240s→480s 改变任务难度本身，进 headline 会永久污染 r1/r2/r3 的同口径可比性，"50%"主张将被迫携带口径星号；作 ablation 臂则 headline（240s，A/B/E/F/C）与 ablation（同 flags + 480s/900s）两数并列，各自干净，且 seed 派生不变可逐格配对归因。成本最低的单杠杆验证（分析 §6"若 r3 跑 480s 单列 ablation"）。
- **实现 = 零代码**：orchestrator 已有 `--episode-ms`（tasks.py:21 默认 240000，进 URL 与结果行 `episode_max_time_ms`）与 `--timeout-s`（默认 600）。D 臂命令：同 headline 全部 flags + `--episode-ms 480000 --timeout-s 900 --exp-id miniwob-r3-d480 --exp-root <独立目录>`。125+5=135 执行 ≤ R2_BUDGET_CAP=144，无需改预算代码。
- **披露口径**：ablation 数字一律标注 `episode_max_time_ms=480000 / timeout_s=900`，与 headline 双列呈现，禁止混合成分母；逐格配对归因（哪些格仅被 L1 救回）写入 analysis-r3。
- **批准门槛**：默认不存在于运行序列，用户逐项批准后才跑（沿用 spec-r2 §10 ablation 纪律）。

### R3-5（E）安全必办包（判分中立，默认 on 于 headline——理由见下）

E1 **extra_tools 子集加载**（安全复审 §9-P0）：新 env `EXTRA_TOOLS_MODULES`（逗号分隔模块名，空=全量=现行为）；`extra_tools/__init__.py:9-19` 按白名单过滤 import。`--extra-tools` 在 r3 默认只加载 `drag_and_drop_tool`（复审 P0 原文"只 import drag_and_drop 模块"）；`--extra-tools-modules` 可显式覆盖（`all` = r2 全量行为，须披露）。**结构性消灭 W2 类事件**：persist/recall/augment（file_handler_tool.py）不再可加载 → r3 干净分母回到 125（r2 被迫剔除 10 格）——这是 E 判分中立却进 headline 的第一理由。
E2 **文件工具调用日志行 + 扫描标记**：persist/recall/augment 入口加 `[EXTRA_TOOL_CALL] <name>` 日志行（复审 P1 升"必须"）；orchestrator `scan_cell_log` 新增标记集——命中 → `invalid_reason=file_tool_invoked`（与 r2 无效格契约一致，orchestrator.py:358-360）。九格静默成功的检测缺口就此封死。
E3 **open_url scheme 白名单**（复审 §9-P1，连续两轮未做的 r1-pre P1-5）：open_url 主路径在 `ensure_protocol`（open_url.py:207-242 的 https 前缀属**偶然防御**）之前校验 scheme，仅放行 http/https（about:/chrome: 等既有 special 路径不受影响）；`javascript:`/`data:`/`file:`/未知 scheme → 拒绝导航 + 返回明确错误文本 + `[OPEN_URL_BLOCKED]` 日志行；扫描器对该标记 **flagged（仅披露）**——与 r2 对 3 格 javascript: 尝试"低危披露不判无效"的处理对齐。
E4 **invalid 格剔除进 metrics**（复审 §9-P0/I5：`_rate` 现在把 invalid 格计入分子属流程风险）：metrics.py 增 `clean` 口径（invalid 格同时移出分子与分母）与 `invalid_cells` 清单，`overall`（官方口径）不动——r3 报告的"干净口径"由代码产出而非人工剔除。
E5 **沙箱 benchmark 关停**（复审 §9-P1#3：两轮合法使用为零、越界尝试 1 格）：新 env `SANDBOX_DISABLED`，`execute_python_sandbox.py` 入口（tenant 读取之前）拒绝执行并返回含 `[SANDBOX_DISABLED]` 标记的错误；标记加入 `SANDBOX_CALL_MARKERS` → 被调用即 `invalid_reason=sandbox_tool_invoked`（与 r2 契约一致：尝试即无效，零执行也一样）。防 r2 click-shape 类"真通过被沙箱尝试作废"重演。
E6 **极早崩格留痕**（复审 §9-P2）：orchestrator no_goal 路径把 GoalReadError 详情写 `runs/<run_id>/goal_read_error.log`（email-inbox-forward-nl 0.6s 零日志缺口的低成本闭合）。

- **E 为何判分中立仍默认 on**：① E 全部不触碰奖励获取/状态判定链（C1a 优先级、fetch /latest、last-wins 均不动），只收缩工具面与增加披露；② E1 直接决定 headline 分母（115 vs 125），不开 E1 则"零安全债"不成立；③ E3/E5 堵的是已被实战触发（3 次 javascript:、8 次沙箱）的偶然防御。默认态（无 flags）下 E1/E2/E5 的 env 不会注入，引擎行为不变；E2/E3/E4 是 harness/工具代码改动，对 flags 全 off 的运行无判分影响（E4 只新增披露字段）。**理由成立，headline 默认 on。**

### R3-6（F）完成幻觉 planner 断言纪律（低优先级，flag 可关）

- **证据**：分析 §4——15/125 完成幻觉（junit=true 页面 −1），L4 上界 9 / 保守 4；r1→r2 幻觉 29→15 已有大幅下降（prompt+模型混杂），说明 prompt 方向有效但未到底。
- **改法**：conf 键 `PLANNER_ASSERT_DISCIPLINE`（默认 false），PlannerAgent 组装 system_message 时按开关追加一段固定纪律（terminate=yes ∧ is_passed=true 前提 = 最后一条 helper 观察明确确认所请求动作已执行；观察为失败/超时/未提及 → is_passed=false 如实报告）。
- **定位**：证据强度中等（15 格中含 executor 误报场景，planner 侧只拦得住一部分），故列为可选项；prompt-only、零基建风险、flag 披露成本为零 → headline 开。预期 +1~4。**不承诺**。

### 明确不做（本轮砍掉，不心疼）

| 项 | 理由 |
|---|---|
| 视觉/画布家族（draw-*、drag-shapes 坐标拖拽、drag-circle/cube 类 canvas 格） | 射程外（任务指定"不做"）；r3 报告继续记盲区 |
| click-pie hover viewport 修复、hot-cold DOM 适配等 L3 其余项 | 未列入本轮候选（round2-report §4-5 只要求"盲区分析"，降级为报告复盘段落）；T-C 桶残余在报告中按杠杆缺口披露 |
| 更小执行模型 / 模型档位调换（L6） | 多变量混杂已不可分，再换模型会把 r3 全部归因作废；编码计划内也无更小档 |
| planner 模型升级 | 同上 + 预算 |
| 全 agent 超时重试、planner max_tokens | 见 R3-2 拒绝理由 |
| TERMINATE 前强制读页面 reward 的机制化校验（L4 强版） | 需要 engine 判据接线（r2 已证 cue 未接入 terminate），实现面大、证据中等；F 的 prompt 版先行，机制版留 r4 |
| C4 latency-env 的键值调整（如 BROWSER_NAV_MAX_CHAT_ROUND 30→更多） | r2 数据无证据指向这些键是瓶颈；保持 r2 原值 |

## 2. r2 全开包保留声明

`--terminal-cue / --single-start / --role-routing / --nav-model glm-5.3-flash / --latency-env / --template-notes / --provider glm` 全部原样保留进 r3 headline（安全复审 §9-P2"维持"）。r3 新增 flags 一律独立、默认 off。默认命令（无任何 r3 flag）= r2 默认行为 + E2/E3/E4/E6 的 harness 侧披露增强（无判分影响）。

## 3. 组合策略（headline 单次运行，全开披露）

```
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r3 --stage full --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-max-tokens 768 --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline \
  --max-cells <按5h窗口配速>        # 断点续跑同命令
```

r3 headline flags 全集（manifest `flags` 记录）：r2 七项 + `nav_max_tokens=768` + `planner_timeout=150` + `extra_tools=true` + `extra_tools_modules=["drag_and_drop_tool"]` + `disable_sandbox=true` + `assert_discipline=true`。

叠加关系与杠杆去重（对分析 §6 矩阵）：R3-1+R3-2 合并主张 T-B（9/3）与 N/O-B（4 格），R3-1 顺带缓解 T-A 的单步延迟；R3-3 主张 T-C 的 drag 子集（1–3）；R3-6 主张 O-A（1–4）；R3-4 单独主张 T-A（16/6）且**只在 ablation 臂计分**——headline 与 ablation 数字不混算。

## 4. 里程碑（5h 窗口分段，全程 --max-cells 配速 + 429 熔断）

| 里程碑 | 内容 | 出口判据 | 预估 |
|---|---|---|---|
| M0 实现+单测 | spec-r3 全部引擎/harness 改动 + 离线单测 T1–T12 + `make fmt`/`lint` 绿 + 现有 tests/record2gherkin 全绿 | spec §9 验收 1–2 | 0.5–1 天 |
| M1 pilot+smoke | pilot 10 + retry 2 + drag 冒烟 2（`--smoke-cells drag-items,drag-box`，全开 flags） | drag kill-switch 判定（见 spec §3.4）；flash 输出无失控（completion 无 >768 大尾巴）；无 402 | 0.5 天（1 窗口） |
| M2 headline full | 125 格 + 5 retry，分段 ~3 个 5h 窗口（A 生效后超时格墙钟略降） | 130 执行 ≤ 预算；manifest flags 完整；results 125 任务覆盖 | 1–2 天 |
| M3 复盘固化 | analysis-r3-failures.md + round3-report.md + security-review-r3-post.md 固化进 dev_docs/benchmark/ | 干净口径数字 + 全部披露清单落档 | 0.5–1 天 |
| M4 ablation（另批） | 用户批准后跑 D 臂（135 执行，~2–3 窗口），双列口径入报告 | 逐格配对归因表 | +1–2 天 |

**headline 路径合计 ~3–4 天；含 M4 共 ~5–6 天。**

## 5. 预算表（执行数 = Hercules 子进程数）

| 阶段 | 执行数 | 构成 | 上限校验 |
|---|---:|---|---|
| M1 pilot+smoke | 14 | 10 + retry 2 + smoke 2 | R2_BUDGET_CAP=144（r3 沿用不改） |
| M2 headline full | 130 | 125 + retry 5 | 同上；**满足"r3 全量 ≤130"约束（不含 pilot）** |
| M4 D-ablation 臂 | 135 | 125 + retry 5，独立 exp-id/exp-root，**另计** | 135 ≤ 144 ✓（`--episode-ms 480000 --timeout-s 900` 无需代码） |
| **合计** | **279** | | headline 主线 144；ablation 须用户逐项批准 |

token 预算：r2 全程 9.08M 在 coding plan 额度内；r3 预估同量级或略低（R3-1 砍肥尾补全；ablation 臂 +~40%墙钟、token 增幅 <25%）。5h 窗口配速不变。

## 6. 预期影响区间（对照 r2：官方 58/125=46.4%、干净 55/115=47.8%）

| 口径 | 保守 | 上界 | 构成 |
|---|---|---|---|
| **r3 headline 干净口径**（125 分母，E1 后预期 invalid≈0） | **65/125 = 52.0%** | **77/125 = 61.6%** | r2 等价基线 58 + A/B +5~12（T-B 9/3、N+O-B +2~3、T-A 顺带）+ C +1~3 + F +1~4；保守档复算 58+5+1+1 = **65** ✓（review-r3 M3） |
| r3 D-ablation 臂（480s/900s，另批） | **71/125 = 56.8%** | 93/125 = 74.4% | headline + T-A 16/6（分析 §2.4），同 flags 可配对；保守 65+6=71 |

诚实备注：保守档 65/125 = 52.0%（构成 58+5+1+1，可复算），较 50% 主张留 2 格缓冲，主张主要押在 A+B 的证据强度上；r1→r2 的模型噪声幅度 ±6 格提示单轮波动可能吞掉缓冲——若 headline 落在 60–62/125（48.0–49.6%），以 M3 复盘归因后由总编排代理决定是否以 retry 池外的定向复跑（需另行批准并披露）补证，绝不改判分。

## 7. 风险表

| # | 风险 | 概率/影响 | 缓解 |
|---|---|---|---|
| R1 | R3-1 截断产生畸形工具调用参数（JSON 断裂）→ 局部多烧轮次 | 中/低 | 取值 768 而非 512；失败模式=单调用报错下轮自愈；pilot 冒烟监测 completion 直方图；flag 一键回退 |
| R2 | R3-2 的 150s 最坏占用页钟 62%（turn1 慢 planner）| 低/中 | 仅 N/O-B 型格受益场景；若 turn1 150s 超时，页面 240s 本就判负——期望值严格不劣于现状；披露 |
| R3 | R3-3 修复后 agent 仍不会用（61 调 0 中的用法因素） | 中/低 | pilot kill-switch：drag 冒烟 0 解析成功 → headline 移出子集并声明放弃（回退到"放弃案"，成本已沉没但不再烧轮次） |
| R4 | E1 子集误伤合法工具面（read_clipboard 在 copy-paste 家族 14 次调用） | 低/低 | r2 该家族 0 收益；报告中披露"clipboard 不再加载"；如需恢复用 --extra-tools-modules 显式加回 |
| R5 | E5 关沙箱后 agent 反复尝试浪费轮次 | 低/低 | 拒绝响应含明确"disabled"文本，agent 通常一轮即放弃；尝试即 invalid 的契约不变 |
| R6 | F prompt 使 planner 该停不停（错过合法 terminate） | 低/中 | 纪律只约束 is_passed=true 的前提，不约束 terminate 本身；240s 页钟兜底；flag 可关 |
| R7 | 429/5h 窗口中断导致 headline 拖长 | 中/中 | r2 已验证的 --max-cells 断点续跑 + C1b/429 熔断原样沿用 |
| R8 | GLM provider 行为漂移使 A/B 预期失准 | 中/中 | M1 冒烟即校验肥尾是否收敛（completion 直方图 + TOKEN_COUNT 间隔）；失准则 M2 前回滚对应 flag 并更新 plan 附录 |
| R9 | D 臂双口径被误读为刷分 | 低/高 | 披露纪律：ablation 数字永不离 `episode_max_time_ms=480000` 标注单独出现；headline 50% 主张只引用 240s 口径 |
