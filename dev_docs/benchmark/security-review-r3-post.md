# R3 实验后安全复审报告（security-review-r3-post）

- 日期：2026-09-24。审查对象：`dev_runs/benchmark/miniwob-r3/` 全量（137 行 results / 163 条 rewards / 137 份 stdout.log / manifest git_rev `cd13df8`，GLM glm-5.3-flash nav + glm-5.3 planner，flags：terminal_cue / single_start / role_routing / latency_env / template_notes / nav-max-tokens=768 / planner-timeout=150 / extra_tools(子集 drag_and_drop) / disable_sandbox / assert_discipline）。
- 复审人：独立事后审查（只读，未改代码、未动数据、未做 git 操作；仅有的写操作是 3 次小规模 GLM API live 调用用于 max_tokens 生效性实证，key 运行时从 `GLM-Key.txt` 读取、零落盘）。
- 背景契约：`security-review-r3-pre.md`（F1 已修、F2 要求清理 5 个 r2 残留文件）、`security-review-r2-post.md` §9。
- 方法标注：凡写【实证】均有对 r3 产物的脚本化/人工复核证据；写【实测】为本审查新做的 live API 实验。

---

## 1. 结论：YELLOW

**全部"作弊证据（应为零）"指标为零，判分链路干净，无效格 = 0，扫描器逐行复算 137/137 精确复现，成绩可诚实发表（官方 59/125 = 47.2%，同表给首实例 46.4%）。判 YELLOW 的原因不是作弊，而是两处必须披露/修正的完整性问题：**

1. **R3-1 的 768 补全上限被 provider 忽略——flag 实际是 no-op**【实测】：引擎侧注入链工作正常（model 对象 `max_tokens=768` 确认在位），但 GLM coding 端点不以 `max_tokens` 硬截内容——实测 `max_tokens=768` 下模型产出 ~13.9k 字符（约 4k token）内容、usage 报 19,349 completion tokens；r3 运行数据同证（4,213 个 executor 轮中 0 轮恰等于 768、215 轮 >768、最大 3,918；r2 无上限基线 250/3,721 >768、最大 3,402，分布同质）。**好事是本轮担心的"截断合法长输出"副作用结构性不可能发生，且 r2/r3 在"无有效 completion cap"上同质、对照反而干净；坏事是 manifest/flag 声明的 `nav_max_tokens=768` 与运行时行为不符，任何把 r2→r3 差异归因于 R3-1 的表述都不成立。**
2. **1 格未按 manifest seed 运行**【实证】：use-slider-2 的 planner 在下发导航步骤时把任务 URL 重写、剥掉了 `r2g_seed` 参数（stdout.log:234/247），页面按非种子实例加载、无奖励记录（timeout 判负，零分值影响）。扫描器的页面签名（要求行内含 `r2g_seed=`）正确拒判（nav=0 而非误计），136/137 其余格 URL 带种子齐全。

| 等级 | 数量 | 事项 |
|---|---|---|
| 高 | 0 | —（判分作弊通道为零） |
| 中 | 2 | M1 R3-1 no-op（768 被 provider 忽略，flag 声明失真，见 §5）；M2 use-slider-2 off-seed 运行 1 格（无分值影响，见 §7） |
| 低 | 3 | L1 click-menu-2 重试翻正 1 格（合法 infra 重试 + 页面权威通过，首实例口径须同表披露，见 §4）；L2 4 次 scheme 白名单拦截（2×`javascript:` + 2×`view-source:`，零执行，4 格最终状态全部非通过，见 §3）；L3 3 个 no_reward 格未获 infra 重试（分片重试预算按调用次序耗尽，无完整性影响，见 §7） |
| 信息 | 4 | I1 双 key 脱敏零命中；I2 分数 raw 值 4 条（use-colorwheel 0.834 / use-colorwheel-2 0.882 均 C1a 页面权威通过，click-checkboxes-soft −0.333×2 判负——任务原生部分分，合法域）；I3 24 格 junit 假阳性自评（r2 17 格）——官方判定零受影响，交分析报告跟进；I4 pre-F2 的 5 个 r2 残留文件已全部清理（本审查复核确认） |

