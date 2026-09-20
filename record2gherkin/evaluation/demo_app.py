"""MiniShop demo app: registry-driven HTML rendering and the UI mutation engine (spec §1-§2).

Everything the page shows is generated from :data:`REGISTRY` — the in-module table of
interactive/assertable elements (spec §1.2, single source of truth). Mutations transform that table
*before* serialisation, so :func:`render_page` is a pure function: same ``(mutation, seed)`` yields
byte-identical output, and the module never reads the clock, the environment or ``random``
(any "randomness" comes from ``hashlib.sha256``).

The inline script is generated from the *mutated* registry as well: it resolves every element
through its ``IDS`` map, so M3 (id/class/data-testid randomisation) can never break the page's own
behaviour (spec §2.1, review 必改 2).  ``tests/record2gherkin/evaluation/test_demo_app.py`` (A12)
asserts that property mechanically.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

#: The five mutation modes of the experiment (spec §2.2).  M0 is the identity control.
MUTATIONS: tuple[str, ...] = ("M0", "M1", "M2", "M3", "M4")

LIST_VIEW = "list"
ORDER_VIEW = "order"
#: Views a document can be rendered for (``render_page(view=...)``); ``common``/``layout`` elements
#: belong to the shared shell and are rendered for every view.
VIEWS: tuple[str, ...] = (LIST_VIEW, ORDER_VIEW)

PAGE_TITLE = "MiniShop 商城"
APP_TITLE = "MiniShop"

# ---------------------------------------------------------------------------------------------
# 固定文案（spec §1.2/§1.4）。这些字符串是蒸馏产物的断言文本与录制流程的输入值来源。
# ---------------------------------------------------------------------------------------------

#: 动态计数文案模板；``{n}`` 在 HTML 中填当前值，在 JS 中编译成 ``"共 " + n + " 件商品"``。
STATUS_ALL_TEMPLATE = "共 {n} 件商品"
STATUS_SEARCH_TEMPLATE = "找到 {n} 件商品"
STATUS_FILTER_TEMPLATE = "筛选后共 {n} 件商品"
CART_TEMPLATE = "清单（{n}）"
QTY_TEMPLATE = "数量：{n}"

ORDER_SUCCESS_TEXT = "下单成功，感谢您的购买！"
ORDER_NUMBER_TEXT = "订单号：D20260920-001"

M2_BANNER_TEXT = "全场满 99 元包邮，次日达限时 8 折"

CATEGORY_OPTIONS: tuple[tuple[str, str], ...] = (("all", "全部"), ("digital", "数码"), ("home", "家居"))
SHIPPING_OPTIONS: tuple[tuple[str, str], ...] = (("standard", "标准配送"), ("nextday", "次日达"), ("pickup", "门店自提"))

#: 录制/回放共用的下单表单输入值（同一常量被录制脚本与基线脚本引用，避免口径漂移）。
SAMPLE_NAME = "张三"
SAMPLE_EMAIL = "zhangsan@example.com"
SAMPLE_ADDRESS = "上海市浦东新区世纪大道 1 号"
SEARCH_QUERY = "台灯"
MAX_PRICE_INPUT = "300"


@dataclass(frozen=True)
class Product:
    """One product card (spec §1.2 ``product_N``)."""

    key: str
    name: str
    price: str
    price_value: int
    category: str  # option value, see CATEGORY_OPTIONS


PRODUCTS: tuple[Product, ...] = (
    Product(key="product_1", name="无线耳机 Pro", price="¥299", price_value=299, category="digital"),
    Product(key="product_2", name="桌面台灯", price="¥129", price_value=129, category="home"),
    Product(key="product_3", name="陶瓷马克杯", price="¥39", price_value=39, category="home"),
)

PRODUCT_COUNT = len(PRODUCTS)
STATUS_ALL_TEXT = STATUS_ALL_TEMPLATE.format(n=PRODUCT_COUNT)
CART_INITIAL_TEXT = CART_TEMPLATE.format(n=0)
QTY_INITIAL_TEXT = QTY_TEMPLATE.format(n=1)


@dataclass(frozen=True)
class Element:
    """One registry entry (spec §1.2)."""

    key: str
    view: str
    group: str
    tag: str
    text: str | None = None
    children: tuple[str, ...] = ()
    attrs: tuple[tuple[str, str], ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    label_for: str | None = None
    hidden: bool = False
    #: 注册表内嵌在容器元素里的子元素 key（按序渲染在容器内部）。
    contains: tuple[str, ...] = ()


def _product_elements(product: Product) -> tuple[Element, ...]:
    name_key, price_key, add_key = f"{product.key}_name", f"{product.key}_price", f"{product.key}_add"
    return (
        Element(product.key, LIST_VIEW, "g_products", "div", contains=(name_key, price_key, add_key)),
        Element(name_key, LIST_VIEW, "g_products", "p", text=product.name),
        Element(price_key, LIST_VIEW, "g_products", "p", text=product.price),
        Element(add_key, LIST_VIEW, "g_products", "button", text="加入清单", attrs=(("type", "button"),)),
    )


def _registry() -> tuple[Element, ...]:
    layout = (
        Element("list_view", "layout", "g_layout", "div"),
        Element("list_filters", "layout", "g_layout", "div"),
        Element("order_view", "layout", "g_layout", "div", hidden=True),
        Element("order_form", "layout", "g_layout", "form"),
    )
    common = (Element("cart_count", "common", "g_header", "p", text=CART_INITIAL_TEXT),)
    list_view = (
        Element("nav_order", LIST_VIEW, "g_nav", "a", text="去下单", attrs=(("href", "#/order"),)),
        Element("list_status", LIST_VIEW, "g_status", "p", text=STATUS_ALL_TEXT),
        Element("search_input", LIST_VIEW, "g_search", "input", attrs=(("type", "text"), ("aria-label", "搜索商品"), ("placeholder", "搜索商品"))),
        Element("search_button", LIST_VIEW, "g_search", "button", text="搜索", attrs=(("type", "button"),)),
        Element("category_select", LIST_VIEW, "g_filter", "select", attrs=(("aria-label", "商品类别"),), options=CATEGORY_OPTIONS),
        Element("max_price_input", LIST_VIEW, "g_filter", "input", attrs=(("type", "text"), ("aria-label", "最大价格"))),
        Element("filter_button", LIST_VIEW, "g_filter", "button", text="应用筛选", attrs=(("type", "button"),)),
    )
    products = tuple(element for product in PRODUCTS for element in _product_elements(product))
    order_view = (
        Element("nav_home", ORDER_VIEW, "g_nav_order", "a", text="返回列表", attrs=(("href", "#/"),)),
        Element("label_name", ORDER_VIEW, "g_name", "label", text="收货人姓名", label_for="name_input"),
        Element("name_input", ORDER_VIEW, "g_name", "input", attrs=(("type", "text"),)),
        Element("label_email", ORDER_VIEW, "g_email", "label", text="联系邮箱", label_for="email_input"),
        Element("email_input", ORDER_VIEW, "g_email", "input", attrs=(("type", "text"),)),
        Element("label_address", ORDER_VIEW, "g_address", "label", text="收货地址", label_for="address_input"),
        Element("address_input", ORDER_VIEW, "g_address", "input", attrs=(("type", "text"),)),
        Element("label_shipping", ORDER_VIEW, "g_shipping", "label", text="配送方式", label_for="shipping_select"),
        Element("shipping_select", ORDER_VIEW, "g_shipping", "select", options=SHIPPING_OPTIONS),
        Element("label_promo", ORDER_VIEW, "g_promo", "label", text="接受促销邮件", label_for="promo_checkbox"),
        Element("promo_checkbox", ORDER_VIEW, "g_promo", "input", attrs=(("type", "checkbox"),)),
        Element("qty_display", ORDER_VIEW, "g_qty", "p", text=QTY_INITIAL_TEXT),
        Element("qty_minus", ORDER_VIEW, "g_qty", "button", text="−", attrs=(("type", "button"),)),
        Element("qty_plus", ORDER_VIEW, "g_qty", "button", text="+", attrs=(("type", "button"),)),
        Element("submit_button", ORDER_VIEW, "g_submit", "button", text="提交订单", attrs=(("type", "submit"),)),
        Element(
            "order_result",
            ORDER_VIEW,
            "g_result",
            "div",
            children=(ORDER_SUCCESS_TEXT, ORDER_NUMBER_TEXT),
            hidden=True,
        ),
    )
    return layout + common + list_view + products + order_view


#: 元素注册表（spec §1.2，单一事实来源）。
REGISTRY: tuple[Element, ...] = _registry()

#: 视图内的分组顺序（变异前）。
LIST_GROUPS: tuple[str, ...] = ("g_nav", "g_status", "g_search", "g_filter", "g_products")
ORDER_GROUPS: tuple[str, ...] = ("g_nav_order", "g_name", "g_email", "g_address", "g_shipping", "g_promo", "g_qty", "g_submit", "g_result")
#: spec §2.2 M1 的分组列表 G（g_submit 固定最后）。
ORDER_FIELD_GROUPS: tuple[str, ...] = ("g_name", "g_email", "g_address", "g_shipping", "g_promo", "g_qty", "g_submit")
#: M1 互换的两个 list 分组（spec §2.2）。
LIST_FIELD_GROUPS: tuple[str, ...] = ("g_search", "g_filter")
#: spec §2.2 M2 (a)：被 ``m2-card`` 包裹的字段分组。
M2_FIELD_GROUPS: tuple[str, ...] = LIST_FIELD_GROUPS + ORDER_FIELD_GROUPS

#: spec §2.2 M4 改写表：参与改写的元素 key -> 候选改写文本。
M4_TABLE: dict[str, tuple[str, ...]] = {
    "search_button": ("查一下", "搜索一下"),
    "filter_button": ("筛选结果", "开始筛选"),
    "submit_button": ("确认下单", "立即提交"),
    "nav_order": ("马上下单", "前往下单"),
    "product_1_add": ("添加到清单", "放入清单"),
    "product_2_add": ("添加到清单", "放入清单"),
    "product_3_add": ("添加到清单", "放入清单"),
}

_VOID_TAGS = frozenset({"input"})

_STYLE = (
    "body{font-family:sans-serif;margin:0 0 24px}"
    ".ms-header{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid #eee}"
    ".ms-header h1{font-size:18px;margin:0}"
    "section,div,p,form,nav{margin:8px 16px}"
    "button{cursor:pointer}"
)


# ---------------------------------------------------------------------------------------------
# 注册表访问
# ---------------------------------------------------------------------------------------------


def get_element(key: str) -> Element:
    """Registry entry for ``key``; raises ``KeyError`` for unknown keys."""
    for element in REGISTRY:
        if element.key == key:
            return element
    raise KeyError(f"unknown element key: {key!r}")


def element_keys() -> tuple[str, ...]:
    """All registry keys, in registry order."""
    return tuple(element.key for element in REGISTRY)


def elements_of_group(group: str) -> tuple[Element, ...]:
    return tuple(element for element in REGISTRY if element.group == group)


def elements_of_view(view: str) -> tuple[Element, ...]:
    return tuple(element for element in REGISTRY if element.view == view)


def stable_id(key: str) -> str:
    """M0 id of a registry element (``product_1_add`` -> ``product-1-add``)."""
    return key.replace("_", "-")


def stable_testid(key: str) -> str:
    """M0 ``data-testid`` of a registry element (same spelling as the id)."""
    return key.replace("_", "-")


def stable_class(key: str) -> str:
    """M0 class of a registry element;唯一 per element so M3 replacement stays unambiguous."""
    return "ms-" + key.replace("_", "-")


def selector_from_attributes(attrs: Mapping[str, str | None]) -> str:
    """Property selector for one element's attributes (spec §5 priority: id -> data-testid -> class)."""
    if attrs.get("id"):
        return f"#{attrs['id']}"
    if attrs.get("testid"):
        return f'[data-testid="{attrs["testid"]}"]'
    if attrs.get("class"):
        return "." + str(attrs["class"]).split()[0]
    raise ValueError(f"element has no locating attribute: {dict(attrs)!r}")


