# 评测框架与 UI 变异实验 — 测试报告（实现阶段，离线可验证范围）

> 模块：`record2gherkin/evaluation/` + `tests/record2gherkin/evaluation/`
> spec：`dev_docs/evaluation/spec.md`（唯一权威）；plan：`plan.md`；审查：`review.md`
> 本报告覆盖 spec 里程碑 D1 + 「实验机器就绪」：demo 应用/变异引擎、demo 服务、录制、蒸馏、基线、
> 子进程执行器（dry 路径）、JUnit 解析、指标、实验矩阵与预算护栏。**未调用任何真实 LLM**；
> D2 pilot 与 D3 全量 sweep 由总编排执行（命令见 §5）。

## 1. 测试命令与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/record2gherkin/evaluation -q` | **68 passed**（8.3s，离线：无 key、无外网，B 组仅本机回环） |
| `uv run pytest tests/record2gherkin -q` | **156 passed**（21.9s，全局无回归：recorder 21 + distiller 67 + evaluation 68） |
| `uv run isort record2gherkin/evaluation tests/record2gherkin/evaluation && uv run black --target-version py311 -l 200 record2gherkin/evaluation tests/record2gherkin/evaluation` | FMT-CLEAN（复跑 `--check` 亦通过） |
| 脱敏扫描：`KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs/ record2gherkin/ tests/ dev_docs/` | **零命中**；key 的 8 字符前/后缀在实验产物与代码中同样零命中 |

按文件分布：

| 文件 | 通过数 | 覆盖 |
|---|---|---|
| `test_demo_app.py` | 16 | spec §9 A1-A12（渲染/变异，纯字符串）+ 注册表与 selector 优先级补充 |
| `test_demo_server.py` | 7 | B13-B15 + 端口占用硬失败 + 状态校验补充 |
| `test_demo_browser.py` | 6 | 真浏览器（回环）：M0/M3 下搜索/筛选/清单/下单行为、`#/order` 直达 |
| `test_runner_parse.py` | 18 | C16-C21、E26-E29 + 命令模板/行结构/JUnit 定位补充 |
| `test_metrics.py` | 5 | D22-D25 + results.jsonl 容错读取 |
| `test_matrix.py` | 4 | F30-F31 + run_id 格式 + 阶段覆盖 |
| `test_pipeline_d1.py` | 12 | D1 录制产物 schema/断言表 + 蒸馏 1 Feature/1 Scenario 可解析（产物缺失时跳过并给出重建命令） |

## 2. spec §10 验收标准逐条勾验

| # | 验收标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | `pytest tests/record2gherkin/evaluation` 全绿、离线可跑 | ✅ | 68 passed；无 LLM/外网依赖；B/浏览器组仅 127.0.0.1 回环 |
| 2 | demo 服务一条命令启动，`/healthz` 可用；同 URL 同 `(m, seed)` 字节级一致 | ✅ | `uv run python -m record2gherkin.evaluation.demo_server`（默认 `127.0.0.1:8461`，端口占用硬报错）；B13/B14 断言字节一致 + `Cache-Control: no-store`；CLI 实测 `curl /healthz`、`POST /__control`、M3 文档含 34 处 `m3-` |
| 3 | D1 录制：6 条流程各恰 1 Feature/1 Scenario（`split_feature_file` 通过），无 `{{TEST_DATA:`/`<masked>` | ✅ | `dev_runs/experiments/features/F1..F6.feature`；`distill_batch` 与 `test_pipeline_d1` 双重校验（见 §3） |
| 4 | D2 pilot：≥1 格 M0 通过且 cost 解析为非 None；模型串结论回写 §4.3 | ⏳ 待总编排 | 机制就绪：env 注入（E27）、唯一 cost 取数键与 suite 兜底（C18/C21）、**JUnit 定位按上游实测路径 `<project-base>/output/run_<TS>/<feature>_result.xml`（两级查找，见 §6.17）**、timeout 杀进程组（E29）、`--dry-run`（E27b）。**pilot 前不得开始 sweep** |
| 5 | D3 sweep：Hercules ≤40、`results.jsonl` 每格恰一行、manifest 完整 | ⏳ 待总编排（护栏已离线验证） | 矩阵 60 格、Hercules 30+4+5=39 ≤40 单测断言（F30）；预算在 sweep 内二次拦截；`--stage baseline` 已真实跑通 30 格（§4） |
| 6 | 脱敏：所有实验文本产物以 key 实值 grep 零命中 | ✅ | `dev_runs/`、`record2gherkin/`、`tests/`、`dev_docs/` 全量零命中（命令见 §1） |
| 7 | `test-report.md` 固化：首轮通过率 / Surv 全表 / 成本三数 / 失败清单 / checkbox 双事件观察点 | ⏳ 最终数字待 sweep | 指标函数（`metrics.summarize`）与 `manifest.metrics` 已就绪，只差真实执行数据；本报告先固化 D1 与离线证据 |

