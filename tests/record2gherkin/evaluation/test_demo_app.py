"""A 组：渲染与变异（spec §9 A1-A12，纯字符串断言，无浏览器）。"""

from __future__ import annotations

import json
import re
import time

import pytest
from record2gherkin.evaluation.demo_app import (
    CART_INITIAL_TEXT,
    CART_TEMPLATE,
    LIST_VIEW,
    M2_BANNER_TEXT,
    M2_FIELD_GROUPS,
    M4_TABLE,
    MUTATIONS,
    ORDER_FIELD_GROUPS,
    ORDER_VIEW,
    PRODUCT_COUNT,
    QTY_INITIAL_TEXT,
    QTY_TEMPLATE,
    REGISTRY,
    STATUS_ALL_TEMPLATE,
    STATUS_ALL_TEXT,
    STATUS_FILTER_TEMPLATE,
    STATUS_SEARCH_TEMPLATE,
    element_html,
    element_keys,
    get_element,
    inline_script,
    m4_text,
    mutated_attributes,
    order_group_order,
    render_page,
    selector_for,
    stable_class,
    stable_id,
    stable_testid,
)

M3_ID_RE = re.compile(r"m3-[0-9a-f]{8}")
M3_CLASS_RE = re.compile(r"c-[0-9a-f]{8}")
#: 注册表里的稳定文本（element text / option 文本 / 子节点文本）。
STABLE_TEXTS = tuple(text for element in REGISTRY for text in ([element.text] if element.text else []) + [label for _, label in element.options] + list(element.children))


def _positions(html_text: str, keys: tuple[str, ...]) -> list[str]:
    """Keys ordered by their first occurrence position in ``html_text``."""
    found = [(key, html_text.index(f'id="{stable_id(key)}"')) for key in keys if f'id="{stable_id(key)}"' in html_text]
    return [key for key, _ in sorted(found, key=lambda item: item[1])]


def _neutralize_variant_attributes(html_text: str, mutation: str, seed: int) -> str:
    """Replace every registry id/class/data-testid (HTML and inline-script literals) by a placeholder.

    Order matters: ``data-testid="X"`` ends with ``id="X"``, so the testid attribute has to be
    normalised before the id attribute.  The final quoted-value pass only affects ``for="..."`` and
    the inline script's ``IDS`` map values.
    """
    for key in element_keys():
        attrs = mutated_attributes(key, mutation, seed)
        html_text = html_text.replace(f'data-testid="{attrs["testid"]}"', 'data-testid="<TID>"')
        html_text = html_text.replace(f'id="{attrs["id"]}"', 'id="<ID>"')
        html_text = html_text.replace(f'class="{attrs["class"]}"', 'class="<CLS>"')
        html_text = html_text.replace(f'"{attrs["id"]}"', '"<ID>"')
    return html_text


# ---------------------------------------------------------------------------------------------


def test_a1_m0_is_deterministic() -> None:
    """A1 M0 确定性：同参数两次渲染逐字节相同。"""
    assert render_page("M0", 0) == render_page("M0", 0)


def test_a2_m0_contains_registry_surface() -> None:
    """A2 M0 完整性：注册表全部稳定 id/testid/关键文本在产物中。"""
    html_text = render_page("M0", 0)
    for key in element_keys():
        assert f'id="{stable_id(key)}"' in html_text, f"missing id for {key}"
        assert f'data-testid="{stable_testid(key)}"' in html_text, f"missing data-testid for {key}"
        assert f'class="{stable_class(key)}"' in html_text, f"missing class for {key}"
    for text in STABLE_TEXTS:
        assert text in html_text, f"missing stable text {text!r}"


def test_a3_m1_reverses_order_field_groups_and_keeps_submit_last() -> None:
    """A3 M1：order 字段组顺序为反转序且 submit 仍最后；组内文本不变。"""
    baseline = render_page("M0", 0, view=ORDER_VIEW)
    mutated = render_page("M1", 0, view=ORDER_VIEW)
    expected_fields = list(reversed(ORDER_FIELD_GROUPS[:-1])) + [ORDER_FIELD_GROUPS[-1]]
    field_keys = tuple(key for group in expected_fields for key in (element.key for element in REGISTRY if element.group == group))
    assert _positions(mutated, field_keys) == list(field_keys)
    assert _positions(baseline, field_keys) != list(field_keys)
    # 组内元素与文本不变：除顺序外，字段分组 HTML 片段应逐字节相同。
    for group in ORDER_FIELD_GROUPS:
        fragments = [element_html(element.key, "M1", 0) for element in REGISTRY if element.group == group]
        for fragment in fragments:
            assert fragment in mutated


def test_a3b_m1_swaps_list_field_groups() -> None:
    """A3 补充 M1：list 视图搜索组与筛选组互换，组内容不变。"""
    mutated = render_page("M1", 0, view=LIST_VIEW)
    search_keys = ("search_input", "search_button")
    filter_keys = ("category_select", "max_price_input", "filter_button")
    positions = _positions(mutated, filter_keys + search_keys)
    assert positions[: len(filter_keys)] == list(filter_keys)
    assert positions[len(filter_keys) :] == list(search_keys)


