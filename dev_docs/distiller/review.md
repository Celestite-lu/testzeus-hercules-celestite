# 蒸馏器 spec 独立审查（review.md）

> 审查对象：`dev_docs/distiller/plan.md`、`dev_docs/distiller/spec.md`
> 契约基准：`PLAN.md` §3.1（事件 schema v1）、§3.2（蒸馏规则）；交叉核对 `dev_docs/recorder/spec.md`
> 下游口径：`testzeus_hercules/utils/gherkin_helper.py:102-134`（serialize_feature_file 原文压缩给 planner）、`split_feature_file`
> 审查纪律：只拦会导致核心目标失败/契约破裂/无法按期实现的问题；已实际核读 `utils/gherkin_generator.py`（clean_gherkin_output）与 `litellm_helper.py`（get_litellm_chat_model）源码验证 spec 引用锚点。

## 结论

**REVISE**（必改项 2 条；均为 spec 层一行级修改，不动架构、不影响排期）

---

## 必改项清单

### 必改 1：模板静态兜底字面值不在事实集、也不在豁免清单 → 缺 target 输入必触发 DistillError，直接违反核心目标推论 2

- **问题**：spec §2.2 各模板的 hint 兜底词是写死的引号字面值——click → `"element"`、input → `"field"`、select → `"dropdown"`、submit → `"form"`。spec §5.2 的字面值豁免只有 `{{TEST_DATA:*}}` 占位符与空串；§1.2 事实集只收录制事实（url/value/name/form_label/assert_texts/tag/role/id/ordinal/page_title/调用方值）。因此当事件的 `target` 缺失或全空时，骨架步骤会带一个未 grounding 的引号字面值 → 按 §2.5 骨架自检失败 → **抛 `DistillError`**。
- **依据**：`plan.md` §1 推论 2：「任何输入（空事件、未知事件类型、**缺失字段**）都必须产出结构合法的 feature，绝不抛异常中断」；plan §4：「缺字段走 fallback 链……schema 漂移不致挂」。target 缺失就是"缺失字段"，当前 spec 下该输入类必崩。真实可达：schema 漂移、手写 fixtures（plan §4 明确双方 fixtures 先行手写，手写 `"target": null` 的 click/input 很自然）、submit 事件 target=null（录制器 §4.5 的 submit name "通常取不到为空串"，仅因 tag="form" 恰好入事实集才在正常录制下侥幸通过）。§2.5 设计意图是"校验器 bug 才触发"，但当前规则使它在合法输入上必然触发，自检不再是纯 bug 探测器。
- **具体改法**：在 spec §5.2 豁免清单增加第三条：「模板静态兜底词（`element` / `field` / `dropdown` / `form`）视为模板常量，不参与事实回查」。不推荐改为把这四个词加进 §1.2 事实集（污染 ground truth 语义）。改完后可复核：模板全部引号字面值 = 事实集成员 或 豁免项，§2.5 的"恒过不变量"才对**任意**输入成立，DistillError 回归纯 bug 探测器定位。

### 必改 2：§5.1 结构校验按 `^` 行首锚定、未规定先 strip 行首空白 → LLM 缩进产出被系统性判非法，润色层在生产上永远回退（审查指令点名的"误杀正常输出"模式）

- **问题**：spec §5.1 写明正则 `^Feature:` / `^Scenario:` 且「其余每行必须以 Given / When / Then / And / But 开头」，未规定处理行首空白。LLM 润色稿普遍给 Scenario/步骤行加缩进；缩进行不匹配 `^` 锚定 → Scenario 计数为 0 或关键词检查失败 → 判 `syntax_invalid` → 整体回退骨架。已核实 `clean_gherkin_output`（`utils/gherkin_generator.py:11-22`）只剥围栏和 Feature: 前导，**不做内部缩进归一化**，救不了场。后果：离线测试全绿（FakePolisher 由测试者手写、天然顶格），生产 DefaultPolisher 却几乎每次被回退——润色层形同虚设，且这种回退不会被 35 条用例暴露。
- **依据**：plan §2「润色层'永远可用'的保证不在 prompt，而在回查层」——回查层自身的措辞性误杀与该保证矛盾；plan 核心目标里润色层是"纯加法"，不应因格式偏好失效。骨架侧模板顶格输出不受影响，问题只出现在润色稿路径。
- **具体改法**：spec §5.1 加一句：「所有行先 strip 行首（及行尾）空白后再做锚定与关键词检查（Feature:/Scenario: 计数同理）」。可同时（可选）在 §4.2 prompt 硬约束里要求顶格输出，双保险；校验端 strip 是必须的。