## 3. D1 六条流程：录制与蒸馏产物摘要

录制：`uv run python -m record2gherkin.evaluation.record_flows`（起 demo 服务 → 注入 `recorder.js` → 按 spec §1.3 流程表逐条录制 → 落 `dev_runs/experiments/recordings/F*.json`，逐条校验 §1.3 断言文本已被 `assert_texts` 捕获）。
蒸馏：`uv run python -m record2gherkin.evaluation.distill_batch`（纯模板 `polisher=None`，无 LLM）→ `dev_runs/experiments/features/F*.feature`；每条恰 1 Feature/1 Scenario、`split_feature_file` 解析通过、无占位符。

| 流程 | 事件（类型序列） | 蒸馏摘要（3 行内） |
|---|---|---|
| F1 搜索 | 3：navigate, input, click | `in 搜索商品 台灯` → `click 搜索` → `Then 找到 1 件商品` |
| F2 加入清单 | 3：navigate, click, click | 两次 `click 加入清单`（第 2 次带 `(occurrence 3)`）→ `Then 清单（1）` / `Then 清单（2）` |
| F3 下单表单 | 11：navigate, click, navigate, input×3, select, input(checkbox), click, submit | `click 去下单` → `navigate #/order` → 填姓名/邮箱/地址 → `select 次日达` → checkbox 双事件（click + `enter "true"`）→ `click 提交订单` → `Then 下单成功，感谢您的购买！` + `订单号：D20260920-001`（click/submit 双事件被蒸馏器合并为 1 步，断言保留） |
| F4 筛选 | 4：navigate, select, input, click | `select 数码` → `enter 300 in 最大价格` → `click 应用筛选` → `Then 筛选后共 1 件商品` |
| F5 视图往返 | 5：navigate, click, navigate, click, navigate | 去下单 → `navigate #/order` → 返回列表 → `navigate #/` → `Then 共 3 件商品`（另含视图切换带出的其余文本断言，共 16 条 Then） |
| F6 数量下单 | 8：navigate, click, navigate, click×3, submit | `+`×2 → `−`×1 → `Then 数量：2` → `click 提交订单` → `Then 下单成功…` + 订单号 |

checkbox 双事件（spec §12 观察点）在 F3 中完整记录：`click "接受促销邮件" (checkbox)` + `When I enter "true" in the "接受促销邮件" field`；执行侧实际表现留 pilot 记录，本阶段不做规避。
`(occurrence 3)`（F2）与 hash URL（`#/order`、`#/`）同样按 spec 规则原样进入 feature，待 pilot 首验。

## 4. 离线验证：基线 30 格真实跑通（`--stage baseline`）

离线冒烟（无 LLM、无 key）：`uv run python -m record2gherkin.evaluation.sweep --exp-id smoke-baseline --stage baseline`
→ 30 格全部产出结果行（`dev_runs/experiments/smoke-baseline/results.jsonl` 30 行 / 30 个唯一格），manifest 完整（含 `git_rev`、`demo_port`、`budget`、`cells`、`metrics`），22 秒完成。

| Surv(baseline, M) | M0 | M1 | M2 | M3 | M4 | 主指标（M1/M2/M3 均值） |
|---|---|---|---|---|---|---|
| 存活 | 1.00 | 1.00 | 1.00 | **0.00** | 1.00 | 0.667 |

与 plan §5 预判一致：M1/M2 下属性 selector 天然命中（基线存活，对比价值在"不劣于"），M3 全灭（id/class/testid 全变），M4 不受文案影响（基线不用文本定位）。M3 每格失败耗时 ≈2.3s（动作超时 2s），30 格总墙钟 <30s。

同页演示的机制也在同一次运行中被验证：sweep 起 demo 服务子进程 → 每格 `POST /__control` + `GET /healthz` 校验 → 追加 `results.jsonl` → 收尾 `manifest.json`（不含 key）。

## 5. pilot / sweep 执行说明（总编排用）

前置（已就绪，可复现重建）：

