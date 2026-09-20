# 评测框架与 UI 变异实验 spec — 实现规格

> 读者为实现者。读完本文不再需要做任何设计决策；未规定处按"最简单确定性行为"处理并记入实现说明。
> 契约来源：`PLAN.md` §3.3/§3.4；方案选型见同目录 `plan.md` §2。
> 密钥红线：`LLM-Key.txt` 内容（单行，deepseek API key）**不得出现在任何文档、代码、日志、argv、测试固件中**，只能经 subprocess `env=` 注入。

## 0. 目录与文件

```
record2gherkin/evaluation/
  __init__.py
  demo_app.py      # 页面渲染纯函数 + 元素注册表（§1、§2）
  demo_server.py   # 本地 HTTP 服务（§3）
  runner.py        # 子进程执行器 + JUnit 解析（§4）
  baseline.py      # 属性 selector 基线（§5）
  metrics.py       # 指标计算（§6）
  sweep.py         # 实验编排 CLI：python -m record2gherkin.evaluation.sweep --exp-id exp001 --stage pilot|full|baseline
tests/record2gherkin/evaluation/
  conftest.py
  test_demo_app.py         # 渲染与变异（§9 A 组）
  test_demo_server.py      # 服务（§9 B 组）
  test_runner_parse.py     # JUnit 解析 + key 脱敏（§9 C/E 组）
  test_metrics.py          # 指标（§9 D 组）
  test_matrix.py           # 矩阵与预算护栏（§9 F 组）
dev_runs/experiments/<exp_id>/    # 运行产物（gitignore，§8）
```

不新增第三方依赖：服务用 `http.server`，XML 用 `xml.etree.ElementTree`，哈希用 `hashlib`，基线驱动用已有 `playwright`。

## 1. Demo 应用：MiniShop SPA

### 1.1 形态与视图

- 单 HTML 文档 SPA，hash 路由两视图：`#/`（商品列表，默认）、`#/order`（下单表单）。服务端对任意路径返回同一文档；页面加载与 `hashchange` 时按 `location.hash` 渲染视图（保证直接打开 `#/order` 也能落到下单视图——蒸馏产物会出现 `When I navigate to "...#/order"`，必须可直达）。
- 服务地址固定 `http://127.0.0.1:8461/`（录制与执行同源同 URL，是变异可控的前提，见 §3.3）。
- 全部文案为固定中文字符串；无密码字段、无登录、无跨文档导航、无外网资源、无随机内容（随机性只来自 §2 的变异引擎）。

### 1.2 元素注册表（demo_app.py 内的数据结构，单一事实来源）

每个可交互/可断言元素一条注册项：`key`（稳定标识，如 `search_button`）、`tag`、稳定 `id`/`data-testid`/`class`、可访问名文本（aria-label 或 label 文本或 innerText）、所属视图与分组。渲染时按注册表生成 HTML。关键注册项：

| 视图 | key | 元素 | 稳定文本 |
|---|---|---|---|
| list | `nav_order` | a href="#/order" | 去下单 |
| list | `search_input` | input | aria-label=搜索商品，placeholder=搜索商品 |
| list | `search_button` | button | 搜索 |
| list | `category_select` | select（全部/数码/家居） | aria-label=商品类别 |
| list | `maxprice_input` | input | aria-label=最大价格 |
| list | `filter_button` | button | 应用筛选 |
| list | `product_N`（N=1..3） | 卡片：名称/价格/按钮 | 无线耳机 Pro ¥299 / 桌面台灯 ¥129 / 陶瓷马克杯 ¥39；按钮均为 加入清单（同名×3，ord-1..3） |
| list | `list_status` | p | 共 3 件商品（动态更新，见 §1.4） |
| order | `nav_home` | a href="#/" | 返回列表 |
| order | `name_input` / `email_input` / `address_input` | input | label：收货人姓名 / 联系邮箱 / 收货地址 |
| order | `shipping_select` | select（标准配送/次日达/门店自提） | label：配送方式 |
| order | `promo_checkbox` | checkbox | label：接受促销邮件 |
| order | `qty_plus` / `qty_minus` | button | + / −（不参与 M4 改写，符号非文案） |
| order | `qty_display` | p | 数量：1 |
| order | `submit_button` | button | 提交订单 |
| order | `order_result` | div（初始隐藏） | 下单成功，感谢您的购买！ + 订单号：D20260920-001 |

