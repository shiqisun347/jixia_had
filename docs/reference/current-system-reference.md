# 稷下 v2.0 当前系统参考

用途：后续调试、审查和发布前的快速入口。本文是当前代码和验证证据的索引，不替代产品需求、技术设计、语音 Spike、OpenAPI 契约或生产发布记录。

## 先读什么

1. `AGENTS.md`：不可违反的工程边界和测试/发布流程。
2. `稷下人机自动辩论实验平台需求文档.md`：产品行为、权限、状态和验收目标。
3. `TechDesign-Jixia-Debate-MVP.md`：架构、状态机、时钟、媒体、失败和部署语义。
4. `docs/research/realtime-voice-spike-2026-07-24.md`：ASR/TTS 的实测参数和限制。
5. `specs/README.md` 与 v2.0 规格 200+：当前切片；161–170 只作历史回归证据。
6. `MEMORY.md`：已做修改、证据和遗留风险。
7. `packages/contracts/src/generated/openapi.d.ts`：Web/Core HTTP 契约；不要手写重复 DTO。
8. `api.example.md`：了解凭据文件结构；经用户明确授权可在本机读取未跟踪的
   `api.md` 用于服务器连接，但内容不得进入代码、文档、日志或 Git。

## 系统边界

| 部件                                | 权威职责                                                     | 不应承担的职责                     |
| ----------------------------------- | ------------------------------------------------------------ | ---------------------------------- |
| `apps/core` / `MatchActor`          | 比赛状态、发言权、权限、计时、回调校验、外部模型编排         | 不把浏览器倒计时当状态来源         |
| `apps/core` / `MatchRuntimeManager` | Actor 生命周期、事务提交后广播、ASR/Agent/Postmatch 回调编排 | 不绕过 Actor 直接修改比赛状态      |
| `apps/jobs`                         | 主持音生成、音频归档、导出、排行榜、清理；任务有界且可重试   | 不在比赛实时路径中决定状态         |
| `apps/web`                          | 展示、交互、缓存、LiveKit 音频订阅和本地平滑显示             | 不决定比赛计时、发言结束或在线状态 |
| `packages/contracts`                | 由 Core OpenAPI 生成的 TypeScript 类型                       | 不手写重复请求/响应 DTO            |
| PostgreSQL                          | 持久状态、runtime snapshot、事件、任务队列、lease 高水位     | 不用新 Redis/Kafka 替代事务边界    |
| LiveKit                             | 仅传输人类/服务端音频                                        | 不承载比赛命令或状态事件           |

运行形态是单机 `jx-core`、`jx-jobs`、`jx-web`、自托管 LiveKit、PostgreSQL 和公网
1Panel OpenResty 反向代理；Core 使用 advisory lock 保证单实例。Caddy 是历史设计基线，
不是当前服务器的 systemd 服务。当前约束不允许 Redis、Kafka、Kubernetes、OSS、LiveKit
Egress 或新微服务。

v2.0 新建房间只支持 4v4：Web 过滤目录，Core 在创建事务内再次校验。
历史非 4v4 快照仍可读取和恢复，因此规则解析、数据库约束和 MatchActor 的
通用实现不是冗余，不得以产品收敛为由删除。

## 代码地图

### Core

- `apps/core/src/jx_core/app.py`：FastAPI factory、lifespan、启动时清理遗留 lease、恢复未终态比赛、注册路由和 health。
- `apps/core/src/jx_core/matches/domain.py`：纯比赛域模型和 `MatchActor`。同一比赛命令单队列串行；内部 timer、epoch 过滤、暂停/恢复和 snapshot view 在此完成。
- `apps/core/src/jx_core/matches/service.py`：Actor manager、数据库事务提交、事件广播、ASR/Agent/Postmatch 回调和恢复加载。
- `apps/core/src/jx_core/matches/routes.py`：比赛快照、命令、主持音、LiveKit token、WebSocket 会话和权限边界。
- `apps/core/src/jx_core/room_connections.py`：`RoomConnection` epoch 高水位和 `RoomConnectionLease` 活动连接集合。
- `apps/core/src/jx_core/presence.py`：WebSocket 与 LiveKit 双通道在线聚合、事件去重和在线迁移。
- `apps/core/src/jx_core/livekit_routes.py`：签名 webhook，仅接受人类 participant join/left/aborted。
- `apps/core/src/jx_core/agent/runtime.py`：LLM 流、generation、TTS、LiveKit 服务端输出、重试和清理。
- `apps/core/src/jx_core/asr/runtime.py` 与 `asr/protocol.py`：人类音轨、PCM、Fun-ASR task 轮换、interim/final 和失败。
- `apps/core/src/jx_core/auth/*`：Session、密码、logout/改密撤销 lease 与权限。
- `apps/core/src/jx_core/models.py`：SQLAlchemy 数据模型；migration 只追加。