---

## 契约交叉核对结果表（字段级）

判定说明：✅ 一致 / ✅* 宽松超集（蒸馏器比契约更宽容，合法）/ ⚠ 注记（不构成漂移，见备注或建议区）

| 契约项 | PLAN §3.1 / 录制器 spec | 蒸馏器 spec | 判定 |
|---|---|---|---|
| 顶层结构 `{session, events}` | PLAN §3.1 = 录制器 §1 | §1.1：dict 或裸 list（session 视为 `{}`） | ✅* |
| `session.origin` | 录制器 §1，string | §1.2 入事实集；§2.1 Feature 命名；navigate url 兜底 | ✅ |
| `session.started_at` | 录制器 §1 | 未消费 | ✅（不消费即可） |
| `seq` | int，从 1 严格递增（录制器 §2） | 排序键，缺失按数组顺序 | ✅ |
| `ts` | epoch ms | 不参与逻辑仅透传 | ✅ |
| `type` 枚举 | `navigate/click/input/select/submit`，不新增（两边一致） | 5 值白名单，未知跳过 + warning `unknown_event_type` | ✅ |
| `url`（事件级） | 所有事件均有；navigate 取跳转后（录制器 §2） | 仅 navigate 模板消费；缺失 → origin → 跳过 | ✅ |
| `page_title` | 可为空串（录制器 §2） | Scenario 命名 + 入事实集 | ✅（空串 vs 缺失未言明，见建议 2） |
| `target.tag` | 恒有（录制器 §2.1） | click hint/role 兜底 + 事实集 | ✅ |
| `target.role` | 显式 role → 隐式映射 → `"generic"`（录制器 §2.1.1） | click 模板 role 槽（未加引号，不参与回查） | ✅ |
| `target.name` | 5 级优先序，可空串，≤200 字符（录制器 §2.1.2） | click hint 首选 + 事实集 | ✅ |
| `target.testid` | string/null | 未消费 | ✅（不消费即可） |
| `target.id` | string/null（空串视为 null） | click/input/select hint 链 + 事实集 | ✅ |
| `target.ordinal` | 同名元素 >1 才写，否则 null（录制器 §2.1.3） | `>1` 才追加 `(occurrence {n})`；ordinal=1 不追加（用例 6） | ✅（录制器点重复集第一个会写 ordinal=1，蒸馏器不追加，行为正确且防御一致） |
| `target.form_label` | 仅 input/textarea/select，取不到 null（录制器 §2.1） | input hint 首选、click/select 链 + 事实集 | ✅ |
| `value` 常规 | input/select 有值；checkbox/radio `"true"/"false"`；其余 null（录制器 §2.2） | input/select 模板；空串仍出步骤 + warning | ✅ |
| `value == "<masked>"` | PLAN：记 `<masked>` 并引用 test_data；录制器：password 恒字面量 `"<masked>"` | 精确匹配 → `{{TEST_DATA:password_{seq}}}`；`test_data_values` 可填真值且真值入事实集 | ✅（占位符豁免回查、验收标准 7 禁 `<masked>` 泄漏，闭环完整） |
| `value == "<file>"` | 录制器 §2.2：file input 恒 `"<file>"` | **未特判**，按普通字面值进步骤 | ⚠ 见建议 1（输出仍合法，不阻塞） |
| `dom_snapshot.assert_texts` | 数组，≤8 条、每条 ≤120 字符、差集语义；采集失败 `{"assert_texts": []}`；初始 navigate snapshot=null（录制器 §3/§4.6） | 每个 assert_texts → `Then I should see`，宽松访问，跨 Scenario 去重 | ✅ |
| **submit 事件** | PLAN §3.2 表未列（§3.1 枚举含 submit）；录制器 §4.5：target 指向 `<form>`，name 通常空串，value=null | §2.2 补充模板 `When I submit the "{hint}" form` 并**显式标注**为补充定义；§9 列为遗留决策点 | ✅（无静默漂移；与录制器"click+submit 各一条"的约定兼容——双步骤冗余属蒸→跑迭代线，spec §9 已认领） |
| 未知 type 处理 | PLAN 未定；录制器枚举封闭 | 跳过 + warning | ✅* |
| select hint 取向 | PLAN §3.2 表示意 `<name>` | §2.2 取 `form_label` 优先 | ⚠ 模板措辞细化（PLAN 未定义 fallback 链，两者均为录制事实、引擎均可定位），不算 schema 漂移 |
| 下游消费 | `gherkin_helper.py:102-134`：feature 原文压缩（`";next;"`、连续空格折叠）给 planner | hint 直接引用录制 name/label（与引擎感知属性同源）；factcheck `norm` 空白归一化与 `serialize_feature_file` 的空格折叠兼容 | ✅ |
| 语法校验方式 | PLAN §3.2「可用 gherkin_helper 已有解析做语法校验」 | §5.1 自带正则校验（venv 无 gherkin 解析器），另用例 34 接 `split_feature_file` 冒烟 | ✅（"可用"非"必须"，等价细化） |

