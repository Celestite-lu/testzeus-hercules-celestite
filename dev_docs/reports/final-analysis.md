# 终审分析 — 「录制即用例」全项目收尾评审

> 终审代理：GLM。日期：2026-09-21。范围：文档生命周期 / 跨模块一致性 / 遗留风险 / 简历口径。
> 方法：只读分析 + 本机实测复核（pytest 实跑、results.jsonl 复算、git 历史比对）。未改任何代码。

## 0. 结论：**COMPLETE-WITH-NOTES**

核心目标（四命令链路 + 可信对比数据）已完成且**经独立复核属实**：

- 全量套件本机实测 `uv run pytest tests/record2gherkin -q` → **292 passed in 24.69s**（无 skip / 无 fail）。
- exp001 六十个格子的存活率、30 次执行、4,109,386 token，从 `results.jsonl` 逐行复算，与 `experiment-report.md` **分毫不差**。
- 五模块四件套齐备，STATUS 决策日志与 git 历史逐条可对上。

**Notes（不阻塞完成判定，但演示前须知）**：① README/STATUS 的 distiller 测试数写 67，实际 68（详见 §2）；② 存在一个顺序依赖的测试夹具隔离问题（§4 风险 R4）；③ `--polish`/`--llm` 真机未验收（§4 风险 R2）；④ 新克隆环境 12 例 skip（§4 风险 R5）。以上均为话术可覆盖、一行文档可修的级别，无"一问就穿"的硬伤。

---

## 1. 文档生命周期完整性审计

### 1.1 五模块四件套（全部齐备，且均有实质内容）

| 模块 | plan.md | spec.md | review.md | test-report.md | 行数（p/s/r/t） |
|---|---|---|---|---|---|
| recorder | ✅ | ✅ | ✅ | ✅ | 64 / 265 / 76 / 211 |
| distiller | ✅ | ✅ | ✅ | ✅ | 77 / 258 / 79 / 150 |
| evaluation | ✅ | ✅ | ✅ | ✅（另有 experiment-report.md） | 88 / 276 / 88 / 131 |
| attributor | ✅ | ✅ | ✅ | ✅ | 113 / 395 / 69 / 154 |
| cli | ✅ | ✅ | ✅ | ✅ | 89 / 330 / 86 / 202 |

旁证：各 test-report 均含"已知问题清单"（recorder 12 条 / cli 11 条 / distiller 4 条 / attributor 23 条 / evaluation 18 条）——记录了 spec 未覆盖边界的确定性取舍，说明 review→fix 闭环真实发生过，不是事后补的空文档。

### 1.2 STATUS.md 决策日志 vs git 历史（逐条核验，一致）

| 决策 | 对应提交 | 核验 |
|---|---|---|
| D0 文档规范/规约/密钥脱敏，基线 `8716f7f` | `8716f7f chore(dev-docs)` | ✅ |
| D1 spec 修订收敛 + 快照归因缺陷修复 | `0ad2075 fix(recorder)`（回归+集成冒烟 9/9） | ✅ |
| D2 P0-1 submit 去重 / P0-2 占位符落 CLI / pilot 复盘三修复 | `fe11083` / `7be9941` / `7ce6c95`（`a186f4f` merge 157 passed） | ✅ |
| D3 exp001 stage full/baseline 双阶段口径 | 无需提交（实验数据 gitignored，报告 `ef25a9c` 固化） | ✅ |
| 收尾 CLI 真实链路验收 + 报告/README 固化 | `ef25a9c docs(evaluation)` | ✅ |

测试基线演进 88→156→157→238→292 与各 merge commit 消息完全吻合。**唯一出入**：STATUS/README 写 distiller 67 例，实际 68（+1 来自 `7ce6c95` pilot-infra 对 `test_templates.py` 的增补，晚于看板计数）。

### 1.3 当前未提交状态

