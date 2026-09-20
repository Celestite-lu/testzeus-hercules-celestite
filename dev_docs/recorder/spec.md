# 录制器 spec.md

> 契约基线：PLAN.md §3.1 事件 Schema v1。已有字段名与语义不得改动（蒸馏器并行开发中）。本文档目标：实现者读完不再需要做设计决策。
> 事件类型枚举、字段名、name 优先级、去噪原则均以 PLAN §3.1 为准；本文允许细化与新增字段。

## 1. 顶层结构

```json
{
  "session": { "started_at": "2026-09-20T12:00:00.000Z", "origin": "https://staging.example.com" },
  "events": [ /* Event 对象，见 §2 */ ]
}
```

- `session.started_at`：`string`，ISO 8601（UTC，`new Date().toISOString()`），`start()` 时刻。
- `session.origin`：`string`，`location.origin`，`start()` 时刻。
- `R2GRecorder.getJSON()` 返回以上结构的 JSON 字符串（2 空格缩进）。

## 2. Event schema（逐字段定义）

```json
{
  "seq": 1,
  "ts": 1695000000123,
  "type": "navigate",
  "url": "https://staging.example.com/search",
  "page_title": "Staging Store",
  "target": null,
  "value": null,
  "dom_snapshot": { "assert_texts": [] }
}
```

| 字段 | 类型 | 含义 | 备注 |
|---|---|---|---|
| `seq` | int | 事件序号，从 1 起严格递增 | 跨类型连续编号，不重置 |
| `ts` | int | 事件发生时刻，epoch 毫秒（`Date.now()`） | 取监听器收到事件的时刻，非入队时刻 |
| `type` | string | 枚举：`navigate / click / input / select / submit` | 不新增枚举值 |
| `url` | string | 事件发生时的 `location.href` | navigate 事件取跳转后的 URL |
| `page_title` | string | 事件发生时的 `document.title` | 可为空串 |
| `target` | Target/null | 事件目标元素的语义描述 | navigate 恒为 null；submit 时指向 form 元素 |
| `value` | string/null | 事件附带的值 | 仅 input/select 有值，其余 null；规则见 §2.2 |
| `dom_snapshot` | Snapshot | 事件后采集的可见文本差集，字段恒存在（所有事件类型，含初始 navigate） | 结构见 §3；无 assert 基线（初始 navigate）或采集失败时为 `{"assert_texts": []}` |

### 2.1 Target 对象

