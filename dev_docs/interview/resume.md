# 面试简历材料 — 「录制即用例」

> 视角：资深面试官反向打磨（10 秒第一判断决定深挖方向）。数据截止 r4（2026-09-24 固化线）。
> 纪律：每个数字可溯源到 dev_docs 出处（见 §4 弹药表）；诚实是第一原则——所有边界主动披露，不编造。
> 使用前提：面试前必须先完成审计报告 G1（成果推送 GitHub）与 G3（两分钟演示视频），否则本材料的第一道入口失效（见 `dev_docs/reports/interview-readiness-audit.md` §2）。

---

## 1. 三个岗位变体（简历项目段）

> 同一个项目的三种讲法，按投递岗位二选一；标题行即 10 秒第一判断，bullet 全部动词开头、全部量化。

### 1.1 AI Agent 开发方向（突出：多 agent 编排 + 四轮迭代方法论 + 评测素养）

> **录制即用例：AI Web 测试工具链 + MiniWoB++ 全量基准四轮迭代（43.2%→61.6%）**（独立开发）

- 设计零样本文本-DOM 观察口径，跑通 MiniWoB++（browsergym registry）全量 125 任务类，四轮迭代 43.2%→46.4%→47.2%→61.6%，越出同 regime 公开可比带（零样本文本-DOM GPT-4 级 47-60%）上沿
- 建立逐格归因驱动的迭代方法论：每轮对 125 格逐格归因，据此定位"可见但不可寻址"元素盲区，扩展 md 终表过滤器后单轮实证 +17/24 通过格，超时格 21→9、无效格连续两轮清零
- 构建防作弊评测纪律：页面原生 reward 唯一权威判分（无 LLM 裁判）、own-seed 过滤、五类作弊扫描、每轮实验前/后双安全审查全零；一次成功路径绕过扫描事件按预登记契约无效化 10 格（宁掉分不洗分）
- 实现录制→蒸馏→语义执行→失败归因四命令管线（录制器/蒸馏器/归因器/编排 CLI 为自研，492 条 pytest），归因器采用 13 条错误签名规则优先 + 证据引用回查、查无此证强制降级
- 解剖同源架构前身 Agent-E 仓库 HEAD 并逐组件对照（13 项机制逐行 diff）：机制组件对照无缺失，判分完整性/多域编排/上下文治理上领先；核实公开 81.6% 数字归属错误（实为 HxAgent 偏科子集），主动废弃错误锚点
- 构建可复现实验基建：seed 派生同构、off 基线 golden 逐字节锁定（T3a/T9a）、双 key 脱敏，单轮 12.2M token 预算内完成全量 125 格评测

### 1.2 测试开发方向（突出：确定性蒸馏 + 防作弊评测纪律 + 归因闭环）

> **录制转自然语言用例的 AI E2E 测试工具：确定性规则蒸馏 + 语义化执行 + 失败自动归因**（独立开发，492 条测试）

- 实现确定性三层蒸馏管线：规则模板骨架（零 LLM）→ 可选 LLM 润色 → 事实回查校验（字面值必须能在录制事件流中找到，不过即回退纯模板），骨架正确性对模型能力零依赖
- 设计受控 UI 变异实验（6 流程 × 5 变异 × 2 方法 = 60 格）：标识符随机化变异下生成用例存活 6/6、selector 式基线 0/6，主指标 1.00 vs 0.667；文案改写变异 5/6 如实量化语义自愈的概率性边界
- 实现失败归因器：13 条错误签名规则优先定类（LLM 不参与）+ LLM 摘要强制引用证据（日志行/截图/工具原文）、程序回查引用存在性、查无此证强制降级"无法定位"——真实失败格完成归因闭环验证
- 建立评测防作弊纪律：页面原生 reward 唯一权威判分、五类作弊扫描、每轮实验前/后双安全审查；成功路径绕过扫描事件按预登记契约压分 10 格（宁可掉分不洗分）
- 编写 492 条 pytest（recorder 21 / distiller 68 / evaluation 68 / attributor 81 / cli 54 + benchmark 200），并能定位自身 fixture 隔离缺陷（session 级 playwright 实例冲突、特定子集乱序误红 6 条）
- 量化执行成本：30 次真实 LLM 执行共 411 万 token（均值 13.7 万/次，单场景 7.3 万-42 万）；MiniWoB++ 全量 125 任务单轮 12.2M token 内完成

