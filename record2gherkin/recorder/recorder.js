(function () {
  "use strict";

  /*
   * record2gherkin recorder —— 注入式同源页面事件录制器。
   *
   * 契约：dev_docs/recorder/spec.md（事件 Schema v1，与 PLAN.md 3.1 对齐）。
   * 约束：单文件、无外部依赖、无模块语法（bookmarklet / DevTools Snippet 可直接执行）。
   * 生产不改变页面行为：所有监听器只读不写，不调用 preventDefault / stopPropagation。
   * 文件首行即为 IIFE，便于 build_bookmarklet.py 原样嵌入 `javascript:` 前缀。
   */

  const SNAPSHOT_DELAY_MS = 300;
  const NAME_MAX_LENGTH = 200;
  const INPUT_VALUE_MAX_LENGTH = 500;
  const ASSERT_TEXT_MAX_LENGTH = 120;
  const ASSERT_TEXT_MAX_COUNT = 8;
  const MASKED_VALUE = "<masked>";
  const FILE_VALUE = "<file>";

  // spec 2.1.1 role 隐式映射表（最小集）
  const TEXT_INPUT_TYPES = ["text", "search", "email", "url", "tel", "password", "number", "date", "datetime-local", "month", "week", "time", "datetime"];
  const BUTTON_INPUT_TYPES = ["submit", "button", "reset"];
  // spec 3：可见文本候选元素的标签排除集
  const EXCLUDED_TEXT_TAGS = ["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "OPTION", "SELECT", "INPUT", "TEXTAREA", "TITLE", "SVG"];

  const previousInstance = window.R2GRecorder;

  let recording = false;
  let eventSequence = 0;
  let events = [];
  let session = null;
  let snapshotTimers = [];
  let lastSnapshotTexts = [];
  let listeners = [];
  let lastNavigateUrl = null;
  let focusSessions = new WeakMap();
  let originalPushState = null;
  let originalReplaceState = null;

  /* ------------------------------------------------------------------ 文本清洗 */

  // spec 2.1.2：截取首行、trim、压缩连续空白、上限 200 字符
  function cleanName(raw) {
    if (raw === null || raw === undefined) {
      return "";
    }
    const firstLine = String(raw).split("\n")[0];
    const collapsed = firstLine.replace(/\s+/g, " ").trim();
    return collapsed.length > NAME_MAX_LENGTH ? collapsed.slice(0, NAME_MAX_LENGTH) : collapsed;
  }

  // spec 2.2：值原样保留（不 trim 内部空白），仅去掉首尾换行并截断
  function cleanInputValue(raw, maxLength) {
    const text = raw === null || raw === undefined ? "" : String(raw);
    const stripped = text.replace(/^[\r\n]+/, "").replace(/[\r\n]+$/, "");
    return stripped.length > maxLength ? stripped.slice(0, maxLength) : stripped;
  }

  // spec 3：压缩连续空白（含换行）为单空格、trim、截断到 120 字符
  function cleanAssertText(raw) {
    const collapsed = String(raw).replace(/\s+/g, " ").trim();
    return collapsed.length > ASSERT_TEXT_MAX_LENGTH ? collapsed.slice(0, ASSERT_TEXT_MAX_LENGTH) : collapsed;
  }

  /* ------------------------------------------------------------- target 语义提取 */

  function resolveRole(element) {
    const explicitRole = element.getAttribute("role");
    if (explicitRole) {
      const cleaned = cleanName(explicitRole);
      if (cleaned) {
        return cleaned;
      }
    }
    const tag = element.tagName.toLowerCase();
    if (tag === "a") {
      return element.hasAttribute("href") ? "link" : "generic";
    }
    if (tag === "button") {
      return "button";
    }
    if (tag === "textarea") {
      return "textbox";
    }
    if (tag === "select") {
      return "combobox";
    }
    if (tag === "input") {
      const type = (element.getAttribute("type") || "text").toLowerCase();
      if (BUTTON_INPUT_TYPES.indexOf(type) !== -1) {
        return "button";
      }
      if (type === "checkbox") {
        return "checkbox";
      }
      if (type === "radio") {
        return "radio";
      }
      if (TEXT_INPUT_TYPES.indexOf(type) !== -1) {
        return "textbox";
      }
    }
    return "generic";
  }

  // spec 2.1.2 / 2.1：associated label 查找顺序 label[for] -> aria-labelledby -> 包裹型 label
  function findAssociatedLabel(element) {
    const id = element.getAttribute("id");
    if (id) {
      const labelledByFor = document.querySelectorAll("label[for]");
      for (let index = 0; index < labelledByFor.length; index += 1) {
        if (labelledByFor[index].getAttribute("for") === id) {
          return labelledByFor[index];
        }
      }
    }
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const ids = labelledBy.split(/\s+/);
      for (let index = 0; index < ids.length; index += 1) {
        const referenced = ids[index] ? document.getElementById(ids[index]) : null;
        if (referenced) {
          return referenced;
        }
      }
    }
    return element.closest ? element.closest("label") : null;
  }

  // spec 2.1.2 name 取值优先级：aria-label -> associated label -> placeholder -> innerText -> title
  function computeName(element) {
    const ariaLabel = cleanName(element.getAttribute("aria-label"));
    if (ariaLabel) {
      return ariaLabel;
    }
    const label = findAssociatedLabel(element);
    if (label) {
      const labelText = cleanName(label.innerText);
      if (labelText) {
        return labelText;
      }
    }
    const placeholder = cleanName(element.getAttribute("placeholder"));
    if (placeholder) {
      return placeholder;
    }
    const innerText = cleanName(element.innerText);
    if (innerText) {
      return innerText;
    }
    return cleanName(element.getAttribute("title"));
  }

  // spec 2.1.3 ordinal：同名元素集合 > 1 时的 1-based 序号，否则 null
  function computeOrdinal(element, tag, name) {
    if (!name) {
      return null;
    }
    const candidates = document.querySelectorAll(tag);
    let occurrence = 0;
    let position = 0;
    for (let index = 0; index < candidates.length; index += 1) {
      if (computeName(candidates[index]) !== name) {
        continue;
      }
      occurrence += 1;
      if (candidates[index] === element) {
        position = occurrence;
      }
    }
    return occurrence > 1 && position > 0 ? position : null;
  }

  // spec 2.1
  function describeTarget(element) {
    const tag = element.tagName.toLowerCase();
    const name = computeName(element);
    let formLabel = null;
    if (tag === "input" || tag === "textarea" || tag === "select") {
      const label = findAssociatedLabel(element);
      const labelText = label ? cleanName(label.innerText) : "";
      formLabel = labelText ? labelText : null;
    }
    return {
      tag: tag,
      role: resolveRole(element),
      name: name,
      testid: element.getAttribute("data-testid"),
      id: element.getAttribute("id") || null,
      ordinal: computeOrdinal(element, tag, name),
      form_label: formLabel,
    };
  }

  /* ----------------------------------------------------------- dom_snapshot 采集 */

  function directText(element) {
    const parts = [];
    for (let index = 0; index < element.childNodes.length; index += 1) {
      const node = element.childNodes[index];
      if (node.nodeType === 3) {
        parts.push(node.nodeValue);
      }
    }
    return cleanAssertText(parts.join(""));
  }

  function hasTextChild(element) {
    for (let index = 0; index < element.children.length; index += 1) {
      if (directText(element.children[index])) {
        return true;
      }
    }
    return false;
  }

  function isVisible(element) {
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      return false;
    }
    if (getComputedStyle(element).visibility !== "visible") {
      return false;
    }
    let node = element;
    while (node && node.nodeType === 1) {
      if (getComputedStyle(node).display === "none") {
        return false;
      }
      node = node.parentElement;
    }
    return true;
  }

  // spec 3：DOM 顺序返回满足「可见 + 叶子文本 + 标签未排除」的文本（已清洗截断）
  function collectVisibleTexts() {
    const collected = [];
    const all = document.querySelectorAll("*");
    for (let index = 0; index < all.length; index += 1) {
      const element = all[index];
      if (EXCLUDED_TEXT_TAGS.indexOf(element.tagName.toUpperCase()) !== -1) {
        continue;
      }
      const text = directText(element);
      if (!text || hasTextChild(element)) {
        continue;
      }
      if (!isVisible(element)) {
        continue;
      }
      collected.push(text);
    }
    return collected;
  }

  // spec 3：与上一次快照做差集（当前 - 上次），DOM 顺序取最多 8 条，并滚动更新基线
  function collectNewTexts() {
    const current = collectVisibleTexts();
    const previous = new Set(lastSnapshotTexts);
    const added = [];
    for (let index = 0; index < current.length; index += 1) {
      if (!previous.has(current[index])) {
        added.push(current[index]);
      }
    }
    lastSnapshotTexts = current;
    return added.slice(0, ASSERT_TEXT_MAX_COUNT);
  }

  function cancelSnapshotTimers() {
    for (let index = 0; index < snapshotTimers.length; index += 1) {
      window.clearTimeout(snapshotTimers[index]);
    }
    snapshotTimers = [];
  }

  function scheduleSnapshot(event) {
    const timer = window.setTimeout(function () {
      const position = snapshotTimers.indexOf(timer);
      if (position !== -1) {
        snapshotTimers.splice(position, 1);
      }
      if (!recording) {
        return;
      }
      const added = collectNewTexts();
      if (added.length === 0) {
        return;
      }
      // 归因规则：新增文本归给「触发时刻最新已入队事件」。
      // 调度事件与触发时刻之间可能入队了后续事件（典型：input 在下一次交互的
      // focusout 时定稿入队，先于引发 DOM 变化的 click），文本实际由那些后续
      // 事件造成；归给更早的事件会让 Then 断言出现在状态变化之前，重放假失败。
      const target = events.length > 0 ? events[events.length - 1] : event;
      for (let index = 0; index < added.length; index += 1) {
        if (target.dom_snapshot.assert_texts.length >= ASSERT_TEXT_MAX_COUNT) {
          break;
        }
        target.dom_snapshot.assert_texts.push(added[index]);
      }
    }, SNAPSHOT_DELAY_MS);
    snapshotTimers.push(timer);
  }

  /* ------------------------------------------------------------------ 事件入队 */

  function createEvent(type, targetElement, value) {
    return {
      seq: (eventSequence += 1),
      ts: Date.now(),
      type: type,
      url: location.href,
      page_title: document.title,
      target: targetElement ? describeTarget(targetElement) : null,
      value: value === undefined ? null : value,
      dom_snapshot: { assert_texts: [] },
    };
  }

  function pushEvent(type, targetElement, value) {
    const event = createEvent(type, targetElement, value);
    events.push(event);
    scheduleSnapshot(event);
    return event;
  }

  /* -------------------------------------------------------------- 事件监听处理 */

  function isElement(node) {
    return !!node && node.nodeType === 1;
  }

  function inputTypeOf(element) {
    return (element.getAttribute("type") || "text").toLowerCase();
  }

  function isTextLikeField(element) {
    const tag = element.tagName.toLowerCase();
    if (tag === "textarea") {
      return true;
    }
    return tag === "input" && TEXT_INPUT_TYPES.indexOf(inputTypeOf(element)) !== -1;
  }

  function handleClick(event) {
    const element = event.target;
    if (!isElement(element)) {
      return;
    }
    // spec 4.2 / 4.7：点空白（document / html / body）不是步骤。
    // body/html 的 innerText 是整页文本，按「name 为空才丢弃」会把整页文本写成 name，故一律丢弃。
    if (element === document.documentElement || element === document.body) {
      return;
    }
    pushEvent("click", element, null);
  }

  function handleFocusIn(event) {
    const element = event.target;
    if (!isElement(element) || !isTextLikeField(element)) {
      return;
    }
    focusSessions.set(element, { initial: element.value, recorded: false });
  }

  // spec 4.3：同一「元素 + 焦点会话」内 change/focusout 只记一条，值与焦点初值相同则不记
  function finalizeTextInput(element) {
    const focusSession = focusSessions.get(element);
    if (focusSession && focusSession.recorded) {
      return;
    }
    const value = element.value === null || element.value === undefined ? "" : String(element.value);
    if (focusSession) {
      focusSession.recorded = true;
      if (value === focusSession.initial) {
        return;
      }
    } else if (value === "") {
      return;
    }
    const recordedValue = inputTypeOf(element) === "password" ? MASKED_VALUE : cleanInputValue(value, INPUT_VALUE_MAX_LENGTH);
    pushEvent("input", element, recordedValue);
  }

  function handleFocusOut(event) {
    const element = event.target;
    if (!isElement(element) || !isTextLikeField(element)) {
      return;
    }
    finalizeTextInput(element);
  }

  function handleChange(event) {
    const element = event.target;
    if (!isElement(element)) {
      return;
    }
    const tag = element.tagName.toLowerCase();
    if (tag === "select") {
      // spec 2.2 / 4.4：select 只产生 select 事件，value 为选中 option 的可见文本
      // （与 name 同一套清洗与截断规则，上限同为 200 字符）
      const selected = element.selectedOptions && element.selectedOptions.length ? element.selectedOptions[0] : null;
      pushEvent("select", element, selected ? cleanName(selected.innerText) : "");
      return;
    }
    if (tag !== "input" && tag !== "textarea") {
      return;
    }
    const type = inputTypeOf(element);
    if (type === "checkbox" || type === "radio") {
      // spec 4.3：checkbox/radio 用 change，值取 checked 状态
      pushEvent("input", element, element.checked ? "true" : "false");
      return;
    }
    if (type === "file") {
      pushEvent("input", element, FILE_VALUE);
      return;
    }
    if (TEXT_INPUT_TYPES.indexOf(type) !== -1) {
      finalizeTextInput(element);
    }
  }

  function handleSubmit(event) {
    const form = event.target;
    if (!isElement(form)) {
      return;
    }
    pushEvent("submit", form, null);
  }

  function handleHistoryChange() {
    if (!recording) {
      return;
    }
    if (lastNavigateUrl === location.href) {
      return;
    }
    lastNavigateUrl = location.href;
    pushEvent("navigate", null, null);
  }

  /* ------------------------------------------------------------ 监听器/历史挂载 */

  function addListener(target, type, handler) {
    target.addEventListener(type, handler, true);
    listeners.push({ target: target, type: type, handler: handler });
  }

  function attachListeners() {
    addListener(document, "click", handleClick);
    addListener(document, "change", handleChange);
    addListener(document, "focusin", handleFocusIn);
    addListener(document, "focusout", handleFocusOut);
    addListener(document, "submit", handleSubmit);
    addListener(window, "popstate", handleHistoryChange);
    addListener(window, "hashchange", handleHistoryChange);
  }

  function detachListeners() {
    for (let index = 0; index < listeners.length; index += 1) {
      const entry = listeners[index];
      entry.target.removeEventListener(entry.type, entry.handler, true);
    }
    listeners = [];
  }

  // spec 4.6.2：包装 history.pushState / replaceState，URL 变化时产生 navigate
  function patchHistory() {
    originalPushState = history.pushState;
    originalReplaceState = history.replaceState;
    history.pushState = function () {
      const result = originalPushState.apply(history, arguments);
      handleHistoryChange();
      return result;
    };
    history.replaceState = function () {
      const result = originalReplaceState.apply(history, arguments);
      handleHistoryChange();
      return result;
    };
  }

  function restoreHistory() {
    if (originalPushState) {
      history.pushState = originalPushState;
      originalPushState = null;
    }
    if (originalReplaceState) {
      history.replaceState = originalReplaceState;
      originalReplaceState = null;
    }
  }

  /* -------------------------------------------------------------------- 公开 API */

  function start() {
    if (recording) {
      return false;
    }
    recording = true;
    eventSequence = 0;
    events = [];
    snapshotTimers = [];
    lastSnapshotTexts = [];
    listeners = [];
    lastNavigateUrl = null;
    focusSessions = new WeakMap();
    session = { started_at: new Date().toISOString(), origin: location.origin };

    attachListeners();
    patchHistory();

    // spec 3：start() 抓一次初始可见文本作为基线（不写入任何事件），故首个 navigate 的 assert_texts 为空
    lastSnapshotTexts = collectVisibleTexts();
    lastNavigateUrl = location.href;
    events.push(createEvent("navigate", null, null));
    return true;
  }

  function stop() {
    if (!recording) {
      return false;
    }
    recording = false;
    detachListeners();
    restoreHistory();
    cancelSnapshotTimers();
    return true;
  }

  function getJSON() {
    return JSON.stringify({ session: session, events: events }, null, 2);
  }

  function status() {
    return { recording: recording, event_count: events.length };
  }

  function copy() {
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        return Promise.resolve(false);
      }
      return navigator.clipboard.writeText(getJSON()).then(
        function () {
          return true;
        },
        function () {
          return false;
        }
      );
    } catch (error) {
      return Promise.resolve(false);
    }
  }

  // spec 5：重复注入脚本 -> 重置为全新停止态实例（先停掉旧实例，避免残留监听器与定时器）
  if (previousInstance && typeof previousInstance.stop === "function") {
    try {
      previousInstance.stop();
    } catch (error) {
      // 旧实例停止失败不影响新实例
    }
  }

  window.R2GRecorder = {
    start: start,
    stop: stop,
    getJSON: getJSON,
    status: status,
    copy: copy,
  };
})();
