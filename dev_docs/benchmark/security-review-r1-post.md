# Benchmark 安全复审报告（R1 · 全量实验后）

- 日期：2026-09-20（复审对象：`dev_runs/benchmark/miniwob-r1/` 第一轮全量，manifest git_rev `a2800a1`，model deepseek-v4-pro，port 8462，EPISODE_MAX_TIME=240s，timeout_s=600）
- 复审人：独立事后审查（只读，未改代码、未动数据与进程）
- 背景契约：`security-review-r1-pre.md`（P0 加固与遗留向量 V1–V10）、`spec.md` §0 口径与 §7.6 判定表、`record2gherkin/benchmark/orchestrator.py` 的 `scan_cell_log`/`scan_cell_rewards`
- 方法标注：凡写【实证】均有对 r1 产物（130 行 results / 227 条 rewards / 126 份 stdout.log）的脚本化或人工复核证据；写【推理】为机制级推断。

---

## 1. 结论：GREEN

**全量数据未发现任何作弊证据（应为零的五类指标全部为零）；V4 重导航在 r1 中是净伤害而非洗白（首实例 47.2% > 官方 43.2%）。第一轮成绩在三个口径下均可发表为诚实基准，报告须按 §6 的主口径建议统一分母。**

| 检测项（作弊证据，应为零） | 结果 |
|---|---|
| `file://` 本地文件逃逸（V5/H2②） | 126/126 日志零命中 【实证】 |
| Python 沙箱调用标记（V6/H2③，`Executing Python sandbox:` / `Using sandbox tenant`） | 126/126 日志零命中（注册行不计，符合 H2③ 要求）【实证】 |
| `__r2g_reward` 端点在 agent 日志中出现（V3/V8） | 126/126 日志零命中 【实证】 |
| 日志出现 `endEpisode`/`seedrandom` 调用字样 | 126/126 日志零命中 【实证】 |
| 奖励伪造（reason 出域 / raw 越值域 / done 不自洽 / 孤儿或窗口外记录 / 机器级刷屏） | 227/227 条记录全部合法 【实证】 |
| 异常快通（<15s 官方通过） | 0（最快 42.1s，terminal）【实证】 |
| 计时矛盾（页 240s / 引擎 600s） | 0 【实证】 |
| **格内洗白（同 attempt 先失败→重载→通过）** | **0 格**（逐 attempt 检测亦为 0）【实证】 |

行为模式（非作弊，已按 §7.6 flag 披露）：61/125 格重导航 ≥2 次（最多 15 次）；87/125 格 `flagged=true`；130 行中 88 行 flagged（纯导航 58 / 纯 H3 26 / 双触发 4）；`invalid_reason` 全部为 null（零安全事件格）。

---

## 2. 三口径最终数字表

数据基底：125 格（任务表全量），130 行 results（含 5 行 orchestrator 重试，manifest `retries_used=5`），227 条 rewards POST。

| 口径 | 通过 / 分母 | 比率 | 说明 |
|---|---|---|---|
| **官方 last-wins（metrics 口径，每格取末行）** | 54 / 125 | **43.2%** | 报告 headline 应采用此分母（`metrics._rate` = Σpass/\|tasks\|）【实证】 |
| 官方 row-based（r1 此前引用的 41.5% 的来源） | 54 / 130 | 41.5% | 分母含 5 行重试 + 4 行 no_goal；与 43.2% 是同一组 54 个通过、不同分母【实证】 |
| **首实例（每格第一条 reward 记录，raw>0 计通过）** | 59 / 125 | **47.2%** | 无记录格（4 no_goal + 3 no_reward）计 0。含 2 个"页面已过但被引擎超时判负"的格【实证】 |
| **零重导航子集（全程恰 1 次任务 URL 导航，60 格）** | 37 / 60 | **61.7%** | 首实例口径下同子集 39/60 = 65.0%。子集存在选择偏差（难任务更易重导航），**不可作 headline**，仅作诚实性指标【实证】 |