### 1.3 前端 + AI 方向（突出：前端测试痛点的诚实解法——能讲清哪 6/6、哪 5/6）

> **为前端团队打造「录制即用例」AI E2E 测试工具：点一遍页面即得用例、UI 改版不挂、失败自动定责**（独立开发）

- 实现注入式 JS 事件录制器（bookmarklet 零安装）：手动操作页面即捕获点击/输入/导航事件流，确定性蒸馏成 Gherkin 用例，生成用例首轮真实执行通过率 6/6
- 受控变异实验（6 流程 × 5 变异 × 2 方法）实证 selector 腐烂痛点：id/class/testid 全部随机化（模拟前端改版）下，语义执行用例存活 6/6，selector 式 Playwright 脚本 0/6
- 语义执行每次现场理解 DOM（属性蒸馏接近 accessibility-tree），无持久 selector；失败归因报告自动定责四类（产品 bug/用例过时/环境/agent 限制，13 条签名规则优先）
- 基于 LangGraph 多 agent 执行内核二次开发，跑通 MiniWoB++ 全量 125 任务类、四轮迭代 43.2%→61.6%，验证语义执行内核在 125 类页面交互上的规模化能力
- 内建安全默认值：密码录制即掩码、LLM key 仅经子进程 env 注入、日志强制脱敏（双 key 扫描全零）
- 如实声明边界（半解而非全解）：多页导航 v1 scope out（检测到跨文档导航干净退出不落脏数据）、跨域 iframe/closed shadow DOM/canvas 为硬盲区、文案改写自愈 5/6 属概率性

---

## 2. 一句话电梯稿

### 2.1 简历一行版（投递系统/内推一句话）

> 独立开发「录制即用例」AI E2E 测试工具（确定性蒸馏+语义执行+失败归因）：受控 UI 变异实验中标识符随机化下用例存活 6/6、selector 基线 0/6（主指标 1.00 vs 0.667）；基于多 agent 执行内核跑通 MiniWoB++ 全量 125 任务，四轮迭代 43.2%→61.6%，越出公开可比带（47-60%）上沿。

### 2.2 口头 15 秒版（面试开场自我介绍嵌一句）

> 前端测试两大痛点：没人写用例、selector 一改版就烂。我的工具让人手动点一遍页面就自动生成自然语言用例，多 agent 内核每次现场理解 DOM 执行——改版不挂、失败自动定责。受控实验里标识符全变时用例存活 6/6 对基线 0/6；MiniWoB++ 全量 125 任务四轮从 43.2% 迭代到 61.6%，每格都能归因。

### 2.3 GitHub profile 版（profile 置顶项目描述）

> **Record2Gherkin —「录制即用例」AI E2E 测试工具**（独立开发）：手动点一遍页面 → 注入式录制器捕获事件流 → 确定性规则蒸馏成 Gherkin（骨架零 LLM、事实回查防幻觉）→ 多 agent 内核语义化执行 → 失败按规则签名+证据引用自动归因。两套硬数字：受控 UI 变异实验（6 流程×5 变异×2 方法）标识符随机化下存活 6/6、selector 基线 0/6，主指标 1.00 vs 0.667，文案改写边界 5/6 如实量化；MiniWoB++ 全量 125 任务类零样本文本-DOM 口径四轮迭代 43.2%→61.6%，越出公开可比带（47-60%）上沿，逐格归因、每轮前/后双防作弊审查。基于开源执行内核 Hercules（AGPL v3）二开：录制器/蒸馏器/评测框架/归因器/编排 CLI 为原创增量，492 条 pytest。

---

## 3. 口径卡（背熟，被追问任何数字时一口气讲完）

### 卡 1：61.6% 六要素口径

一口气版：**MiniWoB++ browsergym registry 全量 125 任务类；零样本文本-DOM 观察（md 属性蒸馏，≈accessibility-tree 档，非视觉）；GLM 5.3 系列（planner glm-5.3 / nav glm-5.3-flash；r2 起切换，r1 为 deepseek-v4-pro）；episode 计时 240s（原版 10s，放宽已主动披露）；每任务单 seed 单次；页面原生 `WOB_RAW_REWARD_GLOBAL > 0` 判分（无 LLM 裁判），官方=clean 零无效格。**