```bash
uv run python -m record2gherkin.evaluation.record_flows     # 6 份录制 → dev_runs/experiments/recordings/
uv run python -m record2gherkin.evaluation.distill_batch    # 6 份 feature → dev_runs/experiments/features/
```

正式执行（要求 8461 端口空闲；key 由 `LLM-Key.txt` 读取后仅经 subprocess env 注入）：

```bash
# D2 pilot（单独 exp，不入正式表；≤4 次 Hercules，up to $0.52 上限）
uv run python -m record2gherkin.evaluation.sweep --exp-id exp001-pilot --stage pilot

# D3 全量（正式表：同一 exp 目录累积两方法的 60 行；Hercules 30 次，up to $3.90 上限）
uv run python -m record2gherkin.evaluation.sweep --exp-id exp001 --stage full
uv run python -m record2gherkin.evaluation.sweep --exp-id exp001 --stage baseline

# 只构造不执行（拿到每格的 seed/run_id/命令与环境，人工核对后再真跑）
uv run python -m record2gherkin.evaluation.sweep --exp-id exp001 --stage full --dry-run
```

- 预算护栏：pilot 4 + full 30 + 基础设施重试缓冲 5 = **39 ≤ 40**（红线）；`assert_budget` 在计划阶段拦截，`Sweep.run` 在每个 Hercules run 前再拦一次。基础设施重试仅用于 `timeout`/`no_junit`（用例失败绝不重跑）。
- 失败数据：`timeout`/`no_junit`/断言失败一律进分母按 0 计，并在 `manifest.metrics.failures` 列 run_id + 摘要。
- pilot 需回写 spec：§4.3 的 litellm 模型串/base_url 结论、§12 的 hash URL / checkbox 双事件 / `(occurrence N)` / M3 页面行为四项实测结果。
- FirstPass 口径：以正式 sweep（`exp001`）的 generated×M0 首跑为准；pilot 用独立 exp 目录、feature 文件零修改（评审建议 1 已满足）。
- 最终指标：`metrics.summarize(load_rows("dev_runs/experiments/exp001/results.jsonl"))` 或直接读该目录 `manifest.json` 的 `metrics` 字段（首轮通过率 / Surv 两方法 × 5 变异 / 成本三数 / 失败清单）。

## 6. 已知问题与实现口径（spec 未覆盖处的选择）

**spec 内部冲突/字面歧义（已按最合理确定性行为处理）**

1. **`RunResult.run_dir` 语义**：spec §4.1 注释写"project_root 的绝对路径"，§8 要求 `<exp_id>/runs/<run_id>/` 内含 opt/ 与 `stdout.log`。取 §8：`run_dir = <exp_id>/runs/<run_id>`，`project_root = run_dir/opt`（子进程 `--project-base`），`stdout.log` 落在 `run_dir`。
2. **seed 与"同格共享页面状态"**：§2.3 公式含 `method`，故 generated 与 baseline 在同一 `(flow, mutation)` 上 seed 不同；§7 "两种方法同格共享同一 (mutation, seed)" 按"同一变异语义 + 服务端状态在方法间保持（不重启服务）"理解；可比性落在变异层而非字节级同页。
3. **token 无 suite 级兜底**：§6.3 只给 cost 规定 suite 兜底（`total_execution_cost`），token 明确"无任何键时为 None"；实现按字面（`total_token_used` 仅作参考不回落），成本与 token 的缺失均计 `None` 并计入缺数计数。
4. **畸形 XML 形态**：§4.1 声明返回 dict、§4.2 要求"抛解析异常视为 no_junit"。取 §4.2：`parse_junit_xml` 抛 `JUnitParseError`（子类 `ValueError`），`run_feature` 捕获后置 `status="no_junit"` 并保留脱敏异常文本；`<error>` 与 `<failure>` 同等视为失败（防御）。
5. **F3 事件数 11 超出 §1.3 "每条 4-8 个事件"**：hash navigate×2 + checkbox `click`+`input` + 提交按钮 `click`+`submit` 双事件导致。属录制器忠实记录（spec §12 已声明不规避），feature 侧 `submit` 被蒸馏器合并、断言保留。

**实现侧已知边界**

