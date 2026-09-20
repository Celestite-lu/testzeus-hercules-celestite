# 子进程编排 CLI spec — 实现规格

> 读者为实现者。读完本文不再需要做设计决策；未规定处按"最简单确定性行为"处理并记入实现说明。
> 方案选型与里程碑见同目录 `plan.md`。契约基线：distiller 公开 API（`record2gherkin/distiller/api.py`）、evaluation runner（`record2gherkin/evaluation/runner.py`）、attributor spec §0/§5（`dev_docs/attributor/spec.md`）。

## 0. 目录与文件

```
record2gherkin/
  __main__.py      # 新增：from record2gherkin.cli import main; raise SystemExit(main())
  cli.py           # 新增：parser + 四个子命令 handler + 共享 helper（单文件，不做 cli/ 子包）
tests/record2gherkin/
  cli/
    conftest.py    # 目录级 conftest：假 RunResult 工厂 / 合成 run_dir 工厂 / 事件样例
    test_parser.py          # A 组
    test_record.py          # B 组（playwright 本地 fixture）
    test_generate.py        # C 组
    test_run.py             # D 组
    test_analyze.py         # E 组（合成产物目录）
```

- 入口：`uv run python -m record2gherkin <sub>`。**不改 pyproject.toml**（无 console-script）。
- 测试目录命名说明：取 `tests/record2gherkin/cli/` 而非 `cli_`——STATUS D1 决策为"每模块 `<module>/` 子目录 + 目录级 conftest"；已核实 `tests/record2gherkin/` 下没有顶层 conftest.py，只有 distiller/recorder/evaluation 三个目录级 conftest，`cli/` 与任何现有 conftest 无冲突。
- 代码风格：black line-length 200 + isort（与 `make fmt` 一致）；日志 `from testzeus_hercules.utils.logger import logger`，**禁 print**（ruff T20）。
- `main()` 第一步调用 `configure_logger("INFO")`（`testzeus_hercules.utils.logger`，裸 import 时无 handler，不装配则用户什么也看不到）。
- 不新增第三方依赖：playwright 已在 uv 环境；argparse/标准库足够。

## 1. 全局约定

### 1.1 参数总表（argparse，子命令均 required）

```
usage: python -m record2gherkin {record,generate,run,analyze} ...
```

| 子命令 | 参数 | 类型/默认 | 说明 |
|---|---|---|---|
| record | `url` | 位置参数，str | 目标页 URL（http/https/file:// 均可）；`--manual` 时忽略 |
| record | `--manual` | flag | 不启浏览器，只打印 bookmarklet 用法（§2.3） |
| record | `--out PATH` | 默认 `dev_runs/recordings/<YYYYmmdd-HHMMSS>-events.json` | 事件 JSON 落盘路径；目录自动创建 |
| record | `--headless` | flag，默认关 | 默认有头（用户要手动操作）；测试/CI 传此开关 |
| record | `--max-duration SECONDS` | float，默认 0.0 = 不限时 | 到时自动结束并保存（测试与无人值守用） |
| generate | `events` | 位置参数，str | 事件 JSON 文件路径 |
| generate | `--out PATH` | 默认 `distiller.default_output_path(events)`（同目录 `<stem>.feature`） | feature 落盘路径 |
| generate | `--polish` | flag，默认关 | 启用 LLM 润色（`DefaultPolisher()`）；缺省纯模板零模型 |
| generate | `--test-data PATH` | str，默认 None | JSON 文件：`{"password_<seq>": "真实值", ...}`（§4 P0-2） |
| run | `feature` | 位置参数，str | feature 文件路径（恰 1 Feature 1 Scenario，generate 产物形态） |
| run | `--out-dir PATH` | 默认 `dev_runs/cli_runs/<run_id>`（自动命名，§5.3） | 本次运行根目录（其下 `opt/` 为 Hercules 项目根） |
| run | `--timeout SECONDS` | int，默认 `runner.DEFAULT_TIMEOUT_S`（900） | 传给 `run_feature(timeout_s=…)` |
| run | `--key-file PATH` | 默认 `runner.LLM_KEY_PATH`（仓库根 `LLM-Key.txt`） | 传给 `read_api_key(path)`；key 只进子进程 env，不进 argv/日志/落盘 |
| run | `--dry-run` | flag | 只构建执行计划并打印（含脱敏 env），不执行、不需真实 key（§5.4） |
| analyze | `run_dir` | 位置参数，str | 一次 run 的产物根目录（含 `output/`，即 run 命令打印的 run_dir） |
| analyze | `--junit PATH` | str，默认 None | 缺省由 `attribute_run` 自行 glob `**/*.xml` 取最新 |
| analyze | `--llm` | flag，默认关 | 启用 LLM 归因层；构造失败降级纯规则（§6.4） |
| analyze | `--out-dir PATH` | 默认 `<run_dir>/analysis` | 报告落盘目录 |

