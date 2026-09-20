# 蒸馏器 test-report — 实现与测试汇报

> 模块：`record2gherkin/distiller/`，测试 `tests/record2gherkin/distiller/`
> 依据：`dev_docs/distiller/spec.md`（含审查修订后的 §5.1 strip 规则、§5.2 豁免清单）、`plan.md`、`review.md`
> 日期：2026-09-20 · 状态：**实现完成，全绿**

## 1. 交付物清单

| 文件 | 职责 |
|---|---|
| `record2gherkin/distiller/events.py` | schema v1 宽松解析归一化 + 事实集构建 |
| `record2gherkin/distiller/templates.py` | 规则模板层（1 Feature 1 Scenario，零模型依赖） |
| `record2gherkin/distiller/factcheck.py` | 字面值提取/转义、结构校验、事实回查、断言存活、回退判定 |
| `record2gherkin/distiller/polisher.py` | `Polisher` 协议、prompt 构建、`DefaultPolisher`（惰性 litellm 接线） |
| `record2gherkin/distiller/api.py` | `distill_events` / `distill_file` / `DistillResult` / `DistillError` |
| `record2gherkin/distiller/__init__.py` | 公开 API re-export（`distill_events` / `distill_file` / `DistillResult` / `Polisher` / `DistillError` / `DefaultPolisher`） |
| `tests/record2gherkin/distiller/{conftest.py,test_templates.py,test_factcheck.py,test_polish_fallback.py,test_api_e2e.py}` | 64 条用例（覆盖 spec §6 的 35 条 + 补充边界） |
| `tests/record2gherkin/distiller/fixtures/{login_search,form_submit,sparse_fields,empty_session}.json` | 手写 schema v1 样例（4 份） |

测试布局对 spec §0 做了机械适配（录制器并行开发）：conftest 与 fixtures 下沉到 `tests/record2gherkin/distiller/`，生成物一律写 pytest `tmp_path`，fixtures 目录无生成物。

## 2. 测试命令与结果

```console
$ uv run pytest tests/record2gherkin/distiller -q
................................................................         [100%]
64 passed in 2.89s

$ uv run pytest tests/record2gherkin -q          # 含录制器并行模块，验证无相互干扰
........................................................................ [ 85%]
............                                                             [100%]
84 passed in 12.55s
```

- 全程离线：无 LLM key、无浏览器、无网络。所有润色用例注入 `FakePolisher`；`DefaultPolisher` 只在离线双打（fake chat model）下测试，且 litellm/gherkin_generator 均为函数内惰性 import。
- 额外收口：`tests/record2gherkin/distiller/conftest.py` 的 `pytest_configure` 里 `os.environ.setdefault("ENABLE_TELEMETRY", "0")`。上游 `testzeus_hercules/config.py` 在 import 时会初始化 Sentry 客户端并在进程退出时尝试外发事件（实测 pytest 结束输出出现 `Sentry is attempting to send 2 pending events`）；置 0 后该网络尝试消失（用 `setdefault`，显式设 `ENABLE_TELEMETRY=1` 时仍以环境为准）。

用例分布（spec §6 的 35 条全部落地，含参数化后共 64 条）：

| 组 | 落地用例数 | 文件 |
|---|---|---|
| A 规则映射（spec 用例 1-17） | 22（17 条规范用例 + 5 条边界） | `test_templates.py` |
| B 事实回查（18-23） | 25（规范用例 18-23 共 9 项，其中用例 18 按 4 份 fixtures 参数化；另 16 项结构/边界用例） | `test_factcheck.py` |
| C 润色与回退（24-30） | 11（7 条规范用例 + 1 条 `polish_empty` + 3 条 `DefaultPolisher` 离线用例） | `test_polish_fallback.py` |
| D API/端到端（31-35） | 6（5 条规范用例 + 1 条 form 流程端到端） | `test_api_e2e.py` |

## 3. 验收标准逐条勾验（spec §7）

| # | 标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | `uv run pytest tests/record2gherkin -q` 全绿、无网络依赖 | ✅ | 见 §2：`64 passed`（模块内）/ `84 passed`（含录制器）；无 key、无浏览器；Sentry 外发已被 §2 的 `ENABLE_TELEMETRY=0` 消除 |
| 2 | fixtures 产物恰 1 `Feature:` + 1 `Scenario:`，且 `split_feature_file` 解出恰 1 条记录 | ✅ | 用例 `test_skeleton_always_passes_selfcheck[*]`（断言计数）+ `test_hercules_helper_integration`（真调 `split_feature_file`，`len(records)==1`）；机械核对见下方命令 A |
| 3 | 确定性：同输入 + `polisher=None` 两次逐字节相同 | ✅ | 用例 `test_deterministic_output`（含事件数组逆序后输出不变的核对 + 两次 `distill_file` 产物 `read_bytes()` 相等） |
| 4 | 骨架路径零模型：三模块不 import litellm/网络 | ✅ | 命令 B（负向 grep 无命中，exit=1）+ 用例 `test_no_polisher_no_llm`（fake 从未被构造） |
| 5 | groundedness：fixtures 骨架过回查；润色稿未过即回退 | ✅ | 用例 `test_skeleton_always_passes_selfcheck[*]` + `test_any_input_yields_a_valid_skeleton[8 种畸形输入]`；C 组 25/26/27/28 覆盖 `fact_check_failed` / `syntax_invalid` / `polish_error` / `assertion_dropped` 四条回退路径，另有 `polish_empty` |
| 6 | `black --check record2gherkin tests/record2gherkin`（line-length 200） | ✅ | 命令 C（`12 files would be left unchanged`；`--target-version py311` 复核同样通过） |
| 7 | 敏感值：最终 feature 不出现 `<masked>` | ✅ | 命令 D（4 份 fixtures 全部 `masked_leak=False`，掩码位以占位符/填充值出现）+ 用例 `test_masked_input_becomes_placeholder` / `test_masked_input_filled_from_test_data` |

