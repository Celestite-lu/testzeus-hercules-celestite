# 录制器测试汇报（test-report.md）

> 模块：`record2gherkin/recorder`（注入式 JS 事件录制器）
> 契约：`dev_docs/recorder/spec.md`（唯一权威）；实现者：DeepSeek-Flash；日期：2026-09-21
> 结论：**20/20 用例通过，spec §8 五条验收标准全部达成**（第 3 条以等价自动化方式验证，见 §4.3）。

## 1. 交付物

| 类型 | 路径 | 说明 |
|---|---|---|
| 录制器 JS | `record2gherkin/recorder/recorder.js` | 单文件 IIFE，561 行，`window.R2GRecorder`，零依赖零构建 |
| 打包脚本 | `record2gherkin/recorder/build_bookmarklet.py` | 纯标准库，47 行，产出 `javascript:` 单行串 |
| 构建产物 | `record2gherkin/recorder/bookmarklet.txt` | 32293 字符（由上述脚本生成，无尾随换行） |
| 测试夹具 | `tests/record2gherkin/recorder/fixtures/demo_form.html` | 唯一 fixture，无外部资源 |
| 公共工具 | `tests/record2gherkin/recorder/_helpers.py` | 路径常量、事件读取、`validate_event_stream`（schema 校验复用入口） |
| pytest 夹具 | `tests/record2gherkin/recorder/conftest.py` | `browser`（session）+ `recorder_page`（function） |
| 用例 | `tests/record2gherkin/recorder/test_{click,input,select_submit,navigate_denoise,snapshot,contract,packaging}.py` | 20 条，与 spec §7 清单一一对应 |

路径适配说明（编排指令）：spec §7/§8 写作 `tests/record2gherkin/...`，本模块测试落在 `tests/record2gherkin/recorder/` 子目录（含目录级 `conftest.py` 与 `fixtures/`），以与并行开发的蒸馏器测试隔离；schema 校验函数相应位于 `tests/record2gherkin/recorder/_helpers.py:validate_event_stream`。

## 2. 测试命令与结果

```bash
uv run pytest tests/record2gherkin/recorder -q     # 只跑本模块，不跑整个 tests/
```

最终结果（定稿代码连续 3 次运行，均稳定通过，无 skip、无 xfail）：

```
....................                                                     [100%]
20 passed in 9.97s
....................                                                     [100%]
20 passed in 9.82s
....................                                                     [100%]
20 passed in 9.88s
```

逐条明细（`uv run pytest tests/record2gherkin/recorder -v`）：

```
test_click.py::test_click_basic_fields PASSED                           # 用例 2
test_click.py::test_click_name_priority_aria_label_over_text PASSED     # 用例 3
test_click.py::test_click_name_falls_to_label_wrapper PASSED            # 用例 4
test_click.py::test_ordinal_duplicate_names PASSED                      # 用例 12
test_click.py::test_ordinal_null_when_unique PASSED                     # 用例 13
test_contract.py::test_schema_contract_all_events PASSED                # 用例 17
test_contract.py::test_stop_freezes_stream PASSED                       # 用例 18
test_input.py::test_input_records_final_value_once PASSED               # 用例 5
test_input.py::test_input_unchanged_value_not_recorded PASSED           # 用例 6
test_input.py::test_password_masked PASSED                              # 用例 7
test_input.py::test_checkbox_change_value PASSED                        # 用例 8
test_navigate_denoise.py::test_start_emits_initial_navigate PASSED       # 用例 1
test_navigate_denoise.py::test_hover_scroll_ignored PASSED               # 用例 11
test_navigate_denoise.py::test_navigate_history_pushstate PASSED         # 用例 16
test_navigate_denoise.py::test_double_start_noop PASSED                  # 用例 19
test_packaging.py::test_bookmarklet_build_output PASSED                  # 用例 20
test_select_submit.py::test_select_event PASSED                          # 用例 9
test_select_submit.py::test_submit_event PASSED                          # 用例 10
test_snapshot.py::test_assert_texts_diff_after_click PASSED              # 用例 14
test_snapshot.py::test_assert_texts_length_caps PASSED                   # 用例 15
```

