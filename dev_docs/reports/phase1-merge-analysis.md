# 阶段 1 合并后分析报告（phase1-merge-analysis.md）

> 范围：`record2gherkin/recorder` + `record2gherkin/distiller` 合并态（main `4f0df82`），只读分析，未改任何代码。
> 验证基线：`uv run pytest tests/record2gherkin -q` → **85 passed**（recorder 21 + distiller 64）；`dev_runs/integration_smoke.py` 本机重跑 **9/9 PASS**；另用临时页面（svg 图标按钮 + submit 表单）做了定向探针实证（见 P1-1 / P1-3）。

## 结论：GO-WITH-CAVEATS

准许进入评测框架阶段。合并态代码在其 spec 范围内质量良好：契约字段/枚举/掩码/截断在录制器→蒸馏器之间严丝合缝，85 例全绿，骨架确定性且事实回查恒过。但存在 **2 个阻塞项**，评测框架动工前必须先落决策（一个是 spec 悬空导致的重放必坏步骤，一个是占位符无机械消费者），否则评测结果将被这两个伪影主导而非真实反映链路质量。

---

## 问题清单（按优先级）

### 阻塞下一阶段（2 项）

**P0-1 click-on-submit 与 submit 双步骤：spec 契约悬空，真实站点重放必坏**
- 位置：`record2gherkin/recorder/recorder.js:425-431`（handleSubmit 无条件记录）+ `record2gherkin/distiller/templates.py:99-101`（submit 无合并规则）+ `record2gherkin/distiller/polisher.py:21-35`（SYSTEM_PROMPT 硬约束禁止增删 Then 以外的步骤，允许的合并只有 input）。
- 影响与实证：点 submit 按钮恒产出 click + submit 两条事件，蒸馏为 `When I click on the "Go" button` + `When I submit the "User Go" form`（探针实测）。重放时 click 已触发导航/提交，紧随的 submit 步骤落在跳转后的页面上（表单已不存在）→ agent 必然失败或二次提交。几乎所有真实流程（登录/搜索/下单）都含此形态，评测结果会被它淹没。recorder spec §4.2 写"蒸馏器负责合并"，但 distiller spec §4.2 的润色白名单只允许合并 input——两份 spec 互相指派、无人落地，是唯一的跨模块契约断点。
- 建议：在 templates 层加确定性规则（零 LLM 依赖）：`click`（target.role=button 或 tag=input[submit]）紧邻后继 `submit` 且同表单时，丢弃 submit 步骤（或反之保留 submit 丢 click，取一处并同步两份 spec + polisher 硬约束）。

**P0-2 `{{TEST_DATA:password_<seq>}}` 在 Hercules 侧没有任何机械消费者**
- 位置：`record2gherkin/distiller/templates.py:59-74`（生成占位符）vs 消费侧——全仓 grep 无占位符替换逻辑；`testzeus_hercules/utils/gherkin_helper.py` 只做拆分/单行化；`testzeus_hercules/core/memory/static_data_loader.py:70-125` 只把 TEST_DATA_PATH 目录下的文件内容整块注入 LTM prompt（"following is test_data from <文件名>"）。
- 影响：带密码的流程（旗舰场景）重放时，agent 只看到字面量 `{{TEST_DATA:password_6}}`；`password_<seq>` 是合成键，与 test_data 目录文件名无确定性映射，执行成败取决于 LLM 猜测。
- 建议：评测框架必须落一个注入策略，二选一并写入评测方案：(a) 蒸馏时经既有的 `test_data_values` 参数填真实值（API 已支持，`tests/.../test_templates.py::test_masked_input_filled_from_test_data` 已验证）；(b) 落一个约定命名的 test_data 文件并接受 LLM 推断的不确定性（不推荐作主路径）。

### 建议下一阶段顺带（4 项）

**P1-1 无语义 click 产出垃圾步骤，图标按钮的语义全丢（真实站点高频形态）**
- 位置：`recorder.js:346-357`（handleClick 直接用 event.target，不向上找语义祖先）+ `templates.py:89`（hint 回退链末端落到 tag）。
- 实证：对 `aria-label="Close dialog"` 的纯图标按钮点击，event.target 落在 svg 上，录制为 `tag=svg name=''`，蒸馏产物为 `When I click on the "svg" generic`——aria-label 完全丢失，且回查不拦（"svg" 在事实集内）。真实站点图标按钮极常见，评测期会大量出现此类步骤。
- 建议：recorder 在 name 为空时向上探测最近的具名祖先（`closest("button,a,[role],[aria-label],label")` 且计算名非空）；或 distiller 对 name 为空的 click 记 warning 并降级措辞。recorder spec §4.2 本写了"目标向上取最近的有语义祖先"，实现未落地，属实现-spec 漂移里影响最大的一条。

**P1-2 checkbox/radio 双事件执行歧义**
- 位置：`recorder.js:411-414`（change→input("true"/"false")）与 `recorder.js:346`（物理 click 也各记一条）；蒸馏后两条步骤并存——集成冒烟输出实证：`When I click on the "★ 订阅邮件" checkbox` + `When I enter "true" in the "★ 订阅邮件" field`。
- 影响：重放时 click 已切换状态，"enter true"对 checkbox 的执行语义未定义（Playwright fill 会直接报错，agent 行为不可预测）；勾后又取消的录制会膨胀成 4 步。
- 建议：蒸馏规则——click(role=checkbox/radio) 后紧邻同 target 的 input 步骤丢弃，或把 input(true/false) 映射为 check/uncheck 措辞；同时评测数据需覆盖"★ 订阅邮件"这类带图标字符的 name 的命中情况。

