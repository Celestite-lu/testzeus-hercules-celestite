# 子进程编排 CLI — 测试报告（test-report.md）

> 实现对象：`record2gherkin/cli.py` + `record2gherkin/__main__.py`（规格 `dev_docs/cli/spec.md`，含审查修订 R1/R2/R3）。
> 测试：`tests/record2gherkin/cli/`（54 条测试项 = spec §8 的 32 条用例展开为 37 条 + 17 条加固用例）。
> 全程离线：无网络、无真实 LLM、无真实 key、无外部站点；浏览器用例只跑本地 `file://` fixture。

## 1. 测试命令与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/record2gherkin/cli -q` | **54 passed** in 5.01s |
| `uv run pytest tests/record2gherkin -q` | **292 passed** in 22.59s（基线 238 + 本模块 54，无回归） |
| `uv run black --check -l 200 record2gherkin/cli.py record2gherkin/__main__.py tests/record2gherkin/cli` | 9 files would be left unchanged |
| `uv run isort --check-only record2gherkin/cli.py record2gherkin/__main__.py tests/record2gherkin/cli` | clean |
| `uv run python -m record2gherkin --help` / `record --manual` | exit 0 / exit 0 |

测试用例注射纪律：`run_feature` 在 D 组全部 monkeypatch（不启动 Hercules 子进程）；`--polish` / `--llm` 通过 `cli._make_polisher` / `cli._make_analyzer` 接缝注入离线 fake；record 用例走真实 headless Chromium（本地 fixture 页）。

## 2. spec §8 用例覆盖对照

| # | 用例 | 测试函数（`tests/record2gherkin/cli/`） |
|---|---|---|
| 1 | help 列四子命令 | `test_parser.py::test_help_lists_four_subcommands`（+ `test_module_entry_point_help`：`python -m record2gherkin --help` 真子进程） |
| 2 | 无子命令 exit 2 | `test_parser.py::test_missing_subcommand_exit_2` |
| 3 | configure_logger 被调用 | `test_parser.py::test_configure_logger_called` |
| 4 | `record --manual` 文案 | `test_record.py::test_manual_mode_prints_bookmarklet_usage` |
| 5 | fixture 录制往返（page 驱动 2 次点击） | `test_record.py::test_record_fixture_roundtrip` |
| 6 | 空录制 warning（R1：events 恰 1 条 navigate） | `test_record.py::test_record_empty_recording_warns` |
| 7 | 不可达 URL exit 2 | `test_record.py::test_record_unreachable_url_exit_2` |
| 8 | 默认路径 `<stem>.feature` | `test_generate.py::test_generate_basic_feature_written` |
| 9 | `--out` 指定路径 | `test_generate.py::test_generate_out_flag` |
| 10 | `--test-data` 填充占位符 | `test_generate.py::test_generate_test_data_fills_placeholder` |
| 11 | 缺值/多余键双警告 | `test_generate.py::test_generate_missing_test_data_warns` |
| 12 | 不给 `--test-data` | `test_generate.py::test_generate_no_test_data_flag_warns_needed_keys` |
| 13 | 形态错误 + bool/空白值（R3） | `test_generate.py::test_generate_bad_test_data_shape_exit_2`、`::test_generate_unusable_test_data_values_are_treated_as_absent` |
| 14 | events 缺失/非法 JSON | `test_generate.py::test_generate_bad_events_exit_2` |
| 15 | `--polish` 回退骨架 | `test_generate.py::test_generate_polish_factory_seam_fallback` |
| 16 | `--polish` 采纳润色稿 | `test_generate.py::test_generate_polish_factory_seam_adopted` |
| 17 | 无 polish 逐字节确定 | `test_generate.py::test_generate_deterministic` |
| 18 | `run --dry-run` exit 0 | `test_run.py::test_run_dry_run_exit_0` |
| 19 | 无 key 文件 dry-run | `test_run.py::test_run_dry_run_without_key_file` |
| 20 | 假 key 明文零命中（dry-run） | `test_run.py::test_run_dry_run_key_masked` |
| 21 | feature 不存在 exit 2 | `test_run.py::test_run_missing_feature_exit_2` |
| 22 | key 文件缺失 exit 2 | `test_run.py::test_run_missing_key_file_exit_2` |
| 23 | 四状态退出码 + failure_message 二次脱敏（R2） | `test_run.py::test_run_status_exit_codes`（4 参数） |
| 24 | 摘要列出产物路径 / 不存在则不打 | `test_run.py::test_run_summary_lists_artifacts`、`::test_run_summary_skips_missing_artifacts` |
| 25 | analyze 提示行 | `test_run.py::test_run_next_step_hint` |
| 26 | 合成目录纯规则归因 | `test_analyze.py::test_analyze_synthetic_rules_only` |
| 27 | 全过 → 不建目录 exit 0 | `test_analyze.py::test_analyze_no_failures_exit_0` |
| 28 | run_dir 不存在 exit 2 | `test_analyze.py::test_analyze_missing_run_dir_exit_2` |
| 29 | 无 JUnit exit 3 | `test_analyze.py::test_analyze_no_junit_exit_3` |
| 30 | `--llm` 降级纯规则 | `test_analyze.py::test_analyze_llm_flag_degrades_gracefully` |
| 31 | `--junit` 选定文件（不再 glob） | `test_analyze.py::test_analyze_junit_flag_selects_file` |
| 32 | 摘要表行 `case-1 \| …` | `test_analyze.py::test_analyze_summary_table_line` |

