# 失败归因器 spec — 实现规格

> 读者为实现者。读完本文不再需要做任何设计决策；未规定处按"最简单确定性行为"处理并记入 warnings。
> 源码锚点（错误串/结构的事实来源，实现时不得凭记忆改写匹配串）：`utils/junit_helper.py:88-146`、`core/runner.py:91-135`、`core/simple_hercules.py:110-124, 347-393, 404-424, 993-1007`、`core/playwright_manager.py:1083-1114`、`core/tools/click_using_selector.py:157-159, 266-310`、`core/agents/high_level_planner_agent.py:183`。

## 0. 目录与文件

```
record2gherkin/
  attributor/
    __init__.py      # re-export attribute_run / attribute_case / AttributionResult / AttributionError / Category
    evidence.py      # 证据加载器：产物目录 → EvidenceBundle / TestCaseEvidence
    rules.py         # 签名表 + 规则层（禁止 import 任何 LLM/网络模块）
    llm_layer.py     # LlmAnalyzer 协议、DefaultAttributorAnalyzer、prompt、输出校验、证据回查、降级
    report.py        # AttributionResult、to_json()、to_markdown()
    api.py           # 公开 API
tests/record2gherkin/attributor/
  conftest.py        # 合成产物目录 fixture 工厂 + FakeAnalyzer
  fixtures/          # 手工合成 JUnit XML / agent_inner_thoughts.json / 0 字节 png
  test_evidence.py / test_rules.py / test_llm_layer.py / test_report.py / test_api_e2e.py
```

代码风格：black line-length 200 + isort；日志 `from testzeus_hercules.utils.logger import logger`（禁 print）。规则层零模型：`evidence.py`/`rules.py`/`report.py` 不 import litellm/requests/网络模块（grep 机械检查 + 用例 29 类不变量）。

## 1. 类型与数据结构

### 1.1 分类（全模块唯一定义处）

```python
Category = Literal["product_bug", "test_rot", "environment", "agent_limit", "inconclusive"]
# 规则层内部路由值（不出现在最终结果）：
Route = Literal[Category, "needs_llm"]
```

### 1.2 证据结构（evidence.py）

```python
@dataclass
class ScreenshotRef:
    path: str            # 截图文件绝对/相对路径
    filename: str        # 文件名（含扩展名），回查用它
    tool: str            # 文件名去 _start/_end/时间戳后的前缀；不合形态时为去扩展名全名
    phase: str           # "start" | "end" | "other"
    ts_ns: int | None    # 文件名尾部 _<纳秒>；缺失/不可解析 → None
    is_final_state: bool # 末状态对成员（见 §2.4）

@dataclass
class ThoughtsRound:
    index: int           # 展平后 0-based 序号（回查引用 thoughts:<index> 的唯一依据）
    role: str            # dict.get("role", "unknown") 字符串化
    content_text: str    # dict/list → json.dumps(ensure_ascii=False)；str → 原文；None/缺失 → ""
    content_json: object | None  # content 本身是 dict/list 时保存解析对象，否则 None

@dataclass
class TestCaseEvidence:
    scenario: str
    feature_name: str                 # junit classname
    failure_message: str | None       # Failure 节点 message；无 Failure → None
    final_response: str | None        # system-out "Final Response: " 行剥前缀
    system_out: str | None
    properties: dict[str, str]        # 全部 <property name=... value=...> 原样（键名含上游 typo 也不修正）
    terminate: str                    # properties.get("Terminate", "unknown")
    feature_text: str | None          # "Feature File" 指向文件的内容；缺失 → None
    feature_path: str | None
    thoughts_path: str | None
    thoughts: list[ThoughtsRound]
    screenshots: list[ScreenshotRef]  # 已按 §2.3 排序
    network_log_path: str | None      # 仅路径透传
    missing: list[str]                # 被引用但磁盘不存在的证据，元素形如 "thoughts"/"screenshots_dir"/"feature_file"

@dataclass
class EvidenceBundle:
    junit_path: str
    cases: list[TestCaseEvidence]     # junit 内每个 testcase 一条，按文件顺序
    suite_properties: dict[str, str]  # total_execution_cost / total_token_used 等
```

### 1.3 结果结构（report.py，字段定义见 §6）

```python
@dataclass
class AttributionResult:   # 字段与 §6.1 JSON 一一对应；to_json() / to_markdown() 各一
    ...
```

## 2. 证据加载器（evidence.py）

### 2.1 JUnit 解析

