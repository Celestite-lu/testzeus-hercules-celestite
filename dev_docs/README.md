# dev_docs — 「录制即用例」二开开发文档

所有二开产物（plan / spec / 审查报告 / 测试汇报 / 实验报告）必须固化到本目录，保证可回顾。
总计划见仓库根 `PLAN.md`（本地文件，已 gitignore，勿提交）。

## 目录结构

```
dev_docs/
  README.md        本规范
  STATUS.md        总进度看板（仅总编排代理维护）
  decisions/       架构与流程决策记录（ADR-NNNN-*.md）
  recorder/        录制器
  distiller/       蒸馏器
  evaluation/      评测框架与 UI 变异实验
  attributor/      失败归因器
  cli/             子进程编排 CLI
  reports/         跨模块汇报（合并后分析、阶段总结）
```

## 每个模块的文档生命周期（强制，缺任何一份视为未完成）

| 文档 | 产出时机 | 要求 |
|---|---|---|
| `plan.md` | 开发前 | 目标、方案选型、里程碑、风险。小而准 |
| `spec.md` | 开发前 | 接口/数据结构定义、行为规则、Out of Scope、验收标准、测试用例清单。**实现者读完不再需要做设计决策** |
| `review.md` | spec 完成后、实现前 | 独立审查结论：`PASS` 或 `REVISE`（附必改项，只拦会导致核心目标失败的问题） |
| `test-report.md` | 实现+测试后 | 测试命令与结果、已知边界问题清单 |

## 核心原则（高于一切）

1. **核心目标优先**：任何阶段都绝不在低收益边界问题上打转。边界情况写入 spec 的 Out of Scope 或 test-report 的已知问题清单，核心功能完成后再通过测试案例完善——不可能一次性考虑周全，追求一次性周全只会导致目标无法收敛。
2. **密钥安全**：`LLM-Key.txt` 与 `.env.local` 已 gitignore。任何文档、代码、日志、git 提交中不得出现密钥明文；命令中通过 `$(cat LLM-Key.txt)` 之类方式内联读取。

## 代码位置约定

- 新代码全部放 `record2gherkin/` 包，测试放 `tests/record2gherkin/`，不改 `testzeus_hercules/`（除非 PLAN.md 明确的接线点）。
- Python 代码 black line-length 200 + isort（与 `make fmt` 一致）。
- 实验产物（录制 JSON、运行输出、报告原始数据）放 `dev_runs/`（已 gitignore），结论固化进 dev_docs。