附加加固用例（17 条，超出 spec 清单但不改变任何规格语义）：

- 全局：`python -m record2gherkin --help` 真子进程入口、`record` 无 url 报错、未知子命令、默认路径仓库锚定、未预期异常兜底（exit 2 且日志带类型名）。
- record：`--max-duration` 无人值守保存；页面中途消失（`page.close()`）→ exit 2 且不落盘。
- generate：`DistillError` → exit 1（唯一 exit 1 路径）；只把 needed ∩ provided 交给终稿蒸馏。
- run：空 key 文件 → exit 2；`None` 指标打 `-`；默认 `--out-dir` 布局与 `opt/input+output` 创建；run_id 冲突后缀 `_1/_2`；Ctrl+C → exit 130 + 残留子进程 warning。
- analyze：`--llm` 且规则已判定用例不触模型（离线保证）；`--out-dir` 覆盖；同输入逐字节确定。

### 2.1 断言有效性抽查（变异测试）

为避免"测试空转"，对三条审查必改项做了变异验证（临时改坏实现 → 对应用例必须红 → 复原并校验 sha1 不变）：

| 变异 | 结果 |
|---|---|
| 去掉 `_log_run_summary` 里的 `mask_secret`（R2） | `test_run_status_exit_codes` 4 条全红 |
| 放宽 `_read_test_data` 值契约（接受 bool/空白，R3 前行为） | `test_generate_unusable_test_data_values_are_treated_as_absent` 红（`filled=1` 虚报） |

## 3. spec §9 验收标准逐条勾验

| # | 标准 | 结论 | 证据 |
|---|---|---|---|
| 1 | `uv run pytest tests/record2gherkin/cli -q` 全绿、全程离线 | ✅ | 54 passed；浏览器用例仅本地 `file://` fixture；`run_feature` 全程 monkeypatch |
| 2 | `python -m record2gherkin --help` 退 0 且列四子命令；`record --manual` 退 0 | ✅ | 手工执行 + `test_parser.py::test_module_entry_point_help`（真子进程 `-m` 入口）、`::test_help_lists_four_subcommands`、`::test_manual_mode_prints_bookmarklet_usage` |
| 3 | P0-2：`--test-data` 后 `{{TEST_DATA:` 零命中且值入文；不给值时保留占位符 + 日志列所需键 | ✅ | 用例 10/11/12 + §5 演示脚本第 2/3 步（实测 `needed=1 filled=1`，产物含 `"s3cret-value"`，`grep -c "{{TEST_DATA"` = 0；不给值时 `missing_test_data:password_3` + 占位符保留） |
| 4 | 无 key 环境 `run --dry-run` 可用；假 key 明文全命令零命中 | ✅ | 用例 19/20/23；演示脚本实测：dry-run 日志 `grep -c dummy-key-0123456789` = **0**，`LLM_MODEL_API_KEY=***REDACTED***` 命中 1 行；写成文件的工作目录全量 grep 仅命中 key 文件自身 |
| 5 | `record → generate → run --dry-run → analyze` 四连离线冒烟，退出码依次 0 | ✅ | §5 脚本实测：0 / 0,0 / 0 / 0（产物目录为 `mktemp -d`，不写仓库） |
| 6 | `make fmt` 后 `black --check`（line-length 200）通过 | ✅ | `black --check -l 200` → 9 files unchanged；`isort --check-only` clean |
| 7 | `git status` 未改 `pyproject.toml` / `uv.lock` / `testzeus_hercules/` | ✅ | `git diff --stat` 为空；`git status --short` 仅四个新路径：`record2gherkin/cli.py`、`record2gherkin/__main__.py`、`tests/record2gherkin/cli/`、`dev_docs/cli/test-report.md`；`dev_runs/experiments/`（后台 sweep）未被触碰，测试未创建 `dev_runs/cli_runs`（默认路径用例已把 `RUNS_DIR` 重定向到 tmp） |

## 4. 实现说明（接缝与关键行为）