- 用标准库 `xml.etree.ElementTree`（venv 无 junit-xml 解析库可用性保证，不新增依赖）。解析 `<testsuite><testcase>`；每个 testcase 取 `name`（scenario）、`classname`（feature）、`<failure message=...>`、`<system-out>` 文本、全部 `<property>`。
- `final_response`：system_out 逐行找前缀 `Final Response: ` 的行，剥前缀；多行取第一处。
- 属性键常量（`junit_helper.py:111-124` 原样，含 typo）：`Terminate`、`Feature File`、`Output File`、`Proofs Video`、`Proofs Screenshot`、`Network Logs`、`Agents Internal Logs`、`Planner Thoughts`，及**前缀**匹配 `Proofs Base Folder`（完整键为 `Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs`，匹配只按前缀，容忍上游改动）。
- 通过率判定：存在 `<failure>` 即失败用例；无 failure 的 testcase 照常加载但标记 `not_a_failure`，`attribute_case` 对其直接返回 category=inconclusive + warning `not_a_failure`，不进规则层。
- JUnit 缺失/解析失败 → 抛 `AttributionError`（唯一的加载期抛错；其余一律 warning 容错）。

### 2.2 路径解析（properties → 磁盘）

对每个路径型属性值依次尝试：(1) 原样路径存在 → 用之；(2) 否则 `run_dir` 下 glob `**/<filename>` 取排序首个匹配。两步都不中 → 记入 `missing`，对应字段置 None。`run_dir` 即 `attribute_run` 的产物目录参数（通常为 Hercules 项目根，如 `opt/`）。

### 2.3 inner_thoughts 解析（`runner.py:91-135` 事实）

- JSON 顶层为 `dict[agent_name, list[msg]]`。加载顺序：先 `planner_agent` 键，其余键按插入序追加；`role` 取 `str(msg.get("role", "unknown"))`，来源键名不进回查语义，仅记日志。
- msg 必含 `content`；runner 已对内容做过 `json.loads`，因此 content ∈ {dict, list, str, None, 数值}。归一：dict/list → `json.dumps(obj, ensure_ascii=False)`（保持插入序）；str → 原文；其余 → `str(content)`。解析结果存 `content_json`（仅 dict/list）。
- `index` 为展平后 0-based 全局序号，跨键连续。空文件/顶层非 dict → thoughts=[] + missing 追加 `"thoughts"`。

### 2.4 截图发现与对齐（v1 近似规则）

- 扫描根：属性 `Proofs Screenshot` 指向目录；缺失时用 `Proofs Base Folder` 前缀属性指向目录下的 `**/*.png`。文件名正则：`^(?P<tool>.+?)_(?P<phase>start|end)_(?P<ns>\d{10,})\.png$`；不匹配 → `phase="other"`、`ts_ns=None`、`tool=去扩展名文件名`。
- 排序键 `(0 if ts_ns is not None else 1, ts_ns or 0, filename)`。
- **末状态对**：最后一个 `phase=="end"` 且 ts_ns 最大的截图 + 在它之前最近的 `phase=="start"` 截图，两者 `is_final_state=True`；无 `_end` 时退化为列表最后一项。
- **轮次锚定（best-effort，只认字面）**：对每个 ThoughtsRound 的 content_text 正则找 `[\w/.\-]+\.png` 提及；提及名与 screenshots[].filename 精确相等（basename 比较）→ 建锚。锚只用于 LLM 证据包展示与报告附录，不改变任何分类逻辑；无锚不造锚。
- 报告/LLM 包明示："截图与 planner 轮次为近似对齐（时间序 + 字面锚），非逐轮映射"。

### 2.5 feature 文本

属性 `Feature File` 解析成功 → 读全文（utf-8，容错 errors="replace"）；用于 test_rot 对照（S8 的"选择器名是否出现在用例中"）与 LLM 包 grounding。

## 3. 规则层（rules.py）

### 3.1 扫描语料与顺序

- 语料字段：`FAIL` = failure_message、`SYSOUT` = system_out（含 final_response 行）、`THOUGHTS` = 全部 rounds 的 content_text。
- 每个签名按固定字段顺序扫描：`FAIL → SYSOUT → THOUGHTS`；命中即停（THOUGHTS 命中时记录**首个与末个**匹配轮 index）。
- 签名按 §3.2 表序执行，**首个命中者生效**（表序即优先级，是载荷性设计：S1/S2 的 assert_summary 也含 EXPECTED/ACTUAL，必须先于 S10）。
- **数据流注记（实现者必读）**：引擎错误串（nav 轮次耗尽串、`[ERROR] <agent> LLM error:` 等）在 `_executor_node` 以 HumanMessage 形态（`[<agent>]: ...`）回灌 planner 会话，经 runner 存入 `agent_inner_thoughts.json`（`simple_hercules.py:805-808` → `runner.py:125-135`）——其**规范落点是 THOUGHTS**；出现在 FAIL/SYSOUT 仅当 planner 恰好把它复述进 assert_summary/final_response（LLM 行为，不可依赖）。

