# 蒸馏器 spec — 实现规格

> 读者为实现者。读完本文不再需要做任何设计决策；未规定处按"最简单确定性行为"处理并记入 warnings。
> 契约来源：事件 schema v1 = `PLAN.md` §3.1（唯一权威，不增改字段）。上游消费方式见 `dev_docs/distiller/plan.md` §2。

## 0. 目录与文件

```
record2gherkin/
  __init__.py
  distiller/
    __init__.py      # re-export 公开 API（distill_events / distill_file / DistillResult / Polisher / DistillError）
    api.py           # 公开 API 与 DistillResult
    events.py        # schema v1 解析归一化 + 事实集构建
    templates.py     # 规则模板层（禁止 import 任何 LLM/网络相关模块）
    factcheck.py     # 字面值提取、结构校验、事实回查、回退判定
    polisher.py      # Polisher 协议、DefaultPolisher（litellm 接线）、prompt 构建
tests/record2gherkin/
  conftest.py        # 样例事件 fixtures + FakePolisher
  test_templates.py / test_factcheck.py / test_polish_fallback.py / test_api_e2e.py
  fixtures/*.json    # 手写 schema v1 样例（与录制器模块共享参考）
```

代码风格：black line-length 200 + isort（与 `make fmt` 一致）；日志用 `testzeus_hercules.utils.logger.logger`（禁 print）。

## 1. 输入解析与事实集（events.py）

### 1.1 解析规则

- 输入为 `dict`（含 `session`/`events` 键）或 `list`（裸事件列表；此时 session 视为 `{}`）。
- `events` 必须按 `seq` 升序排序后处理（`seq` 缺失则按数组顺序）；`ts` 不参与逻辑，仅透传。
- 字段访问全部宽松（`dict.get`）：缺字段不报错，走 fallback 链或跳过。
- `type` 不属于 `{navigate, click, input, select, submit}` 的事件：跳过，记 warning `unknown_event_type:<type>`。
- 空事件列表：仍产出合法空骨架（1 Feature 1 Scenario 0 步骤），记 warning `empty_events`。
- 掩码哨兵：`value == "<masked>"`（精确匹配）视为密码/敏感值。

### 1.2 事实集（facts，回查的 ground truth）

在模板生成**之前**从输入事件构建，与骨架文本无关：

| 类别 | 取值 |
|---|---|
| URL | 每个 `event.url`（非空字符串）；外加 `session.origin` |
| 值 | 每个 `event.value`（非空，且 ≠ `<masked>`） |
| hint | 每个 `target.name`、`target.form_label`（非空） |
| 断言 | 每个 `event.dom_snapshot.assert_texts` 元素（非空字符串） |
| 其他 | 每个 `target.ordinal`、`target.tag`、`target.role`、`target.id`、`page_title`（非空，字符串化） |
| 调用方值 | `test_data_values` 的每个值（若提供；掩码占位符填充后的真实值属调用方提供的事实） |

掩码哨兵 `<masked>` 本身不入事实集（它不是事实，见 §2.3）。

## 2. 规则模板层（templates.py）

输出恒为 1 Feature 1 Scenario，措辞统一英文（元素 name 等录制原文保持原语言）。

### 2.1 命名规则（确定性）

- `Feature: Recorded flow on <origin>`；`origin` 缺失 → `Feature: Recorded flow`。
- `Scenario: <第一个事件的 page_title>`；缺失 → `Scenario: Recorded scenario`。
- 标题做单行化（空白归一化）与长度截断（120 字符）。标题行不受事实回查约束（见 §5）。
- **文件名安全清洗（2026-09-21 pilot002 复盘，阶段 1 分析 P1-4）**：标题会被上游用作 JUnit 文件名（`Recorded flow on http://...` 中的 `://` 产生的 `//` 被当作目录分隔符导致写文件失败），故标题仅保留 `[字母数字下划线、CJK、空格、连字符]`，其余连续段折叠为单个 `_`，首尾 `_`/空格剥除，清洗后为空回退默认标题。

### 2.2 规则映射表（每种事件 → Gherkin 骨架）

按 `seq` 顺序遍历事件，每事件先输出自身步骤，再输出其 `assert_texts` 对应的 Then 步骤。**首个** navigate 用 Given 行，其余事件全部用 When/Then 行。

