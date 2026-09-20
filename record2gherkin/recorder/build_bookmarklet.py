"""把 recorder.js 打包成 bookmarklet 单行串（纯标准库，零依赖）。

用法：
    python record2gherkin/recorder/build_bookmarklet.py

产物：同目录 bookmarklet.txt，内容为 `javascript:` + 全量 URL 编码后的脚本。
复制该文件内容新建书签即可在任意同源页面上点一下开始录制。
对 CSP 严格或 URL 长度敏感的站点，备选路径是把 recorder.js 原文粘贴进 DevTools Snippets 执行。
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from pathlib import Path

RECORDER_DIR = Path(__file__).resolve().parent
SOURCE_PATH = RECORDER_DIR / "recorder.js"
OUTPUT_PATH = RECORDER_DIR / "bookmarklet.txt"

IIFE_PATTERN = re.compile(r"^\(\s*function\s*\(")


def wrap_as_iife(script: str) -> str:
    """源码已是 IIFE 时原样返回，否则包裹为 (function(){ ... })();"""
    if IIFE_PATTERN.match(script.lstrip()):
        return script
    return "(function(){\n" + script + "\n})();"


def build_payload(script: str) -> str:
    return "javascript:" + urllib.parse.quote(wrap_as_iife(script), safe="")


def main() -> int:
    script = SOURCE_PATH.read_text(encoding="utf-8")
    payload = build_payload(script)
    # 不加尾随换行：书签内容需与 payload 逐字节一致
    OUTPUT_PATH.write_text(payload, encoding="utf-8")
    print(f"recorder.js: {len(script)} chars -> bookmarklet.txt: {len(payload)} chars")
    print(f"written: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