### 3.2 签名表（完整定义；ci = case-insensitive 子串/正则）

| # | id | 匹配定义 | 语料字段 | 归类 | 置信度 | 证据引用（必给） | 替代假设（alt，conf<0.8 时必填） |
|---|---|---|---|---|---|---|---|
| S1 | planner_max_rounds | ci 子串 `max planner rounds exceeded` | FAIL∪SYSOUT∪THOUGHTS | agent_limit | 0.95 | 命中字段原文；THOUGHTS 命中时引命中轮 | — |
| S2 | planner_timeout | ci 子串 `llm call timed out after` **或** `planner receives a timely model response`（源码：`simple_hercules.py:320,357-359`） | FAIL∪SYSOUT | agent_limit | 0.90 | FAIL 原文摘录 | — |
| S3 | nav_max_rounds（复合） | ci 子串 `reached before ##TERMINATE TASK##` **且** `max nav rounds (`（源码 `simple_hercules.py:1004-1007`）。主复合匹配按 §3.1 默认顺序扫 FAIL→SYSOUT→THOUGHTS，**两子串须在同一字段内同时命中**方算命中（真实串中两子串相邻，天然同字段；规范落点为 THOUGHTS 回灌行，见 §3.1 数据流注记）。命中后做**次级扫描**：仅对 THOUGHTS 依次用 S8 模式、S5∪S6∪S7 模式复扫——(a) 有 not found 命中 → test_rot 0.75，引用该命中轮；(b) 有网络/断连命中 → environment 0.75，引用该命中轮；(c) 均无 → agent_limit 0.90 | FAIL∪SYSOUT∪THOUGHTS（§3.1 默认序）＋ THOUGHTS（次级复扫） | (a) test_rot (b) environment (c) agent_limit | 0.75/0.75/0.90 | 主命中位置（THOUGHTS 命中则引命中轮）+ 次级命中轮（分支 c 仅主命中位置） | (a) "轮次耗尽可能因引擎定位能力不足，而非用例过时"；(b) — |
| S4 | llm_context_limit | ci 正则 `context_length_exceeded\|maximum context length\|context window.{0,40}exceed\|prompt is too long` | FAIL∪SYSOUT∪THOUGHTS | agent_limit | 0.85 | 首个命中位置 | — |
| S5 | network_dns | ci 正则 `net::ERR_[A-Z_]+\|getaddrinfo\|Temporary failure in name resolution\|Name or service not known\|NS_ERROR_UNKNOWN_HOST` | FAIL∪SYSOUT∪THOUGHTS | environment | 0.90 | 首末命中轮（FAIL/SYSOUT 命中则引其原文） | — |
| S6 | browser_disconnected | ci 正则 `Target (page, context or browser )?closed\|browser has been closed\|Playwright connection (closed\|error)\|Target crashed\|Session with given id not found` | FAIL∪SYSOUT∪THOUGHTS | environment | 0.80 | 同上 | — |
| S7 | tool_error_network | 同一命中片段内：ci 子串 `[tool error]` 或 `[mcp tool error]` **且** ci 正则 `timed out\|timeout\|connection\|econn\|unreachable\|refused\|reset by peer` | FAIL∪SYSOUT∪THOUGHTS | environment | 0.75 | 命中片段所在位置 | "也可能是被测站点自身故障被记为工具错误" |
| S8 | element_not_found | ci 正则 `(element with selector[^\n]{0,80}not found)\|(since the selector is invalid)\|((element\|node\|option\|button\|field\|link\|form)[^\n]{0,60}\bnot found\b)`（源码 `click_using_selector.py:157-159,306`） | THOUGHTS∪FAIL∪SYSOUT | test_rot | 0.70 | 命中轮/字段 + **best-effort**：从命中片段提取引号内选择器名，若该名出现在 feature_text → 追加 `feature:<行号>` 引用 | "元素缺失也可能是产品缺陷或 agent 误定位（静默点错）" |
| S9 | http_404 | ci 正则 `\b404\b[^0-9\n]{0,40}not found\|\bstatus["']?[:= ]+ ?404\b` | THOUGHTS∪FAIL∪SYSOUT | test_rot | 0.65 | 命中位置 | "404 也可能因环境路由/代理而非用例过时" |
| S10 | assertion_mismatch（路由） | FAIL 含 ci `\bexpected\b` **且** `\bactual\b`（覆盖 `EXPECTED RESULT/ACTUAL RESULT` 与 `EXPECTED:/ACTUAL:` 两变体） | FAIL | **needs_llm**（seed 注记：product_bug vs test_rot vs agent 误操作三假设） | — | FAIL 原文 | — |
| S11 | helper_uncertain（路由） | ci 子串 `##TERMINATE TASK##` **且** ci 正则 `\b(uncertain\|incomplete\|contradict\w*)\b`（引擎失败标记词表 `simple_hercules.py:110-124`） | SYSOUT∪FAIL∪THOUGHTS | **needs_llm** | — | 命中位置 | — |
| S12 | llm_call_error | ci 正则 `\[error\] [a-z_ ]+ llm error:`（源码 `simple_hercules.py:917`） | FAIL∪SYSOUT∪THOUGHTS（规范落点为 THOUGHTS 回灌行，见 §3.1 数据流注记） | agent_limit | 0.80 | 命中位置原文（THOUGHTS 命中则引命中轮） | — |
| S13 | generic_tool_error（路由） | ci 子串 `[tool error]` 或 `[mcp tool error]`（未匹配 S7） | FAIL∪SYSOUT∪THOUGHTS | **needs_llm** | — | 命中位置 | — |
| — | fallback | 以上全不命中 | — | **needs_llm**（模糊区长尾） | — | — | — |