全局：`-h/--help`（argparse 默认）；无子命令 → argparse 报错退出 2。

### 1.2 退出码总表（全命令统一）

| 码 | 语义 | 触发处 |
|---|---|---|
| 0 | 成功。含：`--dry-run`、`--manual`、analyze 出报告（哪怕存在失败用例——analyze 的职责是产出报告，报告产出即成功）、generate 保留占位符并警告 | 全部 |
| 1 | run：JUnit 判定失败（`STATUS_FAILED`）；generate：`DistillError`（骨架自检失败，bug 级） | run / generate |
| 2 | 用法/输入错误：参数不合法、文件不存在、JSON 非法或形态错、key 文件缺失（非 dry-run）、record 浏览器启动失败/录制中断、URL 不可达 | 全部 |
| 3 | run：子进程超时（`STATUS_TIMEOUT`）；analyze：`AttributionError`（JUnit 缺失/不可解析） | run / analyze |
| 4 | run：JUnit 缺失或不可读（`STATUS_NO_JUNIT`） | run |
| 130 | record：第二次 Ctrl+C 强制中止（不保存） | record |

argparse 自身错误（未知子命令、缺参数）退出 2，与总表一致，无需特判。

### 1.3 输出与错误格式

- 全部经 `logger`：正常流程 `info`、可继续的问题 `warning`、终止性错误 `error`（格式 `error: <一句话> [<细节>]`）。
- feature/事件文件内容不回显 stdout（产物只落盘；摘要行给出路径）。
- 路径一律输出绝对路径（`Path.resolve()`）。

## 2. 子命令 record

### 2.1 playwright 模式行为

实现为可测接缝函数，CLI handler 只做参数装配：

```python
def record_events(url: str, out_path: Path, *, headless: bool = False,
                  max_duration_s: float = 0.0,
                  stop_when: Callable[[], bool] | None = None) -> int
```

- `stop_when` 返回 True 即触发正常结束（SIGINT handler 与 `--max-duration` 都装进默认实现；测试注入自己的 lambda）。
- playwright 用 **sync API**（与 `dev_runs/integration_smoke.py` 一致），在主线程同步执行；SIGINT handler 只 `set()` 一个 `threading.Event`，不做任何 playwright 调用。
- 步骤（每步失败即清理后返回对应退出码）：
  1. 惰性 `from playwright.sync_api import sync_playwright`；`chromium.launch(headless=headless)`；`new_page()`；`page.goto(url, timeout=30_000)`。启动/跳转失败 → error 日志，exit 2。
  2. 读 `record2gherkin/recorder/recorder.js` 全文，`page.evaluate(js)` 注入；`page.evaluate("R2GRecorder.start()")` 必须 `is True`，否则 exit 2。
  3. info 日志提示"录制中……操作完成后按 Ctrl+C 结束"。
  4. 轮询循环（间隔 0.2s）：`stop_when()` 为真 → 进入停止序列；`page.is_closed()` 为真或对 `R2GRecorder.status()` 的 evaluate 抛错 → 说明浏览器被关或发生跨文档导航（注入 JS 随文档销毁，recorder spec §4.6.3 固有限制）→ error 日志指明原因与已知限制，exit 2（不保存）。
  5. 停止序列：`page.evaluate("R2GRecorder.stop()")` → `raw = page.evaluate("R2GRecorder.getJSON()")` → `json.loads(raw)` 校验含 `session`/`events` 键 → 写 `out_path`（utf-8，目录 `mkdir(parents=True, exist_ok=True)`）→ finally 关浏览器。
  6. info 摘要：输出路径（绝对）、事件数、`session.origin`。exit 0。
