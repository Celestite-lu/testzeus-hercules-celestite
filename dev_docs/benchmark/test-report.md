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


## 12. r2 实现补记（spec-r2 / review-r2 M1–M5 全部落地；2026-09-22）

### 12.1 测试命令与结果（离线全绿）

```
uv run pytest tests/record2gherkin/benchmark -q        # 117 passed（基线 75 + r2 新增 42）
uv run pytest tests/record2gherkin -q                  # 409 passed（基线 367 + 42）
uv run pytest tests/test_simple_hercules_langgraph.py -q  # 12 passed（引擎守卫回归，默认 off）
make fmt / black --check（testzeus_hercules/ tests/ record2gherkin 三个目录）  # CLEAN
```

浏览器组（T5/T6）在本机 chromium 实跑通过（回环伺服器，无外网）；`SKIP_BROWSER_TESTS=1` 时整组自动跳过。

### 12.2 T1 回放锁 +5 实测输出（判分修复可回放性证明）

fixture：`tests/record2gherkin/benchmark/fixtures/r1_replay.json`（由 `dev_runs/benchmark/miniwob-r1/results.jsonl` 130 行 last-wins 去重为 125 格固化，只含 `(runner_status, raw)` 判定输入，无 key、无长文本）。

- 5 个救援格翻正为 `official_passed`（`runner_status` 披露键保留 infra 状态）：

```
login-user-popup            timeout raw=1.0    -> official_passed
multi-layouts               timeout raw=1.0    -> official_passed
use-colorwheel-2            timeout raw=0.5372549019607844 -> official_passed
click-pie                   no_junit raw=1.0   -> official_passed
click-collapsible-2-nodelay no_junit raw=1.0   -> official_passed
```

- 反例锁定：`email-inbox-delete`（r1 终态 `timeout / raw=-1.0 / task_url_navigations=8`）→ `status=timeout`（**非** `official_passed`；按 §1.1 落 infra 状态，metrics 判 0——review-r2 M5-1 口径，非 `official_failed` 字面值）。
- 一致格/infra 格不变：`book-flight`(passed)、`ascending-numbers`(official_failed)、`drag-cube`(no_reward)、`click-pie-nodelay`(timeout, raw=-1)、`click-collapsible-2`(no_junit, raw=-1)。
- 全量摘要重算：对 fixture 内 125 行 `(runner_status, raw)` 重跑新旧两条优先级链，官方合计 **54 → 59（+4.0pp，锁定值）**，与 plan-r2 §C1a 及 review-r2 §5 独立复算一致。

### 12.3 引擎侧改动清单（默认 off 与 r1 行为一致）

| 文件 | 改动 | 默认行为 |
|---|---|---|
| `testzeus_hercules/config.py` | env 映射 + setdefault 注册 `BROWSER_STATE_REFRESH_MODE="always"`、`BROWSER_NAV_MAX_CHAT_ROUND="50"`、`NAV_STEP_TIME_BUDGET_S="0"` | r1 原值 |
| `testzeus_hercules/core/runner.py` | `BaseRunner.__init__` 的 `browser_nav_max_chat_round` 缺省改从 config 读取（函数体内取值，review-r2 §4.c；未配置/非法/≤0 回落 50） | 50，不变 |
| `testzeus_hercules/core/simple_hercules.py` | C4a：`_requires_state_refresh` 模式化（markers 恒打断；`markers_only` 下成功状态变更不打断；非法/空值回落 `always`）；C4b：`_run_nav_agent` 轮循环顶部步级预算（超限带进展返回 `[NAV_STEP_BUDGET_EXHAUSTED] …`，非异常）；提取 `_last_assistant_content`（max-rounds 返回文本逐字不变） | `always`/0 → r1 行为 |
| `testzeus_hercules/core/agents/browser_nav_agent.py` | C4c/d prompt 包：rule 8 重感知放宽、L55/L116 刷新教学撤销替换、末尾输出克制句（文本改动，按 spec §5.2 无开关） | prompt 文本 |
| `testzeus_hercules/core/agents/high_level_planner_agent.py` | C4e prompt 包：Closure Nudge 与 Critical Rule 5 换为 closure 修正句、删 Platform Awareness/Test Data Focus/Executor Operation Detection 章节、`_json_instruction` 删重复 terminate 规则、加禁重导航句与输出克制句 | prompt 文本 |