规则层产出（RuleOutcome）：`route`（Category 或 needs_llm）、`rule_signature`（命中 id，fallback 为 None）、`confidence`（表值）、`evidence[]`（按表列引用生成，excerpt = 命中片段所在行整行截断至 500 字符）、`notes.alternative_hypotheses`（conf<0.8 必填，取表 alt 列）。

### 3.3 suggestions（规则层固定模板，确定性）

```text
agent_limit: "引擎轮次/超时上限内未收敛：检查该步骤页面复杂度（弹窗/iframe），考虑提高轮次上限或换强模型档，并重跑确认是否偶发。"
test_rot:    "页面与录制时可能已不一致：核对失败元素/URL 与录制事件流的原始依据，必要时重录该流程。"
environment: "疑似环境问题：确认目标站点可达、代理/DNS 正常、浏览器可用后重跑。"
product_bug: "疑似产品缺陷：按证据摘录人工复核页面实际行为与预期差异。"
inconclusive:"证据不足以定位：结合 proof 截图人工复查，或补充运行日志后重跑归因。"
```

### 3.4 needs_llm 语义

- `needs_llm` 不是合法的最终 category。analyzer 可用 → 交 LLM 层；不可用（None）或 LLM 层最终降级 → 最终 category=inconclusive（见 §4.5）。
- 规则层已产出的 FAIL 原文等命中证据**原样传递**给 LLM 层作为种子证据（编号入包），LLM 可引用。

## 4. LLM 摘要层（llm_layer.py）

### 4.1 接口签名

```python
class LlmAnalyzer(Protocol):
    async def analyze(self, request: LlmAnalysisRequest) -> LlmAnalysisOutput: ...

@dataclass
class LlmAnalysisRequest:
    scenario: str
    failure_message: str            # 截断 2000 字符
    final_response: str | None      # 截断 2000
    seed_evidence: list[EvidenceItem]   # 规则层命中证据 + 上下文证据，已编号（见 §4.2）
    screenshot_names: list[str]         # 时间序文件名，上限 20；末状态对标记 [FINAL]
    feature_excerpt: str                # 前 2000 字符

@dataclass
class LlmAnalysisOutput:
    category: Category          # 必须是五类之一
    confidence: float           # clamp 到 [0.05, 0.95]
    summary: str                # 非空，截断 2000
    suggestion: str             # 可空；空则用 §3.3 模板按 category 回填
    evidence_refs: list[str]    # 只允许引用请求里的 ref_id

@dataclass
class EvidenceItem:
    ref_id: str        # "E1", "E2", ...
    kind: str          # junit_failure | junit_sysout | junit_property | thoughts | screenshot | feature_file
    reference: str     # §6.3 定位符
    excerpt: str       # 截断 400 字符
```

`DefaultAttributorAnalyzer`：`get_litellm_chat_model("attributor_analyze")`（agents_llm_config 缺该键 → 构造时抛 `AttributionError`，api 层捕获后按 analyzer 不可用处理）；温度 0；总尝试 ≤2（首次 + 1 次带校验错误回执的重试）；单次内部超时 60s；异常向上抛由 api 层降级——协议实现自身不做回退决策（与蒸馏器 polisher 同约定）。

