# 公开基准评测（MiniWoB++）— 测试报告（实现阶段，离线可验证范围）

> 模块：`record2gherkin/benchmark/` + `tests/record2gherkin/benchmark/`
> spec：`dev_docs/benchmark/spec.md`（唯一权威）；plan：`plan.md`；审查：`review.md`
> 本报告覆盖 **D0.5a/b/c**（vendoring 产物入库、伺服+补丁+收集端点、goal 预读与 Gherkin、编排/指标/护栏）
> 与全部离线单测 + 本机回环浏览器组 + 手动 curl 契约双证。**未调用任何真实 LLM、未跑 Hercules**；
> pilot / full 两阶段由总编排执行（命令与预算见 §7），其数字回填见 §9/§10。

## 1. 测试命令与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/record2gherkin/benchmark -q` | **68 passed**（21.6s；离线：无 key、无外网；浏览器组仅 127.0.0.1 回环） |
| `uv run pytest tests/record2gherkin -q` | **360 passed**（47.9s；全局无回归——本模块新增 68，改动前基线 292） |
| `uv run isort record2gherkin/benchmark tests/record2gherkin/benchmark && uv run black --target-version py311 -l 200 record2gherkin/benchmark tests/record2gherkin/benchmark` | FMT-CLEAN（复跑 `--check` 亦通过；vendored html/js/json 未被格式化） |
| 脱敏：`KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs/ record2gherkin/ tests/ dev_docs/` | **零命中**；key 的 8 字符前/后缀同样零命中 |
| 手动 curl 契约验证（伺服 CLI 一条命令） | 见 §5（`/healthz`、补丁 `core.js`、原样页面、`POST /__r2g_reward`、`/latest`、404 族、rewards.jsonl） |

按文件分布（合计 68）：

| 文件 | 通过数 | 覆盖 |
|---|---|---|
| `test_tasks.py` | 9 | A1-A5（125 行/字典序、html 存在、family/visual 派生、seed 派生、pilot 子集与 fail-fast）+ 读表校验 + PROVENANCE 逐项核对 |
| `test_server_patch.py` | 15 | B6-B9（原样字节+no-store、`core.js` 纯追加补丁+逐字结构行、404/穿越/无目录列表、端口占用硬失败）+ `/healthz` |
| `test_reward_endpoint.py` | 13 | C10-C13（合法 POST→`/latest`、last-wins 与 seed 隔离、每次 POST 恰一行、非法体 400 不落盘）+ 负奖励记录 |
| `test_goal_gherkin.py` | 10 | D14-D15（`sanitize_goal` 表驱动、双类型 utterance、三段式模板、上游解析入口 1 Feature/1 Scenario） |
| `test_browser_miniwob.py` | 5 | D16(a)(b)(c) 真浏览器回环：auto-start 证据、无 seed 负例、同 seed 确定性、`endEpisode(1)`→POST、页面超时 `raw=-1` |
| `test_results_metrics.py` | 9 | E17-E20（状态组装 §7.2、disagreement 双向、指标分母/缺行/missing 计数）+ `load_rows` 容错 |
| `test_orchestrator.py` | 7 | F21-F23（计划数 10/125、预算 12/130/142、dry-run 零副作用、断点跳过与 `--force`、重试规则、manifest 字段） |

补充说明：`uv run black --check tests/ testzeus_hercules/` 仍报 9 个**上游原有**文件会被重排（`core/agents/*`、`config.py` 等），与本次改动无关且不在允许改动范围内——本模块的两个目录零差异。

## 2. 构建产物（已落盘，可复现）

