"""``uv run python -m record2gherkin <sub>`` entry point (spec §0: no console-script, no pyproject change)."""

from record2gherkin.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
