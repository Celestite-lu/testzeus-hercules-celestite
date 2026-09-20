# CLI spec 独立审查（review.md）

> 审查对象：`dev_docs/cli/plan.md`、`dev_docs/cli/spec.md`。
> 基准：`PLAN.md` §3.3/§5、`dev_docs/README.md`、AGENTS.md 二开规约。
> 事实核对面：`record2gherkin/distiller/api.py`、`record2gherkin/evaluation/runner.py`、`record2gherkin/attributor/api.py`（+ evidence/llm_layer/report）、`record2gherkin/recorder/recorder.js`、`dev_runs/integration_smoke.py`、`tests/record2gherkin/` 布局。
> 纪律：只拦会让"四命令串成一条可演示链路"失败的问题；验收=§8 的 32 条用例全绿 + §9 机械验收，因此**用例清单里物理上跑不通的条目按必改计**。

## 结论：**REVISE**（3 条必改，全部是 spec 文本级修正，无方案性返工）

核心方案（薄包装四命令、P0-2 两段式蒸馏、退出码表、密钥经 env-only）与被复用模块的真实签名/契约**逐一对上**，架构无需改动。必改项集中在：B 组两条用例与 recorder 真实语义冲突（§8 验收无法全绿）、run 摘要对 failure_message 脱敏的失实断言（密钥红线）、generate 步骤 4 值类型契约与 distiller 不符。

---

## 必改项

### R1（spec §2.1 接缝签名 + §8 用例 5/6）：record 的可测接缝与"空录制"语义按真实代码修正

**问题（两处，均导致 §8 用例物理上跑不通）**
- (a) 用例 5 要求"`stop_when`（驱动 2 次点击后返回 True）"，但 §2.1 定义的接缝是 `stop_when: Callable[[], bool]`——测试闭包拿不到 page 句柄，无法驱动点击；浏览器由 `record_events` 内部创建，测试无其他途径触达页面。实现者照 spec 写不出该测试，只能自行改接缝（违反"读完不需做设计决策"）。
- (b) 用例 6 期望"stop_when 立即 True / 产物 `events=[]`"。**`events=[]` 不可达**：`recorder.js` 的 `start()` 固定 push 一条初始 navigate 事件（`recorder.js:518` `events.push(createEvent("navigate", null, null))`），playwright 模式下事件数下限为 1，§2.1 "0 事件也是合法产物"的前提与 `empty_recording` warning 的触发条件失实。

**依据**：`record2gherkin/recorder/recorder.js:498-520`（start 返回 true 且必 push navigate）、`dev_runs/integration_smoke.py:14-22`（同款注入流程实测）。

**改法**
- 接缝改为 `stop_when: Callable[[Any], bool]`（轮询循环把 `page` 作为实参传入；生产默认包装器——SIGINT Event 与 `--max-duration`——忽略该参数）。用例 5 的闭包即可 `page.click(...)` 两次后返回 True。
- `empty_recording` 语义改为"仅含初始 navigate（无任何用户事件，`len(events) <= 1`）即 warning"；用例 6 期望改为"产物 events 长度 1（一条 navigate），日志含 `empty_recording`"。§2.1 第 6 点与 §1.2 退出码 0 行的"0 事件"措辞同步改。

### R2（spec §5.3）：failure_message"已被 runner 脱敏"断言失实，CLI 侧补一行兜底

**问题**：runner 只对子进程 stdout 做 `mask_secret`（`runner.py:305`，写入 `stdout.log` 与 TIMEOUT/NO_JUNIT 的 message tail）。`STATUS_FAILED` 路径的 `failure_message` 直接取自 JUnit XML（`runner.py:370` → `parse_junit_xml`），**不经任何脱敏**；spec §5.3"内容已被 runner 脱敏"不成立。该字段是 CLI 日志里唯一未过脱敏的外部文本面，且 §8 没有用例覆盖它（用例 20 只测 dry-run 路径）——按 dev_docs README 核心原则 2（密钥安全高于一切），不能靠"引擎大概率不会回显 key"成立。

**依据**：`record2gherkin/evaluation/runner.py:305` vs `:350-370`（两处 message 来源不同，仅前者过 `mask_secret`）。

**改法**：CLI run 摘要打印前自行兜底一行：`mask_secret(result.failure_message or "", key)`（key 即本次 `read_api_key` 结果；`mask_secret` 为 runner 公开函数，空 key 时原样返回，无副作用）。spec §5.3 括注改为"经 CLI 侧 mask_secret 二次脱敏"。可选加固：§9.4 推广一条断言——假 key 场景下构造含假 key 的 failure_message 假 RunResult，日志零命中。

### R3（spec §3.1 步骤 4）：`--test-data` 值类型契约与 distiller 不符，filled 计数会虚报