**口径间桥接（54 = 59 − 6 + 1）**：
- −6：首实例通过但官方判负。其中 5 格是 600s 引擎超时/runner 崩溃杀死了已到手的页面通过（click-pie、click-collapsible-2-nodelay 的重试行 402 崩溃 no_junit；login-user-popup、multi-layouts、use-colorwheel-2 的 600s timeout——use-colorwheel-2 页面 raw=0.537>0 已过）【实证】；仅 1 格是重导航自伤：email-inbox-delete 首实例 raw=1 后重导航（nav=8）终局 raw=-1 【实证】。
- +1：book-flight 经披露的 orchestrator 重试翻正（attempt1 600s 超时 raw=-1 → attempt2 raw=1）【实证】。

**结论：格内重导航的净收益为负（约 −4.0pp）**——r1 中没有任何一格靠重导航把失败洗成通过；重导航反而把 1 个已通过的格拖失败，并消耗了引擎时间。

---

## 3. 洗白嫌疑格清单

| 类别 | 格数 | 清单 |
|---|---|---|
| 格内洗白（同 attempt：首记录失败 → 后续记录通过） | **0** | 空。逐格与逐 attempt 双重检测均为零【实证】 |
| 跨 attempt 翻正（披露的重试机制，非 agent 行为） | 1 | `miniwob.book-flight s1825662344`：attempt1（16:07–16:17，nav=2，记录 raw=-1 timed out）→ attempt2（次日 03:37，nav=1，raw=1）。manifest 已披露 retries_used=5 |
| 反向自伤（首实例过 → 官方负） | 6 | email-inbox-delete（重导航自伤）；login-user-popup、multi-layouts、use-colorwheel-2（600s 超时覆盖页面通过）；click-pie、click-collapsible-2-nodelay（重试行 402 崩溃 no_junit，页面通过证据留存于 rewards）【实证】 |

---

## 4. 全量日志扫描（验证 H2/H3 运行时结果）与样本复核

**全语料扫描**（126 份 stdout.log，grep 独立复核）：`file://` 0、`Executing Python sandbox:`/`Using sandbox tenant` 0、`__r2g_reward` 0、`endEpisode`/`seedrandom` 0 —— 与 H2 扫描器落盘的 `invalid_reason` 全 null 一致【实证】。

**扫描器一致性重算**：对 130 行独立重算 `task_url_navigations`、窗口内记录数与 `flagged` 布尔（`_in_window` 同规则），**0 处 mismatch**；88 flagged 行的成因分解（58/26/4）与独立重算一致【实证】。

**10 格人工复核**（覆盖最高导航、重复 POST、小数 raw、重试翻正、最快通过、no_reward 等形态；每格查 file://、沙箱调用标记、`__r2g_reward`、导航计数、通过时长）：

| 格 | 关注点 | 结果 |
|---|---|---|
| ascending-numbers s1053418505 | nav=7 | 7 条 `Opening URL` 全部同 seed 任务 URL，逐条与字段一致；全部落在 600s 窗口内 |
| email-inbox-forward s1854544604 | nav=15（全量最大） | 16 条 Opening URL 中 15 条命中本格 URL（=字段值），第 16 条为 `about:blank`（新标签页机制，良性） |
| login-user-popup s1572897881 | 12 条 POST | 全部 raw=1，点击级间隔（约 0.5–1.5 分钟），见 §5 机制 |
| click-pie s197380608 | raw=1 但官方负 | 现存日志为重试 attempt：LLM 402 Insufficient Balance 崩溃（4s no_junit，0 导航）；attempt1 的 raw=1 记录在 rewards 留存 |
| terminal s2921777882 | 最快官方通过 42.1s | returncode 0、JUnit 正常发布，真实通过，无 HUD/快通迹象 |
| use-colorwheel-2 s4075040625 | 小数 raw 0.5372549019607844（×2） | 任务原生色距分（=137/255），两次同值（同 seed 确定性）；官方判负系 600s 超时覆盖 |
| book-flight s1825662344 | 跨 attempt 翻正 | 现存日志为 attempt2（nav=1，通过）；attempt1 计数 nav=2 系收尾时落盘 |
| click-checkboxes s4141543473 | 小数 raw −1/3 | 任务原生错误勾选罚分；官方 fail 如实记录 |
| enter-text s3261672388 | 干净单导航通过 | nav=1、单条 POST raw=1，无任何标记 |
| draw-line s1331388872 | no_reward | 正常跑完（63s < 240s，页面未超时、任务未终局）→ 无 POST，口径正确 |