结论措辞：**"越出同 regime（零样本文本-DOM、零样本、全任务集）公开可比带 47-60% 的上沿"**——不说 SOTA，不做跨口径对比。被问"为什么 240s"：MiniWoB 原版 10s 是原论文 RL 设定，LLM agent 单步含推理与工具调用远超该节奏；放宽已作为披露项逐条写入 baselines 文档与各轮报告——计时差异是披露项，不是隐藏项，可比性依据是观察模态与零样本设置（A 档）。

### 卡 2：1.00 vs 0.667 口径

一口气版：**受控实验，6 条录制流程 × 5 种 UI 变异 × 2 种方法 = 60 格，单 demo 应用、单模型（deepseek-v4-pro）。主指标 = 三种结构性变异（字段调换/布局微调/标识符随机化）存活率均值：本工具 1.00、selector 式基线 0.667。**主结论是 M3（id/class/testid 全随机化）6/6 vs 0/6——差距恰好落在"selector 腐烂"真实痛点；M1/M2 双方都免疫（基线按属性定位），如实并列；M4 文案改写 5/6 是概率性边界，唯一失败格是 Then 断言绑定录制时原文案（属测试的正确行为），6 格样本不给概率点估计——限定词写在报告里，不等追问。

### 卡 3：492 测试口径

一口气版：**`uv run pytest tests/record2gherkin -q` → 492 passed（2026-09-24 实测 94.45s）。构成：recorder 21 + distiller 68 + evaluation 68 + attributor 81 + cli 54 = 292 五模块基础 + benchmark 线 200。**被问测试质量时主动讲自己的缺陷：session 级 playwright fixture 与 CLI 的 sync 实例冲突，特定子集乱序会误红 6 条——知道根因、官方全量入口绿、修法是一行 fixture scope。

### 卡 4："超越 Agent-E HEAD"的说法边界

**三不说：**
1. 不说"分数超过 Agent-E"——它从未发表 MiniWoB++ 数字，唯一公开数字是 WebVoyager 73.2%（GPT-4-Turbo、人工评判），与我方口径零交集
2. 不说"距 Agent-E 差 20 个百分点"——该叙事建立在错误归属上，已废弃（round4 报告为历史原文）
3. 不把 81.6% 当作任何人的 MiniWoB 通用成绩引用；若面试官主动提 81.6%，给出更正：那是 **HxAgent**（arXiv 2608.15491）的数字，且四重口径不可比（5 任务族×25 实例偏科子集 / GPT-4o 多模态 / 每族 20 训练实例+8 条 few-shot 经验注入 / 动作序列金标 Exact-Match 判分、±34.6 方差）

**两说：**
1. 说"**机制组件对照无缺失**"：实克隆 Agent-E 仓库 HEAD（f218c3c）逐文件 diff，13 项机制组件（分层 planner、md/mmid DOM 蒸馏、mutation observer 变化观察、LTM、技能集等）我方已继承或改造后覆盖；论文传说中的 Curious Reflector/独立 self-evaluator 在论文 v1 与仓库 HEAD 中均不存在
2. 说"**判分完整性领先**"：页面原生 reward + own-seed 过滤 + 五类作弊扫描 vs 它的人工/字符串判分；另有多域编排（七类 helper 路由 vs 仅 browser）、上下文/token/超时治理、md-extended DOM 覆盖、工具超集（22+7 个工具）

一句话总结措辞："我们不是在追一个更强的祖先，而是已经分化出更严的评测文化。"

---

## 4. 数字弹药表（面试官追问任何数字，3 秒定位出处）

### 基准主线（MiniWoB++ r1-r4）