**问题**：spec 步骤 4 写"值 ∈ {str,int,float,bool} → 接受，str() 化"，且未规定空白串。distiller 侧 `_clean_test_data_values` **丢弃 bool 与空白值**（`events.py:146-159`：`None`/`bool` 直接跳过，`str()` 化后非空白才收）。按 spec 实现：CLI 步骤 5 判 `provided`、摘要报 `filled=N`，但 distill_file 实际未填充——feature 仍留 `{{TEST_DATA:...}}` 占位符，无任何 missing 警告（因 CLI 侧认为已提供）。P0-2 的"缺值警告"闭环在该输入类上失效，且摘要与产物互相矛盾。

**依据**：`record2gherkin/distiller/events.py:146-159`、`record2gherkin/distiller/templates.py:66-74`（占位符填充走 `parsed.test_data_values.get(key)`，被清洗掉的键等同未提供）。

**改法**：spec 步骤 4 改为与 distiller 契约对齐的一句话："值 ∈ {str,int,float} 接受，`str()` 化；`str()` 化后为空白、或值为 bool/null 的键视为未提供（distiller 契约）"。CLI 侧接受值集与 `_clean_test_data_values` 一致后，missing/unused/filled 三者与产物恒一致。

---

## 签名核对表（spec/plan 引用 vs 真实代码）

| # | spec/plan 引用 | 真实签名 / 行为 | 结论 |
|---|---|---|---|
| 1 | `distill_events(payload, polisher=None, test_data_values=None)` | `distiller/api.py:70-74` 同形，返回 `DistillResult` | 一致 |
| 2 | `distill_file(events, output_path=…, polisher=…, test_data_values=…)` | `api.py:100-122` 同形；缺目录自动创建；文件读取失败抛 `DistillError` | 一致 |
| 3 | `default_output_path(events)` | `api.py:94-97` str→str；**不在 `distiller/__init__` re-export 面**（见建议 1） | 签名一致，导入面注意 |
| 4 | `DistillError` / `skeleton_text` / `warnings` / `used_llm_polish` / `fallback_reason` | `api.py:23-36` 全存在；润色异常回退 `polish_error:{Type}`（`api.py:56`，与用例 15 期望前缀一致） | 一致 |
| 5 | P0-2 两段式前提：骨架含占位符、占位符豁免回查、缺值保留 | 占位符 `{{TEST_DATA:password_{seq}}}`（`templates.py:34,62-74`）；无值保留占位符；factcheck 豁免（`factcheck.py:23-24`）；骨架自检不因占位符失败 → 骨架扫描键集→交集填充→缺值警告**逻辑成立** | 一致（值类型契约除外→R3） |
| 6 | `read_api_key(Path)`；`RunnerError`→2 | `runner.py:114-120` 同形 | 一致 |
| 7 | `build_run_plan(feature, project_root, api_key=key)` | `runner.py:222-238`；`api_key` keyword-only；内部 `prepare_run_dir` 建 `input/`、`output/` | 一致 |
| 8 | `run_feature(feature, run_id=…, project_root=…, timeout_s=…, api_key=…, dry_run=…)` | `runner.py:246-255`；`run_id`/`project_root` keyword-only 必填，spec 调用形全 keyword | 一致 |
| 9 | `STATUS_PASSED/FAILED/TIMEOUT/NO_JUNIT/DRY_RUN`、`DEFAULT_TIMEOUT_S=900`、`LLM_KEY_PATH`、`REPO_ROOT` | `runner.py:30-50` 全在；`LLM_MODEL_NAME="deepseek-v4-pro"`、`ENABLE_UBLOCK_EXTENSION="false"` 已入 child env（`build_child_env:140-161`） | 一致 |
| 10 | `RunPlan.env_redacted()` 脱敏打印 | `runner.py:88-91`；key→`***REDACTED***`（与用例 19 期望值一致） | 一致 |
| 11 | `run_dir=project_root.parent`；`stdout.log` 落 run_dir 且已脱敏 | `runner.py:232`（plan.run_dir=project.parent）、`305-306`（masked 写入） | 一致 |
| 12 | HTML 报告"junit 同 stem `.html`" | `testzeus_hercules/__main__.py:122-123`：`<feature>_result.html` 与 XML 同目录同 stem | 一致 |
| 13 | `attribute_run(run_dir, junit_path=…, analyzer=…) → list` | `attributor/api.py:114-118` 同形；`junit_path=None` 时 `**/*.xml` glob 取最新（`evidence.py:179`） | 一致 |
| 14 | `AttributionError`（JUnit 缺失/不可解析）→ analyze 3 | `evidence.py:193,197` `load_bundle` 抛；analyzer=None 时 attribute_run 不抛它（`resolve_needs_llm` 内部降级，见 #17） | 一致 |
| 15 | `to_json()` 返回 dict 或 str 两形态兼容 | `report.py:79-81` `to_json()` 返回 **str**；`to_markdown()` 标题 `# 失败归因报告`——spec §6.1 的双形态兼容写法恰好覆盖 | 一致 |
| 16 | attributor `__init__` re-export 面"不含 analyzer" | 实际已含 `DefaultAttributorAnalyzer`/`LlmAnalyzer`（`__init__.py:19,26`）；spec 自带"若已提升以 `__init__` 为准"兜底条款 | 括注过时，条款已覆盖（建议 2） |
| 17 | "`DefaultAttributorAnalyzer` 构造抛 AttributionError（缺 attributor_analyze 键）→ 降级" | **构造不抛**（懒构建，`llm_layer.py:582-601` docstring 明示）；缺键错误在首次 `analyze()` 内抛出、被 `resolve_needs_llm` 捕获后内部降级（`llm_layer.py:517-521`）→ 端到端仍为 warning + 降级报告 + exit 0，与 §7 表/用例 30 验收一致；步骤 2 的构造守成为无害冗余 | 机制描述过时，结果行为一致（建议 2） |
| 18 | 已知问题"同步契约不可在运行中的事件循环内调用" | attributor test-report #23 属实（`api.py` 内 `asyncio.run`）；CLI 全程 sync main()，generate（distiller 的 asyncio.run）与 analyze 是不同进程 → 不触发 | 无冲突 |
| 19 | `R2GRecorder.start()` 必须 `is True`；`stop()/getJSON()/status()/copy()` | `recorder.js:498-574`：start 返回 true/false；`getJSON()` 返回含 `session`/`events` 的 JSON 串；`status()` 返回 `{recording,event_count}`；重复注入重置实例（`:27,559-566`）支撑 `--manual`"只注入一次"文案 | 一致（`events=[]` 不可达除外→R1） |
| 20 | record 注入方式照 `dev_runs/integration_smoke.py:14-22`；fixture `demo_form.html` | 注入三步（读 js→`page.evaluate`→`start() is True`）逐行吻合；fixture 存在 | 一致 |
| 21 | `configure_logger("INFO")` | `testzeus_hercules/utils/logger.py:10` `configure_logger(level="INFO")` | 一致 |
| 22 | `tests/record2gherkin/cli/` 命名无冲突 | 顶层确无 conftest.py（仅 `__init__.py` + 4 个模块目录各带目录级 conftest） | 一致 |

