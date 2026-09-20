# 录制器 spec 独立审查（review.md）

> 审查对象：`dev_docs/recorder/plan.md`、`dev_docs/recorder/spec.md`
> 契约基准：`PLAN.md` §3.1（Schema v1）；交叉核对 `dev_docs/distiller/spec.md` 输入契约（§0–§2.3）；Hercules 感知口径对照 `testzeus_hercules/utils/get_detailed_accessibility_tree.py`
> 审查纪律：只拦"核心目标失败 / 契约破裂 / 排期不可实现"级别的问题。

## 结论：**REVISE**

必改项 1 条（spec 内部矛盾导致实现者仍需做设计决策，且影响用例 1/17 断言的可写性）。契约本身（对 PLAN §3.1 与蒸馏器 spec）逐字段核对无漂移；改完此条即可转 PASS。

---

## 必改项清单

### 必改 1：初始 navigate 的 `dom_snapshot` 三处定义互相矛盾，实现者无法同时满足

**问题描述**：spec 内三个位置对"初始 navigate（start() 产生的 seq=1 事件）的 dom_snapshot"给出互斥规定：

| 位置 | 说法 |
|---|---|
| spec.md §2 事件示例 JSON（:28-31） | `"dom_snapshot": null` |
| spec.md §4.6.1（:158） | "target/value/**snapshot 为 null**（无 assert 基线）" |
| spec.md §2 字段表 dom_snapshot 行（:43） | "navigate/click/select/submit/input **有**，采集失败时为 `{"assert_texts": []}`，**整字段不省略**" |
| spec.md §3（:121） | "start() 时抓一次初始文本作为基线……因此**首个 navigate 事件的 assert_texts 为空数组**"（即字段存在且为对象） |

§4.6.1/§2 示例（null，旧设计"无基线"）与 §2 表/§3（恒存在对象 + start() 抓基线）是两代设计残留，未收敛。

**依据**：spec.md 头部自述目标"实现者读完不再需要做设计决策"（:3），此矛盾直接违背；用例 1（events[0] 断言）与用例 17（"每条事件必含 dom_snapshot 键"）的断言值形态取决于这个未决决策——按"恒为对象"写的契约校验会挂掉按 null 实现的录制器，反之亦然。下游蒸馏器 spec §1.1 宽松解析虽两种都能消化（不破契约），但录制器 spec 必须唯一确定。

**具体改法**：统一为「`dom_snapshot` 字段恒存在、恒为对象（不为 null、不省略）；初始 navigate 的 `dom_snapshot = {"assert_texts": []}`」。同步修改两处：
1. §2 示例 JSON 中 `"dom_snapshot": null` → `{"assert_texts": []}`；
2. §4.6.1 "target/value/snapshot 为 null" → "target/value 为 null；dom_snapshot = `{"assert_texts": []}`"。
§2 表与 §3 不动。改后与"整字段不省略"、用例 17 的键存在性断言自洽。

---

## 契约交叉核对结果表（字段级）

| 字段 / 约定 | PLAN §3.1 | recorder spec | distiller spec（消费方） | 判定 |
|---|---|---|---|---|
| `session.started_at` | 存在 | ISO 8601 UTC，`start()` 时刻（§1） | 未消费（透传） | 一致 |
| `session.origin` | 存在 | `location.origin`@start（§1） | Feature 命名 + navigate url 兜底（§2.1/§2.2） | 一致 |
| `events[].seq` | 存在 | int，1 起严格递增不重置（§2） | 按 seq 升序处理；掩码占位符 `password_{seq}` | 一致 |
| `ts` | epoch ms 示例 | int，`Date.now()`，监听器收到事件时刻（§2） | 仅透传不参与逻辑（§1.1） | 一致 |
| `type` 枚举 | `navigate/click/input/select/submit` | 同枚举，不新增（§2） | 同集合，未知类型跳过 + warning（§1.1） | 一致 |
| `url` | 存在 | `location.href`；navigate 取跳转后 URL（§2） | Given/When 模板 + 事实集 | 一致 |
| `page_title` | 存在 | `document.title`，可为空串（§2） | Scenario 命名 + 事实集 | 一致 |
| `target.tag/role/name/testid/id/ordinal/form_label` | 7 字段示例 | 7 字段逐一定义（§2.1），无增删 | hint/role 兜底链、`(occurrence n)`、事实集全消费 | 一致 |
| `target.name` 优先级 | aria-label / associated label / placeholder / innerText / title（契约顺序） | 同序，声明"顺序不可改"（§2.1.2） | 不重复定义，按 name 消费 | 一致（见备注 a） |
| `ordinal` | 同名元素序号 | 同 name 集合 >1 才写，1-based（§2.1.3） | 存在且 >1 追加 `(occurrence n)` | 一致（录制器仅在 >1 写 ⇒ 蒸馏器条件恒兼容） |
| `value` 规则 | string | 文本原样 ≤500 / password=`"<masked>"` / checkbox=`"true"/"false"` / file=`"<file>"` / select=option 文本 ≤200（§2.2） | input/select 模板填充；掩码**精确匹配** `"<masked>"`（§1.1/§2.3） | 一致 |
| 掩码约定 | `<masked>` + Gherkin 引用 test_data | 恒字面量 `"<masked>"`，绝不记真实值（§2.2/§4.3） | `{{TEST_DATA:password_{seq}}}` 占位符机制（§2.3） | 一致 |
| `dom_snapshot.assert_texts` | 事件后可见文本，断言 grounding | 差集采集、≤8 条、每条 ≤120 字符、字段恒有键（§3，**除必改 1 的矛盾**） | 逐条 `Then I should see` + 事实集；蒸馏器 20 条警戒线 > 录制器 8 条上限 | 一致 |
| Snapshot 结构 | 仅 `assert_texts` 一键 | 同（§3 输出） | 仅按 `assert_texts` 消费 | 一致 |
| 去噪 | hover/scroll 忽略；change/blur 记最终值 | §4 全集（focusout=捕获安全的 blur，等价实现） | 规则层不合并，润色层合并连续 input | 一致 |
| 输入顶层结构 | `{session, events}` dict | `getJSON()` 返回该结构（§1） | 接受 dict 或裸 list（§1.1，超集） | 一致 |