默认 off 证明：T9 四组断言（markers_only/always/未配置/非法值/空串/大小写归一；预算超限与 0/未配置）+ `tests/test_simple_hercules_langgraph.py` 12 passed + `tests/record2gherkin` 409 passed。spec §9.2 要求的两个引擎 commit（§5.1 引擎代码 / §5.2 prompt 包）按文件组天然可分，git 操作由总编排执行。

### 12.4 harness 侧改动清单（record2gherkin）

- `benchmark/orchestrator.py`：C1a 判分重排 + 行键 `runner_status`/`attempt`/`infra_circuit_break`（schema 见 `metrics.ROW_KEYS`，三键零进分母，T2）；C1b 熔断器（attempt 级三条件 + run 级连续 2 格 `BenchmarkError`，重试池显式排除熔断行与 smoke 格，T3）；C1c 预检 gate（exit 3，不启服务不执行，T4）；C1d `attempt<N>/stdout.log` 分目录（T10）；C5 路由配置生成（`<exp_dir>/agents_llm_config.json` 永不含 key）+ 恰四键 env 注入（T7）；C6 `--extra-tools`/`--smoke-cells`（pilot 专属追加格，不进重试池，预算计入，T11）；C7 `--template-notes` 两变体；`R2_BUDGET_CAP=144`（F21 更新）；manifest 新增 `flags`（T11）。
- `benchmark/preflight.py`（新）：1-token 探测，transport 与引擎同栈（ChatOpenAI 直连 base_url，review-r2 M4），detail 全过 `mask_secret`，零文件写。
- `benchmark/miniwob_server.py`：`AUTO_START_PATCH_SINGLE`（由现状补丁 A 程序化派生，恰三处不同：标记行/新增①拦截分支含 M2 加固/新增②登记，T6d 字节断言）；`TERMINAL_CUE_PATCH`（恒定中性文本）；补丁顺序 原文→A(single)→REWARD_HOOK→TERMINAL_CUE；两 flag 全 off 时输出与 r1 逐字节一致（T5e）。
- `benchmark/goal_reader.py`：`render_feature(notes=, notes_terminal_cue=)` 注释段两变体（T8）；`evaluation/runner.py`：`run_feature(stdout_log_path=)`（None 时路径不变，T10）。

### 12.5 总编排执行 r2 的完整命令（全开 flag 组合）

```bash
# 0) 预检/预演（dry-run 天然跳过 C1c 预检，不进程不写文件）
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-r2 --stage full --dry-run \
  --terminal-cue --single-start --role-routing --nav-model deepseek-flash --extra-tools --template-notes --latency-env

# 1) 冒烟 pilot + drag 冒烟：14 次执行 = pilot 10 + 重试 2 + smoke 2（drag-items/drag-box 不重试）
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-r2 --stage pilot \
  --terminal-cue --single-start --role-routing --nav-model deepseek-flash \
  --extra-tools --template-notes --latency-env --smoke-cells drag-items,drag-box

# 2) headline full（用户确认充值、C1c 预检通过后）：125 + 重试 5 = 130，预算 cap 144 硬拦
uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-r2 --stage full \
  --terminal-cue --single-start --role-routing --nav-model deepseek-flash \
  --extra-tools --template-notes --latency-env
```

非 dry-run 启动即做 C1c 预检（planner 模型 + role-routing 时的 nav 模型，各 1 token）：402/余额/连接异常 → exit 3、不启动伺服器、不执行任何 cell。断点续跑同命令即可（已有 `(task_id, seed)` 行自动跳过）；`--force` 关闭跳过。

### 12.6 已知问题与实现口径（10 条，均不阻塞核心目标）

