# 失败归因器 测试汇报（test-report）

> 模块：`record2gherkin/attributor/`　测试：`tests/record2gherkin/attributor/`
> 依据：`dev_docs/attributor/spec.md`（唯一权威，含 review 修订后的 §3.1 数据流注记 / §4.2 措辞 / S3 复合签名）
> 环境：纯离线（合成产物目录 + FakeAnalyzer；无网络、无 LLM key、无浏览器；PNG 全部 0 字节）
> 日期：2026-09-20

## 1. 测试命令与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/record2gherkin/attributor -q` | **`81 passed in 0.25s`**（0 failed / 0 skipped / 0 xfailed / 0 warning） |
| `uv run pytest tests/record2gherkin/attributor -q --collect-only` | `81 tests collected` |
| `uv run isort record2gherkin/attributor tests/record2gherkin/attributor` | 多处 import 顺序修正；复跑 `--check-only` 通过 |
| `uv run black -l 200 record2gherkin/attributor tests/record2gherkin/attributor` | 3 files reformatted；复跑 0 changed |
| `uv run black -l 200 --check record2gherkin/attributor tests/record2gherkin/attributor` | `All done! 13 files would be left unchanged` |
| `uv run black --target-version py311 -l 200 --check tests/record2gherkin/attributor`（对齐 `make lint` 口径） | `7 files would be left unchanged` |

测试计数：`test_evidence.py` 13 + `test_rules.py` 27 + `test_llm_layer.py` 29 + `test_report.py` 5 + `test_api_e2e.py` 7 = 81。
代码规模：`evidence.py` 500 行 / `rules.py` 474 行 / `llm_layer.py` 614 行 / `report.py` 155 行 / `api.py` 127 行 / `__init__.py` 30 行；测试 1554 行（含 conftest 303 + 静态 fixtures）。

未触碰：`testzeus_hercules/`、`record2gherkin/{recorder,distiller,evaluation}/`、`tests/record2gherkin/{recorder,distiller,evaluation}/`、`dev_runs/`；无 git 操作。

## 2. spec §7 用例清单逐条覆盖（42/42）

### A 组 证据加载（用例 1-11 → `test_evidence.py`）

| # | spec 用例 | 实现测试 | 关键断言 |
|---|---|---|---|
| 1 | 全套产物加载 | `test_load_synthetic_run_dir` | 静态 fixture `fixtures/sample_run/`（相对路径 props → 走 run_dir glob）：scenario/classname/failure/final_response/terminate/properties/missing==[]/thoughts 展平序 0..6/截图序/feature 文本 |
| 2 | 缺 thoughts 容错 | `test_missing_thoughts_tolerated` | `missing` 含 `thoughts`；`apply_rules` 仍出 agent_limit |
| 3 | 缺截图目录容错 | `test_missing_screenshot_dir_tolerated` | `missing` 含 `screenshots_dir`、清单为空 |
| 4 | content 双形态 | `test_thoughts_content_json_and_string` | dict → `json.dumps(ensure_ascii=False)` 且 `content_json` 非 None；多行字符串原文保留；None → `""` |
| 5 | 截图名解析 | `test_screenshot_parse_tool_phase_ts` | `click_using_selector_end_1695000000123456789.png` → tool/phase/ts 精确 |
| 6 | 非标准截图名 | `test_screenshot_nonstandard_name` | `latest_screenshot.png` → phase=other、ts=None、排序垫底 |
| 7 | 末状态对 | `test_final_state_pair` | start,end,end → 末 end + 最近前驱 start 标记 `is_final_state` |
| 8 | 字面锚 | `test_screenshot_path_mention_anchor` | 仅提及轮建锚 `{0: [...]}`，未提及轮不造锚 |
| 9 | failure/final_response 提取 | `test_junit_failure_and_final_response_extraction` | failure_message == assert_summary；final_response 取自 `Final Response: ` 行 |
| 10 | feature 载入与缺失 | `test_feature_file_property_loaded_and_missing` | 存在 → 全文；不存在 → None + `missing` 含 `feature_file` |
| 11 | 路径失效 glob 找回 | `test_unresolvable_path_suffix_glob` | 相对/失效路径按 basename 在 run_dir 深处找回 |