def selector_for(key: str, mutation: str = "M0", seed: int = 0) -> str:
    """Property-selector of one registry element under ``(mutation, seed)``.

    Every registry element carries a stable id, so the chain degenerates to ``#id`` in practice;
    :func:`selector_from_attributes` keeps the documented priority for elements without ids.
    """
    get_element(key)  # validates the key
    return selector_from_attributes(mutated_attributes(key, mutation, seed))


# ---------------------------------------------------------------------------------------------
# 变异（spec §2）
# ---------------------------------------------------------------------------------------------


def _hash_parts(*parts: object) -> str:
    joined = ":".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def mutated_attributes(key: str, mutation: str = "M0", seed: int = 0) -> Mapping[str, str]:
    """``id``/``class``/``data-testid`` of one element under a mutation (spec §2.2 M3)."""
    if mutation == "M3":
        return {
            "id": "m3-" + _hash_parts(seed, key)[:8],
            "class": "c-" + _hash_parts(seed, key + ":class")[:8],
            "testid": "m3-" + _hash_parts(seed, key + ":tid")[:8],
        }
    return {"id": stable_id(key), "class": stable_class(key), "testid": stable_testid(key)}


def m4_text(key: str, seed: int) -> str | None:
    """Rewritten accessible name of ``key`` under M4, or ``None`` when the element is not touched."""
    table = M4_TABLE.get(key)
    if not table:
        return None
    index = int(_hash_parts(seed, key), 16) % len(table)
    return table[index]