### 4.2 证据包构建（确定性）

1. 种子：规则层命中的全部 evidence + FAIL 原文（kind=junit_failure）+ final_response（kind=junit_sysout）。
2. 上下文：THOUGHTS 中**规则命中轮 + 末 6 轮**（去重按 index 升序），每轮一条 EvidenceItem（kind=thoughts，reference=`thoughts:<index>`，excerpt=content_text 截 400）。excerpt 必须保持该轮 content_text 的**纯截断**——不追加任何附注、轮次标注或截图文件名（追加装饰文本会破坏 §4.4-3 的子串回查，导致带锚轮次的合法引用被误杀降级）。
3. 截图：轮次锚截图与末状态对截图**各自独立**打包为 kind=screenshot 的 EvidenceItem（reference=`screenshot:<filename>`，excerpt=文件名；同一文件名只进包一次，末状态对标记 [FINAL]）。截图项与 thoughts 项互不拼接、互不干扰：截图项回查走"存在即通过"分支（§4.4-3），thoughts 项子串校验只针对其纯截断 excerpt。
4. 全部 EvidenceItem 按上述顺序编号 E1..En；总条目上限 30（超限先丢上下文轮再丢非末状态截图，种子永不丢）。

### 4.3 Prompt 设计要求（system prompt 固化要点）

1. 角色限定：失败归因分析员，只做五类归类与证据引用，不修 bug、不臆测修复方案。
2. 五类判定标准各一句（product_bug=页面行为与预期不符且页面/环境正常；test_rot=用例依据的元素/URL/文案已过时；environment=站点/浏览器/网络等运行环境故障；agent_limit=引擎轮次/超时/上下文等硬上限或模型能力不足；inconclusive=证据不足）。
3. 硬约束逐条列出：(a) evidence_refs 只允许填请求中列出的 ref_id，禁止编造文件名/轮次/引用；(b) **宁可疑而不定，不许猜**——证据不足时必须输出 inconclusive，这是合法且被鼓励的输出；(c) 非 inconclusive 结论必须引用 ≥1 条文本类证据（thoughts/junit/feature_file），仅凭截图文件名不足以支撑任何结论（本层不读像素）；(d) 只输出严格 JSON，无围栏无解释。
4. 输出 JSON schema（附示例）：
```json
{"category": "test_rot", "confidence": 0.7, "summary": "一句话根因", "suggestion": "一句话建议", "evidence_refs": ["E3", "E7"]}
```

### 4.4 输出校验与证据引用回查算法（精确定义）

对每次 LLM 输出依次执行，任一步失败即该次尝试失败：

1. **提取**：strip 后剥 ```` ```json ```` 围栏，取首个 `{` 至末个 `}` 子串，`json.loads`。失败 → 尝试失败。
2. **Schema**：五键齐全；category ∈ 五类；confidence 为数值（clamp [0.05,0.95]，非数值 → 失败）；summary 非空字符串；evidence_refs 为字符串列表；suggestion 字符串（可空）。失败 → 尝试失败。
3. **引用回查**（对每个 ref，逐条独立判定）：
   - `items_by_ref` 查无此 id → drop（violation=`unknown_ref`）；
   - kind=screenshot → 存在即通过（文件在打包时已确认存在，无需 excerpt 校验；与 thoughts 项互不干扰，见 §4.2-3）；
   - 其余 kind：按 §6.3 定位符**重新解析**出 referent 文本（thoughts:index → 界内且取该轮 content_text；feature:line → 1-based 界内取该行；junit_failure/junit_sysout → 对应原文；junit_property:name → properties[name]），解析失败（越界/键不存在）→ drop（violation=`unresolvable`）；解析成功 → 校验 `norm(excerpt)` 是 `norm(referent)` 的子串（`norm`=连续空白折叠+strip，大小写敏感；thoughts 类 excerpt 为 content_text 纯截断、打包阶段不得追加装饰文本，见 §4.2-2，正常打包下恒可满足），不成立 → drop（violation=`excerpt_mismatch`）。
4. **结论级约束**：category ≠ inconclusive 时，存活引用须 ≥1 且**至少 1 条为文本类**（thoughts/junit_*/feature_file）；不满足 → 视为本次尝试失败（violation=`insufficient_evidence`）。
5. **重试与降级**：第 1 次失败 → 把违反项以机器文本（如 `violation: unknown_ref E99`）追加为 user 消息重试一次；再失败 → 走 §4.5 降级。LLM 调用异常（超时/网络）→ 不重试解析，直接计一次尝试，两次异常 → 降级。

### 4.5 降级行为（LLM 层输出被拒/不可用时的唯一出口）

| 情形 | warning | 最终结果 |
|---|---|---|
| analyzer 为 None | `llm_not_configured` | inconclusive，confidence=0.30 |
| LLM 异常 ×2 | `llm_error:<ExcType>` | inconclusive，confidence=0.30 |
| 输出校验失败 ×2 | `llm_output_invalid` | inconclusive，confidence=0.30 |
| 回查后存活引用不足（§4.4-4） | `evidence_backcheck_failed:<dropped refs 逗号列表，最多 5 个>` | inconclusive，confidence=min(原值, 0.30) |

降级产物 `decided_by="degraded"`；规则层种子证据（若有）**保留**在最终 evidence 中（保证 inconclusive 也有可查证据）；suggestion 用 §3.3 的 inconclusive 模板。

### 4.6 LLM 采纳成功时

`decided_by="llm"`；最终 evidence = 存活引用对应的 EvidenceItem（按 ref 序）；suggestion = LLM 的 suggestion（空则按 category 回填模板）；规则层 confidence 丢弃不用。

## 5. 公开 API（api.py）

```python
class AttributionError(Exception):
    """JUnit 缺失/不可解析、analyzer 配置键缺失等不可恢复错误。"""