附加 2 条：`test_junit_missing_raises`（唯一加载期抛错 AttributionError：无 XML / XML 损坏）、`test_sample_run_dir_is_self_contained`（静态 fixture 自包含性 + PNG 0 字节）。

### B 组 规则签名（用例 12-27 → `test_rules.py`）

| # | 签名 | 实现测试 | 断言（category / confidence / 证据位置） |
|---|---|---|---|
| 12 | S1 planner_max_rounds | `test_sig_planner_max_rounds` | agent_limit 0.95 / `junit_failure` |
| 13 | S2 planner_timeout | `test_sig_planner_timeout` | FAIL 与 SYSOUT 两形态均 agent_limit 0.90（`junit_failure` / `sysout`） |
| 14 | S3 分支 c | `test_sig_nav_max_rounds_plain` | agent_limit 0.90 / 仅主命中 `junit_failure` |
| 15 | S3 分支 a | `test_sig_nav_max_rounds_not_found_secondary` | test_rot 0.75 / `thoughts:1` / alt 非空 |
| 16 | S3 分支 b | `test_sig_nav_max_rounds_network_secondary` | environment 0.75 / `thoughts:1` |
| 17 | S4 context limit | `test_sig_context_limit` | agent_limit 0.85 / `thoughts:0` |
| 18 | S5 network_dns | `test_sig_network_dns` | environment 0.90 / `thoughts:0` |
| 19 | S6 browser_disconnected | `test_sig_browser_disconnected` | environment 0.80 / `junit_failure` |
| 20 | S7 tool_error_network | `test_sig_tool_error_network` | environment 0.75 / `thoughts:0` / alt 非空 |
| 21 | S8 element_not_found | `test_sig_element_not_found` | test_rot 0.70 / alt 非空 / feature 文本含 `"Go"` → 追加 `feature:5`；不含则无该引用 |
| 22 | S9 http_404 | `test_sig_http_404` | test_rot 0.65 / `thoughts:0` / alt 非空 |
| 23 | S10 路由 + 种子 | `test_sig_assert_mismatch_routes_llm` | route=needs_llm / signature=assertion_mismatch / FakeAnalyzer 收到的包内含 FAIL 原文（`junit_failure`） |
| 24 | S12 llm_call_error | `test_sig_llm_call_error` | agent_limit 0.80 / `junit_failure` |
| 25 | S11 路由 | `test_sig_helper_uncertain_routes_llm` | needs_llm / `sysout` |
| 26 | 表序优先 | `test_precedence_limit_before_mismatch` | FAIL 同时含 S1 与 EXPECTED/ACTUAL → S1 生效（agent_limit 0.95） |
| 27 | 零模型不变量 | `test_rules_zero_llm_imports` | 三文件无 litellm/requests/httpx/aiohttp/urllib/openai import，且不 import `llm_layer` |

附加 10 项（含 2 个额外参数化实例）：S3 分支顺序（not-found 优先于网络）、S5 首末轮双引用、S7 无网络词 → S13 路由、S8 优先于 S13、S3 主命中在 FAIL 而信号在 THOUGHTS（§3.1 数据流注记）、fallback→needs_llm、§3.3 模板全覆盖 5 类、excerpt 截断至 500、`test_package_import_is_offline`（子进程内 `import record2gherkin.attributor` 后 sys.modules 无 litellm/langchain/openai/httpx/urllib3，且未 import `litellm_helper`——DefaultAnalyzer 懒解析，spec §4.1 离线约束）。

### C 组 LLM 层与回查降级（用例 28-36 → `test_llm_layer.py`）

