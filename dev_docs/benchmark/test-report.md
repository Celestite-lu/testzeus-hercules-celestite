# 公开基准评测（MiniWoB++）— 测试报告（实现阶段，离线可验证范围）

> 模块：`record2gherkin/benchmark/` + `tests/record2gherkin/benchmark/`
> spec：`dev_docs/benchmark/spec.md`（唯一权威）；plan：`plan.md`；审查：`review.md`
> 本报告覆盖 **D0.5a/b/c**（vendoring 产物入库、伺服+补丁+收集端点、goal 预读与 Gherkin、编排/指标/护栏）
> 与全部离线单测 + 本机回环浏览器组 + 手动 curl 契约双证。**未调用任何真实 LLM、未跑 Hercules**；
> pilot / full 两阶段由总编排执行（命令与预算见 §7），其数字回填见 §9/§10。

## 1. 测试命令与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/record2gherkin/benchmark -q` | **75 passed**（29.1s；离线：无 key、无外网；浏览器组仅 127.0.0.1 回环）。安全加固前为 68，H1/H2/H3 补记见 §11 |
| `uv run pytest tests/record2gherkin -q` | **367 passed**（55.6s；全局无回归——本模块新增 75，改动前基线 292/加固前 360，见 §11） |
| `uv run isort record2gherkin/benchmark tests/record2gherkin/benchmark && uv run black --target-version py311 -l 200 record2gherkin/benchmark tests/record2gherkin/benchmark` | FMT-CLEAN（复跑 `--check` 亦通过；vendored html/js/json 未被格式化） |
| 脱敏：`KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs/ record2gherkin/ tests/ dev_docs/` | **零命中**；key 的 8 字符前/后缀同样零命中（§11 复核含加固后改动） |
| 手动 curl 契约验证（伺服 CLI 一条命令） | 见 §5（`/healthz`、补丁 `core.js`、原样页面、`POST /__r2g_reward`、`/latest`、404 族、rewards.jsonl） |

按文件分布（合计 75）：

| 文件 | 通过数 | 覆盖 |
|---|---|---|
| `test_tasks.py` | 9 | A1-A5（125 行/字典序、html 存在、family/visual 派生、seed 派生、pilot 子集与 fail-fast）+ 读表校验 + PROVENANCE 逐项核对 |
| `test_server_patch.py` | 16 | B6-B9（原样字节+no-store、`core.js` 纯追加补丁+逐字结构行、404/穿越/无目录列表、端口占用硬失败）+ `/healthz` + **B7 H1 加固行位于补丁 A 标记内且不移除节点** |
| `test_reward_endpoint.py` | 13 | C10-C13（合法 POST→`/latest`、last-wins 与 seed 隔离、每次 POST 恰一行、非法体 400 不落盘）+ 负奖励记录 |
| `test_goal_gherkin.py` | 10 | D14-D15（`sanitize_goal` 表驱动、双类型 utterance、三段式模板、上游解析入口 1 Feature/1 Scenario） |
| `test_browser_miniwob.py` | 7 | D16(a)(b)(c) 真浏览器回环：auto-start 证据、无 seed 负例、同 seed 确定性、`endEpisode(1)`→POST、页面超时 `raw=-1`；**(d) H1 加固：HUD/START 零命中、覆盖层不可点、实例不重开、奖励 POST 完好** |
| `test_results_metrics.py` | 9 | E17-E20（状态组装 §7.2、disagreement 双向、指标分母/缺行/missing 计数）+ `load_rows` 容错 |
| `test_orchestrator.py` | 11 | F21-F23（计划数 10/125、预算 12/130/142、dry-run 零副作用、断点跳过与 `--force`、重试规则、manifest 字段）；**F24/F25 H2+H3 收尾扫描（重导航计数、`file://`/沙箱事件、rewards 行数/reason 域/done-raw 自洽、重试窗口、合并语义、`_execute_cell` 集成）** |

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
| 1 | 本模块 pytest 全绿、离线可跑；`make fmt` 后 `--check` 通过 | ✅ | 75 passed（含 7 个回环浏览器用例；`SKIP_BROWSER_TESTS=1` 时 7 skip 仍全绿）；isort/black `--check` 零差异；安全加固后复核见 §11 |
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

## 9. §0 口径与限制披露原文（7 条，最终报告必须原样转载）

