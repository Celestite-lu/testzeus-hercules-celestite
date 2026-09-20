# AGENTS.md — testzeus-hercules

Hercules: open-source AI agent that turns Gherkin feature files into end-to-end tests (web, API, API-security, SQL, accessibility) via LangGraph orchestration + Playwright. Python `>=3.11,<3.14`, managed with **uv**.

## Commands

```bash
make install          # uv sync + playwright install --with-deps
make fmt              # isort + black (target py311, line length 200) on testzeus_hercules/ and tests/
make lint             # fmt + black --check (mypy is intentionally commented out)
make test             # lint + pytest with coverage, --maxfail=1 (needs LLM keys + browsers)
make run-interactive  # uv run python -m testzeus_hercules.interactive
```

- Focused checks (start here for LangGraph/config changes): `uv run pytest tests/test_simple_hercules_langgraph.py tests/test_mcp_langgraph.py tests/test_llm_cli_aliases.py tests/test_cli_config_file.py`
- Full `make test` runs real Gherkin scenarios against live browsers/LLMs — slow and key-dependent; prefer targeted pytest runs during development.
- Format with `make fmt` before committing; black line length is **200** (Makefile), not the ruff default.

## Layout

- `testzeus_hercules/` — the package. `__main__.py` = `testzeus-hercules` CLI; `mcp_server.py` = `testzeus-hercules-mcp` MCP server; `config.py` = global config singleton; `telemetry.py` = event collection.
- `testzeus_hercules/core/simple_hercules.py` — LangGraph state graph orchestrating the agents; `core/runner.py` (`SingleCommandInputRunner`) executes one scenario.
- `core/agents/` — one agent per test domain (browser, api, sec, sql, mcp, executor, planner, time_keeper). All extend `base_nav_agent.py` / `multimodal_base_nav_agent.py`; new agents must register in `core/agent_registry.py` and get wired into the graph in `simple_hercules.py`.
- `core/tools/` — LangChain tools agents can call (browser selectors, api_calls, sql_calls, accessibility, python sandbox). Registered via `core/tools/tool_registry.py`.
- `core/extra_tools/` — optional/community tools; `core/memory/` — state + prompt compression.
- `utils/` — gherkin parse/generate (`gherkin_helper.py`), LLM plumbing (`llm_helper.py`, `litellm_helper.py`), JUnit/HTML reporting (`junit_helper.py`), DOM/accessibility-tree helpers, `logger.py`.
- `opt/` — default project root at runtime (`PROJECT_SOURCE_ROOT`): `input/test.feature`, `output/`, `proofs/`, `log_files/`, `test_data/`. See `docs/run_guide.md`; `EXECUTE_BULK=true` makes each `tests/` subdir its own project base.
- `frontend/` — static HTML/JS UIs (interactive + non-interactive), no build step.
- `tests/` — pytest; feature-driven tests live in `tests/test_features/`; `tests/test_not_for_ci/` is excluded from CI.

## Rules & gotchas

- Configuration goes through `testzeus_hercules.config.get_global_conf()` — don't read env vars ad hoc in agents/tools. All runtime paths derive from `PROJECT_SOURCE_ROOT` (default `./opt`); env-var reference: `docs/environment_variables.md`.
- Log with `from testzeus_hercules.utils.logger import logger`, never `print` (ruff rule T20).
- Playwright is pinned `<=1.49.0` — don't bump casually; `pyproject.toml` `[tool.uv] override-dependencies` pins transitive deps for security alerts, keep them when regenerating the lockfile.
- Agent LLM/model routing comes from `agents_llm_config.json` (see `agents_llm_config-example.json.txt`, `core/agents_llm_config_manager.py`); Portkey fallback/load-balance is configured in `core/config_portkey_loader.py`.
- Python sandbox tool (`core/tools/execute_python_sandbox.py`) is tenant-restricted (`SANDBOX_TENANT_ID`) — respect mode boundaries when touching it; see `docs/python_sandbox_execution.md`.
- Conventional commits required (`feat(...)`, `fix(...)` — emoji fine).

## Remotes

This checkout is a fork: `origin` → `Celestite-lu/testzeus-hercules-celestite`, `upstream` → `test-zeus-ai/testzeus-hercules`. Push branches to `origin`.

## 二开「录制即用例」开发规约（总编排与所有子代理必须遵守）

总计划见根目录 `PLAN.md`（本地文件，已 gitignore）。所有子代理开工前必读 `dev_docs/README.md`。

- **文档固化（强制）**：plan / spec / 审查报告 / 测试汇报必须写入 `dev_docs/<模块>/`，命名与生命周期见 `dev_docs/README.md`。没有固化文档的工作视为未完成。
- **核心目标优先（最高原则）**：绝不在低收益边界问题上打转，绝不偏移当前阶段的核心目标。边界情况一律写入 spec 的 Out of Scope 或 test-report 的已知问题清单，核心功能完成后再用测试案例完善。审查子代理只拦"会导致核心目标失败的问题"，不吹毛求疵。
- **密钥安全**：`LLM-Key.txt`、`.env.local`、`PLAN.md` 已 gitignore；任何情况下不得把密钥明文写入文件、日志输出或 git 提交。需要 key 时从文件读取（如 `LLM_MODEL_API_KEY=$(cat LLM-Key.txt)`）。
- **Git 纪律**：子代理不做任何 git commit / branch / merge 操作，由总编排代理统一操作；功能在新分支开发，测试通过后合入 main，合入后由总编排代理启动分析子代理复核。
- **代码规约**：二开新代码进 `record2gherkin/` 包 + `tests/record2gherkin/`，不改 `testzeus_hercules/`（除非 PLAN.md 明确的接线点）；black line-length 200 + isort；Python 侧用 logger 不用 print（录制器 JS 除外）。实验产物放 `dev_runs/`（gitignore）。