- SIGINT 语义：第一次 → `Event.set()`（走正常保存路径，exit 0）；保存期间第二次 → 直接 `raise SystemExit(130)`。
- 0 事件也是合法产物（照常保存，附 warning `empty_recording`）。

### 2.2 fixture 复用

测试与演示用 `tests/record2gherkin/recorder/fixtures/demo_form.html`（recorder 模块已有 fixture），`file://` 打开。recorder.js 以文件读入注入，**不走 bookmarklet 通道**（bookmarklet 仅 `--manual` 文案提及）。

### 2.3 `--manual` 模式（不启浏览器）

依次 info 输出（全部静态文案 + 实路径）：

1. bookmarklet 绝对路径：`<REPO_ROOT>/record2gherkin/recorder/bookmarklet.txt`（REPO_ROOT 复用 `runner.REPO_ROOT`）。
2. 用法四步：把文件全文（单行 `javascript:` 串）新建为浏览器书签 URL → 打开目标页点一次书签（注入并自动开始录制）→ 手动操作页面 → 打开 DevTools 控制台执行 `R2GRecorder.copy()`（或 `copy(R2GRecorder.getJSON())`），把剪贴板内容存成 JSON 文件，交给 `generate` 子命令。
3. 注意事项：**只注入一次**——bookmarklet 重复执行会把录制器重置为全新实例（recorder spec §5 幂等注入语义），已录事件丢失。
4. 备选：CSP 严格站点用 DevTools Snippets 粘贴 `recorder.js` 原文。
5. exit 0。

## 3. 子命令 generate（含 P0-2 占位符填充）

### 3.1 处理流程（两段式蒸馏，机械执行）

```
1. 读 events 文件并 json 解析
     OSError/ValueError           → error 日志，exit 2
2. 预蒸馏（纯函数零模型）：
     sk = distill_events(payload, polisher=None, test_data_values=None)
     DistillError                 → error 日志，exit 1
3. 需键扫描：正则 \{\{TEST_DATA:([^{}]+)\}\} 扫 sk.skeleton_text
     → needed（保序去重 list[str]）
4. 若 --test-data：
     json 解析失败                 → exit 2
     顶层非 object                → exit 2（报"必须为 JSON object"）
     值 ∈ {str,int,float,bool}    → 接受，str() 化
     值 ∈ {object,list}           → exit 2（逐个列出坏键）
5. missing = needed − provided；unused = provided − needed
6. 最终蒸馏（只此一次触 LLM，且仅当 --polish）：
     distill_file(events, output_path=--out,
                  polisher=DefaultPolisher() if --polish else None,
                  test_data_values={k: v for k, v in td.items() if k in needed})
     —— 只传 needed ∩ provided：unused 键不得意外命中未来占位符
7. 警告输出（逐条 warning 日志）：
     result.warnings 原样；missing 每键一条 `missing_test_data:<k>`；
     unused 每键一条 `unused_test_data:<k>`；
     missing 非空时追加一条说明："以上键无值，feature 中保留 {{TEST_DATA:...}} 占位符，run 前需人工填充"
8. info 摘要：output_path（绝对）、needed 数、filled 数（= needed − missing）、
     used_llm_polish、fallback_reason（非 None 时）
9. exit 0
```

- 退出码：`DistillError` → 1；文件/JSON/形态错误 → 2；其余（含 `--polish` 润色失败回退骨架、占位符残留）一律 0——蒸馏器契约：占位符是显式 TODO、润色回退是正常降级，均非命令失败。
- `--polish` 的可测接缝：`record2gherkin.cli._make_polisher() -> Polisher` 默认 `return DefaultPolisher()`；测试 monkeypatch 此工厂（离线约束：不得在测试里触发真实 litellm 调用）。

### 3.2 P0-2 说明（为什么这样落）

`{{TEST_DATA:password_<seq>}}` 在 Hercules 引擎侧没有机械消费者（phase1-merge-analysis P0-2），所以填充必须在 generate 期完成：`--test-data` 提供 `password_<seq>` → 真实值时，feature 直出可执行字面值；无值时保留占位符并明确警告列出所需键，用户补值重跑 generate 即可。此为评测框架 merge 分析建议路径 (a) 的 CLI 化。