| 数字 | 口径/上下文 | 出处 |
|---|---|---|
| **61.6% = 77/125** | r4 headline，官方=clean，零无效格 | `dev_docs/benchmark/round4-report.md:9` |
| 四轮线 43.2→46.4→47.2→61.6 | r2 官方 46.4%（clean 47.8%） | `round4-report.md:9`、`dev_docs/STATUS.md:17` |
| 公开可比带 47-60% | A 档：零样本文本-DOM GPT-4 级（~47% 全任务集；BrowserGym 系 GPT-4 50-59%） | `dev_docs/benchmark/baselines.md:16-18,26` |
| 首实例口径 60.8%（76/125） | 与官方=clean 并列披露的第二口径 | `round4-report.md:10` |
| 零重导航子集 68.5%（74/108） | 治理重导航后的子集成绩 | `round4-report.md:11` |
| 超时 21→9；无效格 0（连续两轮） | r4 相对 r1/r2 的可靠性改善 | `round4-report.md:14` |
| token 12.2M/轮 | 单轮全量 125 格预算 | `round4-report.md:14` |
| email 家族 0/20→4/10 | r4 email 族突破 | `round4-report.md:14` |
| **+17/24 格** | --md-extended 终表扩展：24 新通过格中 17 格有 md 点击日志实证，其中 12 格为"可见但不可寻址"元素 | `round4-report.md:21` |
| --verify-before-done 贡献 0 分 | 诚实性组件：收益是 no_reward 终局清零（判分完整性），不虚报分数贡献 | `round4-report.md:22` |
| 方差 +7 格 / 单 seed ±10 格 | 归因时如实单列噪声贡献 | `round4-report.md:23` |
| 五类作弊扫描+双 key 脱敏全零；md 表 max 129<150 无截断 | r4 post 审查 | `round4-report.md:28` |
| own-seed 过滤、endEpisode 复触发不翻案 | 判分权威性细节 | `round4-report.md:29` |
| 剩余 48 格 = 官方失败 ~39 + 超时 9 | 差距构成已知，r5 方向数据指明 | `round4-report.md:34` |
| majority-of-3 降噪 ±10→±3（设计未跑） | 口径决策备答，不冒充已完成 | `round4-report.md:35` |
| 六要素设置（125 任务/240s vs 原版 10s/单 seed/页面原生判分/Gherkin 中转） | 披露义务清单 | `baselines.md:5-10,29` |
| 模型切换：r1 deepseek-v4-pro → r2 起 GLM | deepseek 余额尽，coding plan 切换 | `STATUS.md:16` |

### 工具主线（exp001 + 工程）

| 数字 | 口径/上下文 | 出处 |
|---|---|---|
| **M3 6/6 vs 0/6** | 标识符（id/class/testid）随机化变异存活率 | `dev_docs/evaluation/experiment-report.md:22` |
| **主指标 1.00 vs 0.667** | M1/M2/M3 三变异均值 | `experiment-report.md:22` |
| 首轮通过率 6/6 | M0 对照，生成用例第一次真实执行 | `experiment-report.md:24` |
| M4 文案改写 5/6 vs 6/6 | 概率性边界；唯一失败格归因 `Missing: '去下单' (found '马上下单')` | `experiment-report.md:22,30-31` |
| 60 格 = 6 流程×5 变异×2 方法 | 实验矩阵与样本量限定词 | `experiment-report.md:14` |
| 30 次执行 4,109,386 token（均值 136,980；F1 7.3 万 / F3 约 42 万） | 真实 LLM 执行成本 | `experiment-report.md:25` |
| **492 passed（94.45s，2026-09-24 实测）** | tests/record2gherkin 全量 | `dev_docs/reports/interview-readiness-audit.md:4,111` |
| 模块分布 21/68/68/81/54（=292）+ benchmark 线 200 = 492 | 五模块 + benchmark | `record2gherkin/README.md:26-31`、`interview-readiness-audit.md:39` |
| 13 条错误签名 + 五档分类 | 归因器规则层（LLM 不参与定类） | `record2gherkin/README.md:30` |
| MPA 干净退出 exit 2 不落脏数据 | 多页 scope out 的工程化处理 | `record2gherkin/README.md:47` |
| F1 真实执行 passed，62s / 7.3 万 token | CLI 全链路真实验收 | `STATUS.md:15` |
| W2 成真 10 格无效化压分 | r2 安全事件，按预登记契约处理 | `STATUS.md:17`、`interview-readiness-audit.md:100` |
| 超时格 51.3 轮 vs 通过格 19.9 轮 | executor 强制重感知双倍轮次的架构发现 | `STATUS.md:26` |
| max_tokens no-op：768 配置实测产出 19,349 token | "声明在位≠生效"的 fail 故事 | `interview-readiness-audit.md:99` |
| fixture 子集乱序误红 6 条 | 自知缺陷（诚实工程素养素材） | `interview-readiness-audit.md:101` |
| MiniWoB vendored 360 文件 sha256 入库（BSD-3） | 供题合规 | `interview-readiness-audit.md:49` |

### 对比锚点（谨慎使用，见 §3 卡 4 与 §5 红线）

