# 评测框架 spec 独立审查（review.md）

> 审查对象：`dev_docs/evaluation/plan.md`、`dev_docs/evaluation/spec.md`
> 契约基准：`PLAN.md` §3.3/§3.4/§4/§5、`dev_docs/README.md`、AGENTS.md 二开规约
> 交叉事实核对：`dev_docs/recorder/spec.md`（事件 schema/assert_texts 机制）、`dev_docs/distiller/spec.md`（蒸馏产物形态）、`testzeus_hercules/utils/junit_helper.py`、`testzeus_hercules/__main__.py`、`testzeus_hercules/config.py`、`testzeus_hercules/telemetry.py`
> 审查纪律：只拦"对比数据不可信 / 链路跑不通 / 预算爆炸"级别的问题。

## 结论：**REVISE**

必改项 5 条，全部为局部修订（改公式、补注册表行、加一个单测断言、重排 pilot 格子），无架构性返工；逐条落实后即可转 PASS。核心设计判断是对的：JUnit 取数路径真实存在、预算数学闭合、基线口径已显式声明且非稻草人（M1/M2 预期基线存活已如实预判）、M3 的注册表级变异与蒸馏 fact-check 不冲突（feature 文本只含可访问名/URL，不含 id）。

---

## 必改项清单

### 必改 1：seed 派生公式循环引用，"可重复性闭环"按字面无法实现

**问题描述**：spec §2.3（:99）规定 `seed = int(sha1(f"{exp_id}:{run_id}").hexdigest()[:8], 16)`；而 §4.1（:122）规定 `run_id = "<method>__<flow>__<mutation>__s<seed>"`——run_id 内嵌 `s<seed>` 后缀。即 seed = f(run_id) 且 run_id = g(seed)，循环依赖，按字面写不出代码。

**依据**：审查清单第 1 条"变异定义是否真的可重复（seed 机制是否闭环）"。manifest 虽记录最终值（事后可复现），但 sweep 的确定性派生规则本身不可实现，实现者必须自行发明一个替代公式——违背 spec 头部"读完不再需要做设计决策"。

**具体改法**：seed 从**不含 seed 的格坐标**派生：`seed = int(sha1(f"{exp_id}:{method}:{flow}:{mutation}").hexdigest()[:8], 16)`，run_id 在 seed 确定后拼装为 `<method>__<flow>__<mutation>__s<seed>`。同步：指明派生函数落在 sweep.py；在 test_matrix.py（或 A 组）补一个单测——同格派生恒等、同 exp 内任意两格派生互异。

### 必改 2：M3 下内联 JS 的元素绑定方式未规定——最坏情况是"页面自身坏了"污染主指标

**问题描述**：§1.4 的全部客户端行为（搜索/筛选/清单计数/数量/提交）由 SPA 文档内联 JS 实现，但 spec 未规定 JS 如何定位元素。§2.2 M3 对注册表**全部**元素的 id/class/data-testid 做替换；若实现者按最自然的方式硬编码 `getElementById("search_input")`，则 M3 渲染出的页面**自身行为失效**（搜索不执行、"找到 N 件商品"永不出现），F1/F4 在 M3 下对 generated 与 baseline **双双假失败**——主指标区分度被"页面坏了"吞掉，对比数据整体不可信。

**依据**：现有离线单测恰好防不住它：A 组全是字符串级断言（A8 "M3 vs M0 除 id/class/testid 外可见文本相同"对硬编码 JS 同样通过），B 组只 fetch HTML 不执行 JS。缺陷会潜伏到 D2/D3 才以"数据难看"的形式暴露，且届时无法区分是引擎语义失败还是 demo 自身故障。

**具体改法**：§2 增加一条总则：内联 JS 不得硬编码任何受 M3 影响的定位属性，必须与 HTML 同源——由 render_page 从**变异后**的注册表内插生成绑定，或只用不受 M3 影响的结构（事件委托 + 表单语义）。补一条离线单测：M3 产物中内联 JS 引用的每个 id/testid 均存在于该产物 DOM（M0 同理）。另在 §12 pilot 验证点中加一条：M3 下 F1 的搜索行为在浏览器中实际生效。

### 必改 3：token 指标键 `*.total_tokens` 有歧义，误取会双计

**问题描述**：§6.3（:180）规定优先取"testcase 级展平键 `usage_including_cached_inference.total_cost` / `*.total_tokens`"。展平后实际属性名（junit_helper.py:144-146 对 flatten_dict 结果逐键写 property）包括 `usage_including_cached_inference.deepseek-chat.total_tokens`、`usage_excluding_cached_inference.deepseek-chat.total_tokens` 等多个 `.total_tokens` 结尾键；字面按 `*.total_tokens` 全取会 **usage_including + usage_excluding 双计**，token 成本数据虚高一倍。