| 产物 | 规模/内容 | 校验 |
|---|---|---|
| `record2gherkin/benchmark/miniwob_html/` | **360 个文件 / 4,988,677 字节**（`du` 5.6M），其中 `miniwob/*.html` 130 页，含 `core/`、`common/`、`flight/` 等同源依赖 | A 组单测逐个核对文件数、字节数、**全部 360 个 sha256** 与 PROVENANCE 一致 |
| `record2gherkin/benchmark/PROVENANCE.md` | 源包 `miniwob 1.1.0`、上游 Farama-Foundation/miniwob-plusplus、**BSD-3-Clause**、复制命令、文件数/字节数 + 360 行 sha256 | 同上 |
| `record2gherkin/benchmark/tasks.json` | 125 行、109 个 family、1 个 visual（`miniwob.visual-addition`）、`source = browsergym-miniwob 0.14.3, registry ALL_MINIWOB_TASKS (125 tasks)`、`episode_max_time_ms_default = 240000` | A1-A5 全绿 |

构建命令（构建期一次性，两个包**不进** `pyproject`）：

```bash
uv run --no-project --with miniwob python record2gherkin/benchmark/vendor_miniwob.py --dest record2gherkin/benchmark/miniwob_html
uv run --no-project --with browsergym-miniwob python record2gherkin/benchmark/build_task_table.py --html-root record2gherkin/benchmark/miniwob_html --dest record2gherkin/benchmark/tasks.json
```

入库检查：`git status --untracked-files=all` 显示两目录下 **378 个文件全部可见**（`git check-ignore` 对 vendored 树与 `tasks.json`/`PROVENANCE.md` 均无命中）——`.gitignore` 未误伤（该树无 `*.xml`、无 `input/output/cache/tmp` 目录），**无需 `git add -f`、无需改忽略规则**；本阶段未执行任何 git 操作。

## 3. spec §9 用例清单覆盖（23 条，全覆盖）

| # | 用例 | 落点 |
|---|---|---|
| A1 | 125 行/唯一/字典序 | `test_a1_table_holds_125_unique_task_ids_in_dictionary_order` 等 3 个 |
| A2 | html 文件存在 | `test_a2_every_row_points_at_a_real_vendored_page` |
| A3 | family/visual 派生 | `test_a3_family_and_visual_derivation` |
| A4 | seed 派生恒等且互异 | `test_a4_seed_derivation_is_stable_within_a_task_and_unique_across_tasks` |
| A5 | pilot 恰 10 / ≥5 family / ≥1 visual | `test_a5_pilot_subset_is_ten_tasks_over_five_families_with_a_visual_one` + `test_a5_missing_pilot_task_fails_fast` |
| B6 | 原样字节 + no-store | `test_b6_vendored_bytes_are_served_unchanged` |
| B7 | `core.js` 纯追加补丁 | `test_b7_core_js_is_the_vendored_file_plus_appended_patches`（含 8 条逐字补丁行）+ 带 query 变体 |
| B8 | 404/穿越/无目录列表 | `test_b8_missing_paths_and_traversal_are_404`（10 个路径参数化） |
| B9 | 端口占用硬失败 | `test_b9_busy_port_is_a_hard_error` |
| C10 | 合法 POST→`/latest` 一致 | `test_c10_valid_post_lands_in_memory_and_jsonl` |
| C11 | last-wins / seed 隔离 | `test_c11_last_wins_and_seeds_do_not_leak` + `test_c11_latest_needs_both_task_and_seed` |
| C12 | 每次 POST 恰一行 | `test_c12_every_post_appends_exactly_one_line` |
| C13 | 非法体 400 不落盘 | `test_c13_invalid_posts_are_400_and_write_nothing`（7 个非法体参数化）+ 路由 404 |
| D14 | `sanitize_goal` 纯函数 | `test_d14_sanitize_goal_is_a_pure_function`（7 组） |
| D15 | 三段式 + 可解析 | `test_d15_render_feature_has_the_three_sections_and_the_seeded_url` + `test_d15_feature_parses_through_the_upstream_entry_point` |
| D16 | 浏览器组 (a)(b)(c) | `test_d16a_*`、`test_d16b_*`、`test_d16c_*`（5 个用例，无浏览器时整体 skip） |
| E17 | timeout 照录 reward / no_junit | `test_e17_timeout_records_the_reward_it_has_and_no_junit_is_sticky` |
| E18 | no_reward / no_goal | `test_e18_missing_reward_and_missing_goal` |
| E19 | 官方判定 + disagreement 双向 | `test_e19_official_verdict_and_disagreement_both_directions` |
| E20 | 指标分母/缺行/成本/visual/disagreement | 5 个 `test_e20_*` |
| F21 | 计划 10/125 + 预算护栏 | `test_f21_stage_plan_sizes_and_budget_guard` |
| F22 | dry-run 零副作用 | `test_f22_dry_run_touches_nothing_and_prints_the_plan` + CLI 变体 |
| F23 | 断点跳过 / `--force` | `test_f23_resume_skips_recorded_cells_and_force_restores_them` + 重试规则 + manifest 字段 + 退出码 |