| 数字 | 口径/上下文 | 出处 |
|---|---|---|
| Agent-E 论文唯一数字：WebVoyager 73.2%（GPT-4-Turbo、人工评判） | 从未发表 MiniWoB++ 数字 | `dev_docs/benchmark/agent-e-anatomy.md:27` |
| 81.6% 真实归属 = HxAgent（arXiv 2608.15491） | 四重口径不可比（偏科子集/多模态/经验注入/金标判分 ±34.6） | `agent-e-anatomy.md:7,32-39`、`baselines.md:18,27` |
| 组件对照 13 项无缺失 | Agent-E HEAD f218c3c 逐文件 diff | `agent-e-anatomy.md:67-82`（小结 :82） |
| 超越点 6 条（判分完整性/多域编排/基建/DOM 覆盖/工具超集/任务表示） | 判分完整性与反作弊是核心领先项 | `agent-e-anatomy.md:122-129` |
| 工具超集 22 + 7 个；七类 helper 多域路由 | vs Agent-E 仅 browser | `agent-e-anatomy.md:76,125` |
| 视觉/画布族盲区 ~11 格 | 剩余差距的主要构成之一 | `agent-e-anatomy.md:102` |
| 贡献边界（复用 vs 自研清单） | "哪些是你写的"标准答案 | `PLAN.md:24-39`（§2） |

---

## 5. 红线清单（简历/口头绝不能说）

1. **绝不引用 81.6% 作为锚点或对比分数**（归属错误，实为 HxAgent 偏科子集）；绝不复述"距 Agent-E 差 20 个百分点"的旧叙事（已废弃）
2. **绝不说 "SOTA" / "刷榜" / "超过了 GPT-4"**——只能说"越出同 regime 公开可比带（47-60%）上沿"；不同口径数字（HxAgent 81.6%、97.4%、Synapse 99.2%）一律不直接对比
3. **绝不贬低或隐瞒上游**：不暗示执行内核自研；被问"哪些是你写的"必须诚实分层——执行内核是开源的 Hercules（AGPL，能讲清 planner→executor→assertion 与 md 语义选择机制），录制器/蒸馏器/评测框架/归因器/编排 CLI/benchmark 改进包与全部实验设计是自己写的
4. **绝不夸大自研比例**：不说"自研 AI 测试平台/框架全栈"；GitHub profile 版已写明"基于 Hercules 二开"，简历标题避免"独立开发xx框架"的歧义表述，用"独立开发……工具（基于开源执行内核二次开发）"
5. **绝不隐瞒设置放宽**：240s vs 原版 10s、单 seed 单次、Gherkin 中转、GLM 模型——四个披露项必须主动讲，等被挖出即失分
6. **绝不让 1.00 vs 0.667 / 6/6 vs 0/6 脱离限定词出现**：必须带"6 流程×5 变异×2 方法、单 demo 单模型"；绝不说"覆盖率/通过率 100%";M4 5/6 必须并列讲（是边界不是缺点也不是卖点）
7. **绝不说"只差一步/追平即可"或承诺追平任何锚点**——被问差距的标准答案：先更正归属（81.6% 非 Agent-E，各家数字口径互不可比），再讲我方剩余 48 格的构成已逐格分析、在 Agent-E 的机制箱里找不到对应钥匙，r5 方向由数据指明（agent-e-anatomy §2 小结口径）
8. **绝不把未做的事说成已做**：majority-of-3 多 seed、截图升级观察、480s ablation、多页（MPA）支持、--polish/--llm 真机验收均为未跑/未验收，只能以"已设计/已规划"出现
9. **现场演示两条铁律**：不录 MPA 站点（注入 JS 随导航销毁，exit 2）、不开 --polish/--llm 现场跑真机（60s+ 执行 + key 依赖 + 翻车点）——只用离线四命令链路 + 固化产物录屏（audit G3）
10. **密钥零出现**：任何材料/口头/GitHub 不出现 key 内容；LLM-Key.txt 只讲机制（"gitignored 密钥文件、env 注入、日志强制脱敏"），不展示文件
11. **测试数字不说满**：492 是"pytest 用例数"不是"覆盖率 100%"；被问质量就主动讲 fixture 乱序误红 6 条的自知缺陷（反而加分）

---

## 附：材料使用前提（一行，来自审计报告，面试前必须闭环）

GitHub 上必须能看到成果（74 commits 推送 + 根 README 二开指引，audit G1）并有可播放的演示（离线链路录屏，audit G3）——否则本材料的"10 秒第一判断"入口（面试官点链接）直接失效。