- **record 可测接缝**：`record_events(url, out_path, *, headless, max_duration_s, stop_when: Callable[[Any], bool] | None)`（spec §2.1/R1），轮询循环把 `page` 传给 `stop_when`；生产谓词（SIGINT Event / `--max-duration`）忽略 `page`。CLI handler 通过 `cli._stop_when_for(args)` 取谓词（默认返回 `None`），该函数即测试注入点（用例 5/6）。
- **record 失败面**：启动/跳转/注入失败、页面被关、`R2GRecorder` evaluate 抛错（跨文档导航）→ error 日志 + exit 2 且不落盘；`len(events) <= 1` → `empty_recording` warning 但照常保存（exit 0）。SIGINT：第一次 → 置 Event 走保存路径；第二次 → `raise SystemExit(130)`。
- **generate P0-2 两段式**：`distill_events(polisher=None)` 出骨架 → 正则扫 `{{TEST_DATA:([^{}]+)}}` → `--test-data` 解析（值契约与 distiller `_clean_test_data_values` 对齐：bool/null/空白视为未提供，object/array 为 exit 2）→ 终稿 `distill_file(..., test_data_values=needed ∩ provided)`，只此一次触 LLM 且仅当 `--polish`。
- **run 复用映射**：`read_api_key` → `build_run_plan`（`--dry-run` 分支）/ `run_feature`（非 dry-run）→ `STATUS_*` → 退出码 0/1/3/4；摘要打印前对 `failure_message` 再过一次 `mask_secret`（R2）。
- **analyze**：默认 `analyzer=None`（纯规则、逐字节确定）；`--llm` 惰性构造 `DefaultAttributorAnalyzer`，构造/导入失败 → warning + 降级；报告 `case-<i>.{json,md}` 按 `attribute_run` 返回序写入，`to_json()` 字符串形态原样落盘。

## 5. 四命令链路演示脚本（给总编排最终验收用，全程离线可跑）

前置：仓库根、`uv` 环境可用、playwright chromium 已安装（`make install` 已含）。脚本不使用真实 key、不联网、不调 LLM；工作目录默认 `mktemp -d`，可用 `WORK_DIR=<dir>` 指定。

第 1 步用 `record_events` 的 `stop_when` 接缝驱动交互（人机交互的脚本化替身：填邮箱 + 填密码 + 点按钮）；人工演示时改为 `uv run python -m record2gherkin record "file://$PWD/tests/record2gherkin/recorder/fixtures/demo_form.html"` 手动操作后 Ctrl+C 等价。

```bash
#!/usr/bin/env bash
# 四命令链路演示：record → generate(+P0-2 填充) → run --dry-run → analyze
set -euo pipefail
cd "${REPO:-$(git rev-parse --show-toplevel)}"
export ENABLE_TELEMETRY=0
WORK="${WORK_DIR:-$(mktemp -d -t r2g-cli-demo)}"
FIXTURE="file://$(pwd)/tests/record2gherkin/recorder/fixtures/demo_form.html"
echo "### work dir: $WORK"

echo "### 1/5 record：注入 recorder.js，脚本化驱动交互后停止"
uv run python - "$FIXTURE" "$WORK/events.json" <<'PY'
import sys
from pathlib import Path
from record2gherkin import cli

def drive(page):
    page.fill("#email", "qa.user@example.com")
    page.fill("#password", "s3cret-value")
    page.click("#show-result")
    return True

raise SystemExit(cli.record_events(sys.argv[1], Path(sys.argv[2]), headless=True, stop_when=drive))
PY
echo "record exit=$?"

echo "### 2/5 generate（不给 --test-data：占位符保留 + missing 警告，exit 0）"
uv run python -m record2gherkin generate "$WORK/events.json" --out "$WORK/demo.feature"
echo "generate exit=$?"

echo "### 3/5 从 feature 扫出实际需要的键，生成 test-data 后重跑 generate（P0-2 填充）"
uv run python - "$WORK/demo.feature" "$WORK/test-data.json" <<'PY'
import json, pathlib, re, sys

keys = sorted(set(re.findall(r"\{\{TEST_DATA:([^{}]+)\}\}", pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))))
pathlib.Path(sys.argv[2]).write_text(json.dumps({key: "s3cret-value" for key in keys}), encoding="utf-8")
print("needed keys:", keys)
PY
uv run python -m record2gherkin generate "$WORK/events.json" --out "$WORK/demo.feature" --test-data "$WORK/test-data.json"
echo "generate --test-data exit=$?"
if grep -q "{{TEST_DATA" "$WORK/demo.feature"; then echo "FAIL: 占位符未填充"; exit 1; fi
echo "placeholder check: 0 hits"

echo "### 4/5 run --dry-run（无真实 key；env 已脱敏）"
uv run python -m record2gherkin run "$WORK/demo.feature" --out-dir "$WORK/run" --dry-run --key-file "$WORK/no-such-key.txt"
echo "run --dry-run exit=$?"

echo "### 5/5 analyze（合成一份最小 run 产物：1 个 S1 形态失败用例）"
mkdir -p "$WORK/synth/output" "$WORK/synth/proofs" "$WORK/synth/log_files"
cat > "$WORK/synth/output/demo_result.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="pytest" tests="1" errors="0" failures="1" skipped="0" time="12.5">
  <testcase name="test_recorded_checkout" classname="demo" time="1.5">
    <failure message="Max planner rounds exceeded.">Assertion with result: False</failure>
  </testcase>
</testsuite>
XML
uv run python -m record2gherkin analyze "$WORK/synth"
echo "analyze exit=$?"

echo "### 链路完成：record=0 → generate=0/0 → run=0 → analyze=0"
echo "### 产物目录: $WORK"
```

