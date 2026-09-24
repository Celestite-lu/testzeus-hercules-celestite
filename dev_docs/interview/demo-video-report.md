# 演示视频制作报告 — demo-final.mp4

> 代理：总编排（视频后期）。日期：2026-09-24。
> 交付物：`dev_docs/interview/assets/demo-final.mp4`（89.7s，1920x1080@30fps，H.264+AAC 无音轨，1.7MB）。

## 1. 成片结构（对照 pitch-and-demo.md §2.2 分镜）

| # | 时段 | 内容 | 时长 |
|---|---|---|---|
| 0 | 0:00-0:03 | 片头字卡「录制即用例 record → generate → run → analyze」 | 3s |
| 1 | 0:03-0:13 | ① record：终端实录，events=4 | 9.8s |
| 2 | 0:13-0:24 | ② generate：占位符 + missing 警告 + feature 全文 | 11s |
| 3 | 0:24-0:32 | ③ 注入测试数据重跑 generate：0 hits | 8s |
| 4 | 0:32-0:41 | ④ run --dry-run：`***REDACTED***` 脱敏画面 | 9s |
| 5 | 0:41-0:50 | ⑤ analyze：S1 规则签名归因 | 9s |
| 6 | 0:50-0:53 | 闭环完成帧（四命令全 exit=0） | 2.9s |
| 7 | 0:53-1:15 | 数据表定格：M3 6/6 vs 0/6 高亮 + 限定词硬编码 | 22s |
| 8 | 1:15-1:30 | F1 真实执行固化录屏：MiniShop 搜「台灯」→结果→（字幕注明 JUnit tests=1 errors=0） | 15s |

分镜 7 镜全落（镜 1 的 3s 片头独立成镜 0）；总长 89.7s ≤ 120s 达标。分镜的"编辑器切画面"（镜 1/2/5）简化为终端纯画面+字幕——用户已确认"视频演示+字幕简单解释"路线。

## 2. 制作链路（可复现）

1. **终端实录**：`dev_runs/demo_slow.sh`（慢节奏版，2-7s 步间停顿）。关键坑：`screencapture` 全屏模式被前台应用抢焦点（第一次录制拍成 ZCode 窗口，作废）；改为 **按窗口 ID 录制** `screencapture -v -l<WID>`，与焦点无关。macOS 中文环境下 Quartz 窗口属主名是「终端」不是 "Terminal"（第二次空结果的根因）。
2. **分段**：1fps 抽帧按 JPEG 大小跳变定位 5 步边界（t≈12.6/23.6/31.6/40.6/49.6s），比 scene 滤镜可靠（该 ffmpeg build 上 select+metadata 无输出）。
3. **卡片**：HTML+Playwright 截图（`/tmp/r2g_cards.html`），PingFang/Hiragino 字体栈，数字与 experiment-report §2 逐字核对。
4. **字幕**：ffmpeg drawtext（Hiragino Sans GB），textfile 传中文避免转义；字体路径含空格必须用过滤器内单引号。
5. **拼接**：9 段统一 1920x1080@30 yuv420p 后 concat demuxer + faststart。终端窗 3040x1786 → crop 3040:1710:0:76（去标题栏）→ 1080p。

## 3. 红线检查（分镜 §2.1 逐条）

| 红线 | 结果 |
|---|---|
| 不露 key | ✅ 抽查最密文字帧（t=35s dry-run 段）视觉审查：仅 `LLM_MODEL_API_KEY=***REDACTED***`，无任何 key/token/长十六进制串；全片仅此段展示 env |
| 不现场真 LLM | ✅ 镜 4 为 --dry-run；真实执行用镜 8 固化录屏 |
| 不开 --polish/--llm | ✅ 全片未出现 |
| 单页 fixture | ✅ 镜 1 为 file:// demo_form.html |
| 口径主动给 | ✅ 镜 7 限定词「单 demo 应用、单模型的受控实验；M4 5/6 概率性边界」硬编码在卡上 |
| 数字逐字一致 | ✅ 表格帧视觉核对：M0-M4/主指标 9 个数字全部与 experiment-report §2 一致 |

## 4. 抽查记录（4 帧视觉审查，2026-09-24）

- t=17.5s（镜 2）：字幕完整无裁切，逐字可读；终端 feature 文本清晰。
- t=35s（镜 4）：无密钥泄露（见上）。
- t=78s（镜 7）：表格 9 数字逐项一致，绿色 6/6 / 红色 0/6 高亮清楚，限定词完整。
- t=86s（镜 8）：800x450→1080p 放大后可读，字幕完整。

## 5. 已知边界

- 镜 8 源为 800x450 Playwright 录屏，放大 2.4x 有轻微软化；字幕已注明 JUnit 结果，末帧未嵌入 junit XML 画面（分镜的"末帧切 junit"简化掉，口播可补）。
- 无音轨（用户决定：视频+字幕，不要旁白）。