## 4. 密钥安全（全命令红线）

- key 只经 `read_api_key(--key-file)` 读入内存 → `run_feature(api_key=…)` → 子进程 `env=` 注入。**绝不**出现在 argv、日志、任何落盘文件、`--dry-run` 输出中。
- `--dry-run` 打印 env 时必须经 `RunPlan.env_redacted()` 脱敏（runner 已实现，直接用）。
- 测试不得把真实 key 写入任何文件；一律用临时假 key（如 `dummy-key-0123456789`）。

## 5. 子命令 run

### 5.1 复用映射（flag → runner API，禁止绕过 runner 自建子进程）

| CLI 逻辑 | 调用 | 锚点 |
|---|---|---|
| key 读取 | `read_api_key(Path(--key-file))`；`RunnerError` → exit 2 | runner.py:114-120 |
| 目录准备/命令/env 构建 | `build_run_plan(feature, project_root, api_key=key)` | runner.py:219-235 |
| 执行与结果回收 | `run_feature(feature, run_id=…, project_root=…, timeout_s=…, api_key=key, dry_run=--dry-run)` | runner.py:243-313 |
| 退出码映射 | `result.status` 对 `STATUS_PASSED/FAILED/TIMEOUT/NO_JUNIT/DRY_RUN` | runner.py:45-51 |

### 5.2 run_id 与目录布局

- `run_id = f"cli_{datetime.now():%Y%m%d-%H%M%S}_{Path(feature).stem}"`（冲突时追加 `_1`、`_2`……直到目录不存在）。
- `project_root = <out-dir>/opt`（runner `prepare_run_dir` 会建 `input/`、`output/`；`run_dir = project_root.parent = <out-dir>`，`stdout.log` 落在这里且已脱敏）。

```
dev_runs/cli_runs/cli_20260921-140301/demo-events/
  opt/            # Hercules 项目根：input/ output/ proofs/ log_files/
  stdout.log      # 子进程 stdout+stderr（已 mask_secret）
  analysis/       # analyze 子命令默认产物目录（若执行）
```

### 5.3 执行后摘要（info 逐行）

- `status / passed / duration_s / cost_usd / total_tokens`（None 打 `-`）。
- `junit_xml`（result 自带，绝对路径）；HTML：junit 同 stem `.html` 存在则一并打印。
- `proofs = <project_root>/proofs`、`log_files = <project_root>/log_files`：存在才打印。
- `failure_message` 存在时打印（截 500 字符；内容已被 runner 脱敏）。
- 末行提示：`uv run python -m record2gherkin analyze <run_dir>`。

### 5.4 `--dry-run` 分支

- 不调 `run_feature`，直接 `build_run_plan(...)` 并打印：`plan.cmd`（空格连接一行）+ 经 `plan.env_redacted()` 的关键项（只列 `LLM_MODEL_NAME`、`LLM_MODEL_BASE_URL`、`HEADLESS`、`ENABLE_TELEMETRY`、`LLM_MODEL_API_KEY` 五键，证明 key 已脱敏）。
- key 文件缺失/不可读时：dry-run 特批——warning 后用占位串 `"DRYRUN-PLACEHOLDER"` 充当 api_key 继续构建（不会执行，无泄露面）；非 dry-run 时缺失 → exit 2。
- exit 0。

### 5.5 中断行为（已知边界，如实文档化）

`run_feature` 未暴露协作式中断；run 期间 Ctrl+C 会抛 `KeyboardInterrupt` 且子进程组独立存活。CLI 捕获后 warning"子进程可能仍在后台运行，请等待其自然结束或手动 pkill"并 exit 130。不为此修改 runner。

## 6. 子命令 analyze

### 6.1 流程

