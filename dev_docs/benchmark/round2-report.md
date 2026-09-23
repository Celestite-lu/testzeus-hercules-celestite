# 第二轮报告 — miniwob-r2：改进包 C1-C7 + GLM provider 的全量成绩

> 固化时间：2026-09-23。数据：`dev_runs/benchmark/miniwob-r2/`（gitignore）。安全闭环：实验前 YELLOW→W1 加固（`security-review-r2-pre.md`）→ 实验后 YELLOW（`security-review-r2-post.md`，判分零作弊；W2 成真按预登记契约处理 11 个安全事件格）。
> 与 r1 的对照受多变量混杂（模型更换），本报告逐项披露。

## 1. 成绩（对照 r1 基准 43.2%）

| 口径 | r2 | r1 | 变化 |
|---|---|---|---|
| **干净 headline**（剔除 11 个安全事件格） | **55/115 = 47.8%** | — | 触及公开带 A 档下沿（47-60%） |
| 官方 last-wins | 58/125 = 46.4% | 54/125 = 43.2% | **+4 格** |
| 首实例 | 58/125 = 46.4% | 59/125 = 47.2% | 零洗白（两口径首次完全相等） |
| 零重导航子集 | 55/102 = 53.9% | 37/60 = 61.7% | 诚实性指标 |

- 成本：9.08M token（GLM coding plan 额度内，分段执行 + 429 熔断配速）
- 失败结构：超时 41（最大桶）、官方失败 23、no_goal 1（r1 为 4，goal 修复生效）、no_reward 2

## 2. 相对 r1 的全部行为变更（口径披露，逐条）

1. **模型更换**：deepseek-v4-pro → GLM（planner=glm-5.3，nav/helper=glm-5.3-flash，coding plan 端点）——**多变量混杂，r1→r2 差异不能全部归因于改进包**
2. C1a 判分中立修复（页面奖励优先）——复审实证：零"页面已过被判负"，2 timeout 审计翻正、4 junit 不再否决
3. C2 终局信号（中性 `EPISODE ENDED`）——复审证明 agent 未借 cue 提前放弃（cue 300 处全部晚于终局；失败格中位空转 257s）
4. C3 单次开局——多导航格 **61 → 16**
5. C4 延迟包（markers_only 重感知/步级预算/prompt 修订）
6. C6 extra_tools（drag 工具启用；**副作用见 §3-W2**）
7. C7 模板上下文；goal 预读非空等待（r1 老 bug，no_goal 4→1）
8. 运维：NLTK 语料预装（r1 挂死根因）、chromium 回环代理旁路、429 熔断、--max-cells 配速

## 3. 安全结论（实验后复审）

- **判分零作弊**：五类应为零指标全部为零；11 次重试全部维持原判；洗白嫌疑 0
- **W2 成真**：`persist_findings` 在 10 格被调用（9 格静默写诊断笔记，内容良性零判分影响）——成功路径绕过扫描器，按预登记契约 10 格记无效 → 干净口径 55/115
- click-shape：沙箱 8 次调用全部文件解析失败零执行（扫描器捕获记无效；其通过真实，raw=1 早于沙箱尝试 91s）
- 3 格 `javascript:` URI 尝试被 open_url 的 https:// 前缀偶然化解——**r3 必须补 scheme 白名单**
- key 双 grep 零命中（GLM/DeepSeek 两 key 对 dev_runs 全目录）

## 4. r3 改进方向（数据指向）

1. **超时 41 格**（最大失分桶）：GLM flash 单步延迟高——提速方向：nav 提示词进一步压缩/更小执行模型/减少每步感知轮次；或 240s 计时敏感性 ablation（480s 单列）
2. **extra_tools 治理**：默认关掉 persist/recall/augment 文件工具或按需子集加载 + 调用日志行（安全+口径双收益）
3. **open_url scheme 白名单**（引擎侧，`javascript:` 已实际出现）
4. 拖拽家族仍全灭（工具已加载但 600s 不够完成）——专项分析其交互模式
5. click 家族 14 格失败（click-collapsible/pie/shape 等变体）——DOM 感知对特定控件形态的盲区分析

## 5. 可复现性

命令（两段配速）：见 `manifest.json` flags + `--max-cells`；断点续跑同命令。seed 派生确定性与 r1 同构。