| # | spec 用例 | 实现测试 | 断言 |
|---|---|---|---|
| 28 | 合法输出采纳 | `test_llm_valid_output_adopted` | decided_by=llm / category/confidence/summary/suggestion 生效 / evidence == 被引项（reference 与 excerpt 一致）/ warnings 空 |
| 29 | 未知 ref 降级 | `test_llm_unknown_ref_degrades` | inconclusive / `evidence_backcheck_failed:E99` / degraded / 规则层种子证据保留 / 第 2 次请求带 `violation: unknown_ref E99` |
| 30 | 仅截图引用降级 | `test_llm_screenshot_only_citation_degrades` | product_bug 仅引截图 → inconclusive（`insufficient_evidence` 路径）/ warning 含该 ref |
| 31 | 非法→合法重试 | `test_llm_invalid_then_valid_retry` | 第 2 次被采纳；首请求 `retry_feedback is None`，重试请求含 `violation: invalid_category` |
| 32 | 两次非法降级 | `test_llm_invalid_twice_degrades` | `llm_output_invalid` / confidence 0.30 |
| 33 | 异常×2 降级 | `test_llm_exception_degrades` | `llm_error:TimeoutError` / 2 次调用 |
| 34 | 无 analyzer | `test_no_analyzer_rules_only` | inconclusive + `llm_not_configured` + 0.30 + 种子证据保留 + suggestion=inconclusive 模板 |
| 35 | inconclusive 合法 | `test_llm_inconclusive_allowed` | decided_by=llm / 空 refs 通过 / suggestion 空则回填模板 |
| 36 | 回查语义 | `test_backcheck_locator_semantics` + `test_resolve_reference_boundaries[14 参数]` | thoughts 越界/feature 行越界/property 键缺失/未知语法 → `unresolvable`；excerpt 非子串 → `excerpt_mismatch`；截图不存在 → `unresolvable` |

附加 7 项（`resolve_reference_boundaries` 参数化 14 个实例，故本文件收集 29 项）：部分引用被丢仍采纳（`evidence_ref_dropped` warning，1 次调用不重试）、配置异常（`AttributionError`）立即 `llm_not_configured` 不重试、空白归一化通过/case 差异不通过、证据包 30 条上限（种子保留、末状态对保留、编号连续 E1..E30）、thoughts excerpt 纯截断（= `content_text[:400]`，无装饰文本），DefaultAnalyzer 注入 stub model（懒构造、role=`attributor_analyze`、temperature 0、system+user 消息、模型构造失败 → `AttributionError`）。

### D 组 报告与端到端（用例 37-42 → `test_report.py` / `test_api_e2e.py`）

| # | spec 用例 | 实现测试 | 断言 |
|---|---|---|---|
| 37 | Markdown 必备节 | `test_markdown_required_sections` | 标题、结论（含 S# 与置信度）、证据表（行数 == evidence 数、引用为 §6.3 定位符）、建议、conf<0.8 时警告节含替代假设、附录 |
| 38 | JSON schema | `test_json_schema_fields` | 键集 12 项、schema_version=1、枚举、confidence∈[0.05,0.95]、非 inconclusive 必有 ≥1 证据、rule 且 conf<0.8 必有 alt、llm 决策 alt 为空且有 llm_summary、逐项 round-trip |
| 39 | P0-1 submit 双步骤 | `test_end_to_end_submit_double_step_rot` | test_rot（S3 分支 a）/ 0.75 / 证据 = `junit_failure` + `thoughts:3` / 报告含建议与截图清单 |
| 40 | 静默点错 + fake LLM | `test_end_to_end_assert_mismatch_fake_llm` | S10 路由 → product_bug（decided_by=llm）/ 引用为文本类 / 报告含 `## LLM 分析` / notes.alt 为空 |
| 41 | 无 LLM 确定性 | `test_deterministic_without_llm` | 静态 fixture 跑两遍 `to_json()` / `to_markdown()` 逐字节相等 |
| 42 | 过滤通过用例 | `test_attribute_run_filters_passed` | 1 过 1 挂 → 仅 1 条结果 |

附加 6 条：`## LLM 分析` 仅 LLM 决策出现、报告渲染幂等、附录/证据表保留定位符与 failure 原文、`not_a_failure` 短路（inconclusive + warning，不进规则层）、`junit_path` 显式覆盖、最新 XML 自动发现。

「绝不瞎编」不变量在 e2e 用例中额外机械校验（`assert_citations_resolvable`）：每条结论证据都能用 §6.3 定位符回查到 referent，且 excerpt 归一化后是 referent 的子串（截图项按存在性判定）。

## 3. spec §8 验收标准逐条勾验