def list_group_order(mutation: str) -> tuple[str, ...]:
    """list 视图分组顺序（M1 互换搜索组与筛选组，spec §2.2）。"""
    groups = list(LIST_GROUPS)
    if mutation == "M1":
        first, second = groups.index(LIST_FIELD_GROUPS[0]), groups.index(LIST_FIELD_GROUPS[1])
        groups[first], groups[second] = groups[second], groups[first]
    return tuple(groups)


def order_group_order(mutation: str) -> tuple[str, ...]:
    """order 视图分组顺序（M1 反转 G[:-1]、g_submit 固定最后，spec §2.2）。"""
    fields = list(ORDER_FIELD_GROUPS)
    if mutation == "M1":
        fields = list(reversed(fields[:-1])) + [fields[-1]]
    return ("g_nav_order", *fields, "g_result")


def inline_script(mutation: str = "M0", seed: int = 0) -> str:
    """The page's inline script, generated from the *mutated* registry (spec §2.1 同源总则）。"""
    ids = {element.key: mutated_attributes(element.key, mutation, seed)["id"] for element in REGISTRY}
    products = [{"key": product.key, "add_key": f"{product.key}_add", "name": product.name, "category": product.category, "price": product.price_value} for product in PRODUCTS]
    script = _SCRIPT_TEMPLATE
    for token, value in (
        ("__IDS__", json.dumps(ids, ensure_ascii=False)),
        ("__PRODUCTS__", json.dumps(products, ensure_ascii=False)),
        ("__STATUS_ALL__", _js_status(STATUS_ALL_TEMPLATE)),
        ("__STATUS_SEARCH__", _js_status(STATUS_SEARCH_TEMPLATE)),
        ("__STATUS_FILTER__", _js_status(STATUS_FILTER_TEMPLATE)),
        ("__CART_TEXT__", _js_status(CART_TEMPLATE)),
        ("__QTY_TEXT__", _js_status(QTY_TEMPLATE)),
    ):
        script = script.replace(token, value)
    return script