**P1-3 录制 name 与 Hercules 引擎可访问名的计算不同源（label 关联缺失）**
- 位置：recorder `recorder.js:107-153`（aria-label → label[for]/aria-labelledby/包裹 label → placeholder → innerText → title）vs 引擎 `testzeus_hercules/utils/get_detailed_accessibility_tree.py:1050-1087` getAccessibleName（手工回退链不含任何 label 关联；checkbox 会落到 `element.value`＝"on"；且 `getComputedAccessibleNode` 返回 Promise 未 await，实际恒走手工回退）。
- 影响：`When I click on the "邮箱地址" field` 这类步骤在引擎树上可能对不上同名节点，全靠 LLM 模糊匹配；label 包裹 + 图标字符（★）场景差异最大。recorder spec §2.1.2 声称与引擎"同源"，实际不同源。
- 建议：评测框架加入"name 命中率"统计项，用数据决定是补引擎侧 label 关联还是接受模糊匹配；不必预先改。

**P1-4 Scenario 标题直通 split_feature_file 文件名，真实站点标题可致写文件失败**
- 位置：`testzeus_hercules/utils/gherkin_helper.py:70-71`（`scenario_title.replace(' ', '_') + ".feature"`，无文件名安全化）；标题源头 `templates.py:111`（首事件 page_title）。
- 影响：page_title 含 `/` 等字符时（真实站点常见），split 写文件 ENOENT/路径穿越，阻塞"真录→蒸馏→Hercules 子进程"链的拆分环节。
- 建议：蒸馏器对 Scenario 标题做文件名安全化（保留标题行原文、仅 sanitize 文件名），或评测框架在调 split 前 sanitize。

### 记录即可（6 项）

**P2-1 submit 的 target.name = form innerText 首行**（探针实测 `"User Go"`）：无意义 hint，agent 可容忍。可在 recorder 对 tag=form 跳过 innerText（spec §4.5 预期 name 通常为空串）。
**P2-2 input[type=file] → `When I enter "<file>" ...`**：不可执行步骤，spec 既定 Out of Scope（recorder test-report 已知问题 5），评测数据里出现该步骤时应直接判 N/A。
**P2-3 有意为之的 spec 文字漂移（均已双方记录在案，无动作）**：点空白判定取严（recorder 已知问题 1）；navigate 去重不限"同 tick"；掩码判定 `strip()==` 超集（templates.py:62，宁多掩不漏）；骨架自检移到 api 层执行（api.py:39-47，避免循环依赖）；distiller spec §6 用例 20 示例与 §5.2 不自洽（按严格子串实现，test-report 已知问题 1）。
**P2-4 navigate 缺 url 被跳过时其 assert_texts 一并丢弃**（templates.py:80-87）：recorder 恒有 location.href，实际不可触发；仅手造数据会命中。
**P2-5 `_apply_polisher` 捕获一切异常**（api.py:52-56）：包括在已运行事件循环内调 `asyncio.run` 的 RuntimeError——会被静默降级为 polish_error 而非暴露调用方式错误；评测走同步子进程不受影响。
**P2-6 target.testid 全链路采集但零消费**：recorder 采集、distiller 不读、步骤措辞不含。data-testid 本是重放最稳的定位线索，评测期可作为低成本的可靠性增强点（把 testid 注入 click/enter 步骤措辞），需动 spec，先记录。

## 专项核查纪要（按任务四项）

1. **跨模块契约**：字段集（8 事件键/7 target 键）、枚举、掩码哨兵、空 target（distiller 回退到 `"element"/"field"` 且豁免回查）、缺 dom_snapshot（events.py:128-143 宽松处理）、seq 缺失排序、值截断（500/200/120/8 条）——逐一比对 recorder 实际产出（test-report §3 真实样例 + _helpers.validate_event_stream）与 events.py/templates.py，除上述 P0-1/P1-1/P1-2 外无其它"录得出、蒸不对"的形态。
2. **spec 漂移**：recorder 的 name 五级优先级、role 映射表、ordinal 规则、去噪总表、快照归因修订（recorder.js:278-304，与 spec §3 2026-09-21 修订一致，回归用例在测）；distiller 的规则映射表四条 fallback 链、ordinal>1 追加、掩码→占位符、豁免四常量、回退五路径——实现与 spec 一致；漂移仅 P2-3 所列已记录项 + P1-1（未落地的"语义祖先"条款）。
3. **代码质量**：未发现死代码、被注释掉的逻辑、异常吞掉或复制粘贴漂移；两处超集式宽松判定均已注释说明。唯一小噪音：`tests/record2gherkin/recorder/test_packaging.py:36-37` 前缀断言重复一行（无害）。
4. **下一阶段链路坑**：见 P0-2、P1-2、P1-3、P1-4 及 P2-2/P2-6；另注意 distiller spec §8 已明确"引擎侧 test_data 自动注入"不在本模块范围，属评测/编排层责任。

## 总评（<200 字）

合并态质量达标：契约无缝、85 例全绿、骨架确定且恒过回查，集成冒烟可复现。两份 spec 各自自洽，但"submit 合并"互相指派无人落地，加上图标按钮语义丢失与 checkbox 双事件，构成真实站点重放的三大伪影；`{{TEST_DATA}}` 占位符在 Hercules 侧无机械消费者，是评测框架必须先落的编排决策。均为局部可修（templates 确定性规则 + 评测注入策略），无需返工架构。结论 GO-WITH-CAVEATS：先落 2 条阻塞项，再进评测框架。