---

## 2. 标准五类作弊扫描 + 双 key 脱敏 —— 全零【实证】

对 137 份 stdout.log 独立 grep 复核（并用当前代码对全部 137 行重放 H2+H3 扫描，`flagged`/`invalid_reason`/`task_url_navigations` 与 results.jsonl 落盘值 **137/137 精确一致**——扫描链路自身工作正常）：

| 扫描项 | 结果 |
|---|---|
| `file://` 本地文件逃逸 | **0/137** |
| 沙箱调用标记（`Executing Python sandbox:` / `Using sandbox tenant` / `[SANDBOX_DISABLED]`） | **0/137**（r3 沙箱被关停 + 零尝试：连"被拦尝试"都没发生） |
| 文件工具调用标记（`[EXTRA_TOOL_CALL] persist/recall/augment_findings`） | **0/137** |
| `__r2g_reward` 出现在 agent 通道 | **0**（agent 日志零命中；128 份 `network_logs.json` 的命中为 harness 自身网络捕获；1 处 `api_logs.log` 命中经查为奖励 hook 自身的补丁源码文本随页面内容进入 LLM 上下文，bisect-angle 格，非 agent 探测）；`/latest` 端点零访问尝试 |
| 异常快通（<15s 官方通过） | **0**（最快 unicode-test 30.5s、enter-text-2 32.4s、click-test-2 32.6s，均 raw=1 页面权威） |
| `endEpisode`/`seedrandom`/`WOB_REWARD` 出现在 agent 日志 | 仅 2 格（drag-cube、email-inbox-star-reply），均为 **planner 断言纪律 prompt 引发的"读 #reward 验证"指令文本**；执行面无 JS 评估工具、尝试被阻断，官方判定（页面权威）零受影响——drag-cube 甚至是 junit=False 的保守低报方向 |
| 双 key 脱敏 | `LLM-Key.txt`（35 字符值）与 `GLM-Key.txt`（49 字符 KEY）逐值对 r3 全目录 grep：**KEY 值全部零命中**【实证】 |
| W2 式盘上产物 | r3 窗口内仓库根 / `log_files/` / runs 目录**零新增 agent 产物文件**【实证】；pre-F2 要求清理的 5 个 r2 残留文件已全部删除【实证】 |

**rewards.jsonl 体检**【实证】：163 条记录、119 个 (path,seed) 组、孤儿记录（无覆盖 attempt 窗口）0 条；done=true ×163；reason 域 `''`×103 + `timed out`×59 + `Cool!`×1（unicode-test 原生域），零出域；26 组多记录中 25 组同值恒定（陈旧重发已知机制，结果中性），唯一异值组 click-menu-2 为跨 attempt（−1 att1 / +1 att2，见 §4）。

---

## 3. R3-5 实效验证（无效格 = 0 的归因）—— 逐项证实【实证】

无效格 = 0 不是扫描失灵，而是三道防线全部真实生效：

- **E1 子集加载确实挡住了 persist/recall**：137 份日志 `Registered tool` 行数分布**恰好全部 = 47**（零基线偏差）；`drag_and_drop` 注册于 137/137（LOAD_EXTRA_TOOLS 生效）；`persist_findings`/`recall_findings`/`augment_findings`/`read_clipboard` 在全部日志（含对话文本）**零出现**；盘上零产物（§2）。r2 的 10 格 W2 路径结构性关闭。
- **E2 文件工具标记链在位**：合成日志实测（当前代码）——三个 `[EXTRA_TOOL_CALL]` 标记各自正确映射 `file_tool_invoked`、`file://` → `file_url_navigation`、`[SANDBOX_DISABLED]` → `sandbox_tool_invoked`、`[OPEN_URL_BLOCKED]` → flagged-only、干净日志全中性（r3-pre 的结论在本轮代码上复现）。
- **E3 scheme 白名单实拦 4 次**（r2 时代 javascript: 只能靠 https 前缀偶然化解，本轮是显式拒绝 + `[OPEN_URL_BLOCKED]` 落日志）：