def attribute_run(
    run_dir: str,                       # Hercules 运行产物根目录（含 output/ log_files/ proofs/）
    junit_path: str | None = None,      # None → glob run_dir/**/*.xml 取 mtime 最新；0 个 → AttributionError
    analyzer: LlmAnalyzer | None = None,
) -> list[AttributionResult]:           # junit 内每个失败 testcase 一条；全部通过 → 空列表

def attribute_case(
    evidence: TestCaseEvidence,
    analyzer: LlmAnalyzer | None = None,
) -> AttributionResult
```

- `attribute_run` 内部以 `asyncio.run` 驱动 analyzer（对外同步）；同一 run 的多条用例顺序归因。
- 过滤：无 `<failure>` 的 testcase 不产出结果（§2.1）。
- 确定性：analyzer=None 时整链确定性，同输入两次运行 `to_json()` 逐字节一致（含 warnings 顺序）。

## 6. 报告层（report.py）

### 6.1 JSON（schema_version=1，字段英文）

```json
{
  "schema_version": 1,
  "scenario": "…",
  "feature": "…",
  "category": "product_bug | test_rot | environment | agent_limit | inconclusive",
  "confidence": 0.0,
  "decided_by": "rule | llm | degraded",
  "rule_signature": "S1…S13 id | null",
  "llm_summary": "… | null",
  "suggestion": "…",
  "evidence": [
    {"kind": "junit_failure | junit_sysout | junit_property | thoughts | screenshot | feature_file",
     "reference": "…", "excerpt": "≤500 字符原文"}
  ],
  "warnings": ["…"],
  "notes": {"alternative_hypotheses": ["conf<0.8 的规则决策必非空；LLM 决策为空列表"]}
}
```

### 6.2 Markdown 报告（正文中文，摘录保持原文）

```markdown
# 失败归因报告 — <scenario>
- 结论：<category 中文标签>（置信度 <x.xx>，判定方式：<规则签名 S# | LLM | 降级>）
## 结论
<规则：模板句（category + 命中签名 + 关键摘录一行）；LLM：summary>
## 证据
| # | 类型 | 引用 | 摘录 |     ← 全部 evidence
## LLM 分析        ← decided_by=llm 时；含 evidence_refs 列表
## 建议            ← suggestion
## 警告            ← warnings 与替代假设；无则省略本节
## 附录            ← JUnit failure message 全文（截 2000）+ 末状态对截图文件名 + 截图清单（≤20 行）
```

### 6.3 证据定位符（reference 语法，回查器唯一依据）

| kind | reference | referent |
|---|---|---|
| junit_failure | `junit_failure` | failure message 全文 |
| junit_sysout | `sysout:final_response` 或 `sysout` | final_response / system_out 全文 |
| junit_property | `property:<name>` | properties[name]（键不存在 → unresolvable） |
| thoughts | `thoughts:<index>` | 第 index（0-based）轮 content_text |
| screenshot | `screenshot:<filename>` | 截图清单内 filename 精确匹配 |
| feature_file | `feature:<line>` | feature_text 第 line（1-based）行 |

## 7. 测试方案（纯 pytest 离线；合成 fixtures + FakeAnalyzer）

fixtures 合成规则：`conftest.py` 提供 `make_run_dir(...)` 工厂——写 JUnit XML（手写最小树，字段对齐 §2.1）、`agent_inner_thoughts.json`（含 JSON 对象与多行字符串两种 content 形态）、0 字节 png 若干、feature 文本；LLM 一律注入 FakeAnalyzer（可编程返回值/异常/非法输出序列）。

A 组 证据加载（test_evidence.py）：
1. `test_load_synthetic_run_dir` / 全套产物 / 各字段齐、thoughts 展平序正确
2. `test_missing_thoughts_tolerated` / 删 thoughts 文件 / `missing` 含 `thoughts`，规则层仍可跑
3. `test_missing_screenshot_dir_tolerated` / 无截图目录 / missing + 空清单
4. `test_thoughts_content_json_and_string` / content 为 dict 与多行字符串各一 / content_text 均非空，前者 content_json 非 None
5. `test_screenshot_parse_tool_phase_ts` / `click_using_selector_end_1695000000123456789.png` / tool/phase/ts 正确
6. `test_screenshot_nonstandard_name` / `latest_screenshot.png` / phase=other、ts=None、排序垫底
7. `test_final_state_pair` / start,end,end 三张 / 末 end + 最近 start 标记 is_final_state
8. `test_screenshot_path_mention_anchor` / 某 round 文本提及截图文件名 / 建锚；未提及轮不建锚
9. `test_junit_failure_and_final_response_extraction` / is_assert 失败形态 / failure_message=assert_summary、final_response 取自 `Final Response: ` 行
10. `test_feature_file_property_loaded_and_missing` / 属性指向存在与不存在两种 / 文本载入 vs missing
11. `test_unresolvable_path_suffix_glob` / 属性路径失效但文件在 run_dir 深处 / 按文件名 glob 找回

B 组 规则签名（test_rules.py；每签名 ≥1 正例，断言 category+confidence+rule_signature+证据引用位置）：
12. `test_sig_planner_max_rounds` / FAIL=`Max planner rounds exceeded.` / agent_limit 0.95
13. `test_sig_planner_timeout` / FAIL=`planner_agent LLM call timed out after 60s` / agent_limit
14. `test_sig_nav_max_rounds_plain` / FAIL=`[ERROR] browser_nav_agent max nav rounds (50) reached before ##TERMINATE TASK##. …`（THOUGHTS 无信号）/ agent_limit 0.90
15. `test_sig_nav_max_rounds_not_found_secondary` / 同上 + THOUGHTS 含 `Element with selector: "提交" not found` / test_rot 0.75 且引用该轮、alt 非空
16. `test_sig_nav_max_rounds_network_secondary` / 同上 + THOUGHTS 含 `net::ERR_NAME_NOT_RESOLVED` / environment 0.75
17. `test_sig_context_limit` / THOUGHTS 含 `maximum context length` / agent_limit 0.85
18. `test_sig_network_dns` / THOUGHTS 含 `net::ERR_CONNECTION_REFUSED` / environment 0.90
19. `test_sig_browser_disconnected` / FAIL=`Target page, context or browser has been closed` / environment 0.80
20. `test_sig_tool_error_network` / THOUGHTS 含 `[TOOL ERROR] open_url: Navigation timed out` / environment 0.75
21. `test_sig_element_not_found` / THOUGHTS 含 `Element with selector: "Go" not found` / test_rot 0.70、alt 非空；feature_text 含 "Go" 时追加 feature 行引用
22. `test_sig_http_404` / THOUGHTS 含 `status=404` / test_rot 0.65
23. `test_sig_assert_mismatch_routes_llm` / FAIL=`EXPECTED RESULT: order placed\nACTUAL RESULT: cart still shows item` / needs_llm（FakeAnalyzer 收到种子证据含 FAIL）
24. `test_sig_llm_call_error` / FAIL=`[ERROR] browser_nav_agent LLM error: quota` / agent_limit 0.80
25. `test_sig_helper_uncertain_routes_llm` / SYSOUT 含 `##TERMINATE TASK##` + `uncertain` / needs_llm
26. `test_precedence_limit_before_mismatch` / FAIL 同时含 `max planner rounds exceeded` 与 EXPECTED/ACTUAL / S1 先命中（agent_limit）
27. `test_rules_zero_llm_imports` / grep 断言 rules/evidence/report 无 litellm/requests import