机械核对命令与输出：

```console
# A) fixtures 结构计数
$ uv run python -c "...distill_events(每个 fixture)..."
empty_session.json: feature_lines=1 scenario_lines=1 masked_leak=False placeholders=0
form_submit.json:    feature_lines=1 scenario_lines=1 masked_leak=False placeholders=1
login_search.json:   feature_lines=1 scenario_lines=1 masked_leak=False placeholders=1
sparse_fields.json:  feature_lines=1 scenario_lines=1 masked_leak=False placeholders=0

# B) 骨架三模块零模型依赖（负向 grep）
$ grep -nE '^(from|import)[[:space:]]+(testzeus_hercules|litellm|langchain|openai|anthropic|requests|urllib|httpx|aiohttp|socket)\b' \
      record2gherkin/distiller/events.py record2gherkin/distiller/templates.py record2gherkin/distiller/factcheck.py
grep exit=1            # 无命中 → 三模块只依赖 stdlib 与彼此

# 三个文件的全部 import（正向证据：无第三方依赖）
events.py:   from __future__ / re / dataclasses / typing
templates.py:from __future__ / dataclasses / record2gherkin.distiller.events / record2gherkin.distiller.factcheck
factcheck.py:from __future__ / re / dataclasses / typing / record2gherkin.distiller.events

# C) 格式
$ uv run black -l 200 --check record2gherkin/distiller tests/record2gherkin/distiller
All done! ... 12 files would be left unchanged.

# D) 掩码泄漏（见 A 的 masked_leak 列）
```

> 命令 B 中 `templates.py → factcheck.escape_literal` 是字面值转义/反转义契约的唯一实现点（§2.4 与 §5.2 互逆，用例 `test_quote_escaping_roundtrip` 覆盖），仍是纯规则模块，不引入任何模型依赖。

## 4. 已知问题清单（4 项，均为 spec 未覆盖/一处自相矛盾的边界，按"最简单确定性行为"落地）

1. **spec §6 用例 20 的示例与 §5.2 规则不自洽**：`order placed` 并不是 `Your order has been placed successfully` 的连续子串，按 §5.2 的规范算法（`norm(L)==norm(F)` 或 `norm(L) ⊆ norm(F)`）应判不 grounded。已按 §5.2 规范实现（严格子串），用例内改判为"连续片段通过（`order has been placed`）、非连续词组不通过（`order placed`）"，两种情形都在 `test_substring_match_passes` 中断言。若评测期发现真实润色稿因此被误杀，再考虑放宽为词序子集匹配（会削弱硬闸，需评审）。
2. **`seq` 缺失/混杂的排序规则**：spec §1.1 只说"缺 `seq` 按数组顺序"。实现为：带合法 `seq` 的事件升序在前，缺 `seq` 的事件保持数组顺序排在其后；混合场景（部分事件有 `seq`）因此是确定性的但非"按数组序插入"。占位符命名沿用规则——事件无 `seq` 时用排序后的数组序号（`password_<位置>`）。
3. **spec 未规定的宽松分支**（统一策略：宁出合法骨架、不抛错，仅记 warning）：输入顶层不是 dict/list、`events` 不是 list、`session` 不是 dict、事件不是对象 → 分别记 `invalid_input_payload:<type>` / `invalid_events_payload:<type>` / `invalid_session_payload:<type>` / `invalid_event:<type>`，按空处理；`navigate` 无 url 也无 origin → 整条事件（含其 `assert_texts`）跳过并记 `missing_url:<seq>`。warning 词表：`empty_events` / `unknown_event_type:<type>` / `missing_url:<seq>` / `empty_value:<seq>` + 上述 4 个 payload warning。
4. **两处刻意的实现取舍**（均已在代码注释中标注）：
   - 掩码判定用 `value.strip() == "<masked>"`（spec 写"精确匹配"）——超集判定，宁多掩不可漏（验收 7 是机械检查，防 `<masked>` 泄漏优先）；`test_data_values` 中的空/空白值视为未提供，该步骤保留占位符（执行前由编排层/人工填值，spec §2.3 的显式 TODO 路径）。
   - 骨架自检失败按 §2.5/§5.3 抛 `DistillError`，但检查动作统一放在 api 层执行：`templates.py` 保持纯函数、不 import api 的异常类型（避免循环依赖），对外可见行为与 spec 一致（`DistillError` 仍是唯一主动抛错路径）。

无阻塞项、无 skip、无 xfail；未发现未实现的核心目标。

## 5. 与上下游的接口现状（供编排/评测参考）

- 公开入口：`distill_events(events, polisher=None, test_data_values=None) -> DistillResult`、`distill_file(events_path, output_path=None, polisher=None, test_data_values=None) -> DistillResult`；`Polisher` 协议 = `async polish(skeleton_text, events) -> str`。
- DOM/事件字段消费面：`session.origin`、事件 `seq/type/url/page_title/value/target.*/dom_snapshot.assert_texts`；`ts`、`target.testid` 不参与逻辑（透传/不消费），schema 漂移只影响 warning 不影响合法性。
- 未做（spec §8 Out of Scope 内）：多 Scenario 切分、Background/Outline、中文模板、复杂断言推理、数据抽取、test_data 自动注入、CLI 与模型路由调优。