| 事件类型 | 模板 | 变量填充规则 |
|---|---|---|
| navigate（首个） | `Given I am on the page "{url}"` | `url` = `event.url`；缺失 → `session.origin`；再缺 → 跳过该事件 + warning |
| navigate（后续） | `When I navigate to "{url}"` | 同上 |
| click | `When I click on the "{hint}" {role}` | `hint` = `target.name` → `target.form_label` → `target.id` → `target.tag` → `"element"`（取第一个非空）；`role` = `target.role` → `target.tag` → `"element"`；`target.ordinal` 存在且 >1 时模板尾部追加 ` (occurrence {ordinal})` |
| input | `When I enter "{value}" in the "{hint}" field` | `value` = `event.value`（掩码处理见 §2.3）；`hint` = `target.form_label` → `target.name` → `target.id` → `target.tag` → `"field"` |
| select | `When I select "{value}" from the "{hint}" dropdown` | `value` = `event.value`；`hint` = `target.form_label` → `target.name` → `target.id` → `"dropdown"` |
| submit | `When I submit the "{hint}" form` | `hint` = `target.name` → `target.form_label` → `"form"`。（PLAN §3.2 表未覆盖 submit，此为补充定义，schema v1 合法事件类型） |
| submit（紧邻前驱为 click） | 不生成自身步骤 | **click+submit 去重（确定性规则，P0-1）**：录制器对一次 submit 按钮点击恒产出 `click(seq N)` + `submit(seq N+1)` 两条事件（recorder spec §4.5），"紧邻前驱是 click" 即该双事件形态 → 丢弃本 submit 事件的步骤，保留 click 步骤（它带按钮 name hint，下游定位更有价值）。本 submit 的 `assert_texts` 对应的 Then 步骤**照常生成**，位置在该 click 步骤及其 Then 之后。前驱非 click 的 submit（如 Enter 键直接提交，前驱为 input）不适用本行，照常生成步骤。 |
| 任意事件后 | `Then I should see "{text}"` | 对该事件 `dom_snapshot.assert_texts` 中每个非空 text 各生成一行；跨 Scenario 去重（同文本只在首次出现处生成，后续跳过）；保持首次出现顺序 |

规则层**不做**任何合并/省略/改写：连续 input、重复 click 原样逐步输出（这些合并是润色层的职责）。**唯一例外**是上表的 click→submit 去重——它是确定性规则（零模型依赖），因此属规则层，而不是润色层白名单的一部分。

### 2.3 掩码值（密码）填充

- `value == "<masked>"`：生成占位符 `{{TEST_DATA:password_{seq}}}`（`seq` 为该事件 seq；事件无 seq 时用排序后的数组序号）。
- 调用方传入 `test_data_values: Mapping[str, str]` 且含键 `password_{seq}` → 用真实值填充（该值同时入事实集，见 §1.2）。
- 未提供映射 → step 中保留占位符原文（该 step 执行前需人工/编排层填值，属显式 TODO，不算生成失败）。
- 占位符不参与事实回查（见 §5.2 豁免）。

### 2.4 特殊字符处理（填充时一律执行）

- 值内 `"` → 转义为 `\"`；`\` → `\\`（先转 `\` 再转 `"`）。
- 换行/制表符 → 折叠为单个空格；首尾空白 strip。
- 空值（`""` 或全空白）：该步骤仍生成（引号内为空串），并记 warning `empty_value:<seq>`；事实集中空串不入集，但 §5.2 豁免空串，自检不挂。

### 2.5 骨架自检

骨架生成后立即执行 §5 的结构校验 + 事实回查。模板由事实集生成、按构造应恒过；若不过说明校验器/模板有 bug：**不回退、直接抛 `DistillError`**（这是唯一允许骨架层抛错的场景，测试必须覆盖恒过不变量）。

## 3. 公开 API（api.py）