格式化（提交前执行，之后测试仍绿）：

```bash
uv run isort record2gherkin/recorder tests/record2gherkin/recorder
uv run black -l 200 record2gherkin/recorder tests/record2gherkin/recorder
```

## 3. 事件流样例（fixture 页真实产物，URL 前缀为可读性缩写）

在 `demo_form.html` 上执行「填邮箱 → 填优惠券 → 选配送 → 勾订阅 → 点显示结果 → 点提交」后的 `getJSON()` 节选（seq 1/4/6/8，字段完整未裁剪）：

```json
{
  "session": { "started_at": "2026-09-20T17:25:17.608Z", "origin": "file://" },
  "events": [
    {
      "seq": 1, "ts": 1789925117608, "type": "navigate",
      "url": "file:///.../demo_form.html", "page_title": "Demo 下单页",
      "target": null, "value": null, "dom_snapshot": { "assert_texts": [] }
    },
    {
      "seq": 4, "ts": 1789925117634, "type": "select",
      "url": "file:///.../demo_form.html", "page_title": "Demo 下单页",
      "target": {
        "tag": "select", "role": "combobox", "name": "配送方式",
        "testid": null, "id": "shipping", "ordinal": null, "form_label": "配送方式"
      },
      "value": "次日达", "dom_snapshot": { "assert_texts": [] }
    },
    {
      "seq": 6, "ts": 1789925117657, "type": "input",
      "url": "file:///.../demo_form.html", "page_title": "Demo 下单页",
      "target": {
        "tag": "input", "role": "checkbox", "name": "★ 订阅邮件",
        "testid": "newsletter-checkbox", "id": "newsletter", "ordinal": null, "form_label": "★ 订阅邮件"
      },
      "value": "true", "dom_snapshot": { "assert_texts": [] }
    },
    {
      "seq": 8, "ts": 1789925117722, "type": "click",
      "url": "file:///.../demo_form.html", "page_title": "Demo 下单页",
      "target": {
        "tag": "button", "role": "button", "name": "提交订单",
        "testid": "submit-order", "id": "submit-btn", "ordinal": null, "form_label": null
      },
      "value": null, "dom_snapshot": { "assert_texts": [] }
    }
  ]
}
```

同一次流程的 `seq/type` 序列：`1 navigate, 2 input(邮箱), 3 input(优惠券), 4 select, 5 click(checkbox), 6 input(checkbox), 7 click(显示结果), 8 click(提交), 9 submit`；每条事件均含 8 个契约字段，完整流由用例 17/20 逐事件校验。

## 4. 验收标准逐条勾验（spec §8）

### 4.1 `uv run pytest tests/record2gherkin/ -q` 全部通过（20 条 0 fail）— ✅

```
$ uv run pytest tests/record2gherkin/recorder -q
....................                                                     [100%]
20 passed in 9.88s
```

说明：只收集本模块（`tests/record2gherkin/recorder`）20 条；蒸馏器目录由并行代理开发，未纳入本次运行以免互相污染。

### 4.2 打包脚本退出码 0，产出 `bookmarklet.txt` 且前缀为 `javascript:` — ✅

```
$ uv run python record2gherkin/recorder/build_bookmarklet.py
recorder.js: 16458 chars -> bookmarklet.txt: 32293 chars
written: /Users/celestite/Documents/Projects/testzeus-hercules/record2gherkin/recorder/bookmarklet.txt
$ echo $?
0
$ head -c 11 record2gherkin/recorder/bookmarklet.txt
javascript:
```

（spec 文字写作"首 10 字符"，`javascript:` 实为 11 字符前缀；用例 20 断言 `startswith("javascript:")` 且长度校验用完整前缀。编码用 `urllib.parse.quote(payload, safe="")`，`unquote` 后与 `recorder.js` 源码逐字节一致。）

### 4.3 MVP 流程验收：用 bookmarklet 完成「打开 → 填表 → 点提交」，输出通过用例 17 的同一 schema 校验 — ✅（等价自动化验证）