**依据**：审查清单第 1 条"指标公式无歧义"。上游已有权威选取规则：junit_helper.py:154-158（取 `usage_including_cached_inference.` 前缀且 `.total_tokens` 结尾的键；无该前缀键时才回落全量）。

**具体改法**：§6.3 改为"token 键选取与 `junit_helper.py:154-158` 同一规则：优先 `usage_including_cached_inference.*.total_tokens`（可多键求和），无则回落其余 `*.total_tokens`"。顺带补一句：suite 级 `total_execution_cost`/`total_token_used` 属性值为字符串（junit_helper.py:172-173 `str()` 写入），解析需 float()/int() 转换，转换失败按 None 计。

### 必改 4：pilot 未覆盖 "(occurrence N)" 语法，风险要拖到 D3 全量才暴露且预算无缓冲吸收

**问题描述**：F2 会对同名按钮产生 `(occurrence N)` 后缀步骤（recorder spec §2.1.3 ordinal 机制 + distiller spec §2.2 模板追加），而该措辞在 Hercules 执行侧**从未验证过**。D2 pilot 选的 F3 不含任何 occurrence 步骤，hash URL 与 checkbox 双事件都列了 §12 验证点，唯独这条同类风险漏掉。若该语法执行失败，F2 在 5 个变异格上系统性假失败，直接压低 FirstPass 与全部 Surv 列——且发现时 D3 预算已锁定（30+4+6=40 恰好顶满上限），没有迭代余地。

**依据**：审查清单第 1/2 条。pilot 的存在意义就是吸收执行侧未知；预算 40 恰好用满意味着 D3 的意外不可吸收。

**具体改法**：§12 增加验证点"occurrence 后缀步骤的执行侧行为"；D2 pilot 矩阵改为 F3×{M0,M3} + F2×M0（+≤1 次基础设施重试），基础设施缓冲由 ≤6 降为 ≤5，合计 30+4+5=39 仍 ≤40，§7 预算表与 F 组护栏断言数字同步更新（护栏仍断 ≤40）。若 pilot 证实 occurrence 语法不可用，处置写实验报告（如 F2 改用可区分的商品名按钮），不改 spec 规则。

### 必改 5：注册表缺 `cart_count` 元素，单一事实来源出现空洞

**问题描述**：§1.4 规定"加入清单：头部 `清单（N）` 计数 +1"，F2 的 Then 断言即 "清单（2）"（§1.3 :66），但 §1.2 元素注册表（:42-59）没有该计数元素——同为动态文本的 `list_status`（"共 3 件商品"）有注册项，`cart_count` 没有。

**依据**：审查清单第 5 条"实现者读完不需要做设计决策"。注册表自declare为"单一事实来源"，M3 变换范围（"注册表**全部**元素"）、A2 完整性单测（"注册表全部稳定 id/testid/关键文本在产物中"）、baseline `expected_selectors()` 的覆盖面都由它定义；cart_count 缺失意味着它是否参与 M3 随机化处于未定义状态，实现者需自行拍板，且不同拍板会让 Surv(baseline, M3) 的 F2 格结果不同（计入文本断言不受 id 影响，但点击链路的 selector 来源不一致）。

**具体改法**：§1.2 list 视图补一行：`cart_count` | p（头部） | 稳定文本 `清单（0）`（动态更新，同 list_status 形态）；与必改 2 联动——JS 从注册表取绑定，cart_count 必须在册。

---

## 事实核对表