1. **`runner_status` 行键取值**：实现为"该行在 r1 优先级链下的 status"（域 = `timeout|no_junit|no_reward|official_passed|official_failed|None`，no_goal 为 None）——与 spec §1.1 的值域枚举及 review-r2 M5-1"该行实为 runner_status=timeout"的表述一致，救援格审计方式为 `status != runner_status`。
2. **disagreement 语义**：按 spec"语义不变"保留原表达式；C1a 救援行使该行新进入两态计算，`junit_passed=None` 时表达式落 `False`（r1 上游既有怪癖，r1 中不可能出现此行形）。
3. **C4e"`_json_instruction` 的重复 terminate 规则"删除点**：删单条 *"Set \"terminate\": \"yes\" ONLY after a helper has confirmed the task is done"*（closure 门控已由改写后的 closure 句承担），保留通用句 *"Set \"terminate\": \"no\" when you still have steps to execute"*。
4. **C7 开关参数命名**：spec 只点名 `notes_terminal_cue`；T8 要求两变体，故实现为 `notes: bool`（基础注释段）+ `notes_terminal_cue: bool`（第 5 行，须与 C2 同开，orchestrator 侧联动）。
5. **熔断 marker `"402"` 为裸子串匹配**（spec 逐字）：任何 stdout 出现 `402` 即命中——误报方向只会"少重试/多熔断"，不判分、不洗白，偏保守可接受。
6. **`--smoke-cells` 预算口径**：`hercules_budget` 的 smoke 计数取"声明的追加格"而非"断点跳过后实际将跑的格"，预算作为计划数上限偏保守。
7. **`make fmt` 全仓副作用（披露）**：仓库既有 7 个文件存在 black 基线漂移（历史以默认 88 列格式化），`make fmt`（Makefile 全仓目标）一并重排：`base_nav_agent.py`、`multimodal_base_nav_agent.py`、`config_env_loader.py`、`state_handler.py`、`litellm_helper.py`、`simple_hercules.py`/`runner.py` 的大部分行、`tests/test_simple_hercules_langgraph.py`——纯格式行合并，无语义变化（上述引擎测试全绿佐证）。
8. **spec §9.2 的"两个引擎 commit"**：git 操作属总编排；实现按 commit1 = `config.py`+`core/runner.py`+`simple_hercules.py`（C4a/b）、commit2 = 两个 prompt 文件（C4c/d/e）分组，可分别提交、各自可 revert 而不破坏 harness 测试。
9. **C1c 真实预检未执行**（任务边界：不跑真 LLM）：单测 T4 以 monkeypatch `ChatOpenAI.invoke` 覆盖成功/402/连接三路与脱敏；真实一次预检由总编排充值后执行（任一非 dry-run 命令的启动期自动完成）。
10. **ablation 运行机制未实现**（`--run-cap`、任意 cell 子集、retry=0 语义）：沿 review-r2 §4.a"另批事项"结论，本轮 Out of Scope。

### 12.7 脱敏红线复核

`KEY="$(cat LLM-Key.txt)"; grep -rl -- "$KEY" record2gherkin tests dev_docs` 零命中；T7 断言生成的 `agents_llm_config.json` 无 `model_api_key`/`sk-`（写盘函数对含 key 配置直接抛 `BenchmarkError`）；T4 断言预检失败日志含脱敏原因与"充值"提示且不含 key 实值。

## 13. r2 补记：GLM（智谱 coding plan）实验 provider 集成（2026-09-22）

为 r2 实验链路新增 `--provider {deepseek,glm}`（默认 `deepseek`，现状路径逐字节不变）。用户决定 r2 实验用 **glm-5.3-flash** 跑。

### 13.1 改动范围

- `record2gherkin/evaluation/runner.py`：新增 `LLMProviderConfig`（name/key_path/model/base_url）与 `DEEPSEEK`（现状 §4.3 常量原样打包）/`GLM`（`GLM-Key.txt` / `glm-5.3-flash` / `https://open.bigmodel.cn/api/coding/paas/v4`）实例及 `resolve_provider`（非法名 → `RunnerError`）；`read_api_key` 支持两格式——文件含 `KEY=` 行时按 KV 解析（忽略 `#` 注释与空行，空 `KEY=` 明确报错），否则维持单行 `strip()` 历史行为；`build_child_env(api_key, *, model=None, base_url=None)` 可选覆盖默认常量（`LLM_MODEL_API_TYPE` 恒 `openai`，GLM 端点同为 OpenAI 兼容）；`build_run_plan` / `run_feature` 新增可选 `provider` 参数（`None` = deepseek 现状），key 从 provider 自带 key 路径读取。
- `record2gherkin/benchmark/orchestrator.py`：CLI 新增 `--provider`（默认 deepseek）、`--nav-model` 默认改为 None（按 provider 解析：deepseek → `deepseek-flash`，glm → `glm-5.3-flash`）；GLM 时 C5 路由 planner 默认 `glm-5.3`（`GLM_PLANNER_MODEL`）、helper 走 provider flash 档；key 读取/child env/结果行 `model`/C1c 预检端点全部走 provider 配置；manifest 在**非默认 provider** 时追加 `llm_provider` 与 `model` 两个披露字段（deepseek 现状 manifest 键集逐字节不变，见 13.3）。
- `record2gherkin/benchmark/preflight.py`：`probe_llm` 增加可选 `provider` 参数（model/base_url 缺省时取 provider 常量；显式传入优先），编排侧仍显式传 `base_url=`（既有 spy 断言零修改）。
- 测试：`tests/record2gherkin/evaluation/test_llm_provider.py`（9 条：KV 解析含注释行/缺 MODEL 回退/空 KEY 报错/单行向后兼容、resolve_provider 非法名、build_child_env 覆盖、默认 env 零变化、glm dry-run env 四键、provider 自带 tmp key 文件）+ `tests/record2gherkin/benchmark/test_provider_routing.py`（9 条：glm 路由默认、默认 provider 现状、C5 glm 配置无 key、C1c glm 端点/模型探测、probe_llm provider 参数、manifest 披露与默认键集不变、cell 透传、CLI glm dry-run、非法 provider 拒绝）。