**备注 a（口径事实，非录制器漂移）**：Hercules `getAccessibleName`（get_detailed_accessibility_tree.py:1050-1087，行号引用核实无误）实际手动链为 aria-label → aria-labelledby → **alt → title → placeholder** → input value → innerText，与契约顺序（placeholder 先于 title）不同且多 alt/value 两级。契约是唯一权威，录制器/蒸馏器均遵契约，无三方漂移；该顺序差异是 PLAN 层既定决策，本审查不改为必改。spec/plan 引用的"属性集合同源"表述基本成立（`label[for]`/包裹 label 为录制器增强，Hercules 手动链无此项，但计算名路径隐含）。

---

## 建议不阻塞项（≤3 条）

1. **测试环境前置写进 plan**：实测当前 `.venv` 的 playwright（pin ≤1.49）要求 `chromium_headless_shell-1148`，但本机缓存只有 `chromium-1243`（由其他 playwright 版本装入），首次跑会报 `Executable doesn't exist`。D1 开工前需 `uv run playwright install chromium`（一次性，需网络）；plan.md "uv 环境已有 playwright" 仅对 Python 包成立。顺带：D1 冒烟先验证 file:// 下 `history.pushState`（本审查因浏览器缺失未能实测；Chromium 对 file:// 同文档 pushState 的既有行为是可用，且 spec 同时监听 hashchange，兜底路径存在）。
2. **fixture 清单与用例清单对齐**：spec §7 fixture 设计未列 checkbox，但用例 4（外层 label 包裹 checkbox）与用例 8（checkbox value="true"）依赖它；用例 15 的 ">120 字符新文本" 未指明 fixture 支撑点（可用 `page.evaluate` 注入实现，建议写明）。补两行 fixture 说明即可，不影响排期。
3. **两处措辞与事实的偏差**：(a) §2.1.1 role 隐式映射表与 Hercules `getRole`（:1098-1148）词表不同（select: combobox vs **listbox**；input[type=search]: textbox vs searchbox；日期类: textbox vs combobox），契约未约束 role 词表，建议注明"role 为 ARIA 近似词表，定位以 name/hint 为主"或直接对齐 getRole；(b) §4.5 "form name 通常取不到为空串" 预测不准——`<form>` 有 innerText（整块表单文本），按 §2.1.2 会取到截 200 字符的文本块，建议如实写明该行为（下游润色层可消化）。

---

## 审查范围说明

- 可实现性：3 天排期成立。零构建链成立（build_bookmarklet.py 纯标准库，recorder.js 单 IIFE 无模块语法 ⇒ §6 "若源码已是 IIFE 则直接用"与用例 20 "解码后与源码一致"自洽）；测试形态 pytest+playwright 离线可行（前置见建议 1）。
- 可测试性：20 条用例均有明确操作与断言，可机械执行；fixture 覆盖审查要求的关键字段（label/placeholder/aria-label/password/select/同名按钮/导航）✓（缺口见建议 2）。
- spec 完备性：除必改 1 外无未决设计决策（input 事件 ts 取 change 还是 focusout 时刻属微决策，ts 下游仅透传，不构成阻塞）。
- 验收命令核实：§8.4 的 `grep -c "require(\|import "` 在 macOS BSD grep 下实测有效（BRE `\|` 交替可用，阳性样本计数 2），非空转。
- Out of Scope（open shadow DOM、跨文档导航杀死录制器等）均为文档化已知限制，按纪律不 challenge。