### 1.3 六条录制流程（每条一次录制会话产出一份 events JSON → 一份 feature）

| 流程 | 步骤（录制动作序列） | Then 断言（来自 assert_texts） |
|---|---|---|
| F1 搜索 | 打开 `/` → 输入"台灯"到搜索框 → 点"搜索" | "找到 1 件商品" |
| F2 加入清单 | 打开 `/` → 点商品1"加入清单" → 点商品3"加入清单" | "清单（2）" |
| F3 下单表单 | 打开 `/` → 点"去下单"（hash 导航）→ 填姓名/邮箱/地址 → 选"次日达" → 勾选"接受促销邮件" → 点"提交订单" | "下单成功" |
| F4 筛选 | 打开 `/` → 选类别"数码" → 输入最大价 300 → 点"应用筛选" | "筛选后共 1 件商品" |
| F5 视图往返 | 打开 `/` → 点"去下单" → 点"返回列表" | "共 3 件商品" |
| F6 数量下单 | 打开 `/` → 点"去下单" → 点"+"×2 → 点"−"×1 → 点"提交订单" | "数量：2" 与 "下单成功" |

每条 4-8 个事件。F3 是 pilot 用流程，且是 checkbox 双事件（click + input("true")，录制器忠实记录）的唯一观察点——执行侧表现记入实验报告，不做规避处理。

### 1.4 客户端行为（确定性，全部同步 DOM 更新）

搜索：按名称子串过滤卡片并显示 `找到 N 件商品`；空查询恢复全部并显示 `共 3 件商品`。筛选：按类别+价格过滤并显示 `筛选后共 N 件商品`。加入清单：头部 `清单（N）` 计数 +1。数量：+ / − 各调整 1，下限 1，更新 `数量：N`。提交：隐藏 `order_result` 显示（不做必填校验，任何提交都成功——避免半失败状态引入非确定性）。切视图：pushState + 按 hash 渲染。

## 2. 变异引擎（demo_app.py 精确定义）

### 2.1 总则

- 唯一入口 `render_page(mutation: str, seed: int) -> str`（`mutation ∈ {"M0","M1","M2","M3","M4"}`）；另保留 `view` 参数仅用于单测直取单视图，生产路径一次渲染整文档。
- **确定性**：同 `(mutation, seed)` 任意次渲染逐字节相同；渲染不读时钟、不读环境、不用 `random` 模块（需要随机处一律 `hashlib.sha256`）。
- 变换发生在**结构层**（对 §1.2 注册表/分组列表操作），序列化成 HTML 前完成；不做 HTML 字符串改写。
- 不变量（所有 M 共同保证）：元素的可访问名文本、`order_result` 等断言文案、option 文本、页面标题不变——除 M4 按其规则改写按钮 accessible name 外。

### 2.2 各变异的精确变换

| M | 变换（结构层） | 可重复性 |
|---|---|---|
| M0 | 恒等：按注册表原样渲染 | 无 seed 依赖 |
| M1 | 对 order 表单的字段分组列表 `G = [g_name, g_email, g_address, g_shipping, g_promo, g_qty, g_submit]`：反转子序列 `G[:-1]`，`g_submit` 固定排最后 → `[g_qty, g_promo, g_shipping, g_address, g_email, g_name, g_submit]`；list 视图的搜索组与筛选组两分组互换。组内元素与全部文本不变 | 确定性，与 seed 无关 |
| M2 | (a) 每个字段分组外包一层 `<div class="m2-card" style="border:1px solid #ddd;padding:12px;margin:8px 0">`；(b) 表单容器由块级改为 `display:flex;flex-wrap:wrap`（嵌套与布局变化）；(c) 表单前插入固定横幅 `<div class="m2-banner">全场满 99 元包邮，次日达限时 8 折</div>`（文案不与 §1.3 任何断言文本冲突）；不改任何既有元素的文本/属性 | 确定性，与 seed 无关 |
| M3 | 对注册表**全部**元素的三个属性分别替换：`id = "m3-" + h(seed,key)[:8]`、`class` 内每个稳定类名 → `"c-" + h(seed,key+":class")[:8]`、`data-testid = "m3-" + h(seed,key+":tid")[:8]`，其中 `h(s) = sha256(s).hexdigest()`。**不**改 aria-label/placeholder/label 关联文本/innerText | 同 seed 幂等；异 seed 必不同（单测断言）；每 run 由 sweep 分配新 seed → "动态 id"语义成立 |
| M4 | 对参与改写的按钮/CTA（`search_button`、`filter_button`、`product_N` 的加入清单按钮、`submit_button`、`nav_order`）按改写表概率选取：`idx = int(h(seed,key),16) % len(table[key])`，改写同时作用于 innerText 与 aria-label（两者共同决定 accessible name）。改写表：搜索→[查一下, 搜索一下]；应用筛选→[筛选结果, 开始筛选]；加入清单→[添加到清单, 放入清单]；提交订单→[确认下单, 立即提交]；去下单→[马上下单, 前往下单]。其余元素（含 qty_plus/minus、nav_home、全部断言文案）不动 | 同 seed 确定；异 seed 改写组合不同（"概率性"由 seed 枚举体现） |