### Jobs/Web/运维

- `apps/jobs/src/jx_jobs/host_tts_worker.py`：生成 Ogg 并用 PyAV 写入主持音准确 `duration_ms`。
- `apps/jobs/src/jx_jobs/audio_worker.py`、`export_worker.py`、`leaderboard_worker.py`：归档、导出、清理和排行榜。
- `apps/web/src/features/debate/live-match-page.tsx`：WebSocket 快照消费、LiveKit 连接、主持音按权威剩余时间 seek、字幕恢复。
- `apps/web/src/features/debate/use-match-runtime.ts`：WebSocket 重连、sequence 新旧快照选择、命令 expected sequence/epoch。
- `ops/systemd/jx-core.service.d/pythonpath.conf` 与 `jx-jobs.service.d/pythonpath.conf`：仓库源码必须位于实际导入路径首位；发布需核对 `module.__file__` 和源码 hash。
- `docs/deployment.md`：脱敏发布顺序、健康检查、回滚边界；不记录真实服务器凭据。
- `ops/openresty/`：当前生产 OpenResty 的可审计配置模板；证书私钥只存在服务器受保护目录。

## 权威状态与计时

主状态大致为：`WAITING/READY -> START_PENDING_RUNTIME -> START_COUNTDOWN -> RUNNING <-> PAUSED/SYSTEM_RECOVERY/ERROR -> FINISHED/TERMINATED`。动作状态包括 `HOST_ANNOUNCING`、`PREPARING`、`HUMAN_READY_TO_START`、`HUMAN_SPEAKING`、`SPEECH_FINALIZING`、`AGENT_PREPARING`、`AGENT_SPEAKING`、`AGENT_FINALIZING`、`FREE_SELECTING` 和终态。

不变量：

- 状态变更必须进入当前比赛的 `MatchActor`；事务提交成功后才替换 Actor 状态并广播。
- `MatchActor` 用 monotonic deadline；持久化 snapshot 只保存可恢复的剩余值/墙钟辅助值，不按秒写库。
- Web 可以本地平滑显示，但到零必须等待 Core 事件；`host.finished` 仅兼容旧客户端，主持音由 Core `host.elapsed` 推进。
- `0 ms` 是有效的已到期值，不得用 `or` 当成缺省时长；恢复必须安排立即到期 timer。
- 手动暂停、服务恢复、错误暂停冻结剩余时间；恢复倒计时结束后按原动作重建 deadline。
- `START_COUNTDOWN/NOT_STARTED` 恢复后必须重新进入当前动作，不能卡在 `RUNNING/NOT_STARTED`。

## 掉线、重连与媒体在线

`RoomConnection` 永不因断线删除 epoch 高水位；acquire 分配 `max_epoch + 1`，release 只删除精确 lease。旧连接晚关闭不能降低新连接 epoch。Core 重启时清除旧活动 lease并把对应成员设为离线，但保留高水位。

对真人辩手，在线成立需要同一 `connection_epoch` 同时具有：

1. 活动业务 WebSocket lease；
2. LiveKit participant 媒体存在。

高 epoch online 取消旧离线宽限；低 epoch online/offline/expiry 丢弃。离线宽限是连续 60 秒，59.9 秒不暂停；用户已离线时的更高 epoch offline 只能推进高水位，不能覆盖首次离线时间、重排 expiry 或重复暂停 ASR。非当前选手离线不应暂停比赛。presence 使用可注入 wall clock，避免测试/恢复时间源分裂。