6. **录制节奏（`STEP_WAIT_MS=450`）**：recorder 的新增文本快照有 300ms 延迟且归属"触发时刻最新事件"，机器回放必须在动作间停顿——否则多个动作被压缩进最后一个事件，超出其 8 条 `assert_texts` 上限（实测 F3 的"下单成功"会被挤掉）。真人录制天然满足该节奏。
7. **视图切换最多带出 8 条新文本**：F3/F5/F6 的 Then 因此偏多（19-21 步）；F5 依赖 `list_status`（"共 3 件商品"）在 DOM 前部（导航下方计数行），这是为让 spec §1.3 指定断言落在 8 条窗口内的设计选择。
8. **`cart_count` 渲染在两种视图共享的头部**（spec §1.2 视图列标注 list）：头部是全站组件，计数在切视图后仍可见；F2 的 `清单（2）` 断言来自加入清单点击，不受影响。
9. **M2 `m2-card` 数 = 9**（list 的搜索组/筛选组 + order 的 7 个字段组）："每个字段分组"取"表单字段组"最简解释；`m2-flex` 加在表单/筛选容器（2 处）。
10. **M4 会给被改写元素补 `aria-label`**（spec 要求"改写同时作用于 innerText 与 aria-label"）：M0/M3 下这些按钮无 aria-label，M4 下两者一致。
11. **本机 macOS 系统代理会劫持 `127.0.0.1` 请求**（urllib 经 `ProxyHandler` 拿到 502）：sweep 的 `/__control`、`/healthz` 调用与测试统一显式禁用代理；playwright 不受影响。
12. **基线动作超时 2s**：M3 格（全部 selector 失配）每流程 ~2.3s；如需更严格可调 `DEFAULT_ACTION_TIMEOUT_MS`。
13. **超出 spec §0 文件清单的增补**：`record_flows.py`（D1 录制脚本）、`distill_batch.py`（批量蒸馏 + 产物合法性守卫）、`tests/.../test_demo_browser.py`（M3 页面自身行为的浏览器级证据，对应 review 必改 2 / §12 验证点）、`tests/.../test_pipeline_d1.py`（D1 产物回归）。均为离线、只读上游。
14. **执行器只保证 `--dry-run` 与 env/命令构造正确**（真执行属 pilot 暴露范围）：`run_feature(dry_run=True)` 与 `sweep --dry-run` 均不产生进程、不落 `stdout.log`、不写 `results.jsonl`。
15. **商品卡片嵌套修复**：卡片必须包裹名称/价格/按钮（否则搜索/筛选只更新计数文案而不隐藏商品）。已由浏览器用例（M0/M3 各一条）与 A 组断言共同锁定。
16. **`test_pipeline_d1.py` 依赖 `dev_runs/experiments/recordings/`**（gitignore 产物）：产物缺失时 12 条用例整体跳过并给出重建命令，因此新克隆环境 `pytest` 仍为全绿（skip 12 条）。
17. **上游 JUnit 实际路径 ≠ spec §4.2 所写**（离线实测 `get_junit_xml_base_path()`）：上游返回 `<PROJECT_SOURCE_ROOT>/output/run_<TS>/`（时间戳子目录；`TS` 在子进程 import 时求值），`--output-path` 只落到 `JUNIT_XML_BASE_PATH` 配置项、不影响该 getter。实现改为两级定位：先查 `<output-path>/<feature>_result.xml`，再查 `<output-path>/*/<feature>_result.xml`（同 run 目录隔离，取最新 mtime 兜底），并由新单测锁定；若 pilot 发现其它落点，只需扩 `find_junit_xml`。副作用：`--project-base` 仍是唯一有效的 run 隔离手段（与 spec §4.2 的隔离理由一致）。
18. **中间产物路径均落在 run 目录内**（实测）：`TMP_GHERKIN_PATH = <project-base>/gherkin_files`、proofs/log_files 同样以 `PROJECT_SOURCE_ROOT` 为根。`prepare_run_dir` 预建 `input/` 与 `output/`（时间戳子目录由上游创建），因此 run 目录自包含、互不混叠。

## 7. 产物清单（绝对路径）

- 实现：`record2gherkin/evaluation/{__init__,demo_app,demo_server,record_flows,distill_batch,runner,baseline,metrics,sweep}.py`
- 测试：`tests/record2gherkin/evaluation/{__init__,conftest,test_demo_app,test_demo_server,test_demo_browser,test_runner_parse,test_metrics,test_matrix,test_pipeline_d1}.py`
- 实验产物（gitignore）：`dev_runs/experiments/recordings/F1..F6.json`、`dev_runs/experiments/features/F1..F6.feature`、`dev_runs/experiments/smoke-baseline/{results.jsonl,manifest.json}`