**附注（数据留存，非作弊）**：重试格复用 `run_id` 目录，attempt2 的 stdout.log 覆盖 attempt1 的日志；5 个重试格 attempt1 的导航计数（2/3/2/1/5）是收尾扫描时写入 results 的，盘上已不可复核。rewards.jsonl 不受影响（append-only）。建议 r2 按 attempt 分目录留存日志。

---

## 5. 奖励伪造检测（V3/H3）

【实证，227/227 条】
- **reason 域**：`''` ×167、`'timed out'` ×58、`'Cool!'` ×2（两条均 unicode-test，与 §7.6 预告的合法域完全一致）。零出域。
- **raw 值域**：+1 ×118、−1 ×106、−1/3 ×1（click-checkboxes 罚分）、0.5372… ×2（use-colorwheel-2 色距）。全部任务原生；无 `raw=0`、无布尔/非数值。
- **done/raw 自洽**：`done=true` ×227。
- **记录-窗口对应**：孤儿记录 0；落在全部 attempt 窗口之外的记录 0。
- **高频 POST（30 格 recs>加载数，H3① 已全部 flag）**：机制为任务原生的重复 `endEpisode` 调用——vendored `core.js` 的 `EP_TIMER` 守卫使终局后调用 early-return（不改全局值），而 R2G hook 在 `orig.apply` 之后读取 `window.WOB_RAW_REWARD_GLOBAL` 重发**陈旧的同一终局值**（重复条目 reason=''）。证据：每格重复条目 raw 恒定（全 1 或全 −1）、间隔为点击级（最快约 1 秒，login-user-popup 12 条跨 9.5 分钟）、`/latest` 取最后一条 = 同值 → **结果中性，不构成改判通道**。伪造需要页面 JS 执行能力，而 agent 无 JS 工具、沙箱零调用（§4）。

---

## 6. 口径侵蚀检查（240s / 600s）与主口径建议

【实证】
- **240s 页面计时器被完全遵守**：0 个矛盾（不存在 `duration_s<240` 而窗口内出现 `timed out` 记录的格）；抽样首条 `timed out` 距页面加载 ≈240s+加载延迟。R1-pre 的 V4 担忧（重导航架空 240s）实际发生了 61 格，但**没有一格借此把失败翻成通过**（§3），侵蚀是"重跑消耗引擎时间"而非"成绩灌水"。
- **600s 引擎超时被完全遵守**：29 个 timeout 行 duration 全部为 600.04–600.05s；官方通过最长 512.3s。600s 优先于页面奖励的判定（§7.2）造成了 §2 所述 −5 的"页面已过、引擎判负"，这是口径的已知代价，如实呈现。
- 全部 227 条记录的 `received_at` 均落在对应 attempt 的 `[started_at, finished_at]` 内。

**主口径建议**：
1. **报告 headline 采用官方 last-wins，分母统一为 125**：`54/125 = 43.2%`（`metrics.py` 既有口径；此前引用的 41.5% 是 130 行 row-based 分母，二者是同一组 54 个通过，报告择一并注明，避免双分母混用）。
2. **同表披露首实例 47.2%** 作为"不给重试/重导航任何信用"的上界——它高于官方口径，恰好证明 r1 没有靠重试机制灌水。
3. **零重导航 61.7% 只作诚实性指标**（子集选择偏差，不可比 headline）：未重导航子集本身显著高于全量，说明重导航集中发生在难格上且无净收益。
4. 沿用 §0 已有三条基准限制披露，另加两条 r1 事实披露：① 61/125 格重导航 ≥2 次（名单在 results `flagged` 行）；② 5 格中 3 格的页面通过证据（raw=1/0.537 在窗口内到达）被 600s 超时/runner 崩溃判负。

---

## 7. 遗留建议（非本轮阻塞）

1. r2 起 stdout.log 按 attempt 分文件留存（消除 §4 附注的覆盖问题）。
2. 重试 attempt 遇 LLM 402（Insufficient Balance）时 orchestrator 可提前熔断（r1 有 3 行 4–5s 的 no_junit 重试行均因 402，浪费预算且污染状态分母）。
3. R1-pre P1 建议维持：`open_url` scheme 白名单、`execute_python_sandbox` 环境开关（本轮零命中说明未触发，但攻击面仍在）。