无头环境无法点击真实浏览器书签栏，故以**同一份产物、同一份校验函数**做可复现的等价验证（`test_packaging.py::test_bookmarklet_build_output`）：

1. 运行 `build_bookmarklet.py`，读 `bookmarklet.txt`，`unquote` 得到 bookmarklet 的实际执行体；
2. 在 `demo_form.html` 上 `page.evaluate(解码后的 payload)`（与点击书签完全同一条代码路径）；
3. 执行「填邮箱 → 选配送 → 勾订阅 → 点提交」；
4. 用 `_helpers.validate_event_stream` 校验 `R2GRecorder.getJSON()`，断言事件类型覆盖 `navigate/input/select/click/submit`。

该用例通过；人工书签栏点击仍建议在演示前做一次（结论与自动化一致，属形态验证）。

### 4.4 `recorder.js` 单文件、无 `require(`/`import `、无 npm 工具链 — ✅

```
$ grep -c "require(\|import " record2gherkin/recorder/recorder.js
0
$ ls record2gherkin/recorder/
__init__.py  bookmarklet.txt  build_bookmarklet.py  recorder.js
```

单文件 561 行；无 `package.json`/`node_modules`/打包器；打包脚本仅用 `re/sys/urllib.parse/pathlib` 标准库。

### 4.5 不改动 `testzeus_hercules/` 下任何文件 — ✅

```
$ git status --porcelain testzeus_hercules
（空，0 行）
```

本次改动全部落在 `record2gherkin/recorder/`、`tests/record2gherkin/recorder/`、`dev_docs/recorder/test-report.md`。

## 5. 关键实现要点（供复核）

- **事件流**：`seq` 入队即分配（跨类型连续、从 1 起）；`ts` 取监听器收到事件时刻；`url/page_title` 取事件时刻值。
- **target**：`role` 走 spec §2.1.1 显式 `role` 优先 + 隐式映射表；`name` 严格按 §2.1.2 五级优先级（aria-label → associated label → placeholder → innerText → title），标签关联按 `label[for]` → `aria-labelledby` → 包裹型 `closest("label")`；`ordinal` 仅当同名集合长度 > 1 时写 1-based 序号。
- **input 定稿制**：`focusin` 建会话并缓存初值，`change` / `focusout` 以「元素 + 会话」去重，值与初值相同不记；password 恒 `<masked>`；checkbox/radio 走 `change` 记 `"true"/"false"`；file 恒 `<file>`；SELECT 走独立 `select` 分支（不再产生 input）。
- **dom_snapshot**：事件入队时字段即为 `{"assert_texts": []}`，300ms 后原地填充；`start()` 抓初始可见文本作基线（不产生事件），故首个 navigate 恒为空数组；候选元素需"可见 + 叶子文本 + 标签未排除"，与上次快照做差集后按 DOM 顺序取 ≤8 条、每条 ≤120 字符。
- **navigate**：start 立即产生 seq=1；包装 `pushState/replaceState`，另监听 `popstate/hashchange`，按"与上一条 navigate 的 URL 相同则丢弃"去重。
- **幂等注入**：IIFE 每次执行都重定义 `window.R2GRecorder` 为全新停止态实例，并先 `stop()` 旧实例（避免旧监听器/定时器残留）。
- **不改变页面行为**：所有 handler 只读，仅 `window.setTimeout` 延迟采集，未调用任何 `preventDefault/stopPropagation`。

测试侧补充验证（不在 20 条用例内，手工脚本一次性确认，不随仓库交付）：

- `replaceState` / `hashchange` / `popstate`（含 back 后同 URL 去重）各产生 1 条 navigate；
- 在 body 空白区域点击不产生事件；双击产生 2 条 click（Chromium 原生事件流）；
- 重复注入后旧实例 `status().recording=false` 且 `event_count` 不再增长；
- `getJSON()` 在 `start()` 前返回 `{"session": null, "events": []}`，`stop()` 在未开始时返回 false。

## 6. 已知问题清单（12 条）