1. **全绿 / 离线** — ✅ `81 passed`；测试全部使用 `tmp_path` 合成产物目录（手写最小 JUnit 树、两种 content 形态的 thoughts、0 字节 PNG），LLM 一律 `FakeAnalyzer` 注入，未使用网络/LLM key/浏览器。静态 fixtures 的 PNG 实测 0 字节（`test_sample_run_dir_is_self_contained`）。
2. **规则层零模型** — ✅ `test_rules_zero_llm_imports`（源码逐行 grep：三文件无 litellm/requests/httpx/aiohttp/urllib/openai、不 import `llm_layer`）＋ `test_package_import_is_offline`（子进程验证 import 期零 LLM 依赖）。
3. **签名覆盖** — ✅ S1-S13 每条 ≥1 正例（B 组表 12-25）；S3 三分支各 1 例（14/15/16，另加分支顺序用例）。
4. **不瞎编不变量** — ✅ 用例 38 断言"非 inconclusive ⇒ ≥1 条证据"，用例 39-41 追加引用回查断言；降级路径全覆盖：analyzer 缺配 34、异常×2 33、校验失败×2 32、回查存活不足 29/30、定位符语义 36。
5. **确定性** — ✅ 用例 41（同目录两遍 `to_json()` 逐字节相等）；另 `test_report_is_deterministic` 覆盖渲染幂等。
6. **格式化** — ✅ `black -l 200 --check`（含 `--target-version py311`）通过；isort 复跑 clean。
7. **真实失败样本召回率** — ⏸ 不在本 MVP 内（spec 明列遗留项）：接口已就绪（`attribute_run(run_dir)` 直吃 Hercules 产物目录），待评测实验的真实失败 run 产出后接入统计"规则直接定类 / 落 LLM 层 / inconclusive"三分布。

## 4. 产物清单

```
record2gherkin/attributor/{__init__.py, evidence.py, rules.py, llm_layer.py, report.py, api.py}
tests/record2gherkin/attributor/{__init__.py, conftest.py, fixtures/sample_run/**, test_*.py}
tests/record2gherkin/attributor/fixtures/sample_run/{output/junit.xml, gherkin_files/checkout.feature,
    log_files/stake1/run1/agent_inner_thoughts.json, proofs/stake1/run1/screenshots/*.png(0 字节 ×3)}
```

公开 API：`attribute_run(run_dir, junit_path=None, analyzer=None)`、`attribute_case(evidence, analyzer=None)`、`AttributionResult.to_json()/.to_markdown()`、`AttributionError`、`Category`、`DefaultAttributorAnalyzer`。

## 5. 已知问题清单（23 条）

### A. spec 内部不一致（2 条，按"表列为准/最可读"取值）

1. **S3 分支 (b) 的 alt 冲突**：§3.2 表 alt 列写 `—`，但同节又规定"conf<0.8 必填 alt"，而 (b) conf=0.75。取表列 → environment 分支 `alternative_hypotheses=[]`；`test_sig_nav_max_rounds_network_secondary` 只断言 category/confidence/引用，不对 alt 断言。
2. **`rule_signature` 取值口径**：§3.2 说"命中 id"、§6.1 注释写 "S1…S13 id"、§6.2 写"规则签名 S#"。取 §3.2 `id` 列英文名（如 `nav_max_rounds`，review §5-2 建议项），Markdown 同时渲染 `id（S#）`，兼顾审计可读性。

### B. spec 未覆盖边界的确定性选择（14 条）