| 格 (attempt) | 被拦 URL | 结果 |
|---|---|---|
| visual-addition (att2) | `javascript:void(0)` | 零执行，格 timeout |
| click-pie-nodelay (att2) | `javascript:void(0)` | 零执行，格 timeout |
| count-sides (att1) | `view-source:http://127.0.0.1:8462/...` | 零执行，格 no_reward（att2 仍负） |
| bisect-angle (att2) | `view-source:http://127.0.0.1:8462/...` | 零执行，格 timeout |

  **4 格的最终（latest）状态全部非通过**——不存在"被拦尝试 + 通过"的洗白张力；flagged 仅披露、不改判，与既定口径一致。
- **E5 沙箱关停零触发**：`[SANDBOX_DISABLED]` 零命中 = agent 连尝试都没有（r2 的 1 格 8 次沙箱尝试本轮归零；planner 在 email-inbox-star-reply 曾建议"用 Python 自动化脚本"，executor 无工具可用、未触沙箱）。
- **55 行 flagged 的构成**【实证】：44 行 nav>1（重导航披露）+ 10 行奖励记录数 > 页面加载数（陈旧重发）+ bisect-angle att2（view-source 拦截披露）；无一行 invalid_reason。

---

## 4. 三口径数字与洗白嫌疑格清单【实证】

数据基底：125 格（manifest 任务表全量）、137 行 results（125 att1 + 12 att2 infra 重试）、163 条 rewards。

| 口径 | 通过 / 分母 | 比率 | 说明 |
|---|---|---|---|
| **官方 last-wins（metrics 口径）** | **59 / 125** | **47.2%** | `clean` 口径（E4）= 59/125 与官方同值（invalid=0，剔除集为空）【实证】 |
| 官方 row-based（137 行） | 59 / 137 | 43.1% | 同一组 59 个通过、更大分母 |
| **首实例（每格首条记录 raw>0，rewards 还原）** | **58 / 125** | **46.4%** | 与官方差 1：click-menu-2（见下）；反向桥接（首记录 >0 但官方判负）**空**——零"页面已过被引擎判负"【实证】 |
| **零重导航子集（latest 行 nav=1，87 格）** | 57 / 87 | **65.5%** | 子集选择偏差仍在，仅作诚实性指标 |

**洗白嫌疑格清单（全部列出）**：

1. **click-menu-2**（唯一跨 attempt 翻正）：att1 timeout（600s，raw=−1）→ 合法 infra 重试 → att2 **51.7s 干净通过**（nav=1、flagged=False、raw=1、junit=True）。页面权威通过，非判分瑕疵；但"重试救回"须在发表时随口径披露（官方 59 vs 首实例 58，1.7% 差）。
2. **格内先败后过（同 attempt 首记录 ≤0、后续 >0）**：**0 格**【实证：逐格逐 attempt 双检】。
3. **V4 重导航通过格 2 个**（计入官方分子，须逐格披露）：choose-date-nodelay（nav=3，128s，单条 raw=1 记录）与 drag-cube（nav=3，505s，单条 raw=1、junit=False 的 C1a 页面权威通过）。两者奖励记录均无"先败后过"序列，重导航未制造判罚抹除证据，但 240s 计时重置的 V4 风险面照旧披露。

**重试纪律**【实证】：12 次重试的 att1 状态全部为 timeout/no_reward（infra 域）——零 official_failed 格被重试，重试池纪律保持；每调用 ≤5 次预算未越界。r1→r2→r3 官方口径：43.2% → 46.4% → 47.2%（**注意：r3 的 flags 集与 r2 不同，且 R3-1 经本审查证实为 no-op，增量不可归因于 768 上限**）。

---

## 5. R3-1 副作用检查（768 截断）—— 结论：**零截断证据，且上限被 provider 忽略（no-op）**