def _js_status(template: str) -> str:
    """Compile a ``{n}`` Python template into a JS string-concatenation expression."""
    head, _, tail = template.partition("{n}")
    return json.dumps(head, ensure_ascii=False) + " + n + " + json.dumps(tail, ensure_ascii=False)


# ---------------------------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------------------------


def element_html(key: str, mutation: str = "M0", seed: int = 0, inner: str | None = None, extra_classes: Sequence[str] = ()) -> str:
    """Serialise one registry element; ``inner`` overrides the content (used for containers)."""
    _check_mutation(mutation)
    element = get_element(key)
    attrs = mutated_attributes(key, mutation, seed)
    classes = [attrs["class"], *extra_classes]
    parts = [f'id="{attrs["id"]}"', f'class="{" ".join(classes)}"', f'data-testid="{attrs["testid"]}"']
    if element.label_for is not None:
        parts.append(f'for="{mutated_attributes(element.label_for, mutation, seed)["id"]}"')
    for name, value in element.attrs:
        parts.append(f'{name}="{html.escape(value, quote=True)}"')
    text = element.text
    if mutation == "M4":
        rewritten = m4_text(key, seed)
        if rewritten is not None:
            text = rewritten
            parts.append(f'aria-label="{html.escape(rewritten, quote=True)}"')
    if element.hidden:
        parts.append('style="display:none"')

    if inner is None:
        if element.tag == "select":
            inner = "".join(f'<option value="{html.escape(value, quote=True)}">{html.escape(label)}</option>' for value, label in element.options)
        elif element.children:
            inner = "".join(f"<p>{html.escape(child)}</p>" for child in element.children)
        elif text:
            inner = html.escape(text)
        else:
            inner = ""
    if element.tag in _VOID_TAGS:
        return f"<{element.tag} {' '.join(parts)}>"
    return f"<{element.tag} {' '.join(parts)}>{inner}</{element.tag}>"


