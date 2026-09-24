import json
import os
import time
from typing import Annotated, Any, Dict, List, Union

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.browser_logger import get_browser_logger
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import wait_for_non_loading_dom_state
from testzeus_hercules.utils.get_detailed_accessibility_tree import (
    do_get_accessibility_info,
    rename_children,
)
from testzeus_hercules.utils.logger import logger

# -- spec-r4 §1.2 (R4-A): the interactive-element flatten pipeline, lifted to module level -------
#: The pre-r4 role whitelist (verbatim). Matched against the ``r`` key only when ``extended=False``
#: (today that key never exists in the pipeline — ``rename_children`` is not called — so off-mode
#: role matching stays a dead branch exactly like r3).
INTERACTIVE_ROLES_BASE = {
    "button",
    "link",
    "checkbox",
    "radio",
    "textbox",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "treeitem",
}
#: The pre-r4 tag whitelist (verbatim).
INTERACTIVE_TAGS_BASE = {"a", "button", "input", "select", "textarea"}
#: spec-r4 §1.2: extended-mode additions (flag ``--md-extended`` only).
EXTENDED_TAGS = {"div", "span", "li", "tr", "td", "th", "img", "label"}
EXTENDED_ROLES = {"row", "cell", "listitem", "img"}
#: A div/span/... node is only collected in extended mode when it carries at least one readable
#: identity attribute (no anonymous node flooding).
EXTENDED_IDENTITY_KEYS = ("name", "title", "description", "text", "aria-label", "class", "id")
#: spec-r4 §1.2: hard cap of the *final table* only (never the injection, never the judging).
#: Active in extended mode only; off mode keeps the (uncapped) r3 behaviour.
MAX_FLATTEN_NODES = 150