---

## 建议不阻塞项（≤3）

1. **`"<file>"` 哨兵**：录制器 §2.2 对 file input 恒发 `"<file>"`，蒸馏器会把它当普通值写进 `When I enter "<file>" ...`。建议 §1.1 加一句明确处置（照普通值透传，或与 `<masked>` 同路改占位符）——当前行为输出合法、可执行期再迭代，但应写死避免实现者自行发挥。
2. **空串 vs 缺失**：`page_title=""`、`url=""`、`target.name=""` 在各 fallback 链中是"缺失"还是"原样填充"未言明（如 `Scenario: ` 空标题仍合法，但两条路径 warning 不同）。建议 §1.1 加一句「所有字符串字段空串/全空白一律视为缺失，走 fallback 链」。
3. **§4.3.4 断言存活匹配方向**：「exact 或 ⊆ substring 匹配，同 §5.2 规则」中 ⊆ 的方向（润色字面值 ⊆ 骨架 Then 字面值，即只许缩短不许加长）是唯一合理解读，但建议把主语写明，避免实现者做成双向子串（双向会把"润色稿加长断言"也放行，削弱硬闸）。

附注（范围外，不计数）：新顶层目录 `record2gherkin/` 不在 Makefile `fmt/lint` 的 black 目标内（Makefile 只覆盖 `testzeus_hercules/`、`tests/`）；spec 验收标准 6 已给出显式命令，可自行闭环，仅需注意 CI 不自动覆盖。

---

## 审查清单覆盖说明

1. **契约一致性**：见上表。submit、`<masked>`、枚举、字段名均一致；无静默漂移。
2. **确定性架构**：规则层零模型依赖成立（§0 禁 import + 验收 4 grep 机械检查 + DefaultPolisher 隔离在 polisher.py 且要求 import 无网络）。回退路径完整（5 种失败原因全部收敛骨架 + fallback_reason；`polish_empty` 对应 §4.3 校验 1，无遗漏）。唯一裂缝即必改 1（缺 target 输入触发 DistillError）；修复后"任意输入恒产合法 Gherkin"才真正成立。`distill_file` 坏 JSON 抛 DistillError 已在 §3 显式划界，接受。
3. **事实回查算法**：字面值定义（正则 + 反转义）、匹配规则（exact 或 L⊆F、空白归一化、大小写敏感）精确可实现；转义/反转义往返自洽（§2.4 与 §5.2 对称，用例 22 覆盖）。误杀风险两处：必改 2（缩进）为系统性误杀；必改 1 的兜底词在自愈修复前会以崩代退。合并 input 保留最终值方向安全（回查只要求输出 ⊆ 事实，不要求事实全存活）；标题行豁免与单行化防住了 page_title 换行注入。
4. **可测试性**：35 条用例全部纯离线可跑——FakePolisher 注入、DefaultPolisher 零测试路径、用例 34 只用 `split_feature_file`（已核实其不触 `get_global_conf()`，`aiofiles` 为既有依赖，无网络/浏览器/LLM key）。
5. **spec 完备性**：主链路（映射表、填充规则、豁免、回退原因枚举、API 签名、验收标准）读之即可实现、无需设计决策；残留微决策（建议 1-3 及 None-vs-缺失等）均被 spec 头部"未规定处按最简单确定性行为处理并记 warnings"兜住。