### 13.2 验证证据

- `uv run pytest tests/record2gherkin -q` → **427 passed**（基线 409 + 新增 18，既有测试零修改全绿）。
- `--provider glm` dry-run 实测（`python -m record2gherkin.benchmark.orchestrator --exp-id glm-smoke-check --stage pilot --dry-run --provider glm`）：exit 0，打印 10 cell 计划 + `budget {"breakdown": {"pilot": 10}, "cap": 144, "retry": 2, "total": 12}`，不写任何文件。
- glm child env 实测（假 key，脱敏）：`LLM_MODEL_NAME=glm-5.3-flash`、`LLM_MODEL_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4`、`LLM_MODEL_API_TYPE=openai`、`LLM_MODEL_API_KEY=***REDACTED***`；默认（不传 provider）仍为 `deepseek-v4-pro` / `https://api.deepseek.com`。
- 脱敏复核：`grep -rl -- "$(grep -E '^KEY=' GLM-Key.txt | cut -d= -f2)" record2gherkin tests/record2gherkin dev_docs/benchmark` 零命中；测试全部使用 tmp 假 key 文件/假 key，从不读取真实 `GLM-Key.txt` 内容。

### 13.3 实现口径（3 条，均不阻塞核心目标）

1. **manifest 新字段为条件披露**：`llm_provider`/`model` 仅在非默认 provider 时写入——若无条件追加，deepseek 现状 manifest 键集变化将违反"默认路径逐字节一致 + 现有测试零修改"红线（`test_f23` 断言精确键集）；deepseek 的口径已由既有 `model_name`/`llm_base_url` 隐含披露。
2. **"缺 MODEL 回退"的实现位置**：`read_api_key` 只消费 `KEY=`（字段无关解析，缺 `MODEL=`/`BASE_URL=` 行不影响 key 读取）；运行时 model/base_url 恒取 provider 代码常量（glm-5.3-flash / coding 端点），`GLM-Key.txt` 中的 `MODEL=`/`BASE_URL=` 行为人工参考、运行时不读取——改模型需改代码常量，避免 key 文件内容漂移影响实验口径。
3. **evaluation/sweep.py 未 provider 化**（Out of Scope）：sweep 仍固定 deepseek 常量；r2 实验走 benchmark orchestrator 链路，不经过 sweep。

## 14. r3 实现补记（spec-r3 / review-r3 M1–M3 全部落地；2026-09-23）

> 范围：R3-1…R3-6 全部引擎/harness 改动 + 离线单测 T1–T12。review-r3 三项必改（M1 无条件覆盖、
> M2 provider 层同源、M3 保守档 65/125=52.0%）已在实现中按修订后 spec 落地。**未跑真 LLM、未跑
> Hercules、未做任何 git 操作**（pilot/headline/D 臂由总编排执行，命令见 §14.6）。