## 4. spec §10 验收标准逐条勾验

| # | 验收标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | 本模块 pytest 全绿、离线可跑；`make fmt` 后 `--check` 通过 | ✅ | 68 passed（含 5 个回环浏览器用例；`SKIP_BROWSER_TESTS=1` 时 5 skip 仍全绿）；isort/black `--check` 零差异 |
| 2 | 构建产物入库且可复现（文件数/字节数与 PROVENANCE 一致；tasks.json 125 行、A 组全绿） | ✅ | §2：360 文件 / 4,988,677 字节 / 360 sha256 全对；tasks.json 125 行；A 组 9 passed；`.gitignore` 零误伤（378 文件可见） |
| 3 | 伺服器一条命令启动，`/healthz` 与 `/__r2g_reward` 契约与 §3.3 一致（B/C 组 + 手动 curl 双证） | ✅ | B/C 组 28 passed；§5 curl 实录（healthz/补丁 js/原样页/POST/latest/404/rewards.jsonl） |
| 4 | pilot：10 行结果、≥1 行 `official_passed` 且 `total_tokens` 非 None、Hercules ≤12 | ⏳ 待总编排 | 机制就绪：§7 命令；行组装/预算/断点跳过/重试全部离线锁定；`manifest.budget` 记录 used/cap |
| 5 | full：125 行、manifest 完整（含 metrics）、两阶段累计 ≤130（红线 142） | ⏳ 待总编排 | `plan_cells(full)=125`、`hercules_budget=142=cap` 单测锁定；`_write_manifest` 字段集合单测锁定（含 metrics） |
| 6 | 脱敏：key 实值在 `dev_runs/`、`record2gherkin/`、`tests/`、`dev_docs/` 零命中 | ✅ | §1 命令零命中（含前/后缀） |
| 7 | `test-report.md` 固化：总体/分家族（含 visual）/AvgTokens/AvgDuration/disagreement/JUnit 对照/失败清单/§0 口径/基线引用与差异声明 | ⏳ 部分 | §9 已固化 §0 四条口径原文与基线引用规则；通过率与成本数字待 pilot/full 后由 `manifest.metrics`（`metrics.summarize`）回填 |

## 5. 手动 curl 契约双证（§10.3）

伺服器一条命令启动（默认 `127.0.0.1:8462`，端口占用即硬失败）：

```bash
uv run python -m record2gherkin.benchmark.miniwob_server \
  --root record2gherkin/benchmark/miniwob_html --port 8462 --rewards-file /tmp/r2g/rewards.jsonl
```