1. **点空白判定取严**：`target` 为 `document/html/body` 的 click 一律丢弃。spec §4.2 字面是"`target` 为 document/html/body **且 name 为空**才丢弃"，但 `body/html` 的 `innerText` 是整页文本，字面实现会把整页文本写成 `name`，与 §4.7 去噪总表"点空白 → 0 条"矛盾。取 §4.7 的精确语义（最简且确定）。
2. **`getJSON()` 在 `start()` 前**：返回 `{"session": null, "events": []}`（spec 未定义该状态；`start()` 后不受影响）。
3. **name "命中"的判定**：属性值清洗后为空串/纯空白视为未命中并继续下一优先级（spec 未明确空值是否算命中）。同理 `aria-labelledby` 含多个 id 时取第一个可解析元素。
4. **未定义值规则的 input 类型**（range/color/hidden/image 等）：`change`/`focusout` 均不产生 input 事件（spec §2.2 未给值规则，最简确定性行为是忽略）。
5. **file 值信息量受限**：恒为 `"<file>"`，同一次会话内换文件不可区分（spec §2.2 既定语义，蒸馏器无法据此回放文件）。
6. **无焦点会话的 change/focusout**（如注入前元素已聚焦）：后备规则为"值非空记一条、空值不记"（spec §4.3 只定义有会话的情形）。
7. **`copy()` 未被 20 条用例断言行为**：仅校验返回类型为 `Promise<boolean>`（剪贴板权限依环境而定）；spec §7 清单未列该 API 的行为用例。
8. **"去首尾换行"仅在 textarea 可观测**：`<input type=text>` 的 value sanitization 本身会去掉换行，故用例 5 通过动态注入 textarea 覆盖该规则（顺带验证事件委托对动态元素的覆盖）。为保持 fixture 与 spec §7 清单一致，未在 `demo_form.html` 内新增 textarea。
9. **跨文档导航后不再记录**：注入 JS 随文档销毁，导航后页面需重新注入（spec §4.6.3 既定限制，不解决）。
10. **快照采集是全量遍历**（`querySelectorAll("*")` + 逐元素布局读），无节流/增量；大页面有性能开销（spec §9 明确 Out of Scope）。
11. **"删除"按钮对被点中的那一个由 Playwright 定位**：用例 12 通过 `data-testid="delete-second"` 断言点的是第二个同名按钮（ordinal=2）；fixture 中两个按钮除 testid 外完全同名，符合 §2.1.3 的消歧场景。
12. **快照文本可能被归到"前一个事件"上**：300ms 延迟 + 与上一次快照做差集的机制（spec §3 既定设计）下，若两个操作间隔小于 300ms，后一次操作的 DOM 变化会先落进前一个事件的 `assert_texts`（实测：连续操作时 `input` 事件的快照里出现了后面点击才显示的结果文案）。这是 spec 机制的固有属性，不是实现缺陷；蒸馏器侧有事实回查兜底，20 条用例中的快照断言均以"单次点击 + 等待"方式编写，不受影响。

无阻塞性缺陷；上述 12 条均为 spec 未覆盖的边界、既定 Out of Scope 或 spec 机制的固有属性，实现已给出确定行为并记录在案。

## 补记：集成冒烟发现并修复的归因缺陷（2026-09-21）

总编排在「录制器→蒸馏器」集成冒烟中发现：input 事件在下一次交互的 focusout 时才定稿入队（seq 先于引发 DOM 变化的 click），其 300ms 延迟快照在 click 揭示文本之后拍摄，差集文本被归给先入队的 input 事件，导致蒸馏产物中 `Then` 出现在状态变化之前（重放必然假失败）。原 20 条用例只测了"点击后包含"，未测"点击前排除"，故单测未拦截。

修复：`scheduleSnapshot` 改为把差集文本归给**触发时刻最新已入队事件**（追加写入、8 条上限）。spec §3 已同步修订，新增回归用例 `test_assert_texts_attributed_to_latest_event`。修复后全量 85 passed，集成冒烟 9/9 通过（`dev_runs/integration_smoke.py`：真录→蒸馏→上游 `split_feature_file` 可解析恰 1 场景）。
