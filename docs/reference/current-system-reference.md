# 稷下 v2.0 当前系统参考

用途：后续调试、审查和发布前的快速入口。本文是当前代码和验证证据的索引，不替代产品需求、技术设计、语音 Spike、OpenAPI 契约或生产发布记录。当前交付入口是 [稳定版本说明](../v2-reproduction.md)、[部署](../deployment.md)和[备份恢复](../v2-backup-and-recovery.md)。

## 先读什么

1. `AGENTS.md`：不可违反的工程边界和测试/发布流程。
2. `稷下人机自动辩论实验平台需求文档.md`：先读恢复说明，区分历史需求基线与后续已批准且实际发布的覆盖。
3. `TechDesign-Jixia-Debate-MVP.md`：架构、状态机、时钟、媒体、失败和部署语义。
4. `docs/research/realtime-voice-spike-2026-07-24.md`：ASR/TTS 的实测参数和限制。
5. `specs/README.md` 与 v2.0 规格 200+：当前切片；161–170 只作历史回归证据。
6. `MEMORY.md`：已做修改、证据和遗留风险。
7. `packages/contracts/src/generated/openapi.d.ts`：Web/Core HTTP 契约；不要手写重复 DTO。
8. `api.example.md`：只了解示例结构；`api.md`、真实 `.env` 和任何密钥不得读取进文档、代码或日志，不进入 Git。

## 当前稳定基线

产品版本固定为 **v2.0**，内部 Core/Jobs 包版本 `2.1.0` 保留兼容。

- Core/Jobs：已发布 Spec308/309 及此前可靠性修复；173 个受保护安装源的 SHA 与本地逐字匹配。未发布的 310/312 Core 候选已隔离，不作为当前行为。
- Web：Spec312 已发布展示切片，线上构建 `J6YFijOoE7TSV9ijePYSO`；重新构建不要求重现相同 build ID。
- 数据库：唯一 head `0049_agent_human_avatars`，已发布迁移保持原字节。
- 配置：Spec327 的138条正常模板更新和审计已发布，YXH 修订275、新国辩修订146；仅新房冻结生效，旧房/历史不回填。公开资源包含两条正式赛制的198条完整模板。
- 实时依赖：LiveKit Python SDK固定为 `1.1.20`。当前 ASR/TTS 是 `qwen-audio-3.1-asr-flash-streaming` / `qwen-audio-3.1-tts-flash`；接入和非密钥参数见 `resources/v2/runtime.env.example`，不按旧设计或 Spike 名称自动更换模型。
- 现场展示：既有独立 `apps/showcase`，使用 Flask/SQLite 和独立认证，不参与比赛状态或模型编排。

## 系统边界

| 部件                                | 权威职责                                                     | 不应承担的职责                     |
| ----------------------------------- | ------------------------------------------------------------ | ---------------------------------- |
| `apps/core` / `MatchActor`          | 比赛状态、发言权、权限、计时、回调校验、外部模型编排         | 不把浏览器倒计时当状态来源         |
| `apps/core` / `MatchRuntimeManager` | Actor 生命周期、事务提交后广播、ASR/Agent/Postmatch 回调编排 | 不绕过 Actor 直接修改比赛状态      |
| `apps/jobs`                         | 主持音生成、音频归档、导出、排行榜、清理；任务有界且可重试   | 不在比赛实时路径中决定状态         |
| `apps/web`                          | 展示、交互、缓存、LiveKit 音频订阅和本地平滑显示             | 不决定比赛计时、发言结束或在线状态 |
| `packages/contracts`                | 由 Core OpenAPI 生成的 TypeScript 类型                       | 不手写重复请求/响应 DTO            |
| `apps/showcase`                     | 独立现场投票、评委点评与颁奖展示；独立 SQLite/认证           | 不决定比赛状态或共用 Core Session  |
| PostgreSQL                          | 持久状态、runtime snapshot、事件、任务队列、lease 高水位     | 不用新 Redis/Kafka 替代事务边界    |
| LiveKit                             | 仅传输人类/服务端音频                                        | 不承载比赛命令或状态事件           |

运行形态是单机 `jx-core`、`jx-jobs`、`jx-web`、自托管 LiveKit、PostgreSQL、独立 Showcase 和公网
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
- `apps/core/src/jx_core/asr/runtime.py` 与 `asr/protocol.py`：人类音轨、PCM、流式 ASR task 生命周期、segment checkpoint、interim/final 和失败；实际模型由运行配置指定。
- `apps/core/src/jx_core/auth/*`：Session、密码、logout/改密撤销 lease 与权限。
- `apps/core/src/jx_core/models.py`：SQLAlchemy 数据模型；migration 只追加。

### Jobs/Web

- `apps/jobs/src/jx_jobs/host_tts_worker.py`：生成 Ogg 并用 PyAV 写入主持音准确 `duration_ms`。
- `apps/jobs/src/jx_jobs/audio_worker.py`、`export_worker.py`、`leaderboard_worker.py`：归档、导出、清理和排行榜。
- `apps/web/src/features/debate/live-match-page.tsx`：WebSocket 快照消费、LiveKit 连接、主持音按权威剩余时间 seek、字幕恢复。
- `apps/web/src/features/debate/use-match-runtime.ts`：WebSocket 重连、sequence 新旧快照选择、命令 expected sequence/epoch。

### Showcase、资源与运维

