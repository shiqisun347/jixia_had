# 163 生产中断链路修复

状态：已实现并发布，待真人生产专项回归

## 范围

本规格覆盖生产源码同步、业务 WebSocket 重连、主持音权威计时和 LiveKit 双通道在线判断。Core 是比赛状态、epoch、计时和恢复的唯一权威；Web 只根据快照渲染和建立媒体连接。

## 状态矩阵

| 场景 | 期望行为 |
| --- | --- |
| 当前真人发言者离线未满 60 秒 | 发言计时冻结，比赛不暂停；同一选手以更高 epoch 回来后恢复 |
| 非当前选手离线未满 60 秒 | 比赛继续；该选手恢复不产生重复事件 |
| 所有真人持续离线满 60 秒 | Core 权威进入 `PLAYER_OFFLINE_TIMEOUT` 暂停 |
| 旧连接晚于新连接关闭 | 旧 offline/expiry 被丢弃，不影响新连接 |
| 主持音期间离开或重进 | 计时由 Core 持有，从权威剩余位置继续，结束后不重播 |
| Agent/TTS 真实故障 | 按 162 的错误暂停和有界清理语义处理 |
| Core 重启 | 清除遗留活动 lease，保留 epoch 高水位；未完成比赛按恢复语义暂停 |

## 验收

- 10 秒、59.9 秒重连，以及 expiry 已入队未处理时，均不触发错误暂停。
- 多标签页、重复/乱序 LiveKit webhook、旧连接晚关闭均幂等。
- WebSocket lease 与同 epoch LiveKit participant 同时存在才算在线。
- 主持音快照包含总时长和剩余时长；没有有效时长的 READY 资产阻止发布。
- `/api/matches/{match_id}/livekit-token` 兼容无 body 的旧客户端，并支持 `connection_epoch`。
- `/api/livekit/webhook` 仅接受签名事件，不记录原始 token 或完整供应商载荷。
- 发布验证必须核对实际模块路径及源码哈希、Core live/ready、Web 页面、LiveKit 事件和 5 分钟日志。

## 发布与回滚边界

第一阶段仅在没有 `RUNNING`、`START_COUNTDOWN`、`START_PENDING_RUNTIME` 比赛时发布，包含 Core/Jobs PYTHONPATH override 和连接 epoch 修复。第二阶段先发布 Core webhook 和 Web，再恢复并重启 LiveKit 配置。两阶段均保留仓库外源码、差异补丁和配置备份；回滚只恢复对应阶段的服务源码/override/Web/LiveKit 配置，不回退已回填的 `duration_ms`。

## 实现与发布证据

