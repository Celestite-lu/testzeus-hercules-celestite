# benchmark 模块 spec 独立审查（review.md）

> 审查对象：`dev_docs/benchmark/plan.md`、`dev_docs/benchmark/spec.md`
> 审查方式：文档核对 + 复用接口核对（`record2gherkin/evaluation/runner.py`）+ **本机实测**（uv 1.1.0 `miniwob` / `browsergym-miniwob`、playwright 1.49.0 chromium headless、临时 http.server，测毕全部清理）
> 日期：2026-09-21

## 结论：REVISE（必改项 6 条）

一句话理由：**补丁伺服机制经实测成立（修一处竞态后 book-flight 等 5 个难点页面全通过、同 seed 确定性成立、reward POST 契约成立），但 spec 存在 3 条会导致 pilot 直接跑不通/跑错的硬伤（pilot 任务表含不存在的 `visual-maze`、构建期枚举 API 写错、`getUtterance()` 返回 dict 未处理），以及 3 条会造成测试失败或口径失真的文本错误，均为局部修改，无需改设计。**

---

## 1. 必改项（改动均为局部文本/清单级）

### 1.1 PILOT_SUBDOMAINS 含不存在的 `visual-maze` → pilot 按设计必炸

- **问题**：spec §2.3（spec.md:99）硬编码 `visual-maze` 为 pilot 第 10 项，并规定"任一不在全表内 → 启动即报错"。
- **依据（实测）**：`browsergym.miniwob.ALL_MINIWOB_TASKS` 共 **125** 个任务 id，逐一核对 `miniwob/<subdomain>.html` **全部存在**；但注册表里**没有 `miniwob.visual-maze`**（pip 包 html 树中也无该文件），唯一的 visual 任务是 `miniwob.visual-addition`。按 spec 自己的规则，pilot 启动即硬失败。
- **改法**：`PILOT_SUBDOMAINS` 中 `visual-maze` → `visual-addition`（仍是唯一 visual，满足"含 ≥1 visual"，family 覆盖仍 ≥5）。

### 1.2 构建期枚举写法不可用：`browsergym.miniwob.all` 是模块不是列表

- **问题**：spec §2.1（spec.md:65）"枚举 `from browsergym.miniwob import all as miniwob_all; miniwob_all`"——实测 `all` 是**模块**，iterable 会直接 `TypeError: 'module' object is not iterable`。
- **依据（实测）**：正确入口是包级 `browsergym.miniwob.ALL_MINIWOB_TASKS`（125 个任务类，每类均有 `get_task_id()`/`subdomain`/`desc`，已逐一验证）。
- **改法**：§2.1 改为 `from browsergym.miniwob import ALL_MINIWOB_TASKS` 并枚举之；browsergym-miniwob 版本一并记入 tasks.json `source`。

### 1.3 `core.getUtterance()` 可返回 dict，spec §4.1 按纯 str 处理会崩

- **问题**：spec §4.1（spec.md:198-201）规定 `goal = page.evaluate("core.getUtterance()")` 后按 `str` 清洗。实测 `email-inbox-forward-nl` 返回 `{"utterance": "Gerti wants the email Raye sent to you.", "fields": {...}}`——Python 侧是 dict，`sanitize_goal(str)` 直接 AttributeError 或把 repr 塞进 Gherkin。pilot 集内的 `email-inbox-forward-nl`、全量的 `email-inbox-*-nl-turk` 共 3+ 个任务受影响。
- **依据（实测 + 上游同款处理）**：browsergym-miniwob 自身 `base.py::_get_goal`：`if isinstance(response, dict): goal = response["utterance"]`。
- **改法**：§4.1 补一句：`getUtterance()` 返回非 str（dict）时取 `["utterance"]` 键（与 browsergym `_get_goal` 同规则），再进 `sanitize_goal`。

### 1.4 补丁 A 存在开局竞态：book-flight 实测开局失败（"ui_utils is not defined"）

- **问题**：spec §3.2 补丁 A 只等 `WOB_TASK_READY`（core.js:49 默认即 `true`），但 `core.startEpisodeReal()` 依赖 `window.onload → core.startEpisode()` 先创建的 `cover_div`/`click-canvas`（core.js:57,79-84,86-105）；且补丁在 try **之前** `clearInterval`，异常后**不再重试**。轻页面（click-button）子资源 50ms 内就绪、5/5 次实测侥幸通过；重页面实测 **book-flight 首个 tick 抢在 onload 前 → `genProblem()` 抛 `ReferenceError: ui_utils is not defined` → 永久失去自动开局**（该任务必然 no_goal/no_reward 计 0）。冷缓存下其他页面同属风险类。
- **依据（实测）**：spec 原文补丁：book-flight `start_error="ReferenceError: ui_utils is not defined", ept0_set=false, utterance=""`。替换为下述修正补丁后：book-flight / visual-addition / enter-text / email-inbox-forward-nl / click-test 全部开局成功、utterance 非空、EPISODE_MAX_TIME=240000。
- **改法**（用此修正文本替换 §3.2 补丁 A，仅改轮询条件与重试语义；标记注释不变，测试断言不受影响）：

