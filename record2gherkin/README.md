# record2gherkin — 「录制即用例」AI E2E 测试工具

把"手动点一遍页面"变成可重复执行的 Gherkin 测试：**录制 → 蒸馏 → 语义执行 → 失败归因**，四条命令串起全链路。执行内核基于 [TestZeus Hercules](https://github.com/test-zeus-ai/testzeus-hercules)（AGPL v3），本包为二开增量。

## 四条命令

```bash
# 1. 录制：起浏览器注入录制器，手动操作，结束后事件 JSON 落盘
uv run python -m record2gherkin record "http://localhost:3000" --out events.json
#    （或 --manual 打印 bookmarklet 用法，在任意页面手动注入）

# 2. 蒸馏：事件 JSON → Gherkin feature（确定性规则模板，LLM 仅可选润色；
#    密码自动掩码为占位符，--test-data 填充）
uv run python -m record2gherkin generate events.json --out test.feature [--test-data secrets.json] [--polish]

# 3. 执行：feature → Hercules 子进程（LLM key 从 LLM-Key.txt 读取，仅经 env 注入）
uv run python -m record2gherkin run test.feature --out-dir run1 [--dry-run] [--key-file LLM-Key.txt]

# 4. 归因：失败运行 → 规则签名优先 + 证据引用回查的归因报告（绝不瞎编）
uv run python -m record2gherkin analyze run1/opt [--llm]
```

## 模块

| 模块 | 职责 | 测试 |
|---|---|---|
| `recorder/` | 注入式 JS 事件录制器（bookmarklet，零依赖单文件） | 21 例 |
| `distiller/` | 确定性三层蒸馏：规则模板（零模型）→ 可选 LLM 润色 → 事实回查 | 68 例 |
| `evaluation/` | demo 应用 / 变异引擎 / 子进程执行器 / 指标 | 68 例 |
| `attributor/` | 13 条错误签名 + 五档分类 + 证据定位符回查 | 81 例 |
| `cli.py` | 四命令编排 | 54 例 |

设计文档与审查记录：`dev_docs/`（每模块 plan/spec/review/test-report 全固化）。

## 实验结论（exp001，详见 `dev_docs/evaluation/experiment-report.md`）

| 方法 | M3 动态 id 变异存活率 | 主指标（M1/M2/M3） |
|---|---|---|
| 本工具（录制→蒸馏→语义执行） | **6/6** | **1.00** |
| selector 式基线 | 0/6 | 0.667 |

文案改写变异 5/6——语义自愈的概率性边界已如实量化。

## 演示与测试注意

- 跑测试只用一条命令：`uv run pytest tests/record2gherkin -q`（本机 492 passed；子集乱序会因 session 级 playwright 冲突误红；新克隆环境因 gitignored 录制产物少量 skip 属预期）
- **现场演示不要录制多页（MPA）站点**：注入 JS 随文档导航销毁，record 会干净退出（exit 2）不落脏数据。用本地 MiniShop demo（`record2gherkin/evaluation/demo_server.py`）或 file:// fixture；多页支持是 v1 明确的 scope out

## 安全

LLM key 只从 `LLM-Key.txt`（gitignored）读取并经子进程 env 注入；所有日志输出强制脱敏；密码在录制层即掩码。