### 2.3 可重复性约定

- M3/M4 的 seed 由 sweep 按 `seed = int(sha1(f"{exp_id}:{run_id}").hexdigest()[:8], 16)` 确定性分配并写入 manifest（§8）——同 exp_id 重跑同格得到同页面字节，满足"可重复"；不同 run 间 seed 互异，满足 M3"动态"语义。
- M1/M2 忽略 seed（渲染结果与 seed 无关），manifest 中仍记录传入值。

## 3. Demo 服务（demo_server.py）

1. `ThreadingHTTPServer`（标准库）绑定 `127.0.0.1:8461`。模块级可配端口，默认 8461；启动前探测占用，被占则报错退出（不自动换端口——URL 是实验参数，必须稳定）。
2. 端点：
   - `GET /`（及任意非保留路径）→ 当前 `(mutation, seed)` 下的整文档 HTML，响应头 `Cache-Control: no-store`。
   - `GET /healthz` → `200` + JSON `{"mutation": ..., "seed": ...}`。
   - `POST /__control` → body `{"mutation": "M3", "seed": 42}`，更新服务端状态并返回同 `/healthz` 的 JSON；非法 mutation 返回 400。
3. 状态：进程内单值 `(mutation="M0", seed=0)` 为默认；sweep 每格执行前先 POST `/__control` 再启动执行，启动后再 GET `/healthz` 校验生效。
4. 生命周期：由 sweep 以 `subprocess.Popen([sys.executable, "-m", "record2gherkin.evaluation.demo_server"])` 启停；单测可在线程内直接起 server 实例（走本机回环，不算外网依赖）。

## 4. 执行器（runner.py）

### 4.1 接口（字段名固定，容器类型实现者可用 dataclass 或 TypedDict）

```python
def run_feature(feature_path: Path, *, run_id: str, project_root: Path,
                extra_env: Mapping[str, str] | None = None,
                timeout_s: int = 900) -> RunResult
# RunResult 字段：
#   run_id: str                    # "<method>__<flow>__<mutation>__s<seed>"，如 generated__F3__M3__s17
#   status: str                    # "passed" | "failed" | "timeout" | "no_junit"
#   passed: bool                   # 仅 status=="passed" 时 True
#   duration_s: float | None
#   cost_usd: float | None         # 解析自 JUnit，缺失为 None（禁止记 0）
#   total_tokens: int | None
#   junit_xml: str | None          # 绝对路径
#   failure_message: str | None    # <failure> message 或超时说明；已 mask_secret
#   run_dir: str                   # 本次运行独立 project_root 的绝对路径

def parse_junit_xml(junit_path: Path) -> dict
# 返回 {"passed": bool, "terminate": str|None, "failure_message": str|None,
#       "final_response": str|None, "duration_s": float|None,
#       "cost_usd": float|None, "total_tokens": int|None, "testcase_count": int}

def build_child_env(api_key: str) -> dict[str, str]   # §4.3；返回值含 key，调用方不得打印
def mask_secret(text: str, secret: str) -> str        # 子串替换为 "***REDACTED***"
```

### 4.2 子进程命令与隔离