```json
{
  "tag": "button",
  "role": "button",
  "name": "提交订单",
  "testid": "submit-order",
  "id": "submit-btn",
  "ordinal": 2,
  "form_label": "收货信息"
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `tag` | string | `element.tagName.toLowerCase()` |
| `role` | string | 显式 `role` 属性优先；否则查隐式映射表（§2.1.1）；查不到为 `"generic"` |
| `name` | string | 语义名，取值优先级见 §2.1.2；截取首行、trim、压缩连续空白、上限 200 字符 |
| `testid` | string/null | `data-testid` 属性，无则 null |
| `id` | string/null | `element.id`，空串视为 null |
| `ordinal` | int/null | 同名元素序号，规则见 §2.1.3；唯一或 name 为空时 null |
| `form_label` | string/null | 仅 input/textarea/select 有值：关联 label 文本（`label[for]` / `aria-labelledby` 指向元素 / 包裹型 `closest("label")`），三者按此顺序取首个命中；其余元素 null；取不到 null |

#### 2.1.1 role 隐式映射表（最小集）

| 元素 | role |
|---|---|
| `a[href]` | `link` |
| `button`, `input[type=submit|button|reset]` | `button` |
| `input[type=text|search|email|url|tel|password|number|date|...]`, `textarea`（非 checkbox/radio） | `textbox` |
| `input[type=checkbox]` | `checkbox` |
| `input[type=radio]` | `radio` |
| `select` | `combobox` |
| 其余 | `generic` |

#### 2.1.2 name 取值优先级（契约，顺序不可改）

1. `aria-label` 属性
2. associated label（`label[for=id]` / `aria-labelledby` 指向元素的 innerText / 包裹型 `closest("label")` 的 innerText）
3. `placeholder` 属性
4. `innerText`（`input`/`textarea`/`select` 无 innerText，天然跳过）
5. `title` 属性

全部未命中 → `""`（空串；此时 ordinal 必为 null）。文本清洗与 name 相同：首行、trim、压缩连续空白、≤200 字符。
（属性集合与 `utils/get_detailed_accessibility_tree.py:1050-1087 getAccessibleName` 同源，保证下游引擎能按同样的语义属性定位；顺序按 PLAN §3.1 契约执行。）

#### 2.1.3 ordinal（同名元素序号）

- 定义：对当前元素计算 name 后，若 name 非空，用 `document.querySelectorAll(tag)` 过滤出"计算出的 name 与当前元素相同"的元素集合（用同一套 §2.1.2 逻辑逐个计算），当前元素在该集合中的 1-based 序号。
- 集合长度 > 1 才写 `ordinal`，否则 null（单元素不产生噪音字段）。
- 不做 CSS path / nth-of-type 等定位描述——定位交给下游语义引擎，ordinal 只用于消歧。

### 2.2 value 字段规则

| 事件 | value |
|---|---|
| input（文本类 input/textarea） | 最终值原样（不 trim 内部空白，去首尾换行），上限 500 字符 |
| input[type=password] | 恒为字面量 `"<masked>"`，绝不记录真实值 |
| input[type=checkbox]/[type=radio] | `"true"` / `"false"`（checked 状态） |
| input[type=file] | 恒为 `"<file>"`（文件名不入流） |
| select | 被选中 `<option>` 的可见文本（innerText），≤200 字符 |
| navigate / click / submit | null |

## 3. dom_snapshot（assert_texts 采集规则）

**触发时机**：每个有效事件（见 §4 触发表）入队后，`setTimeout(..., 300)` 延迟采集（让点击引发的 DOM 更新先落地）；采集期间又来新事件则各自独立调度。

**归因规则（2026-09-21 集成冒烟修订）**：快照触发时刻，差集文本归给**当时最新已入队事件**（追加写入、遵守 8 条上限），而非调度该快照的事件。原因：input 在下一次交互的 focusout 时才定稿入队（先于引发 DOM 变化的 click 入队），若按调度事件归因，`Then` 会出现在状态变化之前，重放必然假失败；归给更晚的事件总是安全的（文本在该事件之后持续可见）。

**可见文本候选元素**（同时满足才入选）：
1. 可见性：`getBoundingClientRect()` 宽高均 > 0，且 `getComputedStyle` 的 `visibility` 为 `visible` 且自身或祖先 `display` 非 `none`；
2. 是"叶子文本元素"：自身 direct text（遍历子节点中的 TEXT_NODE 拼接）trim 后非空，且不含含文本的子元素；
3. 标签不在排除集：`SCRIPT, STYLE, NOSCRIPT, TEMPLATE, OPTION, SELECT, INPUT, TEXTAREA, TITLE, SVG`。

**采集与截断**：
- 每条文本：压缩连续空白（含换行）为单空格，trim，截断到 **120 字符**。
- 与上一次快照的文本集合做差集（`当前集合 − 上次集合`），差集按 DOM 顺序取 **最多 8 条** 写入 `assert_texts`。
- `start()` 时抓一次初始文本作为基线（不写入任何事件），因此首个 navigate 事件的 `assert_texts` 为空数组——加载即存在的文本不构成断言依据。
- `dom_snapshot` 字段在任何事件上恒存在；无 assert 基线时（即初始 navigate）为 `{"assert_texts": []}`。

**输出**：`"dom_snapshot": {"assert_texts": ["..."], }`——Snapshot 对象 v1 只有 `assert_texts` 一个键。

## 4. 事件捕获与去噪规则全集

监听方式统一为 `document.addEventListener(type, handler, true)`（capture）。生产环境必须不改页面行为：所有 handler 只读不写，绝不 `preventDefault/stopPropagation`。

### 4.1 忽略的事件（不产生任何记录）

`mouseover / mouseout / mousemove / scroll / wheel / resize / focus / blur（作为事件本身）/ keydown / keyup / keypress / dragstart / contextmenu / dblclick`。
blur 仅作为 input 定稿的触发器（§4.3），不产生独立事件。

### 4.2 click

- 记录 `click`（主键）。目标向上取最近的有语义祖先：若 target 是 `inside <label>`、`<span>` 等 inline 元素，`event.target` 即可（name 逻辑自带祖先 label 能力）；若 `event.target` 为 `document/html/body` 且 name 为空 → 丢弃（点空白不是步骤）。
- 一次物理点击产生一条 click；双击产生两条 click，接受（忠实记录，蒸馏器负责合并）。
- 点 submit 按钮会同时产生 click + submit 两条事件，接受（见 §4.5）。

### 4.3 input（定稿制）

- 触发器：`change` 与 `focusout`。去重键 = `元素引用 + 焦点会话`（`focusin` 开启新会话）；同一会话内 change 与 focusout 只记 **一条**，取定稿时刻的最新值。
- 若会话结束时值与获得焦点时相同（无修改），不记录（比对 `focusin` 时缓存的初值）。
- checkbox/radio 用 `change`（不依赖 focusout），值规则见 §2.2。
- type=password：值恒 `"<masked>"`，其余字段（name/form_label/ordinal）正常采集——下游据此生成 test_data 引用。

### 4.4 select

- `<select>` 的 `change` → `select` 事件；value = 选中 option 文本（§2.2）。select 不再产生 input 事件（互斥：元素为 SELECT 标签时走 select 分支）。

### 4.5 submit

- `document` 上捕获 `submit` → 记录一条，`target` 指向 `<form>` 元素（tag=form，name 按 §2.1.2 计算，通常取不到为空串，可接受）。

### 4.6 navigate

1. **初始 navigate**：`start()` 立即产生一条（seq=1），url/title 为当前页，target/value 为 null，dom_snapshot 为 `{"assert_texts": []}`（无 assert 基线）。
2. **SPA 路由**：patch `history.pushState` / `history.replaceState`（包装原函数，调用后若 URL 变化则产生 navigate）；监听 `popstate`、`hashchange`。
3. **跨文档导航**（整页跳转/刷新）：注入 JS 随文档销毁，**导航后的页面不会被记录**；导航前的事件已可通过 getJSON() 取回。不解决，已知限制。
4. 去重：同一 tick 内 pushState 与 popstate 连发且 URL 相同 → 按 URL 去重（上一条 navigate 与当前候选 url 相同则丢弃）。

### 4.7 去噪总表

| 来源 | 结果 |
|---|---|
| hover / mousemove / scroll / 键盘事件 | 不记录 |
| 一次输入过程（多按键） | 1 条 input，仅最终值 |
| 未修改值的 focus+blur | 0 条 |
| 点空白（body/html/document） | 0 条 |
| 同 tick 同 URL 的重复 navigate | 1 条 |
| 双击 | 2 条 click（忠实记录） |
| submit 按钮点击 | click + submit 各 1 条（忠实记录） |

## 5. JS 公开 API

全局对象 `window.R2GRecorder`，幂等注入（重复注入脚本 → 重置为全新停止态实例）：

| API | 返回 | 行为 |
|---|---|---|
| `R2GRecorder.start()` | boolean | 开始录制：装监听器、记 session、产生初始 navigate；已在录制中 → no-op 返回 false |
| `R2GRecorder.stop()` | boolean | 停止：卸载全部监听器、取消未决快照定时器；已停止 → no-op 返回 false。已入队事件保留 |
| `R2GRecorder.getJSON()` | string | 返回 `{session, events}` 的 JSON 字符串（2 空格缩进）；停止后仍可调用 |
| `R2GRecorder.status()` | object | `{recording: boolean, event_count: int}` |
| `R2GRecorder.copy()` | Promise\<boolean\> | 便捷项：getJSON() 写入剪贴板（`navigator.clipboard`），失败返回 false |

实现约束：单 IIFE、无外部依赖、单文件 `recorder.js`、不使用 ES 模块语法（bookmarklet 环境无模块系统）；`const/let/arrow` 可用（现代浏览器均支持，playwright chromium 覆盖）。

## 6. bookmarklet 打包

`record2gherkin/recorder/build_bookmarklet.py`（纯标准库，无第三方依赖）：

1. 读同目录 `recorder.js` 全文；
2. 包裹为 `(function(){ ... })();`（若源码已是 IIFE 则直接用）；
3. `urllib.parse.quote(payload, safe="")` 全量 URL 编码——不做手写 minify/正则删注释（会改坏语义的风险不值当）；
4. 拼前缀 `javascript:` 写入 `record2gherkin/recorder/bookmarklet.txt`（构建产物），并打印长度。

用法：`python record2gherkin/recorder/build_bookmarklet.py` → 复制 `bookmarklet.txt` 内容新建书签。备选路径：DevTools Snippet 粘贴 `recorder.js` 原文（不受 CSP/长度限制）。

## 7. 测试方案与用例清单

**形态**：pytest + playwright sync API（uv 环境已有 playwright）。`conftest.py` 提供 `recorder_page` fixture：`file://` 打开 fixture HTML → 读 `recorder.js` 源码 → `page.evaluate(js)` 注入 → `page.evaluate("R2GRecorder.start()")`。断言一律 `json.loads(page.evaluate("R2GRecorder.getJSON()"))`。测试只测 `recorder.js` 源文件这一份真源，bookmarklet 另有一条打包产物用例。