def _render_group(group: str, mutation: str, seed: int) -> str:
    """One field group; wrapped in ``m2-card`` under M2 (spec §2.2 M2 (a))。"""
    elements = elements_of_group(group)
    nested_keys = {key for element in elements for key in element.contains}
    inner: list[str] = []
    for element in elements:
        if element.key in nested_keys:  # 已渲染在所属容器内部
            continue
        if element.contains:
            nested = "\n".join(element_html(child, mutation, seed) for child in element.contains)
            inner.append(element_html(element.key, mutation, seed, inner=nested))
            continue
        inner.append(element_html(element.key, mutation, seed))
    body = "\n".join(inner)
    if mutation == "M2" and group in M2_FIELD_GROUPS:
        return f'<div class="m2-card" style="border:1px solid #ddd;padding:12px;margin:8px 0">{body}</div>'
    return body


def _render_list_view(mutation: str, seed: int) -> str:
    body: list[str] = []
    field_groups = [group for group in list_group_order(mutation) if group in LIST_FIELD_GROUPS]
    filters_inner = "\n".join(_render_group(group, mutation, seed) for group in field_groups)
    extra = ("m2-flex",) if mutation == "M2" else ()
    for group in list_group_order(mutation):
        if group in LIST_FIELD_GROUPS:
            if group == field_groups[0]:  # 两个字段分组同属 list_filters 容器，只渲染一次
                body.append(element_html("list_filters", mutation, seed, inner=filters_inner, extra_classes=extra))
            continue
        body.append(_render_group(group, mutation, seed))
    return element_html("list_view", mutation, seed, inner="\n".join(body))


def _render_order_view(mutation: str, seed: int) -> str:
    body = [_render_group("g_nav_order", mutation, seed)]
    if mutation == "M2":
        body.append(f'<div class="m2-banner">{html.escape(M2_BANNER_TEXT)}</div>')
    fields = [group for group in order_group_order(mutation) if group != "g_nav_order"]
    form_inner = "\n".join(_render_group(group, mutation, seed) for group in fields)
    extra = ("m2-flex",) if mutation == "M2" else ()
    body.append(element_html("order_form", mutation, seed, inner=form_inner, extra_classes=extra))
    return element_html("order_view", mutation, seed, inner="\n".join(body))


def render_page(mutation: str = "M0", seed: int = 0, view: str | None = None) -> str:
    """Render the whole demo document for ``(mutation, seed)`` (spec §2.1 唯一入口）。

    ``view`` is a unit-test-only shortcut that renders a single view's container; production and
    recording always use the full document (both views + the inline script).
    """
    _check_mutation(mutation)
    _check_seed(seed)
    if view is not None and view not in VIEWS:
        raise ValueError(f"unknown view: {view!r} (expected one of {VIEWS})")
    views: Iterable[str] = VIEWS if view is None else (view,)

    lines: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{html.escape(PAGE_TITLE)}</title>",
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body>",
        '<header class="ms-header">',
        f"<h1>{html.escape(APP_TITLE)}</h1>",
        element_html("cart_count", mutation, seed),
        "</header>",
    ]
    if LIST_VIEW in views:
        lines.append(_render_list_view(mutation, seed))
    if ORDER_VIEW in views:
        lines.append(_render_order_view(mutation, seed))
    lines.append(f"<script>{inline_script(mutation, seed)}</script>")
    lines.extend(["</body>", "</html>"])
    return "\n".join(lines) + "\n"