- `apps/showcase/app.py`、`audio_events.py` 与 `static/`：独立展示、投票和控制台；不调用 Core 比赛编排接口。
- `apps/showcase/ops/init_config.py`：为新实例交互生成独立认证材料；不带生产口令、投票历史或运行配置。
- `resources/v2/RESOURCE_BUNDLE.json`、`PROVENANCE.json` 与 `assets/`：公开脱敏配置、生产基线来源和必要主持/默认头像素材；不含生产账号、历史或密钥。
- `scripts/ops/bootstrap_v2_admin.py`：默认只预检明确自有新空库；精确迁移占位行核验后，用现有 AuthService 和审计在同一外层事务创建新管理员。不能用于已有管理员改密。
- `scripts/ops/v2_resources.py`：资源/素材校验及新空库预检、事务导入；拒绝已有资源/历史，不调用模型或创建主持生成任务。
- `scripts/ops/backup_v2.py`：受保护目录中的 PostgreSQL/SQLite 一致性备份和逐表清单；完整灾备与公开初始化分开。
- `scripts/qa/fixtures/spec304/`：公开历史 AST 回归夹具，只配合自有临时文件和模拟服务使用，不是生产安装入口。
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

1. 重新核对0非终态房间、比赛和活动连接；有活动就等待，不自动暂停、终止或删除。配置更新和源码发布分别绑定实际计划。
2. 保留仓库外生产源码、Web standalone、LiveKit 配置和差异补丁回滚点；禁止目录级 `rsync --delete`。
3. 核对 systemd 实际 `ExecStart/PYTHONPATH`、模块 `__file__`、源码 hash、migration head 和关键配置权限。
4. 仅在授权的应用发布中按 migration -> Core -> Jobs -> Web 恢复，并核实际 systemd 依赖与媒体服务；检查 live/ready、服务、首页和日志。Spec327 只有配置事务，不需要源码部署或重启。
5. 用专用比赛验证：Agent 发言离开 10 秒/59.9 秒重进、主持音中离开/重进、声音、字幕、事件序列和 60 秒暂停边界。

回滚只恢复对应阶段源码/standalone/override/LiveKit 配置，不破坏性回退 migration，也不回退已回填 duration 字段。

## 当前证据与缺口

### Spec328 本次已完成分项（2026-10-18）

下列分项有本次独立回执；不能合并写成全部标准门禁已经通过。原始私有备份和诊断工件不进入 Git，也不是公开初始化的依赖。

| 分项                 | 已核范围                                                                                                                |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 私有 PostgreSQL 恢复 | 83表、431,029行；隔离恢复后逐表计数和有序整行 SHA 全部一致                                                              |
| 独立 Showcase 恢复   | SQLite 一致备份，8表恢复核对                                                                                            |
| 公开初始化           | 新空库49迁移、精确占位管理员替换/正常审计、198条模板及资源事务导入；两条正式规则正常 API 建房并核完整冻结模板，正常关闭 |
| 主持素材             | 14段完整解码、大小/哈希和时长核对；没有调用模型重新生成                                                                 |
| 真实 PostgreSQL 集成 | 独立可销毁库：Core331项、Jobs5项通过；认证浏览器1项通过                                                                 |
| Web 浏览器           | 336项通过、12项跳过；跳过保留，不计作通过                                                                               |
| Storybook            | 98项通过                                                                                                                |
| Showcase 专项        | 48项单元测试、3项浏览器测试通过                                                                                         |
| `pnpm test`          | 最小单测夹具修复后正在重跑，尚不宣称全量通过                                                                            |

公开初始化通过的是正常鉴权/房间 HTTP 路径与真实 PostgreSQL；未启动比赛、实时 worker 或提供方调用。源码、备份恢复、资源冻结和浏览器测试均不能替代完整语音比赛。

### 已发布配置的语音证据

Spec327 同批12条全文 LLM/TTS 中10条时长与窄内容联合合格；8条120秒四辩为90.47025～111.05025秒。两条时长失败保留，不保证未来每次生成都命中75%～100%窗口。该项是既有配置发布资格，不冒充 Spec328 新的语音或压力验收。

### 仍需单独验收

真人麦克风与双通道冷启动、噪声/蓝牙设备、现场扬声器、完整混合比赛、至少10人音色盲听、五场长链路压力和50路长期 LLM 饱和，不因本次仓库整理而标记通过。Git同步、最终待提交树检查及尚在运行的门禁以 Spec328 最终回执为准。

<details>
<summary>历史证据索引（不作为当前版本验收）</summary>

- 规格161–170是v1.0历史回归。Spec170的旧测试计数、旧部署回滚点和服务观察只支持当时切片，不能覆盖当前v2.0门禁。
- 2026年8月的 migration `0015_match_admin_note`、OpenResty恢复、Spec167真人开始窗口与Spec169/170高epoch回归，均为历史运行证据。
- Spec200记录早期v2.0发布。旧记录中的“PostgreSQL不可用、26项集成跳过”和合成麦克风未通过设备门禁是当时限制；本次真实PG结果见上表，现场语音限制仍保留。
- 历史状态矩阵见 `specs/164-state-matrix-regression.md`，本文初始知识索引见 `specs/165-project-knowledge-reference.md`。历史私有回执和完整失败证据已另行备份，不作为公开复现安装输入。

</details>

## 后续工作方式

- 先把问题归类到状态、计时、连接/媒体、外部回调、事务、权限、Jobs、Web 恢复或部署证据之一。
- 先写能复现失败的测试，再改最小代码；同时检查相邻状态和 timer，避免修复 A 引入 B。
- 每次逻辑变更后运行对应专项，再运行 Core/Jobs/Web 全量和 contracts/build/browser 门禁。
- 最终答复分开写“本机通过”“生产通过”“真人待验收”，不能用静态测试替代现场证据。