**fixture HTML 设计**（`tests/record2gherkin/fixtures/demo_form.html`，唯一 fixture）：
一张"下单"页面，集中覆盖全部语义分支——
- 带关联 label 的文本输入（`label for=email`）、placeholder 输入、`aria-label` 输入、password 输入；
- `<select>` 下拉（配送方式）；
- 两个同文案按钮「删除」（验证 ordinal）；
- `aria-label="提交订单"` 的 submit 按钮（验证 aria-label 优先 + submit 捕获）；
- 点击后显示的隐藏结果区 `#result`（验证 assert_texts 差集）；
- 一个调用 `history.pushState` 的伪 SPA 链接（验证 navigate）；
- 一段 hover 目标（验证去噪）。
静态 HTML + 少量内联 `<script>`，无外部资源。

**用例清单**（名称 / 操作 / 断言）：

| # | 名称 | 操作 | 预期事件流断言 |
|---|---|---|---|
| 1 | test_start_emits_initial_navigate | start 后立即取 JSON | events[0].type=navigate，url 含 demo_form.html，target=null，seq=1，dom_snapshot={"assert_texts": []} |
| 2 | test_click_basic_fields | 点「提交订单」按钮 | 1 条 click；tag=button、role=button、name=提交订单（来自 aria-label）、id/testid 正确 |
| 3 | test_click_name_priority_aria_label_over_text | 点一个 aria-label 与 innerText 不同的按钮 | name=aria-label 值 |
| 4 | test_click_name_falls_to_label_wrapper | 点 span（外层 label 包裹 checkbox） | name=label 文本（associated label 优先于 innerText） |
| 5 | test_input_records_final_value_once | fill 三段文字后 blur | 该元素仅 1 条 input，value=最终完整值 |
| 6 | test_input_unchanged_value_not_recorded | focus 后不改值直接 blur | 0 条 input |
| 7 | test_password_masked | fill 密码框 | 1 条 input，value 严格等于 `"<masked>"` |
| 8 | test_checkbox_change_value | 勾选 checkbox | 1 条 input，value="true"，role=checkbox |
| 9 | test_select_event | select 选择某 option | 1 条 select（无 input），value=option 文本 |
| 10 | test_submit_event | 点 submit 按钮 | 存在 1 条 submit，target.tag=form；click 与 submit 各 1 条 |
| 11 | test_hover_scroll_ignored | page.hover + mouse wheel 滚动 | 事件流无任何新增事件 |
| 12 | test_ordinal_duplicate_names | 点第二个「删除」按钮 | 该 click 的 ordinal=2 |
| 13 | test_ordinal_null_when_unique | 点唯一名称按钮 | ordinal=null |
| 14 | test_assert_texts_diff_after_click | 点「显示结果」按钮后等 500ms | 其 dom_snapshot.assert_texts 包含结果区文本 |
| 15 | test_assert_texts_length_caps | 造一条 >120 字符的新文本 | 对应条目长度 ≤120，条数 ≤8 |
| 16 | test_navigate_history_pushstate | 点伪 SPA 链接 | 新增 1 条 navigate，url 为 pushState 后的 URL |
| 17 | test_schema_contract_all_events | 走完整"填表→提交"流程 | 每条事件必含 seq/ts/type/url/page_title/target/value/dom_snapshot 键，dom_snapshot 恒为对象且含 assert_texts 数组，type 在枚举内，seq 严格递增 |
| 18 | test_stop_freezes_stream | stop 后继续 click/fill | event_count 不变；getJSON 仍可调用 |
| 19 | test_double_start_noop | start 后再 start | 第二次返回 false，初始 navigate 仅 1 条 |
| 20 | test_bookmarklet_build_output | 运行 build_bookmarklet.py | 产物以 `javascript:` 开头、URL 解码后与源码一致；解码体在页面 evaluate 可执行且 R2GRecorder 可用 |