`dev_docs/STATUS.md` 有一处未提交修改（看板更新为"全项目开发完成、终审进行中"），属正常收尾动作；建议终审后与 `final-analysis.md` 一并提交（本次终审不执行 git 操作）。

---

## 2. 跨模块一致性抽查（全部实跑复核）

| 声称（README/STATUS） | 实测 | 结论 |
|---|---|---|
| 全局 292 passed | `pytest tests/record2gherkin -q` → 292 passed, 24.69s | ✅ |
| recorder 21 / evaluation 68 / attributor 81 / cli 54 | --collect-only 逐目录：21 / 68 / 81 / 54 | ✅ |
| **distiller 67** | **collect 68** | ❌ 差 1，建议 README 与 STATUS 分解式改为 68（21+68+68+81+54=292） |
| attributor "13 签名 + 五档分类" | `evidence.py:34-37`：S1-S13；`CATEGORIES = (product_bug, test_rot, environment, agent_limit, inconclusive)` | ✅ |
| 实验主指标 1.00 vs 0.667；M3 6/6 vs 0/6；M4 5/6 | results.jsonl 60 行复算：generated M0-M3=6/6、M4=5/6；baseline M3=0/6、其余 6/6；manifest.metrics 同口径 | ✅ |
| 30 次执行 / 4,109,386 token | results.jsonl generated 侧 30 行，token 求和 = 4,109,386 | ✅（逐字一致） |
| README 四命令及 flags（--manual/--test-data/--polish/--dry-run/--key-file/--llm） | cli test-report §2 全部有用例对应 | ✅ |
| 292 例在新克隆环境 | `test_pipeline_d1.py` 12 例依赖 gitignored 录制产物 → 会变 280 passed + 12 skipped | ⚠️ 口径须注明"本机"（见 R5） |

抽样实跑（任务要求抽 2 个模块）：`recorder`（21 passed）与 `cli`（54 passed）各自独立全绿；又加跑全量确认 292。

---

## 3. 对照 PLAN.md §6 的最终评估

**总体判定：简历表述撑得起，但需按下面三条收敛口径。**

1. **主指标话术必须以 M3 为主打，不能笼统说"三项变异全赢"**。PLAN §6 简历行原稿"（字段调换/布局/动态 id）下存活率 X% vs 基线 Y%"会给面试官留下三项全赢的预期；实际 M1/M2 两种方法都 6/6（基线按属性定位天然免疫字段调换/布局微调），1.00 vs 0.667 的差距**全部来自 M3 标识符随机化**。`experiment-report.md` §4 的已填数表述（"标识符随机化变异下 6/6 vs 0/6（主指标 1.00 vs 0.667）"）是准确口径，简历应采用它，弃用 PLAN §6 原稿措辞。被追问时主动并列 M1/M2 双免疫——这反而是"非稻草人基线"的加分项。
2. **样本量限定词要主动给**：6 流程 × 5 变异 × 2 方法、单 demo 应用、单模型（deepseek-v4-pro）；M4 的 5/6 是 6 格样本，不能给概率点估计。报告 §3 已如实写明，面试时别等追问。
3. **"零依赖/grounding/规则优先"三个核心声称均有实证，但各有一条边界要备好**：
   - "蒸馏环节对 LLM 零依赖" ✅（exp001 generated 侧纯模板；事实回查失败回退有专层测试）。
   - "断言来自录制时真实 DOM" ✅，但 M4/F5 表明断言绑定录制时原文案——话术是把同一事实翻转成"测试的正确行为"（文案改了断言就该红，操作步骤仍全部语义映射成功），实验报告 §3-2 已备好。
   - "规则优先失败归因" ✅（13 签名/五档/证据引用回查/诚实降级，真实失败 F5/M4 验证）；但**不能说 LLM 归因生产可用**——`--llm` 真机未验收（见 R2）。