- 命令：`[sys.executable, "-u", "-m", "testzeus_hercules", "--input-file", <feature 绝对路径>, "--project-base", <project_root 绝对路径>, "--output-path", <project_root/output 绝对路径>]`，`cwd=仓库根`。**key 不进 argv**。
- 每 run 独立 `project_root = dev_runs/experiments/<exp_id>/runs/<run_id>/opt`（sweep 预建 `input/`）；独立根规避 config 输出目录时间戳别名与证据混叠（`config.py:14,60`）。
- JUnit 产物定位：`<output-path>/<feature 文件名>_result.xml`（上游命名规则，`__main__.py:118`）。进程正常退出但该文件缺失 → `status="no_junit"`（失败数据）；超时 → kill 进程组、`status="timeout"`；`parse_junit_xml` 抛解析异常视为 `no_junit` 并保留异常文本（脱敏后）进 failure_message。
- 通过判定（见 §6.1）：JUnit 中**不存在** `<failure>` 子元素 → `passed=True`。timeout/no_junit 一律 `passed=False` 且计入分母。

### 4.3 LLM 配置与 key 脱敏注入

`build_child_env(api_key)` 返回 `os.environ | {`：

```
LLM_MODEL_NAME="openai/deepseek-chat"
LLM_MODEL_BASE_URL="https://api.deepseek.com"      # litellm openai/ 前缀下拼接 /chat/completions；备选 "/v1"
LLM_MODEL_API_TYPE="openai"
LLM_MODEL_API_KEY=<api_key>                        # 仅经 env；argv/日志/文档禁现
ENABLE_TELEMETRY="0"                               # config import 即初始化 Sentry，必须压掉
HEADLESS="true"
```

- key 读取：`LLM_KEY_PATH = 仓库根/LLM-Key.txt`，全文 `strip()` 即 key（文件单行）。
- **D2 pilot 验证点**：上述组合是否被 litellm 接受（401→key/类型问题；404→base_url 路径问题）。备选方案（同样 env 注入）：`LLM_MODEL_NAME="deepseek/deepseek-chat"` + `DEEPSEEK_API_KEY`。**验证结论必须回写本节**后才开始 sweep。
- 脱敏：子进程 stdout/stderr 捕获后必须先 `mask_secret(., key)` 再落盘 `<run_dir>/stdout.log`；`failure_message` 同理。单测断言 key 任意前后缀不出现在 cmd 列表与日志文本中（§9 E 组）。

## 5. 基线（baseline.py）——口径声明

- **口径**：模拟经典"录制回放"型 UI 自动化产物，**仅用属性 selector**：稳定 `#id` → `[data-testid=...]` → `.class`，按此优先级手写 6 条流程函数（每条对应 §1.3 同一步骤序列与断言）；**不使用** `get_by_role`/`get_by_label`/文本定位。此口径即 PLAN "selector 式基线"的定义，最终报告须原样声明。
- 判定：每条流程函数内的断言（文本存在性/计数）失败即抛异常；驱动器捕获异常记 `passed=False`，无异常 `passed=True`。基线运行不经 Hercules、无 LLM、无 JUnit。
- 基线 selector 从 M0 渲染产物的注册表生成（可写一个 `expected_selectors()` 纯函数供单测核对），不手抄页面源码，防漂移。

## 6. 指标定义（metrics.py，精确公式）

设流程集 `F`（|F|=6），变异集 `M = {M0..M4}`，方法 `method ∈ {generated, baseline}`；`pass(i, M, method) ∈ {0,1}` 为该格运行结果（含 timeout/no_junit/异常 → 0）。

1. **首轮通过率**（仅 generated、仅 M0、且为该 feature 的第一次执行，pilot 数据不计入正式表）：
   `FirstPass = Σ_i pass(i, M0, generated) / |F|`
2. **变异存活率**（主表，逐 method 逐 M 一列）：
   `Surv(method, M) = Σ_i pass(i, M, method) / |F|`
   汇报口径：M1/M2/M3 三列均值为"主指标存活率"；M4 单列，不并入。
3. **成本**（仅 generated）：
   单次 `cost_usd`/`total_tokens` 取自 JUnit 解析（优先 testcase 级展平键 `usage_including_cached_inference.total_cost` / `*.total_tokens`，缺回落 suite 级 `total_execution_cost`/`total_token_used`，再缺为 None）；
   `AvgCost = Σ cost_usd(非 None) / count(非 None)`，`TotalCost = Σ cost_usd(非 None)`，并报告 `cost_missing_count`（None 不计 0，不虚构）。