```
1. run_dir 非目录 → exit 2
2. analyzer = None
   若 --llm：惰性 import DefaultAttributorAnalyzer——契约位置 `record2gherkin.attributor.llm_layer`
     （attributor spec §0 的 `__init__` re-export 面只含 attribute_run/attribute_case/
      AttributionResult/AttributionError/Category，不含 analyzer；若实现期已提升至 `__init__`
      则以 `__init__` 为准，import 失败视同配置缺失降级）
     构造抛 AttributionError（agents_llm_config 缺 attributor_analyze 键）
       → warning "llm analyzer unavailable (<exc>)，降级纯规则"，analyzer 保持 None
3. results = attribute_run(run_dir, junit_path=--junit, analyzer=analyzer)
     AttributionError（JUnit 缺失/不可解析）→ error 日志，exit 3
4. results 为空 → info "no failing testcases found; nothing to analyze"，exit 0（不建目录）
5. mkdir <out-dir>；按 junit 内失败用例顺序（attribute_run 返回序）第 i 个（1-based）写：
     case-<i>.json   # result.to_json()；返回 dict → json.dumps(indent=2, ensure_ascii=False)；
                     # 返回 str → 原样写（兼容 attributor 实现的两种返回形态）
     case-<i>.md     # result.to_markdown()
6. info 摘要表，每失败用例一行：
   case-<i> | <scenario 截 60> | <category> | conf=<x.xx> | by=<decided_by> | sig=<rule_signature 或 ->
   末行输出 out-dir 绝对路径
7. exit 0
```

### 6.2 默认纯规则的依据

analyzer=None 时 attributor 全链确定性（attributor spec §5：同输入 `to_json()` 逐字节一致），离线零依赖，演示稳定；`--llm` 是显式增强而非默认。analyze 的退出码不反映用例成败（职责是产出报告），链路中用例成败由 run 的退出码承载。

## 7. 错误处理策略汇总

| 场景 | 命令 | 行为 | 退出码 |
|---|---|---|---|
| 找不到 key 文件 / key 为空 | run | error 日志指明路径；`--dry-run` 下特批占位串继续 | 2 / 0 |
| feature 不存在 | run | runner `RunnerError` 捕获，error 日志原文透出 | 2 |
| events 文件缺失/非法 JSON | generate | error 日志 | 2 |
| `--test-data` 形态错（非 object / object·list 值） | generate | error 日志列出坏键 | 2 |
| 骨架自检失败（`DistillError`） | generate | error 日志（bug 级，不应出现） | 1 |
| `--polish` 润色失败/回退 | generate | warning 透出 `fallback_reason`，feature=骨架 | 0 |
| 占位符无值 | generate | warning 逐键列出 `missing_test_data:<k>`，保留占位符 | 0 |
| 执行超时 | run | `STATUS_TIMEOUT`，failure_message 含 stdout 尾部 | 3 |
| JUnit 缺失/不可读 | run | `STATUS_NO_JUNIT` | 4 |
| JUnit 判失败 | run | 正常摘要 + failure_message | 1 |
| analyze 时 JUnit 缺失/不可解析 | analyze | `AttributionError` 捕获 | 3 |
| `--llm` 但配置缺键 | analyze | warning + 降级纯规则继续 | 0 |
| record 中浏览器关闭/跨文档导航 | record | error 日志（含已知限制说明），不保存 | 2 |
| record 第二次 Ctrl+C | record | 立即中止不保存 | 130 |
| run 期间 Ctrl+C | run | warning"子进程可能残留"，exit 130 | 130 |

未捕获的意外异常：最外层 `try/except Exception` → error 日志（类型名 + 消息）→ exit 2（兜底，不裸 traceback 崩给用户；日志里带异常类型便于排查）。

## 8. 离线测试用例清单（pytest，全程无网络/无真实 LLM/无外部站点）

conftest 提供：`make_run_result(status, **overrides)`（构造 `RunResult` 假对象）、`make_synth_run_dir(tmp)`（最小合成产物：手写含 1 个 `<failure>` testcase 的 JUnit XML + 空 `proofs/`/`log_files/` 目录）、样例事件 dict（含 1 条 `<masked>` input）。

A 组 全局（test_parser.py）
1. `test_help_lists_four_subcommands` / `--help` / 退出 0，输出含 record/generate/run/analyze
2. `test_missing_subcommand_exit_2` / 无参数 / 退出 2
3. `test_configure_logger_called` / 任意命令 / `configure_logger` 被调用（monkeypatch 断言）