```js
/* __R2G_PATCH_START__ */
(function () {
  function q(name) {
    var m = new RegExp("[?&]" + name + "=([^&#]*)").exec(location.search);
    return m ? decodeURIComponent(m[1]) : null;
  }
  var seed = q("r2g_seed");
  if (seed === null) return;
  var ms = parseInt(q("r2g_ms") || "240000", 10);
  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    if (window.WOB_TASK_READY === true && document.readyState === "complete" && core.cover_div) {
      try {
        Math.seedrandom(seed);
        core.EPISODE_MAX_TIME = ms;
        core.startEpisodeReal();
        clearInterval(timer); /* 只在开局成功后停止轮询 */
      } catch (e) {
        if (tries > 200) { clearInterval(timer); window.__R2G_START_ERROR = String(e); }
      }
    } else if (tries > 200) {
      clearInterval(timer);
      window.__R2G_START_ERROR = "WOB_TASK_READY timeout";
    }
  }, 50);
})();
/* __R2G_PATCH_END__ */
```

要点：增加 `document.readyState === "complete"`（等 onload 完成）与 `core.cover_div`（等 `core.startEpisode()` 建好显示层）两个条件；`clearInterval` 移入 try 成功路径，失败可重试。重试中的重复 `Math.seedrandom(seed)` 不破坏确定性（同 seed 恒等重放）。

### 1.5 超时口径写错：`endEpisode(0, ...)`/`raw=0`，实际是 `endEpisode(-1, false, 'timed out')`/`raw=-1`

- **问题**：spec §3.2 末条（spec.md:179）与 plan.md §2（plan.md:52）称页面超时回 `endEpisode(0,...)`、POST `done=true, raw=0`。
- **依据（实测 + 源码）**：core.js:101 `core.endEpisode(-1, false, 'timed out')`；实测收到的 POST 为 `{"reward": -1, "raw": -1, "done": true, "reason": "timed out"}`。
- **改法**：两处文本改为 `endEpisode(-1, false, 'timed out')`、POST `done=true, raw=-1`。成败判定（`raw > 0`）不受影响，但按现文本写实现测试/写报告会失真。

### 1.6 浏览器组断言写错：开局成功后 `EPISODE_ID` 仍为 0

- **问题**：spec §9 用例 16(a)（spec.md:338）以 "`EPISODE_ID > 0`" 作为自动开局证据。
- **依据（实测 + 源码）**：`WOB_EPISODE_ID++` 只在 `core.endEpisode` 内（core.js:130）；实测开局成功后（5/5 页 + 4 个其他任务）`WOB_EPISODE_ID === 0`，完成一次 endEpisode 后才变 1。按现断言该单测必失败，阻塞"全绿"验收。
- **改法**：16(a) 的开局证据改为 `core.ept0 != null`（且 utterance 非空、`core.EPISODE_MAX_TIME === 240000`、无 `__R2G_START_ERROR`）。

---

## 2. 补丁可行性实测记录（核心机制验证）

环境：uv 临时安装 `miniwob==1.1.0`；html 树整树复制到临时目录，**手工把 spec 附录补丁逐字追加到 core.js 副本**；`ThreadingHTTPServer` 伺服（`Cache-Control: no-store`、POST 收集）；playwright 1.49.0 chromium headless 打开 `http://127.0.0.1:<port>/miniwob/<task>.html?r2g_seed=<n>&r2g_ms=<ms>`。测毕临时目录/服务进程全部清理。