### 14.1 测试结果（离线，无 key、零外网）

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/record2gherkin -q` | **456 passed**（74.6s；r2 基线 427 + 新增 29 = 456，零丢失零跳过） |
| `uv run pytest tests/record2gherkin/benchmark/test_r3_improvements.py -q` | **29 passed**（T1–T12 全覆盖；T3 为 subprocess 隔离，T5/T6/T9 为 monkeypatch stub，T12 为 D 臂 dry-run） |
| `uv run pytest tests/test_simple_hercules_langgraph.py -q` | **12 passed**（改动前后 stash 对照各跑一次，结果一致——引擎回归零变化） |
| `uv run black --target-version py311 -l 200 --check`（全部触碰文件 + `record2gherkin/` + `tests/record2gherkin/`） | FMT-CLEAN；isort 同过（profile=black） |
| 判分链 diff 审计 | `build_result_row` 函数体、`fetch_reward`/`/latest`、`miniwob_server.py`、`goal_reader.py`、`tasks.py`、`preflight.py` diff 为 **0 行**（spec §9 验收 2） |

T1–T12 → 测试函数对照：T1 = `test_t1_*`（3）；T2 = `test_t2_*`（4，含 wait_for 层 + provider 层）；T3 = `test_t3_*`（2，subprocess）；T4 = `test_t4_*`（1）；T5 = `test_t5_*`（2）；T6 = `test_t6_*`（2）；T7 = `test_t7_*`（1）；T8 = `test_t8_*`（3，含 >50 截断）；T9 = `test_t9_*`（4）；T10 = `test_t10_*`（5）；T11 = `test_t11_*`（1）；T12 = `test_t12_*`（1）。另含 CLI 全 flag 组合 dry-run 冒烟（§14.6 注）。

### 14.2 r2 flag 零回归证明

1. **r2 十二个开关的行为面未动**：`terminal_cue/single_start/role_routing/nav_model/latency_env/extra_tools/template_notes/smoke_cells/provider/max_cells/dry-run/force` 的构造参数、`LATENCY_ENV_OVERRIDES` 五键值（逐字节）、`hercules_budget`/`R2_BUDGET_CAP=144`、C1b/C1c/C1a 全部原样；`git diff` 对这些路径只有增量 append/新增分支，无既有行改写（除 `_child_extra_env` 的文档串与新增 if 块）。
2. **r2 既有测试适配 4 处（均由 spec-r3 §7/T10 强制，非行为回归）**：
   - `test_f23_manifest_fields_and_no_secrets` / `test_t11_default_flags_are_all_off` / `test_t11_manifest_records_flags`：manifest `flags` 精确键集断言扩为 r2 八键 + r3 五新键（off 时 `nav_max_tokens=0`、`planner_timeout=0`、`extra_tools_modules=[]`、`disable_sandbox=false`、`assert_discipline=false`）——spec-r3 §7 要求五个新键恒在 manifest `flags`。
   - `test_t7_latency_and_extra_tools_merge_over_routing`：`--extra-tools` 现默认同时注入 `EXTRA_TOOLS_MODULES="drag_and_drop_tool"`，env 键数 5+1+4 → 5+2+4（spec-r3 §5.1/T10a）。
   - 语义全部保留：off 语义、键值、预算、smoke 规则断言一字未改。
3. **其余 r2 测试零修改全绿**（427 → 456 中 423 条未触碰）。

### 14.3 E 类判分中立默认值清单（spec-r3 §5 前言口径）

| 项 | 载体 | 默认态（flags 全 off / 默认命令）行为 | 对判分影响 |
|---|---|---|---|
| E1 子集加载 | env `EXTRA_TOOLS_MODULES`（config relevant_keys + setdefault `""` + getter） | 未注入 → allow 为空 → 全量加载 = r2 | 无（env 不注入时引擎不变） |
| E2 文件工具日志行 + 扫描 | `file_handler_tool.py` 三函数入口 + `FILE_TOOL_CALL_MARKERS`/`INVALID_REASON_FILE_TOOL` | 无 flag；日志行只在真被调用时出现；命中 → `invalid_reason=file_tool_invoked` | 判分中立（只增 invalid 披露，不改 status/official_passed）；对零调用运行零输出 |
| E3 scheme 白名单 | `open_url.py`（special 块后、`ensure_protocol` 前；新增 `add_event/EventType/EventData` import） | 无 flag、对任何运行生效；`javascript:/data:/file:` 等拒绝且零导航；`[OPEN_URL_BLOCKED]` → 仅 `flagged=True` | 判分中立（安全增强，review-r3 §二-12 已裁定为"唯一例外"） |
| E4 metrics clean 口径 | `metrics.py`：`clean_rate` + `Summary.clean`/`invalid_cells`（≤50 条截断，`invalid_cells_total` 记全量） + `as_dict` | 无 flag；`overall`/`_rate` 零改动（invalid 格仍在官方分母） | 无（manifest `metrics` 只增披露字段） |
| E5 沙箱关停 | env `SANDBOX_DISABLED`（config + getter）；`execute_python_sandbox.py` 入口拒绝；`SANDBOX_CALL_MARKERS` 追加 `[SANDBOX_DISABLED]` | 未注入 → `get_sandbox_disabled()=="false"` → 走原路径（首行日志与 r2 逐字节同） | 无（env 不注入时引擎不变） |
| E6 极早崩留痕 | orchestrator `GoalReadError` 分支写 `runs/<run_id>/goal_read_error.log`（mkdir + 写失败仅 warning） | 无 flag；仅 no_goal 格新增一个留痕文件 | 无（`failure_message` 与结果行语义不变） |

R3-1/R3-2/R3-6 三个引擎行为 flag 默认 off 的逐字节复现由 T1（env 未设 → 4096）、T2（env 未设 → 双层同为 `LLM_REQUEST_TIMEOUT`、planner 产物 timeout==90）、T11（off/未设 → system_message 与 r2 快照相等）锁定。

### 14.4 实现口径与已知问题（4 条 + 1 条 spec 勘误，均不阻塞核心目标）

1. **spec T8 "overall = 1/3" 系算术勘误**：按其行构造（invalid&passed=True + invalid&failed + 正常通过），在 `_rate` 零改动约束下 overall=**2/3**（`_passed` 对 official_passed=True 一律计 1，r2 官方口径本就含 invalid-but-passed 格；spec §5.4/§10 两次明令禁止改动）。测试锁定 2/3 + clean=1/1 + invalid_cells 恰两格，并注明理由。
2. **`invalid_cells` 截断计数载体**：spec 只定 `Summary` 增 `clean`/`invalid_cells` 两字段、未给截断计数落点；按"最简单确定性行为"增整数披露字段 `invalid_cells_total`（`as_dict` 同步），使 `len(invalid_cells) ≤ 50 < total` 可审计（T8 第三条锁定）。
3. **scheme 白名单的 `localhost:PORT` 边缘**（review-r3 S2，spec 未采纳放行）：`urlsplit("localhost:5000/x").scheme=="localhost"` 会被拒。benchmark 任务 URL 恒为 `http://127.0.0.1:…`（带 scheme）不受影响；r3 报告如出现该形态被拒格，按 E3 的 `[OPEN_URL_BLOCKED]` 披露口径呈现即可。
4. **file:// 被拦截尝试的双标记**：`open_url("file://…")` 的拒绝日志行同时含 `file://` 子串 → V5 扫描判 `file_url_navigation`（invalid）。与 r2"file:// 即安全事件"契约方向一致（偏保守、只多不少），无需分支特判；T7 的 open_url-blocked 用例用 javascript:/data: 验证"仅披露"通道。
5. **black 对 `_llm_ainvoke` 的折行**：spec §2.2 的多行三元式被项目 black（-l 200）折为单行，语义逐字节等价（T2 四条锁定）。