4. 失败运行（timeout / no_junit / 断言失败）全部进入分母、按 0 计，并在报告中单列失败清单（run_id + failure_message 摘要）。

## 7. 实验矩阵与预算合计

| 方法 | 单元格 | 运行数 | 执行方式 | 成本上限 |
|---|---|---|---|---|
| generated（Hercules） | F1-F6 × {M0,M1,M2,M3,M4} | 30 | 子进程，串行 | 30 × $0.13 = $3.90 |
| generated pilot（D2，单独记录，不入正式表） | F3 × {M0, M3}（+≤2 次基础设施重试） | ≤4 | 子进程 | ≤$0.52 |
| 基础设施故障重跑缓冲（仅 timeout/no_junit/服务崩溃；**用例失败不重跑**） | — | ≤6 | 子进程 | ≤$0.78 |
| baseline（本地 playwright） | F1-F6 × {M0..M4} | 30 | sweep 内同步驱动 | 0 |
| **Hercules 合计** | | **≤40** | | **≤$5.20（红线 $10）** |

- `test_matrix.py` 用例断言矩阵单元格数 = 60、Hercules 计数（30+pilot 上限+缓冲上限）≤ 40——护栏落在单测里。
- 基线与 generated 同格共享同一 `(mutation, seed)` 页面状态（服务端状态在两种方法间保持），保证同格可比。

## 8. sweep 编排（sweep.py）与产物

流程：读 `--exp-id` → 起 demo 服务（探活）→ 建 `dev_runs/experiments/<exp_id>/` → `--stage pilot`：2-4 格；`--stage full`：30 格 generated；`--stage baseline`：30 格基线 → 每格：POST `/__control`（seed 按 §2.3 公式）→ healthz 校验 → 执行 → 追加一行 `results.jsonl` → 结束停服务、写 `manifest.json`。

- `results.jsonl` 行：`{run_id, method, flow, mutation, seed, status, passed, duration_s, cost_usd, total_tokens, junit_xml, failure_message, started_at, finished_at, model}`（baseline 行 cost/tokens/model 为 null，junit_xml 为 null，`failure_message` 为异常摘要）。
- `manifest.json`：`{exp_id, git_rev, started_at, model_name, llm_base_url, demo_port, budget: {hercules_used, cap: 40}, cells: [{run_id, mutation, seed}]}`。**不含 key，也不含 key 前缀**。
- 每格 run 目录 `<exp_id>/runs/<run_id>/`：独立 opt（proofs/log_files/output 全在里面）+ `stdout.log`（已脱敏）。`**/*.xml` 全局 gitignore，JUnit 留在 dev_runs 内即可。

## 9. 离线单测用例清单（不跑真 LLM、不依赖外网；B 组走本机回环）

**A 组 渲染与变异（test_demo_app.py，纯字符串断言）**
1. M0 确定性：同参数两次渲染逐字节相同
2. M0 完整性：注册表全部稳定 id/testid/关键文本在产物中
3. M1：order 字段组顺序为反转序且 submit 仍最后；组内文本不变
4. M2：m2-card 包裹数 = 字段分组数；横幅文本固定存在；字段 label 文本不变
5. M3 幂等：同 seed 两次渲染逐字节相同
6. M3 异 seed：注册表每元素 id 在 seed A/B 下必不同
7. M3 格式：id/data-testid 匹配 `m3-[0-9a-f]{8}`、类名匹配 `c-[0-9a-f]{8}`；aria-label/placeholder/label 文本不变
8. M3 vs M0：除 id/class/data-testid 外可见文本相同
9. M4：各参与元素文本 ∈ 改写表；同 seed 确定；`order_result`/状态文案不变
10. M4 概率性：固定枚举 8 个 seed，至少出现 2 种不同改写组合
11. 渲染纯净性：产物不含时钟戳/随机数痕迹（两次不同时刻渲染逐字节相同）