B 组 record（test_record.py，playwright 本地 chromium）
4. `test_manual_mode_prints_bookmarklet_usage` / `record --manual` / 退出 0，日志含 bookmarklet.txt 绝对路径、"R2GRecorder.copy()"、只注入一次警示；无浏览器启动
5. `test_record_fixture_roundtrip` / 注入 `stop_when`（驱动 2 次点击后返回 True）+ `--out` 指向 tmp / 退出 0；文件可 json 解析，`events` 长度 ≥3，含 `session.origin`
6. `test_record_empty_recording_warns` / `stop_when` 立即 True / 退出 0，产物 events=[]，日志含 `empty_recording`
7. `test_record_unreachable_url_exit_2` / `http://127.0.0.1:1/`（连接拒绝）+ `--headless` / 退出 2

C 组 generate（test_generate.py）
8. `test_generate_basic_feature_written` / 样例事件 / 退出 0；默认路径（同目录 `<stem>.feature`）存在，含 `Feature:` 与 `Scenario:`
9. `test_generate_out_flag` / `--out` 指向 tmp 子目录 / 文件落在指定处（目录自动创建）
10. `test_generate_test_data_fills_placeholder` / 事件含 masked input(seq=6) + `--test-data {"password_6":"s3cret"}` / feature 无 `{{TEST_DATA`、含 `s3cret`，摘要 filled=1
11. `test_generate_missing_test_data_warns` / 同上但 `--test-data {"password_9":"x"}` / 退出 0；日志含 `missing_test_data:password_6` 与 `unused_test_data:password_9`；feature 保留 `{{TEST_DATA:password_6}}`
12. `test_generate_no_test_data_flag_warns_needed_keys` / masked 事件、不给 `--test-data` / 退出 0；日志列出 `password_6`
13. `test_generate_bad_test_data_shape_exit_2` / `--test-data` 指向 JSON 数组文件 / 退出 2；另例：值为 object 的键 → 退出 2
14. `test_generate_bad_events_exit_2` / 文件不存在与非法 JSON 两例 / 退出 2
15. `test_generate_polish_factory_seam_fallback` / `--polish` + monkeypatch `_make_polisher` 返回抛错 fake / 退出 0；feature=骨架；日志含 `fallback_reason`（`polish_error:` 前缀）
16. `test_generate_polish_factory_seam_adopted` / fake 返回合法 grounded 润色稿 / 摘要 `used_llm_polish=True`，feature 为润色稿
17. `test_generate_deterministic` / 同输入跑两遍（无 `--polish`）/ 产物逐字节相等

D 组 run（test_run.py）
18. `test_run_dry_run_exit_0` / tmp feature + `--key-file`（内容 `dummy-key-0123456789`）+ `--dry-run` / 退出 0；日志含 `python` `--input-file` 与 `<out-dir>/opt`；`opt/input`、`opt/output` 已创建
19. `test_run_dry_run_without_key_file` / 不传 `--key-file`（仓库根无 key 或指向不存在路径）+ `--dry-run` / 退出 0；日志含 warning 与脱敏后的 `LLM_MODEL_API_KEY` 行（值为 `***REDACTED***`）
20. `test_run_dry_run_key_masked` / 假 key + `--dry-run` / 日志全文不含假 key 明文
21. `test_run_missing_feature_exit_2` / 不存在的 feature / 退出 2
22. `test_run_missing_key_file_exit_2` / 非 dry-run，`--key-file` 指向不存在路径 / 退出 2（不启动任何子进程）
23. `test_run_status_exit_codes` / monkeypatch `record2gherkin.cli.run_feature` 返回 `STATUS_PASSED/FAILED/TIMEOUT/NO_JUNIT` 四种假 `RunResult` / 退出码分别 0/1/3/4；FAILED 与 NO_JUNIT 的 failure_message 出现在日志
24. `test_run_summary_lists_artifacts` / 假 passed 结果且测试预建 `opt/proofs`、`opt/log_files`、junit 同 stem `.html` / 日志含三路径；不存在的路径不出现
25. `test_run_next_step_hint` / 假结果 / 日志含 `analyze <run_dir>` 提示行