LiveKit identity 绑定 `match_id/user_id/connection_epoch`。Webhook 必须验证官方签名、房间、身份、成员角色、epoch、event id/SID 幂等和乱序；浏览器 session 不可替代 webhook 身份。

## 外部回调与失败语义

所有 ASR/Agent/TTS 回调至少核对 `match_id`、`speech_id`、`attempt_no`、`generation_id`、`connection_epoch`、`context_version`；过期结果直接丢弃。Agent 已开始播放后 provider/TTS failure 的顺序是：权威错误/暂停 -> 有界媒体清理；不能因清理延迟继续消耗比赛计时。暂停恢复 Agent 时先清理旧任务，再由 Actor 恢复事件启动新任务。

ASR interim 只读且可丢；当前 Actor 同步保存临时字幕用于同进程重连恢复，但不逐片写库。最终文字由服务端持久化，用户只能编辑自己的最终文本，后续 Agent 使用最新 display text。

当前真人发言者掉线会在 presence 事件提交后串行暂停同一 `speech_id` 的
ASR，并冻结 MatchActor 发言 deadline。相同用户在 60 秒内恢复双通道在线
后，Core 先等待 pause 清理，再从已完成 segment checkpoint 恢复 ASR；Web
根据权威 `HUMAN_SPEAKING` 状态自动重新发布麦克风。非当前成员掉线不影响
ASR，真人持续在线时的供应商故障仍遵循一次重试、再次失败进入错误暂停。

## WebSocket 与前端恢复

WebSocket 建立时先注册 lease/presence，再订阅事件队列，最后取最终 snapshot，避免竞态漏事件。每条命令重新校验精确 lease 和未撤销/未过期 Session，并带 `message_id`、`expected_sequence`、`connection_epoch`。关闭时 sender task 必须显式等待，finally 仍要释放 lease/presence。

Web 以 snapshot sequence 选择新状态，旧 snapshot 不覆盖新状态；重连时重新获取 epoch 和 LiveKit token。主持音等待 metadata 后依据 `duration - authoritative_remaining` seek；已经结束不播放，重进不从 0 重播。旧 Room 或异步 token 完成时必须断开并清理隐藏 audio 元素。

## 数据与 Jobs

runtime snapshot 是恢复输入，不是完整 Event Sourcing；关键事件另存递增 sequence。主持音 READY 资源必须有有效 `duration_ms`，否则阻止发布/开赛；脚本 `scripts/ops/backfill_host_audio_duration.py` 默认 dry-run、可幂等回填。

Jobs 任务必须有状态、attempt、错误码、租约/超时和幂等键；文件操作只能在配置的音频/导出根目录内。音频路径、供应商原始响应、token 和内部堆栈不能进入用户错误或日志。

## 接口事实来源

HTTP/WS DTO 以 Core Pydantic/OpenAPI 生成结果为准。关键实时接口包括：

- `GET /health/live`、`GET /health/ready`；
- `GET /api/matches/{match_id}/snapshot`；
- `WS /api/matches/{match_id}/events`；
- `POST /api/matches/{match_id}/livekit-token`，可选 `connection_epoch`；
- `POST /api/livekit/webhook`，签名保护，不接受浏览器会话身份；
- 比赛命令由 WebSocket `MatchCommandRequest` 携带 expected sequence、epoch 和 message id。

契约变更流程是修改 Core schema/route 后运行 `pnpm contracts:check`，提交生成的 `packages/contracts/src/generated/openapi.d.ts`，Web 只从 generated types 消费。

## 发布和回滚门禁

发布前必须：