def test_a4_m2_wraps_every_field_group_and_adds_banner() -> None:
    """A4 M2：m2-card 包裹数 = 字段分组数；横幅文本固定存在；字段 label 文本不变。"""
    html_text = render_page("M2", 0)
    assert html_text.count('class="m2-card"') == len(M2_FIELD_GROUPS)
    assert html_text.count('class="m2-card" style="border:1px solid #ddd;padding:12px;margin:8px 0"') == len(M2_FIELD_GROUPS)
    assert M2_BANNER_TEXT in html_text
    assert html_text.count("m2-flex") == 2  # 下单表单 + 列表筛选容器
    baseline = render_page("M0", 0)
    for key in ("label_name", "label_email", "label_address", "label_shipping", "label_promo"):
        assert element_html(key, "M2", 0) == element_html(key, "M0", 0)
    # M2 不改既有元素文本/属性：注册表元素的 HTML 片段在 M0/M2 下逐字节相同（除 m2 容器与横幅）。
    for key in element_keys():
        if get_element(key).tag in {"form", "div"}:
            continue
        assert element_html(key, "M2", 0) in html_text
        assert element_html(key, "M2", 0) in baseline


def test_a5_m3_is_idempotent_per_seed() -> None:
    """A5 M3 幂等：同 seed 两次渲染逐字节相同。"""
    assert render_page("M3", 42) == render_page("M3", 42)


def test_a6_m3_ids_differ_across_seeds_for_every_element() -> None:
    """A6 M3 异 seed：注册表每元素 id 在 seed A/B 下必不同。"""
    for key in element_keys():
        assert mutated_attributes(key, "M3", 1)["id"] != mutated_attributes(key, "M3", 2)["id"], key
        assert mutated_attributes(key, "M3", 1)["testid"] != mutated_attributes(key, "M3", 2)["testid"], key
        assert mutated_attributes(key, "M3", 1)["class"] != mutated_attributes(key, "M3", 2)["class"], key
    seed_a = render_page("M3", 1)
    seed_b = render_page("M3", 2)
    for key in element_keys():
        stable = stable_id(key)
        assert f'id="{mutated_attributes(key, "M3", 1)["id"]}"' in seed_a
        assert f'id="{mutated_attributes(key, "M3", 2)["id"]}"' in seed_b
        assert f'id="{stable}"' not in seed_a


def test_a7_m3_attribute_format_and_semantic_surface_unchanged() -> None:
    """A7 M3 格式：id/data-testid 匹配 m3-[0-9a-f]{8}、类名 c-[0-9a-f]{8}；aria-label/placeholder/label 文本不变。"""
    html_text = render_page("M3", 7)
    for key in element_keys():
        attrs = mutated_attributes(key, "M3", 7)
        assert re.fullmatch(r"m3-[0-9a-f]{8}", attrs["id"])
        assert re.fullmatch(r"m3-[0-9a-f]{8}", attrs["testid"])
        assert re.fullmatch(r"c-[0-9a-f]{8}", attrs["class"])
        assert f'id="{attrs["id"]}"' in html_text
        assert f'data-testid="{attrs["testid"]}"' in html_text
        assert f'class="{attrs["class"]}"' in html_text
    baseline = render_page("M0", 0)
    for element in REGISTRY:
        for name, value in element.attrs:
            assert f'{name}="{value}"' in baseline
            assert f'{name}="{value}"' in html_text, f"M3 changed {name} of {element.key}"
        for option_value, option_label in element.options:
            assert f'<option value="{option_value}">{option_label}</option>' in html_text
    assert STATUS_ALL_TEXT in html_text
    assert CART_INITIAL_TEXT in html_text
    assert QTY_INITIAL_TEXT in html_text


def test_a8_m3_changes_nothing_but_locating_attributes() -> None:
    """A8 M3 vs M0：除 id/class/data-testid 外（含 aria-label/placeholder/label 属性值）可见内容相同。"""
    baseline = _neutralize_variant_attributes(render_page("M0", 0), "M0", 0)
    mutated = _neutralize_variant_attributes(render_page("M3", 11), "M3", 11)
    assert baseline == mutated


def test_a9_m4_rewrites_only_table_entries() -> None:
    """A9 M4：各参与元素文本 ∈ 改写表；同 seed 确定；order_result/状态文案不变。"""
    seed = 3
    html_text = render_page("M4", seed)
    assert render_page("M4", seed) == html_text
    for key, table in M4_TABLE.items():
        element = get_element(key)
        rewritten = m4_text(key, seed)
        assert rewritten in table
        assert f'aria-label="{rewritten}"' in html_text
        assert f"{rewritten}</{element.tag}>" in html_text
    for key in ("list_status", "cart_count", "qty_display", "order_result"):
        assert element_html(key, "M4", seed) == element_html(key, "M0", 0)
    assert STATUS_ALL_TEXT in html_text
    assert "下单成功，感谢您的购买！" in html_text
    for element in REGISTRY:
        if element.key in M4_TABLE:
            continue
        assert element_html(element.key, "M4", seed) == element_html(element.key, "M0", 0), element.key