C 组 LLM 层与回查降级（test_llm_layer.py，全 FakeAnalyzer）：
28. `test_llm_valid_output_adopted` / fake 返回合法五键 + 引用存在的 E / decided_by=llm、evidence=被引项、category 生效
29. `test_llm_unknown_ref_degrades` / fake 引用 `E99` / 强制 inconclusive、warning `evidence_backcheck_failed`、decided_by=degraded、种子证据保留
30. `test_llm_screenshot_only_citation_degrades` / fake 对 product_bug 只引截图 ref / inconclusive（insufficient_evidence 路径）
31. `test_llm_invalid_then_valid_retry` / fake 首次 category=`bug`、二次合法 / 采用二次（重试路径）
32. `test_llm_invalid_twice_degrades` / 两次均非法 / inconclusive + `llm_output_invalid`
33. `test_llm_exception_degrades` / fake 抛 TimeoutError 两次 / inconclusive + `llm_error:TimeoutError`
34. `test_no_analyzer_rules_only` / analyzer=None + needs_llm 签名 / inconclusive + `llm_not_configured`
35. `test_llm_inconclusive_allowed` / fake 返回 inconclusive + 空 refs / 采纳，decided_by=llm
36. `test_backcheck_locator_semantics` / 直接测回查器：thoughts:越界、feature:行越界、excerpt 非子串、property 键缺失 / 各 violation 判定正确

