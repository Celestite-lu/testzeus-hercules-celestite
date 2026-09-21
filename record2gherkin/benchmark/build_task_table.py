"""Build-time generation of the static task table ``tasks.json`` (spec §2).

Run once, from the repository root (``browsergym-miniwob`` is a build-time dependency only)::

    uv run --no-project --with browsergym-miniwob python record2gherkin/benchmark/build_task_table.py \\
      --html-root record2gherkin/benchmark/miniwob_html --dest record2gherkin/benchmark/tasks.json

Enumerates ``browsergym.miniwob.ALL_MINIWOB_TASKS`` — the **package-level list**; ``browsergym.miniwob.all``
is a sub-module and is not iterable (review 必改 1.2).  Any of the hard failures below (task count,
duplicate id, ``task_id != "miniwob." + subdomain``, missing HTML file) aborts with a non-zero exit
code and writes no artefact.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_HTML_ROOT = MODULE_DIR / "miniwob_html"
DEFAULT_DEST = MODULE_DIR / "tasks.json"

REGISTRY_PACKAGE = "browsergym-miniwob"
EXPECTED_TASK_COUNT = 125
#: spec §0 口径 2: native 10s → 240s (parameterisable at serving time through ``r2g_ms``).
EPISODE_MAX_TIME_MS_DEFAULT = 240000
#: Family = subdomain with a trailing pure-number segment stripped (spec §2.2).
TRAILING_NUMBER = re.compile(r"-\d+$")
VISUAL_PREFIX = "visual-"


class BuildError(RuntimeError):
    """Hard failure of the task-table build (spec §2.1: no artefact is written)."""


def family_for(subdomain: str) -> str:
    """``click-test-2`` → ``click-test``; ``email-inbox-forward-nl-turk`` stays as-is (spec §2.2)."""
    return TRAILING_NUMBER.sub("", subdomain)


def is_visual(subdomain: str) -> bool:
    return subdomain.startswith(VISUAL_PREFIX)


def load_registry() -> list[Any]:
    """The package-level task-class list (review 必改 1.2: never ``browsergym.miniwob.all``)."""
    try:
        from browsergym.miniwob import (  # noqa: PLC0415 - build-time dependency
            ALL_MINIWOB_TASKS,
        )
    except ImportError as exc:  # pragma: no cover - build-time guard
        raise BuildError(f"{REGISTRY_PACKAGE} is not importable; run this script with `uv run --no-project --with browsergym-miniwob`") from exc
    return list(ALL_MINIWOB_TASKS)


def build_payload(*, html_root: Path, generated_at: str, registry_version: str) -> dict[str, Any]:
    """Assemble the task table; every §2.1 hard failure raises :class:`BuildError`."""
    tasks: list[dict[str, Any]] = []
    for task_class in load_registry():
        task_id = str(task_class.get_task_id())
        subdomain = str(task_class.subdomain)
        if task_id != f"miniwob.{subdomain}":
            raise BuildError(f"task id/subdomain mismatch: {task_id!r} vs subdomain {subdomain!r}")
        html_path = html_root / "miniwob" / f"{subdomain}.html"
        if not html_path.is_file():
            raise BuildError(f"missing task page for {task_id}: {html_path}")
        tasks.append(
            {
                "task_id": task_id,
                "subdomain": subdomain,
                "family": family_for(subdomain),
                "visual": is_visual(subdomain),
                "desc": str(task_class.desc),
                "html": f"{html_root.name}/miniwob/{subdomain}.html",
            }
        )

    if len(tasks) != EXPECTED_TASK_COUNT:
        raise BuildError(f"registry holds {len(tasks)} tasks, expected {EXPECTED_TASK_COUNT}")
    ids = [task["task_id"] for task in tasks]
    if len(set(ids)) != len(ids):
        duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
        raise BuildError(f"duplicate task ids in registry: {duplicates}")

    tasks.sort(key=lambda task: task["task_id"])
    return {
        "generated_at": generated_at,
        "source": f"{REGISTRY_PACKAGE} {registry_version}, registry ALL_MINIWOB_TASKS ({len(tasks)} tasks)",
        "episode_max_time_ms_default": EPISODE_MAX_TIME_MS_DEFAULT,
        "tasks": tasks,
    }


def summarize(payload: dict[str, Any]) -> str:
    tasks = payload["tasks"]
    families: dict[str, int] = {}
    for task in tasks:
        families[task["family"]] = families.get(task["family"], 0) + 1
    visual = [task["task_id"] for task in tasks if task["visual"]]
    lines = [
        f"build_task_table: {len(tasks)} tasks, {len(families)} families, {len(visual)} visual",
        f"build_task_table: visual tasks = {visual}",
    ]
    suffixes = sorted((name, count) for name, count in families.items() if count > 1)
    if suffixes:
        lines.append(f"build_task_table: families with >1 member = {suffixes}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the static MiniWoB++ task table (spec §2)")
    parser.add_argument("--html-root", default=str(DEFAULT_HTML_ROOT), help="vendored html root (miniwob_html/)")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="tasks.json destination")
    args = parser.parse_args(argv)

    html_root = Path(args.html_root).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()
    if not (html_root / "miniwob").is_dir():
        sys.stderr.write(f"build_task_table: html root does not contain miniwob/: {html_root} (run vendor_miniwob.py first)\n")
        return 2

    try:
        import importlib.metadata  # noqa: PLC0415 - build-time only

        registry_version = importlib.metadata.version(REGISTRY_PACKAGE)
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = build_payload(html_root=html_root, generated_at=generated_at, registry_version=registry_version)
    except BuildError as exc:
        sys.stderr.write(f"build_task_table: {exc}\n")
        return 2

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(summarize(payload))
    print(f"build_task_table: written {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
