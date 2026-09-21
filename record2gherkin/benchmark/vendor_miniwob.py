"""Build-time vendoring of the MiniWoB++ HTML tree (spec §1).

Run once, from the repository root (the ``miniwob`` package is a build-time dependency only)::

    uv run --no-project --with miniwob python record2gherkin/benchmark/vendor_miniwob.py \\
      --dest record2gherkin/benchmark/miniwob_html

The whole ``<miniwob-pkg>/html`` tree is copied verbatim (no content rewriting: the serving-time
patch lives in :mod:`record2gherkin.benchmark.miniwob_server`) and one ``PROVENANCE.md`` is written
next to the tree with the package version, licence, copy command, file count / byte total and the
sorted ``sha256`` list used for drift detection.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import shutil
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_DEST = MODULE_DIR / "miniwob_html"
DEFAULT_PROVENANCE = MODULE_DIR / "PROVENANCE.md"

PACKAGE_NAME = "miniwob"
PACKAGE_HTML_DIRNAME = "html"
UPSTREAM_URL = "https://github.com/Farama-Foundation/miniwob-plusplus"
LICENSE_NAME = "BSD-3-Clause"
COPY_COMMAND = "uv run --no-project --with miniwob python record2gherkin/benchmark/vendor_miniwob.py --dest record2gherkin/benchmark/miniwob_html"


class VendorError(RuntimeError):
    """Vendoring cannot proceed (package missing, html tree missing, unusable destination)."""


def locate_html_root() -> Path:
    """``<miniwob-pkg>/html`` of the installed build-time package (spec §1.1)."""
    try:
        import miniwob  # noqa: PLC0415 - build-time dependency, imported lazily on purpose
    except ImportError as exc:  # pragma: no cover - build-time guard
        raise VendorError(f"package {PACKAGE_NAME!r} is not importable; run this script with `uv run --no-project --with miniwob`") from exc
    root = Path(miniwob.__file__).resolve().parent / PACKAGE_HTML_DIRNAME
    if not root.is_dir():  # pragma: no cover - build-time guard
        raise VendorError(f"html root not found: {root}")
    return root


def copy_tree(source: Path, dest: Path) -> list[Path]:
    """Recreate ``dest`` as an exact copy of ``source``; returns the copied files (sorted, relative)."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_dir():
            (dest / relative).mkdir(parents=True, exist_ok=True)
        else:
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return [path for path in sorted(dest.rglob("*")) if path.is_file()]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provenance_text(*, source_root: Path, package_version: str, dest: Path, files: list[Path]) -> str:
    """The drift-detection record; byte/file figures count the vendored tree only."""
    total_bytes = sum(path.stat().st_size for path in files)
    lines = [
        "# MiniWoB++ HTML tree — provenance",
        "",
        f"- source package: `{PACKAGE_NAME}` `{package_version}` (PyPI)",
        f"- source root: `{PACKAGE_NAME}/html` (installed location recorded at build time: `{source_root}`)",
        f"- upstream: {UPSTREAM_URL}",
        f"- license: {LICENSE_NAME}",
        f"- vendored destination: `{dest.name}/` (whole tree, no content rewriting)",
        "- copy command:",
        "",
        "```bash",
        COPY_COMMAND,
        "```",
        "",
        f"- files: {len(files)}",
        f"- total bytes: {total_bytes}",
        "",
        f"## sha256 (sorted by relative path, {len(files)} entries)",
        "",
    ]
    for path in files:
        lines.append(f"{sha256_of(path)}  {path.relative_to(dest).as_posix()}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vendor the MiniWoB++ HTML tree (spec §1)")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="destination directory (recreated from scratch)")
    parser.add_argument("--provenance", default=None, help="PROVENANCE.md path (default: next to --dest)")
    parser.add_argument("--source-root", default=None, help="override for the package html root (tests only)")
    args = parser.parse_args(argv)

    dest = Path(args.dest).expanduser().resolve()
    provenance = Path(args.provenance).expanduser().resolve() if args.provenance else dest.parent / "PROVENANCE.md"

    try:
        source_root = Path(args.source_root).expanduser().resolve() if args.source_root else locate_html_root()
        if not source_root.is_dir():
            raise VendorError(f"html root not found: {source_root}")
        package_version = importlib.metadata.version(PACKAGE_NAME)
        files = copy_tree(source_root, dest)
    except VendorError as exc:
        sys.stderr.write(f"vendor_miniwob: {exc}\n")
        return 2

    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(provenance_text(source_root=source_root, package_version=package_version, dest=dest, files=files), encoding="utf-8")
    total_bytes = sum(path.stat().st_size for path in files)
    print(f"vendor_miniwob: copied {len(files)} files ({total_bytes} bytes) from {source_root} -> {dest}")
    print(f"vendor_miniwob: provenance written to {provenance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