D 组 报告与端到端（test_report.py / test_api_e2e.py）：
37. `test_markdown_required_sections` / 规则决策样例 / 标题、结论、证据表齐；conf<0.8 时警告节含替代假设
38. `test_json_schema_fields` / to_json() / 键集、枚举、confidence 范围、非 inconclusive 必有 ≥1 evidence
39. `test_end_to_end_submit_double_step_rot` / 合成 phase1 P0-1 形态（submit 落在跳转后页面，THOUGHTS 含 not found）/ test_rot，报告含建议
40. `test_end_to_end_assert_mismatch_fake_llm` / 合成静默点错形态（EXPECTED/ACTUAL + inner_thoughts 显示点了错误目标）/ FakeAnalyzer product_bug 需 ≥1 文本引用
41. `test_deterministic_without_llm` / 同目录 analyzer=None 跑两遍 / to_json() 逐字节相等
42. `test_attribute_run_filters_passed` / junit 含 1 过 1 挂 / 只产出失败用例一条

## 8. 验收标准（可机械检查）

1. `uv run pytest tests/record2gherkin/attributor -q` 全绿；全程无网络、无 LLM key、无浏览器（fixtures 全合成，png 0 字节）。
2. 规则层零模型：`rules.py`/`evidence.py`/`report.py` 不 import litellm/requests（grep + 用例 27）。
3. 签名覆盖：§3.2 表中 S1-S13 每条 ≥1 正例；S3 三分支各 1 例（用例 14-16）。
4. 不瞎编不变量：所有非 inconclusive 结论 ≥1 条可回查证据（用例 38 断言）；回查降级路径全覆盖（用例 29/30/33/34/36）。
5. 确定性：analyzer=None 时同输入两次运行 JSON 逐字节一致（用例 41）。
6. `make fmt` 后 `black --check record2gherkin/attributor tests/record2gherkin/attributor`（line-length 200）通过。
7. 真实失败样本验证（签名召回率）**不在本 MVP 验收内**，列入评测框架接入后的遗留项（§9）。

## 9. Out of Scope

- 截图视觉比对/多模态分析（LLM 层纯文本，截图仅文件名级引用）。
- 跨 run 趋势分析、历史对比、用例库/报告 CRUD、UI 呈现（后者属编排 CLI/前端）。
- 修复建议的自动执行；suggestion 只是文本。
- step 级证据精确对齐（PLAN §3.5 明确 v1 近似：时间序 + 字面锚）。
- 网络 HAR/API/sec 日志内容解析（仅路径透传 + 文本扫描）。
- 修改 `testzeus_hercules/` 引擎（含 click JS fallback 误记 success 的上游 bug——仅作为归因上下文认知，不修）。
- 大规模 junit（>50 testcase）性能优化与并发归因。
- 英文报告模板/多语言报告。

## 10. 遗留决策点（真实失败样本暴露后再补，不预先设计）

- 签名召回率：评测实验的真实失败运行接入后，统计"规则直接定类率 vs 落入 LLM 层 vs inconclusive"三分布，再决定是否增补签名（预期候选：静默点错的事后形态、helper 图片比对 contraduct 的细分）。
- `needs_llm` 签名的 seed 注记是否需要携带规则层倾向假设（当前 S10 三假设并列）。
- 截图多模态升级（读像素）是否值得——取决于 product_bug 类的 LLM 层误判率数据。
- agents_llm_config 中 `attributor_analyze` 键的默认模型档位（先随评测实验统一配置）。