| 请求 | 实测响应（摘要） |
|---|---|
| `GET /healthz` | `200`，`Cache-Control: no-store`，`{"served_root": "<abs>/miniwob_html", "patched": true, "rewards": 0}` |
| `GET /core/core.js` | `200 text/javascript`，响应体含 `__R2G_PATCH_START__`/`__R2G_REWARD_HOOK__`（前缀与 vendored 原文逐字节一致，补丁为纯追加） |
| `GET /miniwob/click-test.html` | `200 text/html; charset=utf-8`，字节与 vendored 文件一致 |
| `POST /__r2g_reward`（合法体） | `200 {"ok": true, "received_at": "2026-09-21T14:53:58+00:00"}`，`rewards.jsonl` 追加恰一行 |
| `GET /__r2g_reward/latest?task=click-test&seed=99` | `200`，即 payload 原样 + `received_at` |
| `GET /__r2g_reward/latest?task=click-button&seed=99`、`/miniwob/nope.html` | `404`（`{"error": "no_reward"}` / `{"error": "not_found"}`） |

## 6. 关键机制的本机回环证据（浏览器组）

- **auto-start**（16a）：`?r2g_seed=<n>&r2g_ms=240000` 打开后 `core.ept0 != null`、utterance 非空、`core.EPISODE_MAX_TIME === 240000`、`window.__R2G_START_ERROR` 为 null；**且 `WOB_EPISODE_ID === 0`**（开局成功后仍为 0，与 review 必改 1.6 一致——因此断言不采用它作为开局证据）。
- **无 seed 负例**（16a）：不带 `r2g_seed` 的页面 `core.ept0 == null`，不自动开局，不干扰人工调试。
- **同 seed 确定性**（16b）：同一 URL 两次预读 utterance 恒等；预读页**不产生** reward 记录（`/latest` 404）。
- **终局上报**（16c）：页内 `core.endEpisode(1)` → 同步 POST → `/latest` 收到 `raw>0, done=true, reason=""`，`WOB_EPISODE_ID` 变为 1，`rewards.jsonl` 恰一行。
- **页面超时路径**（16c 补充）：`r2g_ms=1500` 到点后收到 `raw=-1, reward=-1, done=true, reason="timed out"`——与 spec §7.2.1「负值照录且仍计官方失败」闭环。

## 7. pilot / full 执行命令与预算（总编排用）

```bash
# 0) 预算与计划预演（不进程、不预读、不写任何文件）
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-pilot --stage pilot --dry-run
# 1) pilot：10 个任务（PILOT_SUBDOMAINS），产物落 dev_runs/benchmark/miniwob-pilot/
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-pilot --stage pilot
# 2) 回写 spec §12 遗留点后开 full：125 个任务，产物落 dev_runs/benchmark/miniwob-full/
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-full --stage full
# 3) 断点续跑（崩溃/中断后直接重跑同命令，已有 (task_id, seed) 行自动跳过）；--force 关闭跳过
```

| 项 | 执行数 | 预算上限 | 说明 |
|---|---|---|---|
| pilot | 10（+≤2 基础设施重试 = 12） | ≤$1.56（$0.13/次上限） | 墙钟外推 15-25 min；`timeout_s=600`/cell |
| full | 125（+≤5 基础设施重试 = 130） | ≤$16.90 | 顺利时 2.5-3.5h；广泛 timeout 最坏 125×600s≈21h（护栏只限次数与 $，墙钟如实记录） |
| 累计红线 | **142** | **$20** | `assert_budget(142)` 单测锁定；运行时二次拦截（stage 上限与 cap 双保险） |

前置：`LLM-Key.txt` 存在（key 只经 subprocess `env=` 注入，绝不进 argv/产物）；本机 chromium 已装；端口 8462 空闲。
产物：`dev_runs/benchmark/<exp_id>/`（`results.jsonl` 追加式、`rewards.jsonl`、`features/<run_id>.feature`、`runs/<run_id>/opt` + 脱敏 `stdout.log`、`manifest.json`）。
中途崩溃安全：每行结果 append+flush，manifest 只在收尾写；重跑同命令即从断点续跑。

**pilot 必须回写的 spec §12 遗留点**（spec 规定 pilot 暴露后回写才允许开 full）：`open_url` 对 `?r2g_seed=&r2g_ms=` query 的实际行为；240s 窗口内单步延迟分布与是否上调 `episode_max_time_ms`；预读 utterance 与执行侧一致性抽查（≥3 任务）；JUnit cost/token 属性在 deepseek 下的回传；实测单 run 时长/token → full 墙钟与预算外推。