1. 确认没有 `RUNNING`、`START_COUNTDOWN`、`START_PENDING_RUNTIME` 活动比赛；有活动比赛就等待，不自动暂停、终止或删除。
2. 保留仓库外生产源码、Web standalone、LiveKit 配置和差异补丁回滚点；禁止目录级 `rsync --delete`。
3. 核对 systemd 实际 `ExecStart/PYTHONPATH`、模块 `__file__`、源码 hash、migration head 和关键配置权限。
4. 按 migration -> Core -> Jobs -> Web -> LiveKit 的受控顺序发布；每阶段检查 live/ready、四个服务、首页和日志至少 5 分钟。
5. 用专用比赛验证：Agent 发言离开 10 秒/59.9 秒重进、主持音中离开/重进、声音、字幕、事件序列和 60 秒暂停边界。

回滚只恢复对应阶段源码/standalone/override/LiveKit 配置，不破坏性回退 migration，也不回退已回填 duration 字段。

## 当前证据与缺口

### 已有本机证据

- 规格 170 发布前完整门禁：工具 47、QA 5、Web 166、Core 197、Jobs 19、
  Storybook 82、Playwright 200/200；Ruff、Pyright、lint、typecheck、
  contracts 和正式 Web build 通过。
- v2.0 4v4 创建边界专项已通过 Core room tests 16、Web 167 和 Ruff；完整
  v2.0 门禁结果以规格 200 为准。
- contracts、Ruff、Pyright、ESLint、TypeScript、正式 Web build、`git diff --check` 已通过。
- Playwright 四视口 200/200 已通过。
- 状态矩阵规格：`specs/164-state-matrix-regression.md`；本知识文档规格：`specs/165-project-knowledge-reference.md`。

### 已有生产/公网证据

- 历史生产记录（2026-08-05）显示四服务 active、Core live/ready、migration `0015_match_admin_note`、活动比赛 0、任务 17/17 成功、数据盘约 44%。这是历史证据，不代表当前发布版本。
- 当前公网首页和指定比赛页可达；未授权 LiveKit webhook 返回 401。
- 2026-08-22 服务器重启后 1Panel OpenResty 因单文件挂载源被初始化为空目录而退出，导致
  80/443 拒绝连接。已从同版本镜像恢复公共配置、从现存 Let’s Encrypt 证书恢复 SSL 文件，
  `openresty -t` 通过，容器恢复为 running；公网首页、`/experiments` 和能力接口均返回 200。
- 规格 169 已 Core-only 发布，生产主机 59/60 秒高 epoch 回归通过。
- 规格 170 已 Core-only 发布，回滚点
  `/opt/jixia-backup-before-170-20260821142353`；实际导入仓库路径且哈希与
  已验证产物一致。观察超过五分钟后四服务 active、Core/Web
  `NRestarts=0`、健康和 Web 200、Core/Jobs warning 为 0、非终态比赛 0。

### 明确未证实

- v2.0 规格 200 已在零活动比赛门禁下发布，运行回滚点
  `/opt/jixia-backup-before-v2-20260821144920`，源码/文档回滚点
  `/opt/jixia-backup-before-v2-docs-20260821145415`。生产版本、导入路径、
  关键源码哈希、四服务、健康、日志和公网浏览器均已核验；观察超过五分钟。
- 真人双通道冷启动边界：自动化浏览器合成麦克风没有有效振幅，
  未通过设备门禁；不能用生产主机确定性 Actor 测试替代这项现场设备验收。
- 本机 PostgreSQL integration tests（26 项）因 Docker/PostgreSQL 不可用跳过。
- 真人浏览器麦克风、噪声/蓝牙设备、至少 10 人音色盲听、五场长链路压力和 50 路长期 LLM 饱和。
- 规格 167 已发布并以两场专用生产比赛验证真人开始窗口：服务端
  `speech.ready` 至 `HUMAN_START_TIMEOUT` 分别为 60,009ms 和 60,008ms。

## 后续工作方式

- 先把问题归类到状态、计时、连接/媒体、外部回调、事务、权限、Jobs、Web 恢复或部署证据之一。
- 先写能复现失败的测试，再改最小代码；同时检查相邻状态和 timer，避免修复 A 引入 B。
- 每次逻辑变更后运行对应专项，再运行 Core/Jobs/Web 全量和 contracts/build/browser 门禁。
- 最终答复分开写“本机通过”“生产通过”“真人待验收”，不能用静态测试替代现场证据。
