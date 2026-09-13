# 线上公开页面巡检（2026-08-20）

## 范围与限制

本次使用独立 `agent-browser` 会话检查 `https://debate.vsagents.online` 的公开页面。未使用生产账号、未创建比赛、未读取 `api.md`、`.env` 或任何凭据。由于当前会话尚未核实正式生产 SSH 入口与授权，未执行服务器源码同步、服务重启或线上发布。

## 已验证流程

- 首页、排行榜、使用指南、登录、注册、条款页可达。
- 未登录进入大厅会跳转到登录页并保留 `return_to`。
- 未登录进入指定比赛会跳转登录并保留 `match_id`。
- `/api/auth/me` 返回未认证响应；未授权 LiveKit webhook 请求被拒绝。
- 排行榜数据可加载，页面无浏览器 console/page error。

## 发现与修复状态

## 正式服务器只读核验

- SSH 正式入口已按项目部署配置核实；未执行写操作、服务重启或数据修改。
- `jx-core`、`jx-jobs`、`jx-web`、`jx-livekit` 均为 `active`。
- Core 实际监听 `127.0.0.1:8100`，`/health/live` 与 `/health/ready` 均返回 HTTP 200；公网 Web 返回 HTTP 200。
- Core/Jobs systemd 将 `/opt/jixia-debate/apps/*/src` 置于 runtime-site 之前，实际模块导入路径已确认来自仓库源码。
- 线上数据库当前有 1 场 `PAUSED` 比赛，按 `scripts/qa/release_state.py` 的活动状态集合属于发布门禁范围；因此本轮未同步或重启。
- 线上关键 Core 模块哈希与本机工作树不同；线上保留 `before-163` 和总备份目录，发布前仍需重新生成完整脱敏快照和差异清单。

### ISSUE-001：首页领奖台名次的 ARIA 属性不合法

首页名次文本原先在无角色的 `span` 上使用 `aria-label`，Axe 报告 `aria-prohibited-attr`。本机已改为带 `role="img"` 的可访问名次标记，并补充测试。该修复尚未发布线上。

### ISSUE-002：排行榜横向滚动区域不可键盘访问

线上 `/leaderboard` Axe 结果：`scrollable-region-focusable`，严重级别，命中 `<div class="overflow-x-auto">`。本机已将该容器设为 `role="region"`、`aria-label="排行榜数据表"`、`tabIndex={0}`，并新增组件回归测试。排行榜数据与筛选逻辑未改变。该修复尚未发布线上。

线上复测时仍观察到 ISSUE-002，符合当前未部署状态；发布后必须重新运行 Axe，确认该 violation 消失。

## 后续门槛

- 已完成正式发布和 5 分钟观察：活动比赛门禁为 0；Core/Jobs/Web 源码同步、备份、服务重启和健康检查通过；发布后错误级日志无记录。
- 发布后首页与排行榜 Axe 均为 0 violation；仍需下一场受控真人比赛验证 Agent 发言中离开/重进、主持音中离开/重进的声音、字幕和事件序列。

## 2026-08-21 重连 sequence 专项

- 生产专用比赛复现：`match.online/offline` 已推进 Core `sequence`，Web
  仅刷新 room snapshot，导致开始发言和终止比赛都以旧
  `expected_sequence` 被 `match_state_conflict` 拒绝。
- 规格 166 将 presence 事件改为同时刷新 match 与 room snapshot；没有改动
  Core、计时、epoch、LiveKit 或数据库。
- 发布前自动化通过：Web 160、Core 175、Jobs 19、Storybook 82、Playwright
  200，以及 lint、typecheck、contracts 和 27 页正式构建。
- Web-only 发布以活动比赛 0 为门禁，`BUILD_ID` 与 staging Linux runtime
  均已核对；回滚点为 `/opt/jixia-backup-before-166-20260821013500`。
- 发布后新建专用比赛，单标签离开约 5 秒再返回；在处理浏览器自动播放授权后，
  “开始发言”成功进入真人实时发言，未再出现 sequence 冲突。随后通过同一正常
  UI 成功终止比赛，证明终止命令也已恢复。
- 发布满 5 分钟后四服务均为 active，Core live/ready、本机 Web 与公网均返回
  200；活动比赛为 0，Web/Core/Jobs 错误级日志与浏览器错误列表均为空。

## 2026-08-21 真人开始窗口专项

- 生产确认 `RUNNING/HUMAN_READY_TO_START` 原先没有 Core timer，一场专用
  比赛在 `speech.ready` 后超过 157 秒仍未暂停，违反 PRD 的 60 秒窗口。
- 规格 167 增加 Core 权威 `human.start_timeout`，覆盖固定动作、自由辩论、
  reset、全局恢复、59.9/60 秒边界、开始/到期竞态、重连与所有取消路径；
  等待窗口不扣减 `speech_remaining_ms`，普通重连不延长窗口。
- 第一场发布后生产事件间隔为 60,009ms，状态为
  `PAUSED/RECOVERY_REQUIRED`，snapshot 保存 `HUMAN_START_TIMEOUT`。申请恢复
  经 3 秒全局倒计时重新回到“开始发言”，证明新窗口重建。
- 现场同时发现 `PAUSED` 页面原先只为 `ERROR/SYSTEM_RECOVERY` 显示
  `error_code`，用户看不到具体原因。Web follow-up 仅在 PAUSED 且存在明确
  error code 时显示原因，手动暂停不误报服务故障；Playwright 覆盖四视口、
  sequence 更新和既有 TTS 恢复提示。
- 第二场生产事件间隔为 60,008ms，真实页面显示“当前辩手 60 秒内未开始
  发言，比赛已暂停；确认设备后可申请恢复。”两场均通过正常 UI 终止，活动
  比赛回到 0。
- 自动化最终通过：tooling 47、QA 5、Web 166、Core 184、Jobs 19、
  Storybook 82、Playwright 200/200、Ruff、Pyright、ESLint、TypeScript、
  contracts 和 27 页正式构建。26 项 PostgreSQL integration 因本机无服务未执行。
- 回滚点为 `/opt/jixia-backup-before-167-20260821023303` 和 Web-only
  `/opt/jixia-backup-before-167-web-message-20260821024824`。最终四服务 active，
  Core ready、公网 Web 200；Core/Jobs 无 warning-or-higher。Web 唯一记录是
  受控 restart 的 SIGTERM 143，随后正常启动且 `NRestarts=0`。