## 8. 已知问题清单（10 条，均不阻塞核心目标）

1. **同进程内 `read_goal` 与其它 sync-playwright 上下文互斥**：playwright sync API 在同一线程不允许两个并发上下文（其事件循环被标记为 running）。orchestrator 是顺序单进程调用（每 cell 一次预读，无并发上下文），实测无冲突；测试组因此把浏览器 fixture 设为 function scope。若将来把预读并行化，必须改为多进程。
2. **`no_reward` 无任何兜底**（spec §7.2.3 明确不做的设计）：POST 丢失或页面被杀即如实记官方失败，只靠每 cell 至多 1 次基础设施重试缓解。收敛依据：页面 240s 自超时会先于 600s harness 超时 POST（`raw=-1, done=true, reason="timed out"`），harness 无需自行判定 episode 超时。
3. **visual 组 n=1**：registry 中唯一 visual 任务是 `visual-addition`（spec §2.3 已核）；报告须按 n=1 组披露，不做豁免。
4. **墙钟外推的语义**：护栏只在执行次数与 $ 上；full 最坏墙钟 ≈21h。实测墙钟由总编排如实记录。
5. **pilot 观察点**：JUnit cost/token 属性在 deepseek 下的回传沿用 exp001 结论核对，变化则记入本报告。
6. **5 个 vendored 页面未被 registry 引用**（如 `book-flight-nodelay.html`）：任务表从 registry 构建，只做存在性校验，不影响口径。
7. **伺服子进程 stdout 采用 PIPE 且不读取**：默认 INFO 级下无输出风险；若把日志级别调到 DEBUG 并跑满 125 cell，理论上接近管道缓冲上限（届时改用文件重定向）。
8. **`--dry-run` 不校验 key/浏览器可用性**（按 spec：不进程、不预读、不写文件），因此 dry-run 全绿不等于可执行；真正的环境校验发生在第一个 cell。
9. **9 个上游文件本就不是 black-clean**（`testzeus_hercules/core/agents/*`、`config.py`、`utils/litellm_helper.py`、`tests/test_simple_hercules_langgraph.py` 等）：pre-existing，`make fmt` 会重排它们；本模块未触碰这些文件。
10. **预读/执行 utterance 一致性只在 `click-test` 上机验证**（浏览器组 16b），spec §12 要求的多任务人工抽查留给 pilot。

## 9. §0 四条口径披露原文（最终报告必须原样转载）

1. 本基准测**执行内核能力**（自然语言任务 → Gherkin → Hercules 语义化执行），不含录制/蒸馏（exp001 已覆盖）。
2. `EPISODE_MAX_TIME` 由原生 10s 放宽至 240s（默认，参数化），沿用 BrowserGym `episode_max_time` 可配的做法；若 pilot 后调整，报告须披露最终值与理由。
3. 每 task 单 seed 单次，非官方多 instance 均值口径。
4. **官方成败以页面原生奖励为唯一权威**（`reward_raw > 0`）；Hercules JUnit 结果与奖励不一致的行如实标记 disagreement，不改判。

## 10. 与公开基线的对比声明（待 full 后填充）

- 只引用**可查证**文献/榜单数字（论文或官方仓库公布值），并逐条注明设置差异：模型与版本、观察空间（文本/截图）、动作空间、`episode_max_time`、任务实例数与 seed 口径、以及本基准为「自然语言任务 → Gherkin → 语义化执行」而非端到端 RL/IR 策略。
- 本基准为单 seed 单次、无多 instance 均值，**不得**与官方多实例均值直接等价比较；披露时须并排列出设置差异表。
- 禁止在本报告中出现未注明出处的数字；pilot/full 数字回填时同步补全本节的引用与差异声明（由总编排执行）。
