# MiniWoB++ 公开基线与目标阶梯（第一轮报告对照用）

> 调研时间：2026-09-21。来源为公开检索（arXiv/OpenReview/项目页），报告引用时需注明"检索自公开资料，设置细节以原论文为准"。

## 我们的实验口径（对照前提）

- 零样本（无演示、无少样本示例）、文本/DOM 观察（md 属性蒸馏 DOM，接近 accessibility-tree 档）
- 意图经 Gherkin 中转（Given=任务 URL / When=指令原文），执行内核 Hercules，模型 deepseek-v4-pro
- episode 计时 240s（原版 10s，放宽已披露）；每任务单 seed 单次；官方成败=页面原生 `WOB_RAW_REWARD_GLOBAL > 0`
- 全量 125 任务类（browsergym registry）

## 目标阶梯（按可比性分层）

| 档位 | 方法 | 分数 | 设置备注 |
|---|---|---|---|
| **A. 零样本/文本-DOM（最可比）** | GPT-4 agent（"The Unsolved Challenges of LLMs as Generalist Web Agents", NeurIPS 2023） | ~47% 全任务集 | 零样本文本观察 |
| A | BrowserGym/WorkArena 系 GPT-4 基线 | ~50-59% | 依赖观察格式，AST 优于裸 DOM 文本 |
| A | Agent-E（125 challenging instances） | 81.6% exact-match | Hercules 的直系前身，**本项目的冲刺锚点** |
| B. 少样本/带演示 | Synapse（trajectory-as-exemplar） | ~99.2% | 64 任务子集+示例，不同 regime |
| B | RCI（Reflection-Critique-Improvement, NeurIPS 2023） | 当时 SOTA | 少量演示 |
| C. 训练模型 | WebGUM（Google, 多模态） | 94.2% | 专门训练 |
| 参考 | 人类 | ~91%+ | 原论文 |

## 解读（写报告时的口径）

1. 与我们同 regime（零样本、文本/DOM、全任务）的公开数字带为 **47-60%**——第一轮成绩落入或超过此带即具备公开可比性
2. Agent-E 的 81.6% 是同源架构的合理冲刺目标（SOTA-for-regime 锚点）
3. B/C 档（带演示/训练模型 94-99%）**不是公平对照**，报告只作背景提及，不得直接对比
4. 设置差异必须逐条披露：240s 放宽、Gherkin 中转、deepseek-v4-pro（非 GPT-4）、单 seed 单次

## 来源

- The Unsolved Challenges of LLMs as Generalist Web Agents: A Case Study（OpenReview / UCLouvain）
- Synapse: Trajectory-as-Exemplar Prompting（OpenReview）
- WebGUM / mm-webnav（Google 项目页）
- BrowserGym 生态系统与 WorkArena 基线（ServiceNow+MILA）
- Agent-E（EmergenceAI，arXiv 2407.13032 系）