- Core/Jobs 的 systemd override 以仓库源码路径为首；生产进程实际 `PYTHONPATH`、模块路径和关键源码 SHA-256 已核对一致。
- `RoomConnection` 保留 epoch 高水位，活动连接使用精确 lease；Core 启动清理遗留 lease，Actor 丢弃旧 epoch 并取消已重连成员的离线到期事件。
- 主持音时长由 Jobs 使用 PyAV 写入，Core 持有结束计时、暂停剩余时间和恢复；Web 只按权威剩余时间 seek。生产 40 个 READY 资产均有时长，范围为 6000-23300 ms。
- LiveKit token 绑定连接 epoch，签名 webhook 只处理人类参与者连接事件；WebSocket lease 与相同 epoch 媒体参与者同时存在才提交在线。
- 密码修改/管理员重置仅撤销活动 lease，保留 `RoomConnection` epoch 高水位；旧会话延迟关闭不会与新登录的 epoch 冲突。缺失 `offline_since_ms` 的旧客户端事件由 Core 归一为当前时间，避免默认 Unix epoch 导致立即超时。
- 退出登录、密码修改和管理员重置会撤销用户活动 lease、清除房间在线标记并清理 Core presence；长 WebSocket 每条命令重新确认精确 lease 和 session，认证失效后不能继续控制比赛。
- Web 主持音 seek 等待 `loadedmetadata` 后再定位到权威进度，effect 取消时主动解除媒体监听，避免重进/epoch 切换时从零播放或残留旧音频任务。
- WebSocket 重连快照竞态已修复：事件队列在最终 snapshot 前订阅，并重新读取 Actor 权威视图，避免快照与订阅之间漏掉状态迁移；已随本次受控发布上线。
- Agent/ASR 临时字幕已纳入同一 Core 进程的运行时 snapshot，重连页面可恢复正在显示的字幕；Core 重启后的临时字幕持久化仍未纳入本切片。
- LiveKit 页面/epoch 切换期间的异步连接取消会清理已完成连接及其隐藏音频元素，防止旧媒体连接与新连接并存；已随本次受控发布上线。
- WebSocket 关闭时等待 sender 任务结束并隔离发送异常，避免后台发送协程异常导致 lease/presence finally 清理中断；已随本次受控发布上线。
- Web 对缺少 `interim_text` 的兼容 snapshot 保留当前实时字幕，不让旧客户端/旧 Core 清空新事件；Live-match 四视口专项已通过。
- 离线宽限的记录、恢复调度和到期裁决统一使用 Actor 权威 wall clock；59.9 秒仍保持运行，满 60 秒才进入 `PLAYER_OFFLINE_TIMEOUT`，边界测试已通过。
- 更高 `member.online` epoch 即使不产生在线状态切换也持久化到 runtime snapshot；数据库提交失败时 Actor epoch 辅助高水位随状态回滚，避免内存/数据库分叉。
- 临时字幕更新等待正在进行的 Actor 提交完成后再修改内存 snapshot，防止提交完成时覆盖并发到达的 ASR/Agent 字幕；不增加逐片数据库写入。
- `system.recover` 在主持音附着于真人或 Agent 动作时保留 `HOST_ANNOUNCING` 并冻结剩余时长；恢复后重建权威 deadline，从剩余位置继续而非跳过主持音。
- `system.error` 进入错误暂停时清除临时 ASR/Agent 字幕，错误恢复页面不会展示过期实时文本。
- 运行时 `system.error` 在主持音播放中与 `system.recover` 采用相同语义：保留 `HOST_ANNOUNCING`、冻结剩余时长，恢复后继续而非跳过主持音。
- Agent 播放开始后的 provider failure 已采用 162 的先权威错误暂停、后有界媒体清理语义。
- 生产独有的 `/admin/logs` 页面已恢复并通过精确 `.gitignore` 反向规则纳入源码，不放宽其他日志目录。
- 两次发布门禁均为 0 个活动比赛；`jx-core`、`jx-jobs`、`jx-web`、`jx-livekit` 均为 active，Core live/ready 和公网比赛页通过。
- 回滚点包括 `/opt/jixia-backup-before-163.oftt`、`/opt/jixia-web.before-163.20260819033335` 和 `/opt/jixia-web.before-163-complete-20260819042107`；LiveKit 原配置已保存于第一处备份目录。

## 自动化验收

- 工具测试 47、QA 5、Web 单元测试 158、Core 非集成测试 175、Jobs 非集成测试 19，全部通过。
- Storybook 82、Playwright 四视口浏览器测试 200，全部通过；实时比赛专项覆盖单调 epoch、重连媒体重建和权威麦克风状态。
- 双通道 presence 白盒测试覆盖多标签聚合、更高 epoch 媒体接入、旧 epoch 乱序离开和重复 webhook；生产签名 webhook 返回 204，无效签名返回 401。
- Ruff、ESLint、Pyright、TypeScript、OpenAPI contracts check、全仓 Prettier 检查和 `pnpm build` 均通过。
- 本机没有 Docker/PostgreSQL daemon，数据库 integration 标记测试未在本机执行；生产只执行了只读数据校验和应用级回填脚本。

## 本次发布证据（2026-08-20）

- 活动比赛清零后创建 `/opt/jixia-backup-before-release-20260820225620`，同步 Core/Jobs 源码和 Web standalone；未执行目录级删除或数据库写入。
- 远端 Python 编译、关键模块 SHA-256、实际导入路径、四服务状态、Core live/ready、公网 Web 均通过。
- 发布后观察 5 分钟，Core/Jobs/Web 错误级日志无记录；首页和排行榜线上 Axe 均为 0 violation。

## 待现场验收

- 仍需使用专用生产比赛和真实浏览器，分别在主持音与 Agent 发言期间离开 10 秒和接近 60 秒后重进，核对声音、字幕、事件序列及 60 秒后不误暂停。未完成前不把真人生产回归写成通过。