E 组 analyze（test_analyze.py，合成产物目录）
26. `test_analyze_synthetic_rules_only` / `make_synth_run_dir`（junit 含 1 个 S1 形态 `Max planner rounds exceeded.` 失败用例）/ 退出 0；`analysis/case-1.md`、`case-1.json` 存在；md 含"失败归因报告"标题；json `category=agent_limit`、`decided_by=rule`
27. `test_analyze_no_failures_exit_0` / junit 全过 / 退出 0；不建 analysis 目录；日志含 `no failing testcases`
28. `test_analyze_missing_run_dir_exit_2` / 不存在的路径 / 退出 2
29. `test_analyze_no_junit_exit_3` / 空目录（无 xml）/ 退出 3
30. `test_analyze_llm_flag_degrades_gracefully` / `--llm` 且 agents_llm_config 无 `attributor_analyze` 键（临时 HOME/工作目录隔离或 monkeypatch 构造抛 `AttributionError`）/ 退出 0；日志含降级 warning；报告仍产出（category 可能 inconclusive）
31. `test_analyze_junit_flag_selects_file` / `--junit` 显式指定合成文件 / 正常出报告（不再 glob）
32. `test_analyze_summary_table_line` / 同 26 / 日志含 `case-1 |` 起头的摘要行（含 category 与 conf）

## 9. 验收标准（可机械检查）

1. `uv run pytest tests/record2gherkin/cli -q` 全绿（§8 全部用例）；全程无网络、无真实 key、无外部站点。
2. `uv run python -m record2gherkin --help` 退出 0 且列出四子命令；`uv run python -m record2gherkin record --manual` 退出 0。
3. P0-2：对含 masked input 的样例事件执行 `generate --test-data`，产物中 `{{TEST_DATA:` 零命中且值入文；不给值时产物保留占位符且 stderr 级日志列出所需键（用例 10-12）。
4. `run --dry-run` 在无 key 文件环境下可用（用例 19），且任何日志/落盘文件中以假 key 明文 grep 零命中（用例 20 推广到全命令）。
5. `record（file:// fixture，headless）→ generate → run --dry-run → analyze（合成目录）` 四连真机冒烟脚本走通，退出码依次 0。
6. `make fmt` 后 `black --check record2gherkin/cli.py record2gherkin/__main__.py tests/record2gherkin/cli`（line-length 200）通过。
7. `git status` 确认未改 `pyproject.toml`、`uv.lock`、`testzeus_hercules/`（密钥红线：任何文档/日志不含 key 明文）。

## 10. Out of Scope

- 不做 daemon/常驻服务/HTTP API 化（进程内连跑被 PLAN §3.3 禁止，服务化超出单机演示定位）。
- 不做配置文件体系：模型档、key 路径、超时等全部走 flag 默认值；不读自定义配置文件（Hercules 侧 agents_llm_config 除外）。
- 不做多 feature 批量/并行执行：一次一条链路，一命令一产物；批量属评测框架（evaluation/sweep.py 已有）。
- 不做进度条/富 UI/交互式向导/彩色输出；logger 文本即界面。
- 不做 record 的跨文档导航事件续录（重注入+多页合并）、多 tab、录制回放/编辑 UI。
- 不做 `{{TEST_DATA:*}}` 的 run 期注入（引擎无消费者是既知事实，填充只在 generate 期）。
- 不暴露模型路由参数（planner/nav/蒸馏/归因各档由 runner 常量与 agents_llm_config 决定）。
- 不为 run 中途协作式取消改造 runner（§5.5 已知边界）。
- 不做 Windows 适配承诺（SIGINT/进程组语义按 POSIX 实现）。

## 11. 遗留决策点（实现/使用期暴露后再补，不预先设计）

- analyze 报告文件命名在多 testcase 场景是否需要带 scenario 名（当前 `case-<i>` 序号制，等真实多失败样本再定）。
- `--llm` 是否值得提供 `--analyzer-model` 之类细粒度覆盖（取决于 agents_llm_config 的 `attributor_analyze` 档位使用体验）。
- record 的 `--max-duration` 到期时若用户正在输入，是否需要宽限期（当前到即停，事件以已入队为准）。
- run 摘要是否需要机器可读 JSON 形态（当前面向人，管道化需求出现再加 `--json` flag）。