1. 本基准测**执行内核能力**（自然语言任务 → Gherkin → Hercules 语义化执行），不含录制/蒸馏（exp001 已覆盖）。
2. `EPISODE_MAX_TIME` 由原生 10s 放宽至 240s（默认，参数化），沿用 BrowserGym `episode_max_time` 可配的做法；若 pilot 后调整，报告须披露最终值与理由。
3. 每 task 单 seed 单次，非官方多 instance 均值口径。
4. **官方成败以页面原生奖励为唯一权威**（`reward_raw > 0`）；Hercules JUnit 结果与奖励不一致的行如实标记 disagreement，不改判。
5. **伺服补丁为纯追加**：`core/core.js` 的响应体 = vendored 原文 + 追加补丁 A（auto-start + HUD/START 加固）与补丁 B（reward hook），vendored 字节从不改写；加固用 `display:none` 隐藏 `#reward-display` 并置空 `core.updateDisplay`/`core.startEpisode`，使官方奖励文本与 START 重开覆盖层都不出现在 agent 的文本视角（`body.innerText`）。加固**封的是 agent 的观察/点击路径**，不是 JS 执行能力：仍在页面上下文里的任何 JS（例如 V6 的沙箱 `page.evaluate`）都能直接调用 `core.endEpisode`/`core.startEpisodeReal`。
6. **奖励收集端点无防伪造能力**：`POST /__r2g_reward` 只校验 `path`/`seed` 非空，补丁 B 对浏览器公开，任何浏览器侧密钥都会出现在伺服的 `core.js` 里——"防伪造"在机制上不可根除，只能检测（spec §7.6 H3）+ 披露。`GET /__r2g_reward` 本体 404、"导航到端点读历史奖励"不成立。
7. **agent 可重新导航同 URL**：`open_url` 对 URL 无 scheme/次数限制，重开 Given 的同 seed URL 会重跑补丁 A → 同一实例从头开局、240s 计时重置、此前失败提交的终局状态作废。pilot 9 个已完成 run 中 5 个自发出现（非攻击行为）。r1 全量**保留官方奖励但逐行披露**（`task_url_navigations`/`flagged`），不据此改判；更严选项（`sessionStorage` 单次自动开局）未实施。

（口径 5/6/7 为安全审查 R1 §4.4 的 P1 增补，spec §0 与本节逐字一致。）

## 10. 与公开基线的对比声明（待 full 后填充）

- 只引用**可查证**文献/榜单数字（论文或官方仓库公布值），并逐条注明设置差异：模型与版本、观察空间（文本/截图）、动作空间、`episode_max_time`、任务实例数与 seed 口径、以及本基准为「自然语言任务 → Gherkin → 语义化执行」而非端到端 RL/IR 策略。
- 本基准为单 seed 单次、无多 instance 均值，**不得**与官方多 instance 均值直接等价比较；披露时须并排列出设置差异表。
- 禁止在本报告中出现未注明出处的数字；pilot/full 数字回填时同步补全本节的引用与差异声明（由总编排执行）。

## 11. 安全加固补记（R1 §4 P0/P1；H1 + H2 + H3 + 披露）

> 依据：`dev_docs/benchmark/security-review-r1-pre.md` §4（加固实施清单）+ §2.V1-V6（实证）。范围：`record2gherkin/benchmark/`、`tests/record2gherkin/benchmark/`、`dev_docs/benchmark/`；**未触碰 `testzeus_hercules/`**，未做 git 操作，未跑真 LLM。
> 加固期间 pilot 仍在运行（端口 8462 的内存旧代码）：未 kill、未占用、未改动其任何产物；本补记的全部实测都用**独立临时端口**（49615/49699 等）+ 临时 rewards 文件。pilot 于 15:44Z 自然结束（manifest：11 次 Hercules 执行 = 10 + 1 重试，overall 8/10），全程未受本次加固干扰。

### 11.1 改动清单

