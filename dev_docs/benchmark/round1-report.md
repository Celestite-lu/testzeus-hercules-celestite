# 第一轮报告 — miniwob-r1：MiniWoB++ 全量 125 任务基准成绩

> 固化时间：2026-09-22。数据：`dev_runs/benchmark/miniwob-r1/`（gitignore）。安全闭环：实验前审查 YELLOW→P0 加固（`security-review-r1-pre.md`）→ 实验后复审 GREEN（`security-review-r1-post.md`），五类作弊证据全部为零。
> 本报告是**多轮迭代的基准轮**：所有后续轮次与此对照。

## 1. 实验设置（完整口径披露）

- 任务：browsergym registry 全量 125 任务类，每任务单 seed 单次（seed = sha1(exp_id:miniwob:task_id)）
- agent：Hercules 执行内核（planner→executor 三节点 LangGraph），模型 deepseek-v4-pro（全角色单模型），意图经 Gherkin 中转（Given=带 seed 任务 URL / When=指令原文 / Then=任务完成）
- 观察：md 属性蒸馏 DOM（文本档，非视觉）
- 计时：页面 episode 240s（原版 10s，**放宽已披露**）；引擎侧 600s
- 判分：页面原生 `WOB_RAW_REWARD_GLOBAL > 0`（无 LLM 裁判）；P0 加固后 HUD 奖励显示与 START 重开层已封死
- 与公开数字的可比性限制：模型非 GPT-4、计时放宽、Gherkin 中转、单 seed 单次

## 2. 成绩（三口径）

| 口径 | 数字 | 说明 |
|---|---|---|
| **官方 last-wins（主口径）** | **54/125 = 43.2%** | 页面最终实例奖励 |
| 首实例 | 59/125 = 47.2% | rewards.jsonl 还原每格首个实例结果（4 no_goal + 3 no_reward 计 0） |
| 零重导航子集 | 37/60 = 61.7% | 仅有选择偏差，作诚实性指标不作 headline |

- 成本：12.9M token（全程红线内）；wall ≈ 336s/格
- 状态分布：官方通过 54、页面/引擎超时 29、官方失败 36、no_goal/no_reward 6
- 复审关键发现：**格内洗白 0 格**——重导航在 r1 是净伤害（6 格反向自伤，其中 5 格页面已过但被 600s 超时判负——harness 判分瑕疵，r2 修复：页面奖励优先于进程超时）

## 3. 对照公开基线（`baselines.md`）

- 可比带 A 档（零样本/文本-DOM/全任务 GPT-4 级）：**47-60%**——我们的首实例口径 47.2% 触及带内下沿，官方口径 43.2% 略低于带
- Agent-E（81.6%，同源架构冲刺锚点）：差距 34-38 个百分点
- 带演示/训练档（94-99%）：不同 regime，不作对比
- **结论**：未经任何优化的第一轮已具备公开可比性；与 A 档的差距主要可归因于（按证据强度）：① 29 格超时（延迟问题非能力问题）；② deepseek-v4-pro vs GPT-4 的规划差距；③ 视觉/几何类任务（DOM agent 天然盲区）

## 4. 失败结构（r2 改进的证据基础）

1. **超时 29 格（最大单一失分桶）**：240s 页面计时 vs 每步 10-40s LLM 延迟；多步任务（ascending-numbers、email-inbox 系列、book-flight）烧不完
2. **判分瑕疵 5 格**：页面已过但引擎 600s 超时判负（白丢分）
3. **重导航自伤 1 格 + 普遍行为**：61/125 格出现重导航，无一洗白、1 格直接致败——planner 在卡住时倾向"刷新重来"，应治理
4. **任务家族集中失败**：日期选择器（choose-date×3）、订机票（book-flight×2）、几何视觉（bisect-angle/circle-center 等）
5. **运维问题**：3 格重试行因 LLM 402（余额不足）秒崩——r2 需熔断器；attempt 日志覆盖（r2 按 attempt 分目录）

## 5. r2 改进方向（待多子代理分析定稿，此处为数据指向的候选）

- 判分修复：页面奖励优先于引擎超时（+潜在 4%）
- 延迟压缩：planner 轮次/提示词精简、或页面计时口径调整（须重跑对照并披露）
- 重导航治理：prompt 约束或补丁层单次开局（P2 可选项 B，现有证据支持采纳）
- 402 熔断与 per-attempt 日志

## 6. 可复现性

命令：`uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-r1 --stage full`（断点续跑；seed 派生确定性）。安全审查与复审报告为成绩有效性背书。