用例文件拆分建议：`test_click.py`(2-4,12,13)、`test_input.py`(5-8)、`test_select_submit.py`(9,10)、`test_navigate_denoise.py`(1,11,16,19)、`test_snapshot.py`(14,15)、`test_contract.py`(17,18)、`test_packaging.py`(20)。

## 8. 验收标准（可机械检查）

1. `uv run pytest tests/record2gherkin/ -q` 全部通过（20 条用例 0 fail）。
2. `python record2gherkin/recorder/build_bookmarklet.py` 退出码 0，产出 `bookmarklet.txt`，首 10 字符为 `javascript:`。
3. MVP 流程验收：手动在 demo_form.html 用 bookmarklet 完成"打开→填表→点提交"，getJSON() 输出通过用例 17 的同一 schema 校验脚本（`tests/record2gherkin/test_contract.py` 中导出的校验函数复用）。
4. `recorder.js` 为单文件、`grep -c "require(\|import " recorder.js` 为 0；无 npm 工具链依赖。
5. 不改动 `testzeus_hercules/` 下任何文件（`git status` 可查）。

## 9. Out of Scope（不做及原因）

| 不做 | 原因 |
|---|---|
| 跨域 iframe 内部事件 | 同源策略拿不到内部 DOM，target 语义无从提取；PLAN §4 已列为硬盲区 |
| shadow DOM（含 open） | closed 拿不到；open 的合成事件 target 被重定向，语义提取失真，处理链路复杂度远超 MVP 收益 |
| canvas / webgl 内部交互 | 无 DOM 语义可提取，下游引擎同样看不到 |
| 多 tab / 多窗口 | 录制器实例绑定单文档；跨文档状态同步是编排层问题 |
| 浏览器扩展形态 | 需要 manifest/加载机制与商店审核，注入式已满足零安装演示 |
| 录制的回放/编辑/可视化 UI | 与"可靠捕获事件流"核心目标无关；消费方是蒸馏器不是人 |
| 性能优化（监听器节流、快照增量化） | demo/评测站点规模下无实测瓶颈；过度设计 |
| 手写 JS minify | 全量 URL 编码已满足 bookmarklet 需求，minify 有改坏语义风险 |
| 断言自动生成（在录制器内判断"该断言什么"） | 只提供 grounding 素材（assert_texts），判断归蒸馏器 |