| 项 | 文件 | 内容 |
|---|---|---|
| H1 | `record2gherkin/benchmark/miniwob_server.py` | 补丁 A 的 `core.startEpisodeReal()` 成功后追加四行加固（`#reward-display` → `display:none`；`core.updateDisplay = function () {}`；`core.startEpisode = function () {}`），仍在 `__R2G_PATCH_START__`…`__R2G_PATCH_END__` 内、纯追加；`#query` 指令区不动；用 `display:none` 而非 `remove()`（`endEpisode` 仍要写 `#episode-id`，移除会抛 `TypeError`） |
| H2 | `record2gherkin/benchmark/orchestrator.py` | `CellScan` + `scan_cell_log()`：① 任务 URL 导航次数 → `task_url_navigations`，`>1` → `flagged`；② 日志含 `file://` → `flagged` + `invalid_reason="file_url_navigation"`（cell 无效 + 安全事件日志行）；③ 沙箱**调用**标记 → `flagged` + `invalid_reason="sandbox_tool_invoked"` |
| H3 | 同上 | `scan_cell_rewards()`：核对 `rewards.jsonl` 中该 `(path, seed)` 落在本 attempt 窗口内的行数（`> max(navigations, 1)` → flagged）、`reason` 域、`done`/`raw` 自洽性 |
| 接线 | 同上 | `_execute_cell` 在 `fetch_reward` 之后调用 `_scan_cell`，三键写入结果行（`build_result_row` 新增三个带默认值的关键字参数）；`metrics.ROW_KEYS` 同步（§7.1 键集合断言不破） |
| 测试 | `tests/record2gherkin/benchmark/{test_server_patch,test_browser_miniwob,test_orchestrator}.py` | B7 加固行断言（位置 + 不移除节点）、D16(d) 两例真浏览器断言、F24/F25 扫描断言（含 `_execute_cell` 集成与重试窗口） |
| 披露 | `dev_docs/benchmark/spec.md` | §0 新增口径 5/6/7（三条基准限制）；§3.2 补丁 A 逐字文本与行为要点同步加固；§7.1 schema 增三键；新增 §7.6 扫描判定表；§9 增 7b/D16(d)/E19/F24/F25；§10 增验收第 8 条 |

结果行新增三键（`task_url_navigations:int` / `flagged:bool` / `invalid_reason:null|str`）**不参与任何判定与分母**：`status`/`official_passed` 只由官方奖励决定（口径 4）。语义分级：`flagged=true, invalid_reason=null` = 披露但保留（V4 重导航、V3 记录异常）；`invalid_reason` 非空 = cell 判为无效（V5/V6 安全事件），行仍照录、统计不剔除，由报告逐行披露。

### 11.2 实测复核（验收红线逐条）

| 红线 | 结果 |
|---|---|
| `uv run pytest tests/record2gherkin/benchmark -q` | **75 passed**（23.7s） |
| `uv run pytest tests/record2gherkin -q` | **367 passed**（48.8s；加固前 360，本模块新增 7 个用例，全局无回归） |
| 格式化 | `isort` + `black -l 200` 复跑 `--check` 零差异（只覆盖 benchmark 两目录；vendored 资产未被触碰） |
| `KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" dev_runs record2gherkin tests dev_docs` | **零命中**；8 字符前/后缀同样零命中 |
| H1 独立端口复核（端口 49615/49699，临时 rewards；click-test / enter-text / login-user 三页，成功 + 失败两条路径） | **HUD 零命中**：回合内与 `endEpisode` 后 `body.innerText` 均不含 `reward/START/Time left/Episodes done/Last 10 average`；`#reward-display`、`#sync-task-cover` 均 `display:none`、`is_visible=false`；`#reward-last` 文本仍为模板初值 `"-"`（终局不回写） |
| 同上：奖励 POST 完好 | 三页各 2 条记录（成功 `raw=1` + 失败 `raw=0`，均 `done=true, reason=""`）经 `/latest` 200 取回并落 `rewards.jsonl`；预读前后记录数 6 → 6（预读不产生记录） |
| 同上：START 重开路径关闭 | 失败终局后覆盖层仍 `display:none`；`page.click("#sync-task-cover", timeout=1200)` → `TimeoutError`（普通 DOM 点击不可达）；`core.getUtterance()` 与终局前**恒等**（实例未重开）；`WOB_EPISODE_ID=1` |
| 同上：utterance 预读不变 | 加固后伺服（独立端口）重读 4 个 pilot cell 的 goal，与 pilot `results.jsonl` 记录值**逐字节相同**（click-test / enter-text / login-user / use-autocomplete，4/4 match） |
| H2/H3 对 pilot 真实日志的复核（零误报检查） | 10 个 run 目录（11 行结果：`email-inbox-forward-nl` 有一次基础设施重试，重试复用同一 run 目录）的 `stdout.log` 全量扫描：`file://` 零命中、沙箱调用标记零命中（工具**注册**行不误判）、导航计数 = click-button 2 / enter-password 2 / login-user 2 / use-autocomplete 3 / email-inbox-forward-nl 6 / 其余各 1（与 R1 §2.V4 的 pilot 取证同序；email-inbox 从报告时的 4 增至 6，因该 cell 此后又跑了一轮重试）→ 5 个 cell（对应 6 行，`email-inbox-forward-nl` 有 1 行重试）命中 `>1` 而 `flagged`，与 R1 §3 的名单一致；`rewards.jsonl` 12 条记录（8 条 `reason=""` + 4 条 `"timed out"`）全部在合法域内、`done=true`、`raw` 为数字 → 无一行被误判为安全事件 |