| spec/plan 声称 | 实际核对 | 判定 |
|---|---|---|
| junit_helper.py:108-124 内嵌全部证据路径 properties | :108-124 恰为 Property 块（Feature File/Proofs/Screenshots/Network Logs/Planner Thoughts 等） | 一致 |
| testcase 级展平成本键存在 | flatten_dict(cost_metric) 逐键写 property（:144-146）；`usage_including_cached_inference.total_cost` 是真实键名（:150、:322-341 示例） | 一致（键选取歧义见必改 3） |
| suite 级 `total_execution_cost`/`total_token_used` 兜底键存在 | :172-173 `suite.add_property` 写入；值为字符串 | 一致（需类型转换） |
| 通过判定 = `<failure>` 缺席 | add_test_case：is_assert 且非 is_passed → Failure；terminate=="no" → Failure（:94-106） | 一致 |
| merge 保留 testcase 级 properties | merge_junit_xml 对每 suite 逐 testcase `add_testcase`（:212-213），suite 级同名数值属性 float 相加 | 一致 |
| JUnit 产物 `<output-path>/<feature 文件名>_result.xml`（`__main__.py:118`） | :28 `basename(input_gherkin_file_path)`，:118 拼 `_result.xml`；`--output-path` → `JUNIT_XML_BASE_PATH`（config.py:157-160,414-417） | 一致 |
| 子进程模板 mcp_server.py:161-172 | :162-172 `create_subprocess_exec` + wait_for + kill | 一致 |
| 输出目录 import 时时间戳别名（config.py:14,60） | :14 `TS = get_timestamp_str()`（import 时求值），:60 `self.timestamp = TS` | 一致 |
| legacy 直连 LLM env 路径（config.py:826-832） | :826-831 `LLM_MODEL_NAME/API_KEY/BASE_URL/API_TYPE` 组装 model_cfg | 一致 |
| `ENABLE_TELEMETRY=0` 必须压掉（config import 即初始化 Sentry） | telemetry.py:20 读 env，:65-67 `if ENABLE_TELEMETRY: sentry_sdk.init`（import 时） | 一致 |
| CLI 三参数 `--input-file/--project-base/--output-path` | config.py argparse :229-244；`project_base`→`PROJECT_SOURCE_ROOT`（:160,420-421） | 一致 |
| 单价上限 $0.13/次（PLAN §3.3） | junit_helper.py:322-341 实证样例 gpt-4o 46945 tokens/$0.129；deepseek 远低于此，作上限成立 | 一致 |
| 预算数学 | 30 sweep + ≤4 pilot + ≤6 缓冲 = ≤40；40×$0.13=$5.20 ≤ 红线 $10；矩阵 6×5×2=60 | 数学闭合（必改 4 重排后仍 ≤40） |
| 基线口径声明 | plan §2 基线口径行 + spec §5 显式声明"id→testid→class、不含语义定位，最终报告原样声明"；plan §5 如实预判 M1/M2 基线存活、M4 单列 | 满足"公允且明确声明"，非稻草人（M3 全灭是属性 selector 录制回放的机制性后果，且是项目立论点） |
| 蒸馏产物与 demo 相容 | 6 流程全部元素有 aria-label/label/innerText → feature 只含可访问名/URL/值，无 id → M3 不破坏 feature 字面值，fact-check 不受影响；无密码字段 → 无 `{{TEST_DATA:` 占位符（distiller §2.3 触发条件不出现）；F5 断言"共 3 件商品"在回列表后的 diff 快照中可捕获（recorder §3 差集机制） | 一致 |
| 离线单测覆盖（清单第 4 条） | A1/A5/A6/A9/A10 变异可重复性；C15-19 JUnit 解析含兜底与畸形 XML；D20-23 指标含 None/timeout/no_junit 分母；E24-26 key 不进 cmd/仅 env/脱敏；F28 预算护栏；真 LLM 仅 pilot/sweep | 覆盖充分（必改 1/2 各需增一条单测） |
| 密钥红线（清单第 3 条） | key 仅经 subprocess `env=`（§4.2 :142）；stdout/stderr 先 mask 再落盘（§4.3 :162）；failure_message 脱敏；manifest 不含 key 及前缀（§8 :202）；验收 §10.6 全产物 grep 且用 `$KEY` 变量避免 key 入命令行；`ENABLE_TELEMETRY=0` 关闭 Sentry（telemetry.py:65-67，且 :76 本身也置空 sys.argv）；`LLM-Key.txt` 存在且已 gitignore | 闭环（argv/日志/文档三路全堵） |
| 4 天可行性（清单第 2 条） | recorder/distiller 已实现且冒烟 9/9（STATUS.md 阶段 2c；`dev_runs/integration_smoke.py` 的 playwright 注入模式可直接复用于 D1 六条流程录制）；demo_app+server 纯标准库、无 node、无外网；chromium 已装；D3 串行 30 次按 900s 上限最坏 7.5h、预期 2-4h | 成立（D1 偏重但判据可测） |
| gitignore | `dev_runs/`（:176）、`**/*.xml`（:152）均在 .gitignore | 一致 |

---

## 建议不阻塞项

1. **FirstPass 口径脚注**：sweep 全量中 F3×M0 并非 F3 字面上的"第一次执行"（pilot 已跑过一次）。spec 已声明 pilot 不入正式表且 feature 全程零修改，实质公允；建议 test-report.md 加一句口径说明（"FirstPass 以正式 sweep 首跑为准，pilot 未改动 feature 文件"），防面试/评审口径质疑。
2. **A8 建议加强**："M3 vs M0 除 id/class/data-testid 外可见文本相同"之外，可顺带断言 aria-label/placeholder/label **属性值**逐元素相等（现 A7 只抽查文本不变性），让"语义感知面不变"的不变量完全机械可验。
3. **stdout.log 兜底**：§4.2 已规定 no_junit 保留异常文本；建议 sweep 对 `status="timeout"` 的格也把已捕获的部分 stdout（脱敏后）附入 failure_message 字段，便于 D4 失败清单归因，不增加运行成本。