def test_a10_m4_is_probabilistic_across_seeds() -> None:
    """A10 M4 概率性：固定枚举 8 个 seed，至少出现 2 种不同改写组合。"""
    combos = {tuple(m4_text(key, seed) for key in sorted(M4_TABLE)) for seed in range(8)}
    assert len(combos) >= 2


def test_a11_render_is_clock_and_randomness_free() -> None:
    """A11 渲染纯净性：产物不含时钟戳/随机数痕迹（两次不同时刻渲染逐字节相同）。"""
    first = render_page("M3", 5)
    time.sleep(0.01)
    second = render_page("M3", 5)
    assert first == second
    script = inline_script("M3", 5)
    for forbidden in ("Math.random", "Date.now", "new Date", "performance.now", "crypto.getRandomValues"):
        assert forbidden not in script, f"inline script depends on {forbidden}"


def test_a12_inline_script_bindings_exist_in_rendered_dom() -> None:
    """A12 JS 绑定完整性：内联 JS 引用的每个 id/testid 字面量都在该产物 DOM 中，且不外挂字面量。

    「M3 下页面自身行为完好」的离线证据（spec §2.1 同源总则，review 必改 2）：JS 通过
    ``IDS`` 映射取变异后的 id，映射块之外不得出现任何注册表 id/testid 字面量。
    """
    for mutation, seed in (("M0", 0), ("M3", 42)):
        html_text = render_page(mutation, seed)
        script = inline_script(mutation, seed)
        match = re.search(r"var IDS = (\{.*?\});", script, re.S)
        assert match, "inline script must publish its IDS map"
        ids = json.loads(match.group(1))
        assert set(ids) == set(element_keys()), "IDS map must cover the whole registry"
        for key, value in ids.items():
            assert mutated_attributes(key, mutation, seed)["id"] == value
            assert f'id="{value}"' in html_text
        stripped = script.replace(match.group(0), "")
        assert not M3_ID_RE.findall(stripped), f"{mutation}: hardcoded mutated id in inline script"
        for key in element_keys():
            assert f'"{stable_id(key)}"' not in stripped, f"{mutation}: hardcoded registry id {stable_id(key)} in inline script"
            assert f'"{stable_testid(key)}"' not in stripped, f"{mutation}: hardcoded registry testid {stable_testid(key)} in inline script"
        assert not re.search(r"getElementById\(\s*\"", stripped), f"{mutation}: literal id passed to getElementById"
        assert "data-testid" not in stripped


def test_a12b_inline_script_templates_match_registry_texts() -> None:
    """A12 补充：JS 里的动态文案模板与注册表初始文案同源（防止状态文案漂移）。"""
    script = inline_script("M0", 0)
    for template in (STATUS_ALL_TEMPLATE, STATUS_SEARCH_TEMPLATE, STATUS_FILTER_TEMPLATE, CART_TEMPLATE, QTY_TEMPLATE):
        head, _, tail = template.partition("{n}")
        assert f'"{head}" + n + "{tail}"' in script, f"missing JS template for {template!r}"
    assert STATUS_ALL_TEMPLATE.format(n=PRODUCT_COUNT) == STATUS_ALL_TEXT
    assert CART_TEMPLATE.format(n=0) == CART_INITIAL_TEXT
    assert QTY_TEMPLATE.format(n=1) == QTY_INITIAL_TEXT
    assert f'"{STATUS_ALL_TEXT}"' not in script  # JS 不另行硬编码初始文案


def test_registry_lookup_and_validation_helpers() -> None:
    """补充：注册表访问、变异/seed 校验与 selector 优先级链（spec §5）。"""
    from record2gherkin.evaluation.demo_app import selector_from_attributes

    assert get_element("search_button").tag == "button"
    with pytest.raises(KeyError):
        get_element("nope")
    with pytest.raises(ValueError):
        render_page("M9", 0)
    with pytest.raises(ValueError):
        render_page("M0", -1)
    with pytest.raises(ValueError):
        render_page("M0", 0, view="nope")
    assert selector_for("search_input") == "#search-input"
    assert selector_from_attributes({"id": "x", "testid": "y", "class": "z"}) == "#x"
    assert selector_from_attributes({"id": None, "testid": "y", "class": "z"}) == '[data-testid="y"]'
    assert selector_from_attributes({"id": None, "testid": None, "class": "z zz"}) == ".z"
    with pytest.raises(ValueError):
        selector_from_attributes({"id": None, "testid": None, "class": None})
    assert all(mutation in MUTATIONS for mutation in ("M0", "M1", "M2", "M3", "M4"))


def test_order_group_order_matches_spec() -> None:
    """补充：M1 的 order 分组序列与 §2.2 表格逐字一致。"""
    assert order_group_order("M0") == ("g_nav_order", *ORDER_FIELD_GROUPS, "g_result")
    assert order_group_order("M1") == ("g_nav_order", "g_qty", "g_promo", "g_shipping", "g_address", "g_email", "g_name", "g_submit", "g_result")