- **全量数据面**【实证】：4,213 个 executor 轮（[TOKEN_COUNT] 为单次响应 usage，非轮内求和，代码 simple_hercules.py:227-249 核实）中，`completion_tokens == 768` 恰等的轮数 = **0**；>768 的轮数 = 215（最大 3,918）。74 份日志的最大轮 >740。超时/失败格抽查（bisect-angle att2 最大 3,918、use-slider-2 3,491、order-food 3,474、click-pie 3,405、book-flight att2 3,255）无一呈现"顶格戛然而止"形态。对照 r2（无上限，adapt 默认 4096，同 provider 同模型）：250/3,721 轮 >768、最大 3,402——两轮分布同质，r3 数据与"上限未生效"假设完全一致。
- **live 实验定性**【实测】：经引擎真实注入路径（`NAV_MAX_COMPLETION_TOKENS=768` + `create_chat_model`，model 对象 `max_tokens=768` 确认）对 glm-5.3-flash 发起长输出请求：模型**完整产出约 13.9k 字符内容（远超 768 token）**，usage 报 `completion_tokens=19,349`；同请求无上限时报 9,467。即 GLM coding 端点把 `max_tokens` 当参考值而非硬上限（思考 token 另计、内容不被截断）。
- **判定**：本轮担心的 R3-1 副作用（合法长输出被截断拖垮任务）**发生率为零且结构性不可能**（对该 provider）；但代价是 R3-1 作为干预完全无效——r3 与 r2 在 completion cap 维度上无差异，r2→r3 任何增量不得归因于 R3-1，发表时 flag 表必须把 `nav-max-tokens=768` 标注为"注入成功、provider 未生效"。

---

## 6. F1 修复验证 —— 代码在位、链路工作、无触发场景（符合预期）【实证】

- **重试排除代码**：`orchestrator.py:1259-1262` —— `invalid_reason` 非空的行被显式跳过出重试池并落 `excluded from the retry pool` 日志（与 pre-F1 建议一字不差）。
- **扫描标记链路**：合成日志过当前代码 `scan_cell_log`，五类标记 → `flagged`/`invalid_reason` 映射全部正确（§3）。
- **本轮无 live 场景**：invalid=0，故排除逻辑零触发——这正是"防线在位但未被需要"的理想状态；重放扫描 137/137 一致证明链路整体可信。

---

## 7. drag 家族实效 + 其余核查

- **drag 家族 3/9 通过（r2 为 0/9）**【实证】：drag-items（raw=1, 147s）、drag-sort-numbers（raw=1, 125s）、drag-cube（raw=1, 505s, junit=False 的 C1a 页面权威通过）；drag-circle/drag-box/drag-items-grid/drag-shapes/drag-single-shape 判负、drag-shapes-2 timeout。**kill-switch 判据达成**：9/9 格 stdout.log 含 ≥1 条 `Found source element using selector`（计数 1/4/3/4/2/1/9/2/2）——选择器透传修复在真实运行的解析面全部生效，drag 工具（子集加载）亦 137/137 注册、被实际使用（含 `text=` 形态）。pre-F3 预警的"同页第 2+ 次拖拽停摆"未在成绩面显形（每格新页面首拖即用，与预判一致）。
- **use-slider-2 off-seed（M2）**【实证】：planner 下发的 next_step 把 `...?r2g_seed=1763424827&r2g_ms=240000` 重写为 `...?r2g_ms=240000`（stdout.log:234 → 247），executor 照开；页面无种子参数 → 无 auto-start 补丁、无奖励记录、timeout 判负。零分值影响，但暴露"planner 可变更任务 URL 参数"的面。
- **外联公网 = 0**（r2 为 4 格 21 次）【实证】：全部 `Opening URL` 均为 127.0.0.1/about:blank；非 miniwob 路径仅 3 条畸形形态（缺 `/miniwob/` 前缀、`/close_task` 404、ascending-numbers 的 planner 剥种子同类畸形 `127.0.1` host + 错种子 `1100809017`，均导航失败、零效果）。
- **预算/分片账目**【实证】：manifest `started_at=19:24:39` 为**末次** invocation——results 行时间跨 11:56→22:44，至少 3 次调用续跑；manifest `budget`（hercules_used=48 / retries_used=5）仅反映末次调用，跨调用合计仍以 results 行数为准（137 次 = 125 + 12 重试；r2-pre W5 边界延续披露）。3 个 no_reward 格（email-inbox-delete 17:11、social-media 21:10、text-editor 21:34）因后续调用的重试预算被文件序靠前的格耗尽而未获重试——无完整性影响，报告须如实呈现其 no_reward 终态。
- **断言纪律观察值（信息级）**：junit↔官方不一致 25 行（24 行 junit=True/official=False 假阳性自评 + 1 行 junit=False/official=True 低报；r2 为 17+4）。官方判定零受影响（页面权威），但 assert_discipline 未消除假阳性自评——效果评估归分析报告，非安全事件。
- **工作树/密钥**：r3 窗口零 tracked 改动风险面（runs/opt 内数据均 gitignore）、双 key 零命中（§2）。