def compact_value(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        if len(cleaned) > 300:
            return f"{cleaned[:300]}...[truncated]"
        return cleaned
    if isinstance(value, list):
        return [compact_value(item) for item in value[:30]]
    if isinstance(value, dict):
        return {str(key): compact_value(item_value) for key, item_value in value.items() if item_value not in ("", None, [], {})}
    return value


def compact_interactive_node(node: dict) -> dict:
    # spec-r4 review MF-1: "class" is unconditionally allowed through compact.  Off mode never
    # carries a class key (the DOM attribute fetch only appends it in extended mode and compact
    # filters by ``key in node``), so the off output is byte-identical to r3 (locked by golden T3a/T9).
    allowed_keys = (
        "md",
        "tag",
        "role",
        "r",
        "name",
        "title",
        "description",
        "text",
        "aria-label",
        "class",
        "value",
        "tag_type",
        "type",
        "placeholder",
        "tooltip",
        "clickable",
        "focusable",
        "checked",
        "selected",
        "disabled",
        "expanded",
        "level",
        "options",
        "additional_info",
    )
    return {key: compact_value(node[key]) for key in allowed_keys if key in node and node[key] not in ("", None, [], {})}


def flatten_interactive_nodes(root: dict, *, extended: bool, max_nodes: int = MAX_FLATTEN_NODES) -> List[Dict[str, Any]]:
    """Flatten the accessibility/DOM tree into the final interactive-element table.

    ``extended=False`` reproduces the r3 ``flatten_elements`` behaviour byte-for-byte (same
    traversal order, same parent name/title inheritance, same inclusion test).  ``extended=True``
    adds the spec-r4 §1.2 collection rules (extended tags with an identity key, extended roles
    matched on the live ``role`` key) and caps the table at ``max_nodes`` entries with a
    ``[R2G_MD_TRUNCATED]`` warning (no sentinel node in the JSON output).
    """
    elements: List[Dict[str, Any]] = []
    roles = INTERACTIVE_ROLES_BASE | (EXTENDED_ROLES if extended else set())
    dropped = 0

    def flatten_elements(node: dict, parent_name: str = "", parent_title: str = "") -> None:
        nonlocal dropped

        if "children" in node:
            # Get current node's name and title for passing to children
            current_name = node.get("name", parent_name)
            current_title = node.get("title", parent_title)

            for child in node["children"]:
                # If child doesn't have name/title, it will use parent's values
                if "name" not in child and current_name:
                    child["name"] = current_name
                if "title" not in child and current_title:
                    child["title"] = current_title
                flatten_elements(child, current_name, current_title)

        # Include elements with interactive roles or clickable/focusable elements
        # (spec-r4 §1.2: ordered short-circuit, off mode degenerates to the r3 test exactly.
        #  Extended tags (div/span/...) enter ONLY through ``extra_hit`` — i.e. gated on a readable
        #  identity key — because plan-r4 §1-R4-A and spec §5-T3(b) demand "no anonymous node
        #  flooding": a bare {md, tag: div} without any identity must stay out of the table.)
        role_hit = node.get("r", "").lower() in roles or (extended and str(node.get("role") or "").lower() in roles)
        tag_hit = node.get("tag", "").lower() in INTERACTIVE_TAGS_BASE
        extra_hit = extended and node.get("tag", "").lower() in EXTENDED_TAGS and any(node.get(k) not in ("", None, [], {}) for k in EXTENDED_IDENTITY_KEYS)
        if "md" in node and (role_hit or tag_hit or node.get("clickable", False) or node.get("focusable", False) or extra_hit):
            if extended and len(elements) >= max_nodes:
                dropped += 1
                return
            new_node = node.copy()
            new_node.pop("children", None)
            elements.append(compact_interactive_node(new_node))

    if isinstance(root, dict):
        flatten_elements(root)
    if dropped:
        logger.warning("[R2G_MD_TRUNCATED] interactive table capped at %d (dropped >=%d)", max_nodes, dropped)
    return elements


@tool(
    agent_names=["browser_nav_agent"],
    description="""DOM Type dict Retrieval Tool, giving all interactive elements on page.
Notes: [Elements ordered as displayed, Consider ordinal/numbered item positions, List ordinal represent z-index on page]""",
    name="get_interactive_elements",
)
async def get_interactive_elements() -> Annotated[str, "DOM type dict giving all interactive elements on page"]:
    add_event(EventType.INTERACTION, EventData(detail="get_interactive_elements"))
    start_time = time.time()
    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    await browser_manager.wait_for_page_and_frames_load()
    page = await browser_manager.get_current_page()

    await browser_manager.wait_for_load_state_if_enabled(page=page)

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    extracted_data = ""
    await wait_for_non_loading_dom_state(page, 1)

    extracted_data = await do_get_accessibility_info(page, only_input_fields=False)

    # Flatten the hierarchy into a list of elements (spec-r4 §1.2: module-level entry point)
    extended = get_global_conf().get_md_interactive_extended().strip().lower() == "true"
    flattened_data = flatten_interactive_nodes(extracted_data, extended=extended, max_nodes=MAX_FLATTEN_NODES) if isinstance(extracted_data, dict) else []

    elapsed_time = time.time() - start_time
    logger.info(f"Get DOM Command executed in {elapsed_time} seconds")

    # Count elements
    rr = 0
    if isinstance(flattened_data, list):
        rr = len(flattened_data)
    add_event(
        EventType.DETECTION,
        EventData(detail=f"DETECTED {rr} components"),
    )

    #     if isinstance(extracted_data, dict):
    #         extracted_data = await rename_children(extracted_data)

    #     extracted_data = json.dumps(extracted_data, separators=(",", ":"))
    #     extracted_data_legend = """Key legend:
    # t: tag
    # r: role
    # c: children
    # n: name
    # tl: title
    # Dict >>
    # """
    #     extracted_data = extracted_data_legend + extracted_data
    extracted_data = json.dumps(flattened_data, separators=(",", ":"))
    return extracted_data or "Its Empty, try something else"  # type: ignore