```python
from dataclasses import dataclass, field
from typing import Mapping, Protocol


class DistillError(Exception):
    """骨架层自检失败等不可恢复错误（唯一的主动抛错路径）。"""


@dataclass
class DistillResult:
    feature_text: str            # 最终输出（润色稿或骨架稿）
    skeleton_text: str           # 纯模板稿（永远存在，供测试/调试/回退）
    output_path: str | None      # 仅 distill_file 写文件后非 None
    used_llm_polish: bool        # 润色稿是否最终被采用
    fallback_reason: str | None  # 未采用润色稿时的原因；取值见下
    warnings: list[str] = field(default_factory=list)


class Polisher(Protocol):
    async def polish(self, skeleton_text: str, events: Mapping | list) -> str: ...


def distill_events(
    events: Mapping | list,                      # 解析后的 JSON 对象 / 裸事件列表
    polisher: Polisher | None = None,            # 润色开关：传实例=启用，None=纯模板
    test_data_values: Mapping[str, str] | None = None,  # 掩码占位符 → 真实值
) -> DistillResult: ...


def distill_file(
    events_path: str,                            # 事件 JSON 文件路径
    output_path: str | None = None,              # None → 同目录 <stem>.feature
    polisher: Polisher | None = None,
    test_data_values: Mapping[str, str] | None = None,
) -> DistillResult: ...
```

- `fallback_reason` 取值（字符串前缀约定）：`polish_error:<ExcType>` / `syntax_invalid` / `fact_check_failed:<字面值列表>` / `assertion_dropped:<字面值列表>` / `polish_empty`。
- `distill_events` 内部以 `asyncio.run` 驱动异步润色，对外同步。
- 输入 JSON 文件解析失败（`distill_file`）→ 抛 `DistillError`（文件级错误不属于"输出永远合法"的范围，后者指生成逻辑对任意合法解析结果都产出合法 feature）。

## 4. LLM 润色层（polisher.py）

### 4.1 协议与默认实现

- `Polisher` 协议见 §3。`DefaultPolisher(get_model=...)`：经 `testzeus_hercules.utils.litellm_helper.get_litellm_chat_model` 取弱模型档（role key `distiller_polish`），温度 0，总尝试 ≤2 次（首次 + 1 重试），单次内部超时 60s。任何异常向上抛由 api 层捕获降级——协议实现自身不做回退决策。
- 清洗复用 `testzeus_hercules.utils.gherkin_generator.clean_gherkin_output`（剥围栏/前导）。
- 所有测试走注入 fake；DefaultPolisher 无专属测试（离线约束），仅要求 import 时不产生网络调用。

### 4.2 Prompt 设计要求

System（要点，实现时固化文本）：
1. 角色限定：你只做 BDD 步骤润色，不改测试语义。
2. 白名单动作：合并**连续且同一目标字段**的 input 步骤（保留最终值）；改善措辞；重命名 Feature/Scenario 标题（英文）。
3. 硬约束（逐条列出）：不得增删任何 `Then` 步骤；不得新增/修改/删除任何引号内字面值；不得改变步骤顺序与 Given/When/Then 语义；输出恰好 1 个 Feature、1 个 Scenario；只输出 feature 文件本体，无解释无围栏。
4. 输入：骨架 feature 全文 + 紧凑事件清单（每行 `seq|type|target.name|value`，供合并判断）。

### 4.3 润色产物校验（全部通过才采用，否则回退骨架）

依次执行，任一失败即整体丢弃润色稿：
1. `clean_gherkin_output` 清洗后非空；
2. 结构校验（§5.1）通过：恰 1 个 Feature 行、1 个 Scenario 行、行首关键词合法；
3. 事实回查（§5.2）通过：润色稿每个引号字面值 ⊆ 事实集；
4. 断言存活检查：骨架中所有 Then 步骤的引号字面值，在润色稿中仍以引号字面值形式存在（exact 或 ⊆ substring 匹配，同 §5.2 规则）。

## 5. 事实回查算法（factcheck.py，精确定义）

### 5.1 结构校验（依赖-free，正则实现；venv 无 gherkin 解析器，不新增依赖）

对逐行处理：**每行先 strip（去除行首及行尾空白）再做下述锚定与关键词检查**，含 `Feature:`/`Scenario:` 计数（LLM 润色稿普遍带缩进，`clean_gherkin_output` 不做缩进归一化，校验端必须自行 strip，否则缩进产出会被系统性误判非法）。忽略空行与 `#` 注释行：
- 首个非空非注释行必须以 `Feature:` 开头；全文恰一个 `Feature:` 行、恰一个 `Scenario:` 行（正则 `^Feature:` / `^Scenario:`，忽略大小写）；
- 其余每行必须以 `Feature:` / `Scenario:` / `Given ` / `When ` / `Then ` / `And ` / `But ` 开头（忽略大小写）；
- 违反任一条 → 结构非法。

### 5.2 字面值提取与匹配

