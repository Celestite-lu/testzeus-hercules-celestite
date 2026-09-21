"""MiniWoB++ public-benchmark harness (spec: ``dev_docs/benchmark/spec.md``).

Serving + patching + reward collection of the vendored MiniWoB++ HTML tree, goal pre-reading,
Gherkin rendering, orchestration CLI and the honest metric aggregation.  Build-time scripts
(``vendor_miniwob``, ``build_task_table``) are run with ``uv run --no-project --with <pkg>``; the
runtime modules depend on the repository environment only.
"""