### 14.5 r3 引擎侧改动文件清单（全部为 plan-r3 §1 采纳项落点）

| 文件 | 改动 |
|---|---|
| `testzeus_hercules/utils/llm_helper.py` | +`get_nav_max_completion_tokens()`、+`get_llm_planner_request_timeout_seconds()`、`create_chat_model` 兜底段后 env>0 无条件覆盖 `max_tokens`（M1 修法） |
| `testzeus_hercules/core/simple_hercules.py` | `_llm_ainvoke`：planner 走 `get_llm_planner_request_timeout_seconds`，其余 agent 原值（§2.2） |
| `testzeus_hercules/core/agents/high_level_planner_agent.py` | provider timeout 兜底同源（§2.3，M2 修法）；`PLANNER_ASSERT_DISCIPLINE=true` 时前置 `_ASSERT_DISCIPLINE_INSTRUCTION`（逐字）（§6）；+`get_global_conf` import |
| `testzeus_hercules/core/extra_tools/drag_and_drop_tool.py` | source 解析替换为 candidates 透传（删 `[md='…']` 包裹缺陷段）；find_element 按序尝试、全败列全候选；description/参数 docstring 更新；target 侧与鼠标序列零改动（§3.1） |
| `testzeus_hercules/core/extra_tools/__init__.py` | LOAD_EXTRA_TOOLS 门控内增 `EXTRA_TOOLS_MODULES` 白名单过滤，空=全量（§5.1） |
| `testzeus_hercules/core/extra_tools/file_handler_tool.py` | persist/recall/augment 三入口各 +1 行 `[EXTRA_TOOL_CALL] <name> path=…`（§5.2） |
| `testzeus_hercules/core/tools/open_url.py` | scheme 白名单（http/https 放行；拒绝返回固定文案 + `[OPEN_URL_BLOCKED]` 日志 + 事件），插入点= special 块后（§5.3） |
| `testzeus_hercules/core/tools/execute_python_sandbox.py` | 函数体最前 `SANDBOX_DISABLED=true` 拒绝（tenant 读取/任何日志标记之前）（§5.5） |
| `testzeus_hercules/config.py` | relevant_keys + `EXTRA_TOOLS_MODULES`/`SANDBOX_DISABLED`/`PLANNER_ASSERT_DISCIPLINE`；`_finalize_defaults` 三个 setdefault；+`get_extra_tools_modules()`/`get_sandbox_disabled()` |
| `record2gherkin/benchmark/metrics.py` | `clean_rate`/`invalid_cell_list`（`INVALID_CELLS_LIMIT=50`）、`Summary.clean`/`invalid_cells`/`invalid_cells_total`、`summarize`/`as_dict` 接线；`_rate` 零改动（§5.4） |
| `record2gherkin/benchmark/orchestrator.py` | 五新 CLI + `__init__` 参数/校验（空 modules 硬失败）/flags 五新键；`_child_extra_env` 五新 env（`all` → 不注入 `EXTRA_TOOLS_MODULES`）；扫描器 `FILE_TOOL_CALL_MARKERS`/`INVALID_REASON_FILE_TOOL`/`OPEN_URL_BLOCKED_MARKER`（仅 flagged）/`SANDBOX_CALL_MARKERS`+`[SANDBOX_DISABLED]`；E6 `goal_read_error.log`；docstring 增 r3 段 |