- **字面值定义**：步骤行（§5.1 strip 后以 Given/When/Then/And/But 开头的行）中正则 `"((?:[^"\\]|\\.)*)"` 的捕获内容，提取时做 §2.4 的反转义（`\\`→`\`、`\"`→`"`）。Feature/Scenario 标题行、注释行不是字面值来源。
- **匹配规则**：字面值 `L` grounded ⇔ 存在事实 `F` 使 `norm(L) == norm(F)` 或 `norm(L)` 是 `norm(F)` 的子串；`norm(s)` = 反转义后空白归一化（连续空白折叠为单空格、strip），大小写敏感。允许子串方向 `L ⊆ F` 是为容忍润色截取（如断言长句取短语）。
- **豁免（不算字面值，不参与校验）**：`{{TEST_DATA:*}}` 占位符；空串；模板静态兜底词 `element` / `field` / `dropdown` / `form`（§2.2 各 fallback 链末端的写死常量，非录制事实，豁免后 §2.5"任意输入恒过"不变量才成立）。
- **事实集**：§1.2。ordinal 等数值一律字符串化入集（如 `2`）。

### 5.3 回退行为（api.py 统一执行）

- 润色稿任一校验失败 → `feature_text = skeleton_text`，`used_llm_polish = False`，`fallback_reason` 按 §3 约定，原因中的字面值列表最多列 5 个。
- 骨架稿是唯一回退目标；不存在"部分采用润色稿"。
- skeleton 自检（§2.5）失败是程序 bug → 抛 `DistillError`，静默兜底会掩盖 bug，不允许。

## 6. 测试方案（纯 pytest 离线；每条：名称 / 输入 / 预期）

A 组 规则映射（test_templates.py）：
1. `test_first_navigate_is_given` / 单 navigate 事件 / 产出 `Given I am on the page "<url>"` 行
2. `test_second_navigate_is_when` / [navigate, navigate] / 第二条为 `When I navigate to "<url>"`
3. `test_click_uses_name_and_role` / click(name=提交订单, role=button) / `When I click on the "提交订单" button`
4. `test_click_role_fallback_to_tag` / click(name=x, 无 role, tag=div) / `When I click on the "x" div`
5. `test_click_hint_fallback_chain` / click(无 name，form_label=搜索) / hint=搜索
6. `test_click_ordinal_appended` / click(name=Delete, ordinal=2) / 行尾含 `(occurrence 2)`；ordinal=1 不追加
7. `test_input_uses_form_label` / input(value="wireless headphones", form_label=搜索) / `When I enter "wireless headphones" in the "搜索" field`
8. `test_input_hint_fallback_to_name` / input(无 form_label, name=email) / hint=email
9. `test_masked_input_becomes_placeholder` / input(value="<masked>", seq=7) / 引号内为 `{{TEST_DATA:password_7}}`
10. `test_masked_input_filled_from_test_data` / 同上 + `test_data_values={"password_7": "s3cret"}` / 引号内为 `s3cret`
11. `test_select_skeleton` / select(value=Blue, name=Color) / `When I select "Blue" from the "Color" dropdown`
12. `test_submit_skeleton` / submit(name=提交订单) / `When I submit the "提交订单" form`
13. `test_assert_texts_become_then_steps` / 事件含 assert_texts=[a,b] / 之后依序两行 `Then I should see`
14. `test_assert_texts_deduped` / 两事件含相同 assert text / 该文本只出现一次（首次位置）
15. `test_unknown_event_type_skipped` / 含 type="hover" / 无对应步骤，warnings 含 `unknown_event_type:hover`
16. `test_empty_events_still_valid` / events=[] / 合法 Feature+Scenario 零步骤，warning `empty_events`
17. `test_feature_scenario_naming_rule` / session.origin + 首事件 page_title / 标题按 §2.1 规则生成

B 组 事实回查（test_factcheck.py）：
18. `test_skeleton_always_passes_selfcheck` / 全部 fixtures / 骨架自检恒过（不变量）
19. `test_ungrounded_literal_detected` / 手写含未录制字面值的 gherkin / 校验器报出该字面值
20. `test_substring_match_passes` / 字面值 `order placed` ⊆ 事实 `Your order has been placed successfully` / 通过
21. `test_whitespace_insensitive_matching` / 事实 `a  b` vs 字面值 `a b` / 通过
22. `test_quote_escaping_roundtrip` / 值含 `"` 与 `\` / 输出正确转义，回查器反转义后通过
23. `test_placeholder_and_empty_exempt` / 占位符与空串字面值 / 不判违规