3. **部分引用被丢仍采纳时的告知**：规格只定义"存活引用不足"降级；本文实现允许"存活引用足够（≥1 文本类）而个别引用被丢"的采纳，并新增 warning `evidence_ref_dropped:<violations>`（`test_llm_partial_drops_still_adopt`）。
4. **证据包 30 条上限的丢弃顺序**：先丢上下文轮（按 index 从旧到新），再丢非末状态截图（时间序从前到后），末状态对与种子永不丢（§4.2-4 只给了优先级，未给方向）。
5. **截图 evidence 的 `[FINAL]` 标记落点**：`excerpt` 保持纯文件名（§4.2-3 "excerpt=文件名"字面），`[FINAL]` 落在 `screenshot_names`（§4.1 明示）与报告附录；由此锚定轮次与截图的对应关系不进入证据包（§2.4 声明锚仅用于展示与附录）。
6. **截图扫描模式**：专用截图目录用 `*.png` 平铺、base folder 兜底用 `**/*.png`（§2.4 只对兜底写了 `**`）。
7. **`missing` 只登记 spec 点名的三类**（`thoughts`/`screenshots_dir`/`feature_file`）；`Network Logs`/`Proofs Base Folder`/`Output File` 解析失败静默置 `None`（路径透传，不产生告警噪声）。
8. **多 `<system-out>` 节点连接**：junit_helper 每个输出行组写一个节点，加载时按 `"\n"` 连接后做 `Final Response: ` 行提取。
9. **`<failure>` 无 message 属性** → `failure_message=""` 且仍计失败（`is_failure ⟺ failure_message is not None`），以此保持 §1.2 字段集不增字段。
10. **S8 的 feature 行引用取"首个"**：命中片段内引号名按出现序，取第一个出现在 feature 文本中的名字（§3.2 为单数措辞）。
11. **S3 次级分支 (a) 不追加 feature 行引用**（§3.2 只要求"引用该命中轮"），仅 S8 自身做该 best-effort 追加。
12. **THOUGHTS 命中统一给"首个+末个匹配轮"证据**（§3.1 的总规则）；S1/S4 等列写的"首个命中位置/命中轮"以首命中为主、末命中为附。
13. **`not_a_failure` 路径的三项未规定取值**：`decided_by="rule"`、`confidence=0.30`、`suggestion`=inconclusive 模板（§2.1 只说 inconclusive + warning）。
14. **降级 warning 由最后一次尝试的失败类型决定**：如首次回查失败、二次 schema 失败 → `llm_output_invalid`（规格未规定跨尝试归并规则）。
15. **confidence 归一**：clamp 到 [0.05,0.95] 后四舍五入 2 位小数（保证 JSON 稳定与可读）。
16. **analyzer 抛 `AttributionError`（配置不可用）** → 直接 `llm_not_configured`，不消耗 2 次尝试预算（§4.5 表述为"api 层捕获后按不可用处理"）。

### C. 与 spec 的有意偏离（5 条，均在代码 docstring 记录）

17. **`LlmAnalysisRequest` 增加 `retry_feedback: str | None`**：§4.4-5 要求把违反项"追加为 user 消息"，而协议（§4.1）入参是请求对象，需要一个承载字段。
18. **`LlmAnalyzer.analyze` 返回类型放宽为 `LlmAnalysisOutput | str | Mapping`**：§4.4-1 要求本层做围栏剥离与 JSON 提取，故原始文本必须能到达本层；`DefaultAttributorAnalyzer` 返回原始文本，测试 fake 可返回对象以构造非法枚举。
19. **"总尝试 ≤2" 落在层内实现**（§4.1 原文把它写在 DefaultAnalyzer 名下）：这样重试与回执对任意 analyzer 实现可观测（用例 31/32 依赖）；DefaultAnalyzer 单次调用单次 60s 超时。
20. **`AttributionResult` 增加 3 个 Markdown 专用字段**：`failure_message` / `screenshot_names` / `llm_evidence_refs`，只服务 §6.2 附录与 LLM 节，**不进入** §6.1 JSON（`to_dict()` 键集严格等于 §6.1）。
21. **类型与异常的定义位置**：`Category`/`Route`/`EvidenceEntry`/`AttributionError` 定义在 `evidence.py`（全模块唯一定义处），`api.py` 再导出——避免 api 与 llm_layer 的循环导入。

### D. 遗留与契约说明（2 条）

22. **真实失败样本回归不在本 MVP**（spec §8-7）：签名表"准"由源码固定文本保证，签名覆盖与召回率的真实样本统计待评测实验接入（预期候选：静默点错事后形态、helper 图片比对 contradict 细分）。
23. **同步契约限制**：`attribute_case`/`attribute_run` 内部用 `asyncio.run` 驱动 analyzer，不能在已运行的事件循环内直接调用（spec §5 的对外同步约定）；异步调用方应直接用 `llm_layer.resolve_needs_llm`。