def _check_mutation(mutation: str) -> None:
    if mutation not in MUTATIONS:
        raise ValueError(f"unknown mutation: {mutation!r} (expected one of {MUTATIONS})")


def _check_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"seed must be a non-negative int, got {seed!r}")


_SCRIPT_TEMPLATE = """(function () {
  "use strict";
  var IDS = __IDS__;
  var PRODUCTS = __PRODUCTS__;
  var STATE = { cart: 0, qty: 1 };

  function byId(key) {
    return document.getElementById(IDS[key]);
  }

  function setText(key, value) {
    var element = byId(key);
    if (element) {
      element.textContent = value;
    }
  }

  function statusAll(n) { return __STATUS_ALL__; }
  function statusSearch(n) { return __STATUS_SEARCH__; }
  function statusFilter(n) { return __STATUS_FILTER__; }
  function cartText(n) { return __CART_TEXT__; }
  function qtyText(n) { return __QTY_TEXT__; }

  function showView() {
    var listBox = byId("list_view");
    var orderBox = byId("order_view");
    var isOrder = location.hash.indexOf("#/order") === 0;
    if (listBox) { listBox.style.display = isOrder ? "none" : ""; }
    if (orderBox) { orderBox.style.display = isOrder ? "" : "none"; }
  }

  function setCardVisible(item, visible) {
    var card = byId(item.key);
    if (card) { card.style.display = visible ? "" : "none"; }
  }

  function search() {
    var box = byId("search_input");
    var query = box && box.value ? box.value.trim() : "";
    var count = 0;
    for (var index = 0; index < PRODUCTS.length; index += 1) {
      var item = PRODUCTS[index];
      var matched = query === "" || item.name.indexOf(query) !== -1;
      setCardVisible(item, matched);
      if (matched) { count += 1; }
    }
    setText("list_status", query === "" ? statusAll(count) : statusSearch(count));
  }

  function applyFilter() {
    var categoryBox = byId("category_select");
    var priceBox = byId("max_price_input");
    var category = categoryBox ? categoryBox.value : "";
    var raw = priceBox && priceBox.value ? priceBox.value.trim() : "";
    var limit = raw === "" ? null : Number(raw);
    var count = 0;
    for (var index = 0; index < PRODUCTS.length; index += 1) {
      var item = PRODUCTS[index];
      var categoryOk = category === "" || category === "all" || item.category === category;
      var priceOk = limit === null || (isFinite(limit) && item.price <= limit);
      var visible = categoryOk && priceOk;
      setCardVisible(item, visible);
      if (visible) { count += 1; }
    }
    setText("list_status", statusFilter(count));
  }

  function addToCart() {
    STATE.cart += 1;
    setText("cart_count", cartText(STATE.cart));
  }

  function changeQty(delta) {
    STATE.qty = Math.max(1, Math.min(99, STATE.qty + delta));
    setText("qty_display", qtyText(STATE.qty));
  }

  function submitOrder(event) {
    if (event && event.preventDefault) { event.preventDefault(); }
    var box = byId("order_result");
    if (box) { box.style.display = "block"; }
  }

  function bind() {
    var searchButton = byId("search_button");
    if (searchButton) { searchButton.addEventListener("click", search); }
    var searchBox = byId("search_input");
    if (searchBox) {
      searchBox.addEventListener("keydown", function (event) {
        if (event.key === "Enter") { search(); }
      });
    }
    var filterButton = byId("filter_button");
    if (filterButton) { filterButton.addEventListener("click", applyFilter); }
    for (var index = 0; index < PRODUCTS.length; index += 1) {
      var add = byId(PRODUCTS[index].add_key);
      if (add) { add.addEventListener("click", addToCart); }
    }
    var plus = byId("qty_plus");
    if (plus) { plus.addEventListener("click", function () { changeQty(1); }); }
    var minus = byId("qty_minus");
    if (minus) { minus.addEventListener("click", function () { changeQty(-1); }); }
    var form = byId("order_form");
    if (form) { form.addEventListener("submit", submitOrder); }
    window.addEventListener("hashchange", showView);
    showView();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
"""