**B 组 服务（test_demo_server.py，线程内起服务 + urllib）**
12. `/healthz` 返回当前 (mutation, seed)，默认 M0/0
13. `POST /__control` 合法体后 `/` 内容随 M0→M3 变化（同 URL 不同 HTML）
14. `POST /__control` 非法 mutation → 400 且状态不变

**C 组 JUnit 解析（test_runner_parse.py，手写 fixture XML 字符串）**
15. 通过用例（无 failure，含 cost/token 属性）→ passed=True 且数值正确
16. 失败用例（含 `<failure>` 与 message）→ passed=False 且 message 提取
17. 无 cost 属性 → cost_usd=None（非 0）；suite 级 `total_execution_cost` 兜底路径生效
18. 畸形 XML → 返回明确定义的 error 形态（不静默当通过）
19. 多 testcase（防御，正常不出现）→ passed=all 且 testcase_count>1

**D 组 指标（test_metrics.py）**
20. FirstPass 计算（含某流程失败）
21. Surv 按 (method, mutation) 分组正确；空组/|F|=0 防御
22. 成本聚合：None 排除出均值、missing 计数正确
23. timeout/no_junit 计入分母且按 0 计

**E 组 执行器 dry（test_runner_parse.py，monkeypatch subprocess，不真跑）**
24. cmd 含 `--input-file/--project-base/--output-path` 三参数且为绝对路径；key 不在 cmd 任何元素中
25. child env 含 §4.3 全部键；`ENABLE_TELEMETRY=0`；key 仅存在于 env 值
26. `mask_secret`：含 key 的文本替换后无残留；空 key 时原样返回
27. 超时路径：以 0.5s timeout 跑 `sleep 5` 子进程 → status="timeout"

**F 组 矩阵护栏（test_matrix.py）**
28. 矩阵 6×5×2=60 格；Hercules 计数（30+4+6）≤ 40 断言成立

## 10. 验收标准

1. `uv run pytest tests/record2gherkin/evaluation -q` 全绿，离线可跑（无 key、无外网；B 组仅本机回环）。
2. demo 服务一条命令启动，`/healthz` 可用；同 URL 同 (m, seed) 字节级一致。
3. D1 录制：6 条流程各自蒸馏出恰 1 Feature/1 Scenario（`split_feature_file` 解析通过），全部 feature 不含 `{{TEST_DATA:` 与 `<masked>`。
4. D2 pilot：≥1 格 M0 通过且 cost 被解析为非 None；模型串/base_url 结论回写 §4.3。
5. D3 sweep：Hercules 累计执行 ≤40，`results.jsonl` 矩阵每格恰一行（失败也是行），manifest 完整。
6. 脱敏：对所有实验文本产物（日志/jsonl/manifest）以 key 实值 grep 零命中（执行时用变量引用，命令模板 `grep -r "$KEY" dev_runs/experiments/<exp_id>/`，key 不落文档）。
7. `dev_docs/evaluation/test-report.md` 固化：首轮通过率、Surv(method, M) 全表（M4 单列）、成本三数（AvgCost/TotalCost/cost_missing_count）、失败清单、checkbox 双事件观察点记录。

## 11. Out of Scope

报告网站/图表渲染；统计显著性检验与置信区间；语义 selector（get_by_role/get_by_label）基线；变异组合（如 M1+M3 复合变异）；多浏览器/多分辨率矩阵；并发执行与分布式 sweep；登录/密码/test_data 注入流；外网真实站点；录制事件合并、编辑、回放 UI；Hercules 内核任何改动（含 `click_using_selector.py:266-284` 误记成功 bug 的修复）；planner/nav 模型分档路由（本期单模型 deepseek）。

## 12. 遗留决策点（pilot 暴露后再补，不预先设计）

- litellm 接受的 deepseek 模型串与 base_url 精确组合（§4.3 回写）。
- hash URL（`...#/order`）经 open_url 的实际行为；不可用则 F3/F5/F6 改纯点击切视图并回写 §1.3。
- checkbox 双事件（click + input("true")）在执行侧的实际表现与是否造成 F3 假失败；处置写入实验报告，不改 spec 规则。
- 单 run 实测时长分布 → sweep 墙钟与 timeout_s（默认 900）是否调整。
- deepseek 下 JUnit cost 属性是否正常回传；缺失则 cost 维度降级为 tokens-only 并如实说明。