## 审查清单逐项结论

1. **接口契约**：除 #3 导入面（建议 1）与 #17 机制描述（建议 2，行为不受影响）外，四命令引用的每个签名/常量/返回形态与真实代码一致。
2. **P0-2 两段式**：扫描→交集填充→缺值警告的主逻辑与 distiller 契约吻合（#5）；唯步骤 4 值类型契约错位会虚报 filled（R3）。
3. **退出码**：自洽。run 0/1/3/4、generate DistillError 1、输入错 2、analyze AttributionError 3、record 2/130 各路径互不吞并；兜底 `except Exception` 不会吞 `SystemExit`/`KeyboardInterrupt`（130 语义保得住）；analyze exit 0 与 run exit 1 分工明确（报告产出≠用例通过）。
4. **密钥闭环**：key 仅 env ✓、dry-run 占位串不执行且 `env_redacted` ✓、测试假 key ✓；唯 failure_message 的 JUnit 路径脱敏断言失实（R2）。
5. **可测试性**：32 条 = 3+4+10+8+7，四命令主干全被覆盖；record 的 playwright 交互测试总体可行（headless + file:// fixture + 接缝注入），唯用例 5/6 按 R1 修正后才可落地；agent_limit 签名匹配大小写不敏感（`rules.py:306,311`），用例 26 可过。
6. **spec 完备性**：dry-run 分支、run_id 冲突后缀、目录布局、降级路径、产物命名、信号语义均已写死；修正 R1 接缝签名后，实现者无需再做设计决策。

## 建议不阻塞项（3 条，均不产生错误行为或首跑即暴露、修复机械）

1. **`default_output_path` 导入面**：它只在 `record2gherkin.distiller.api`，不在包 `__init__` re-export 面；plan §4 复用清单按"从 `record2gherkin.distiller` import"写会 ImportError。实现时从 `.api` 导入，或顺手在 distiller `__init__` 补一行 re-export。首跑即报错，无静默风险。
2. **§6.1 步骤 2 机制描述过时**（核对表 #16/#17）：`DefaultAttributorAnalyzer` 已提升至 `attributor.__init__`，且构造不抛、缺键降级发生在 `resolve_needs_llm` 内部。spec 的兜底条款 + 用例 30 已保证实现与验收正确；实现时可把构造 try/except 留作冗余保险（无害），文案以 `__init__` 与 llm_layer 实际行为为准。
3. **锚点与计数小幅漂移**：runner 行号实为 `build_run_plan` 222-238、`run_feature` 246-316（spec 写 219-235/243-313）；plan D5"三个目录级 conftest"现为四个（attributor 并行开发已补）；§1.2 退出码表 130 行未列"run Ctrl+C"（§5.5/§7 已写，两处取值一致不冲突）。均不影响实现，随 R1-R3 修订顺手更正即可。