| 验证点 | 结果 |
|---|---|
| 定位 html 树与 core.js | `core.EPISODE_MAX_TIME = 10000` 确在 **core.js 第 50 行**；`miniwob/*.html` 共 **130** 页；seedrandom 内联于 core.js 顶部（补丁里 `Math.seedrandom` 可用） |
| (a) 自动开局（spec 原文补丁） | click-button `?r2g_seed=42`：5/5 次加载 `ept0_set=true、utterance='Click on the "next" button.'、EPISODE_MAX_TIME=240000`；但 `WOB_EPISODE_ID` 开局后仍为 **0**（见必改 1.6）；book-flight **开局失败**（见必改 1.4） |
| (a') 自动开局（修正补丁 A） | book-flight（utterance 为完整航班指令）、visual-addition、enter-text、email-inbox-forward-nl、click-test 全部开局成功、utterance 非空 |
| (b) 同 seed 确定性 | seed=42 五次加载 utterance 恒等；enter-text seed=42 两次加载恒等；seed=1337 得到不同 goal（seed 生效）；无 `r2g_seed` 的页面不自动开局（ept0 为 null，负例通过） |
| (c) 正确点击 → POST | 点击 utterance 指定按钮后收到 POST `{"path":"/miniwob/click-button.html","seed":"1337","reward":1,"raw":1,"done":true,"reason":"","ts":...}`；页面终态 `raw=1, done=true, EPISODE_ID=1` |
| 超时路径 | `r2g_ms=2000` 到点后 POST `{"reward":-1,"raw":-1,"done":true,"reason":"timed out"}`（同步 XHR 在超时回调内可达，spec 补丁 B 的 POST 契约成立） |
| POST 契约 | payload 键与 spec §3.2 完全一致；同步 XHR 同源无 CORS 问题；`endEpisode` 复发（episode 已结束后再点）被 core.js EP_TIMER 空值守卫拦截，POST 只是带原终态值的无害重复，不污染 last-wins |

**机制判定**：伺服-补丁-收集架构成立；补丁 B 原文可用；补丁 A 需按 1.4 修正。

## 3. 其余清单核对

- **复用接口（spec §6）**：六个符号全部存在且签名一致——`build_run_plan(feature_path, project_root, *, api_key=None)`、`run_feature(feature_path, *, run_id, project_root, extra_env=None, timeout_s=900, api_key=None, dry_run=False)`（可传 `timeout_s=600`）、`read_api_key(path=None)`、`mask_secret(text, secret)`、`find_junit_xml(feature_path, project_root)`、`parse_junit_xml(junit_path)`（返回含 `terminate`/`final_response`/`cost_usd`/`total_tokens`）；child env 确含 deepseek-v4-pro、`ENABLE_TELEMETRY=0`、`HEADLESS=true`、`ENABLE_UBLOCK_EXTENSION=false`（runner.py:140-161）。注意 `RunResult` 不含 `terminate`，行内 `junit_terminate` 需对 `junit_xml` 路径补一次 `parse_junit_xml`（函数可直接 import，非障碍）。
- **评测有效性**：奖励权威 `raw > 0` 贯穿 §0/§7.2/§8，闭环（POST→/latest→official_passed）实测成立；disagreement 双向记录 + §8.4 清单 + 用例 19 两侧覆盖；seed 派生 sha1 恒等 + 用例 4 锁互异；240s 放宽/单 seed 单次/不含录制蒸馏/基线引用声明四条口径在 plan §1 与 spec §0 双双显著披露。
- **预算数学**：pilot 12×$0.13=$1.56 ✓；full 130×$0.13=$16.90 ✓；合计 142 次红线 ✓；142×4.7万≈6.7M 落在"5-10M token"区间 ✓；$20 > $18.46（142 次上限成本）✓，自洽。
- **可测试性**：23 条用例中 A/B/C/E/F 与 D 组 14/15 纯离线（无 LLM、无 Hercules），用例 16 浏览器组仅本机回环 + 本地 vendored 资产、无浏览器整体 skip，符合"真离线"要求；用例 9 引用的 demo_server 端口占用硬失败行为存在（evaluation/demo_server.py:149 OSError 路径）；exp001 test-report 已知问题 11（macOS 代理劫持 127.0.0.1）确有其条，spec §3.3 禁代理解法与之对齐。
- **registry ↔ html（抽全不抽样）**：125 个任务 id 全部唯一、`miniwob.<subdomain>` ↔ `<subdomain>.html` 全部存在（比任务书要求的抽 5 个更严）；130 个 html 中 5 个未被 registry 引用，不影响本方案（表从 registry 构建，校验只查存在性）。

## 4. 建议不阻塞项（≤3）

1. **junit_passed 可空性**：§7.1 示例为 bool、§7.2.4 用 `is not None`，但 §7.2.1/7.2.2 未明说 timeout/no_junit 行的 `junit_passed` 应为 `null`（RunResult.passed 恒为 bool）。建议补一句："status 非 passed/failed 时 junit_passed=null"。
2. **plan 文本小失真**：§1 称"口径四条"实列 5 条；§6 风险"视觉类任务（visual-*）大概率全挂"在当前 registry 下 visual 组仅 **1 个任务**（visual-addition），报告呈现时按 n=1 组说明即可。
3. **墙钟估计的语义**：full "2.5-3.5h" 是按顺利运行的外推，广泛 timeout 时最坏 125×600s≈21h；护栏只在执行次数与 $ 上，建议 test-report 如实记录实测墙钟即可，不必改护栏。

## 5. 审查覆盖声明

- 实测临时产物与进程已全部清理（临时 html 副本、两个临时 http.server、/tmp 脚本目录均已删除，无残留监听端口）。
- 本次审查只写了本文件，未改动任何文档/代码，无 git 操作。