实测输出要点（2026-09-21 本机）：

```
record: events=4 origin=file:// output=…/events.json            record exit=0
missing_test_data:password_3 / generate: … needed=1 filled=0 …   generate exit=0
needed keys: ['password_3']
generate: output=… filled=1 used_llm_polish=False …              generate --test-data exit=0
placeholder check: 0 hits
run: dry-run run_id=… / project_root=…/run/opt / env LLM_MODEL_API_KEY=***REDACTED***
                                                                run --dry-run exit=0
case-1 | test_recorded_checkout | agent_limit | conf=0.95 | by=rule | sig=planner_max_rounds
analyze exit=0
```

产出的 feature（第 3 步后）：

```gherkin
Feature: Recorded flow on file

Scenario: Demo 下单页

Given I am on the page "file:///…/tests/record2gherkin/recorder/fixtures/demo_form.html"
When I enter "qa.user@example.com" in the "邮箱地址" field
When I enter "s3cret-value" in the "密码" field
When I click on the "显示结果" button
```

## 6. 已知问题清单（11 条，均不阻塞演示链路）

1. **record 跨文档导航即中止（exit 2，不保存）**：注入 JS 随文档销毁（recorder spec §4.6.3 固有限制）。CLI 只检测并报错，不做重注入/多页合并（spec §10 Out of Scope）。真实多页站点请用 SPA 页或 `--manual` 分页录制。
2. **run 中途 Ctrl+C 遗留 Hercules 子进程**：`run_feature` 未暴露协作式中断，子进程组独立存活；CLI warning「可能仍在后台运行，请等待或手动 pkill」+ exit 130（spec §5.5 已知边界，不为 CLI 改 runner）。
3. **第二次 Ctrl+C 的强制中止路径（exit 130、不保存）无自动化测试**：SIGINT 需要落进特定的录制/保存窗口才可复现，没有确定性注入点；代码路径存在（`record_events` 内 SIGINT 计数 ≥2 → `SystemExit(130)`），真机演示按"第二次 Ctrl+C = 立即中止不保存"约定。
4. **未知 `RunResult.status` → exit 2 + warning**：spec §1.2 未定义该分支；按"最简单确定性行为"选用法错误码，便于暴露 runner 契约变更。
5. **`--test-data` 无法表达"值就是空串/true"**：bool/null/空白一律视为未提供（R3 契约，与 distiller `_clean_test_data_values` 一致）；若真需要空串值，需改 distiller 契约（超出本模块）。
6. **`--polish` 真机未验收**：离线测试只覆盖注入 fake（用例 15/16）；真实润色可用性取决于 `agents_llm_config` 的 `distiller_polish` 档位。失败也会按契约回退骨架（exit 0 + `polish_error:*`）。
7. **`--llm` 真机 LLM 归因未验收**：已覆盖构造/降级路径与"规则已判定用例不触模型"的离线保证；`needs_llm` 用例的真实归因依赖 `agents_llm_config` 的 `attributor_analyze` 档位。
8. **analyze 报告按序号命名（`case-<i>`）**，多失败场景不携带 scenario 名（spec §11 遗留决策点，等真实多失败样本再定）。
9. **run 摘要为人类可读文本，无 `--json` 形态**（spec §11 遗留决策点，出现管道化需求再加）。
10. **`record --max-duration` 到点即停**：用户正在输入时也立即结束，产物以已入队事件为准（spec §11 遗留决策点）。
11. **测试环境假设**：`test_record_unreachable_url_exit_2` 依赖本机 `127.0.0.1:1` 连接被拒；若本机代理会转发该请求并返回页面，该用例会退化为 `--max-duration` 结束（断言失败但不会挂起）——本机（直连，无 `HTTP_PROXY`）实测稳定通过。