### 14.6 总编排执行 r3 的完整命令

r2 七开关原样保留；断点续跑=重跑同命令（已有行自动跳过）；`--max-cells` 按 5h 窗口配速。

```bash
# 0) 预演（不进程、不写文件、跳过 C1c 预检）
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r3 --stage full --dry-run --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-max-tokens 768 --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline

# 1) M1 pilot + drag 冒烟（14 执行 = 10 + 重试 2 + smoke 2；kill-switch 判据：2 格 stdout.log 中
#    "Found source element using selector:"（进入鼠标序列的代理信号，review-r3 S1）出现 ≥1 次 → 保留
#    子集进 headline；仍为 0 → headline 去掉 --extra-tools 并在报告声明放弃拖拽家族）
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r3 --stage pilot --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-max-tokens 768 --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline \
  --smoke-cells drag-items,drag-box \
  --max-cells <按5h窗口配速>

# 2) M2 headline full（125 + 重试 5 = 130 ≤ cap 144；flags 全集入 manifest）
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r3 --stage full --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-max-tokens 768 --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline \
  --max-cells <按5h窗口配速>
```

D-ablation 臂（R3-4，零代码，**须用户逐项批准后执行**；独立 exp-id/exp-root 另批 135 执行；所有数字必须携带 `episode_max_time_ms=480000 / timeout_s=900` 标注双列呈现，禁止与 headline 合并分母）：

```bash
uv run python -m record2gherkin.benchmark.orchestrator \
  --exp-id miniwob-r3-d480 --stage full --provider glm \
  --terminal-cue --single-start --role-routing --latency-env --template-notes \
  --nav-max-tokens 768 --planner-timeout 150 \
  --extra-tools --disable-sandbox --assert-discipline \
  --episode-ms 480000 --timeout-s 900 \
  --exp-root dev_runs/benchmark-ablation \
  --max-cells <按5h窗口配速>
```

（T12 已离线验证 D 臂 CLI 组合：dry-run 计划 125 格、budget `{"breakdown": {"full": 125}, "retry": 5, "total": 130, "cap": 144}`、URL 含 `r2g_ms=480000`、零执行零落盘、不触发预检；本实现阶段另以全部 headline flags + `--episode-ms 480000 --timeout-s 900` 做过一次 CLI dry-run 冒烟，exit 0。）

### 14.7 脱敏红线复核

本补记全部改动不读写任何 key 文件；`role_routing_env`/`write_agents_llm_config` 的"key 只走 env、配置文件永不落 key"红线代码未动（T10 断言 env 注入、既有 T7 断言文件无 key 继续生效）；测试使用的均为内存/临时假 key。