C 组 润色与回退（test_polish_fallback.py，全部注入 FakePolisher）：
24. `test_polish_used_when_valid` / fake 返回合法且 grounded 的改写稿 / `used_llm_polish=True`
25. `test_fallback_on_ungrounded_literal` / fake 注入 `http://evil.example` / 回退骨架，reason 前缀 `fact_check_failed`
26. `test_fallback_on_syntax_invalid` / fake 返回 `not gherkin` / 回退，reason=`syntax_invalid`
27. `test_fallback_on_polish_exception` / fake 抛 RuntimeError / 回退，reason=`polish_error:RuntimeError`，异常不外泄
28. `test_fallback_on_assertion_dropped` / fake 删除一个 Then 字面值 / 回退，reason 前缀 `assertion_dropped`
29. `test_no_polisher_no_llm` / polisher=None / `used_llm_polish=False`，无任何 LLM 调用路径（fake 未被构造）
30. `test_merge_is_polisher_only` / fake 把两步合并为一步且回查可过 / 采用润色稿（证明合并不被骨架层/回查层误杀）

D 组 API 与端到端（test_api_e2e.py）：
31. `test_end_to_end_sample_flow` / fixtures/login_search.json（navigate→input→masked input→click→assert→navigate→select→click） / feature 含全部 hint 与断言、掩码为占位符、1 Feature 1 Scenario
32. `test_distill_file_default_output_path` / events_path + output_path=None / 写入同目录 `<stem>.feature`，result.output_path 正确
33. `test_distill_file_bad_json_raises` / 非法 JSON 文件 / 抛 `DistillError`
34. `test_hercules_helper_integration` / 生成 feature → `gherkin_helper.split_feature_file` / 返回恰 1 条 scenario 记录（上游消费冒烟）
35. `test_deterministic_output` / 同输入跑两遍（polisher=None） / `feature_text` 逐字节相等

## 7. 验收标准（可机械检查）

1. `uv run pytest tests/record2gherkin -q` 全绿，全程无网络依赖（无 LLM key、无浏览器）。
2. fixtures 样例生成的 feature：恰 1 个 `Feature:` 行、1 个 `Scenario:` 行，且能被 `split_feature_file` 解析出恰 1 条记录（用例 34）。
3. 确定性：同输入 + polisher=None 两次运行输出逐字节相同（用例 35）。
4. 骨架路径零模型：`templates.py`/`events.py`/`factcheck.py` 不 import litellm/网络模块（用 grep 机械检查 + 用例 29）。
5. groundedness：所有 fixtures 的骨架输出过事实回查（用例 18）；润色稿未过即回退（C 组）。
6. `make fmt` 后 `black --check record2gherkin tests/record2gherkin`（line-length 200）通过。
7. 敏感值：任何输出/测试中不出现 `<masked>` 字面值进入最终 feature（掩码只允许以占位符或填充值形式出现）。

## 8. Out of Scope

- 多 Feature / 多 Scenario 切分、跨录制流程合并、Background/Examples/Outline。
- 中文或双语 Gherkin 模板措辞（骨架统一英文；元素录制名保持原语言）。
- 复杂断言推理：比较/数值/顺序/状态类断言、从 dom_snapshot 之外推断断言。
- 录制数据自动参数化、数据抽取、CSV/数据驱动。
- 引擎侧 test_data 自动注入机制（本模块只产出 `{{TEST_DATA:*}}` 占位符 + 生成期可选值替换）。
- 除 navigate/click/input/select/submit 外的事件类型语义（未知类型一律跳过+warning）。
- 跨域 iframe、closed shadow DOM、canvas、多 tab（录制层 scope out 的下游继承，不在此补救）。
- 润色层模型路由调优、prompt 工程精细化（先跑通，弱模型档即可）。
- 性能优化（万级事件流）、并发/批量生成 CLI（属编排 CLI 模块）。
- `review.md` 之外的文档产出（test-report.md 在实现完成后由实现代理写）。

## 9. 遗留决策点（实现期按测试暴露再补，不预先设计）

- `submit` 在真实录制中的形态（Enter 键 vs 按钮 click）若与骨架不符，依蒸→跑迭代数据调整模板。
- assert_texts 数量爆炸（>20 条/事件）时的截断策略：当前不去量、只去重，留给评测数据决定。