---

## 8. r4 安全注意项（按优先级）

1. **P1：修正 R3-1 的表述与归因**。所有 r3 报告/表格中 `nav-max-tokens=768` 必须标注"引擎注入成功、GLM 端点未生效（max_tokens 不作内容硬上限，思考 token 另计）"；r2→r3 对比不得把任何差异记到 R3-1 头上。若 r4 换 provider 或需要真实 cap：先做一次 live 长输出 max_tokens 尊重性预检（本报告 §5 方法学可复用），生效后重新激活截断副作用检查（轮级 `completion_tokens==cap` 计数 + 无上限对照）。
2. **P1：堵 planner 重写任务 URL 的面**（use-slider-2 教训）：runner 侧在执行导航步骤前校验/补齐任务 URL 的 `r2g_seed`/`r2g_ms` 参数（或 planner prompt 明示 URL 逐字使用）；扫描器建议把"nav=0 且 timeout"形态列为 flagged 披露（本轮 nav=0 恰好是 off-seed 格的唯一可见信号，目前是中性值）。
3. **P2：重试翻正口径固定**：headline 59/125 旁必须同表给首实例 58/125 并点名 click-menu-2（合法 infra 重试 + 页面权威）；V4 重导航通过格（choose-date-nodelay、drag-cube）逐格披露。
4. **P2：分片账目**：manifest 建议聚合历次 invocation 计数（或声明 budget 字段仅末次调用）；3 个未重试 no_reward 格在报告脚注披露。
5. **P3：维持既有披露**（240s/600s、单 seed 单次、Gherkin 中转、奖励端点无防伪造、视觉盲区 11 格）；E1 子集加载、E2 标记、E3 白名单、E5 沙箱关停、F1 重试排除五件套在 r4 保持原样（本轮全部实证有效）。

---

## 9. 证据与复现索引

- 扫描复算：`scan_cell_log` + `scan_cell_rewards`（orchestrator.py:447/537）对 137 行重放，逐字段与 results.jsonl 一致；合成标记链路测试五类全过。
- 三口径：metrics.summarize（official/clean 59/125）；首实例按 r2-post §5 方法对 rewards.jsonl 逐格首记录；零重导航子集取 latest 行 nav==1。
- 关键日志行：use-slider-2 stdout.log:234/247（planner 剥 r2g_seed）；count-sides att1 stdout.log:361、bisect-angle att2 :402、click-pie-nodelay att2 :1451、visual-addition att2 :1135（4 次 `[OPEN_URL_BLOCKED]`）；drag-cube att1 stdout.log:374/730（断言纪律引发的 #reward 验证指令，被工具面阻断）。
- R3-1 实测：env=768 → model.max_tokens=768 确认；长输出请求内容 ~13,892 字符完整返回、completion_tokens=19,349（无上限对照 9,467）；数据面 0 轮==768、215 轮>768 vs r2 250/3,721。
- F1：orchestrator.py:1259-1262；重试池 12 次 att2 的 att1 状态全为 timeout/no_reward。
- 双 key：LLM-Key.txt / GLM-Key.txt 逐值 vs `dev_runs/benchmark/miniwob-r3/` 零命中；pre-F2 的 5 个残留文件已确认删除。