H1 加固后的 agent 文本视角实录（`document.body.innerText`，端口 49699）：

```
click-test  回合内/终局后均: "Click the button.\nClick Me!"            ← 只剩 goal + 控件，HUD/START 消失
login-user  回合内/终局后均: "Enter the username \"tora\" and the password \"4Qn\" into the text fields and press login.\n\nUsername\n\nPassword\n\nLogin"
```

（对照 R1 §2.V1 加固前实录：回合内 `Last reward: - / Last 10 average: - / Time left: 240 / Episodes done: 0`，`endEpisode(1)` 后 `Last reward: 1.00 / Last 10 average: 1.00`。）

### 11.3 与审查报告字面口径的两处必要偏差（均已回写 spec §7.6）

1. **沙箱调用标记的字符串**：报告写 `Executing execute_python_sandbox`，但产品代码里真实打印的**调用**标记是 `Executing Python sandbox: file=…`（`execute_python_sandbox.py:62`）与 `Using sandbox tenant: …`（:70）；`execute_python_sandbox` 只出现在**注册**日志（`[TOOL_DEBUG] Processing tool 'execute_python_sandbox'` / `Registered tool: execute_python_sandbox`），**每个 run 都有**。若按报告字面实现会对 100% 的 cell 误报，故实现用真实调用标记，并在 `orchestrator.SANDBOX_CALL_MARKERS` 注释与 spec §7.6 写明理由；单测含"只有注册行 → 不 flag"的反例断言。
2. **`reason` 合法域**：报告给的是 `{"", "timed out"}`；复核 vendored 树发现 `miniwob/unicode-test.html:53,55` 是**唯一**传第三个参数的任务页（成功 `'Cool!'`、失败 `'You clicked on <x> when you should have clicked on <y>'`，其余 129 页都不传）。若按 `{"", "timed out"}` 实现，full 中 `unicode-test` 的合法行会被误判，故合法域 = `{"", "timed out", "Cool!"}` ∪ 前缀 `"You clicked on "`，并在 spec §7.6 注明出处。

另有两条实现口径（同样回写 spec §7.6）：H3 只统计 `received_at` 落在本 attempt 窗口内的记录（基础设施重试复用同一 `(path, seed)`，按整文件计数会把每个重试 cell 误判）；H3 的行数规则是**披露启发式而非不变量**——reward hook 对每次 `core.endEpisode` 调用都 POST，终局后仍被调用的任务可以合法地为一次加载追加记录。pilot 取证：12 条已记录 POST 的 `reason` 全在合法域内、`done=true`、`raw` 为数字，且每个 cell 的记录数都不超过其页面加载数（10 个 cell 中 9 个恰为 1 条）。

### 11.4 对 pilot 数据与 full 的影响（披露，不改判）

- **pilot 已完成的行不含三键**（旧代码、内存中进程所写）：pilot 的 `results.jsonl` 行没有 `task_url_navigations`/`flagged`/`invalid_reason`，读者须用 `.get()` 容错读取；full 阶段（新代码）行才带三键。pilot 的 V4 事实（5/9 已完成 run 存在同 URL 重复导航）由 R1 §3 披露，本节不重复改判。
- **P1-5 上游产品建议只记录不实施**（超出本次范围、且按约定不动 `testzeus_hercules/`）：`open_url` 增加 scheme 白名单（仅 http/https，根除 V5）；`execute_python_sandbox` 增加环境开关（如 `SANDBOX_DISABLED=1` 直接拒执行）并由 orchestrator 经 `run_feature(extra_env=...)` 注入（根除 V6）。V6 的机制性结论（restricted 档非安全边界）未变，只能靠 H2③ 检测 + 披露。
- full 报告需按口径 5/6/7 逐行披露 `flagged`/`invalid_reason` 的计数与名单（含 pilot 的 V4 计数），并说明本节的两处字面偏差。