4. PLAN §0 "维护成本≈0" 收敛为"标识符腐化场景（selector 腐烂的真实痛点）维护成本≈0；文案改写有概率性边界（5/6）"。PLAN §3.4 成功标准"首轮通过率 ≥70%"实际 100%（6/6），可如实说超目标达成。
5. "Hercules 贡献边界"（PLAN §2 分层）与预案无冲突，照答即可。

---

## 4. 演示规避话术清单（按翻车概率排序）

| # | 风险 | 出处 | 演示规避 / 话术 |
|---|---|---|---|
| R1 | **record 跨文档导航即中止**（exit 2 不落盘）：注入 JS 随文档销毁，MPA 站点一跳转录制就断 | recorder 已知#9 / cli 已知#1 | **绝不在演示中录多页站点**。用本地 MiniShop 单页 demo 或 file:// fixture。被问多页："v1 明确 scope out，CLI 检测后干净退出不产脏数据；v2 方案是导航重注入+多段合并，写在已知边界里。" |
| R2 | **`--polish` / `--llm` 真机未验收**：离线只测了注入 fake 与降级路径 | cli 已知#6/#7 | 演示不开这两个 flag。卖点本来就是"骨架零模型、analyze 纯规则逐字节确定"；被问"LLM 层能用吗"："润色与 LLM 归因有完整的降级与回查测试，真实 LLM 归因验收只做了规则层（F5/M4），LLM 层标为待验收。" |
| R3 | **run 真实执行要 key、约 60s+、约 7.3 万 token/简单流程** | STATUS 收尾、exp001 | 现场跑真执行风险高。方案：`--dry-run` 展示编排+脱敏（秒级），随后展示 exp001 已固化产物（JUnit/proofs/归因报告）；已验收的 F1 真实链路（62s passed）可录屏兜底。 |
| R4 | **测试合跑顺序陷阱**：`pytest tests/record2gherkin/recorder tests/record2gherkin/cli`（recorder 在前）会挂 cli 的 6 条 record 用例——recorder conftest 的 session 级 `browser` fixture 全程持有 `sync_playwright()`，与 `cli.py:160` 的第二个 sync 实例冲突（本次终审实测复现：该顺序 6 failed / 75 例反序全过） | 本次终审新发现 | 演示跑测试只用一条命令：`uv run pytest tests/record2gherkin -q`（默认字母序 cli 在前，292 passed）。万一被问到："这是测试夹具的 session 级资源隔离问题，不是产品缺陷；官方入口全量跑是绿的。"（修复属一行 fixture scope 调整，终审不改代码。） |
| R5 | **新克隆环境 ≠ 292**：`test_pipeline_d1` 12 例依赖 gitignored 录制产物 → 280 passed + 12 skipped | evaluation 已知#16 | 口径固定为"本机 292 passed"。被问："那 12 例是吃本地录制产物的管线回归，仓库内附重建命令；刻意不把大产物提交进 git。" |
| R6 | **录制节奏**：脚本驱动交互时动作间须 ≥450ms 停顿，否则快照压缩进最后一个事件 | evaluation 已知#6 | 现场用真人手速录制天然满足；若用脚本驱动（cli test-report §5 的 stop_when 接缝），保持脚本默认节奏即可。 |
| R7 | **macOS 系统代理劫持 127.0.0.1**（urllib 502） | evaluation 已知#11 | sweep/测试代码已显式禁代理；演示机若行为异常，关系统代理或改用 file:// fixture。 |

---

## 5. 建议的收尾小修（一行级，终审未执行）

1. `record2gherkin/README.md` 模块表：distiller 67 → **68**（使 21+68+68+81+54=292 自洽）。
2. `dev_docs/STATUS.md` 基线分解式同步改 68；连同看板更新与本报告一起提交。
3. （可选）README 测试数旁注明"本机基线；新克隆 280 passed + 12 skipped"。

除上述外，无需要改动的事项；不建议在演示前做任何重构。
