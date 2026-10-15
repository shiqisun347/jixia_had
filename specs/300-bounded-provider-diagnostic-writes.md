# Spec 300 — Provider 诊断异步有界落库

- 版本：v2.0
- 状态：2026-10-13用户明确批准；本地实现、构造及尺寸预算、实际PG／Unicode导出、临时Linux限额补验通过；2026-10-15随1,564门禁完整流程版本受保护发布，0046升级完成；真实长期压力仍待验收
- 日期：2026-10-13
- 依据：Spec 214 的脱敏 Provider 研究采集与故障隔离契约；TechDesign §12.2、§19；Spec 217 的身份信封；Spec 273/274 的流收敛及事务隔离
- 授权边界：用户回复“批准”明确授权本规格的新表、writer、冻结投影和拥塞策略；不扩展至 Spec 299 调度政策或真实非终态房间／比赛处理。

## 问题与现状证据

TechDesign §19 要求非权威诊断经有界内存队列异步批量写入 PostgreSQL，诊断失败只重试
一次，不阻塞 ASR、LLM、TTS 或播放，也不暂停比赛。批准前工作树只部分满足这一要求。
以下为实施前的源码基线，实际隔离 PostgreSQL 风险证据另见 Spec 272；不代表
这些路径已在生产完成故障注入或压力验收。

Spec 272 的独立本地 PostgreSQL 六类显式探针最终 40 项：20 控制通过、20 风险失败；
其中五条采集路径 36 项连续两轮均 18／18。模型已返回后，管理／裁判／预览／主持
业务结果仍可被 raw capture 阻挡；ASR capture 持有 Match/Speech 锁时，实际暂停
无法提交／广播。另有两项导出正文／hash 冻结风险。这些保留的红检是本规格的待修复
验收输入，不是本规格实现通过证据，详见 Spec 272 的逐项结果与边界。

事实来源漂移：已批准且上线的 Spec 214 §3 明确允许受控的脱敏 Provider 原始 JSON、
允许列表 headers、关键事件和聚合响应；原 TechDesign §12.2／历史 PRD §14.3 的
笼统禁止表述遗漏此例外。本规格继承 Spec 214 的明确授权，不把整条 SSE/token 流、
ASR interim、二进制音频或凭据纳入例外。纯文档对齐不授权本规格的新表、writer 或
拥塞策略；业务实施依据用户对本规格的明确批准，PRD 当前缺失路径不恢复或推测改写。

| 位置                                                                                                                             | 当前行为与事务边界                                                                                                                                                                      |
| -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/core/src/jx_core/data_capture/provider_persistence.py::persist_provider_capture`                                           | 调用者提供 session/ExternalCall；先 flush 权威修改，再用 savepoint 存可选原始 request/response，仍须等待存储。回滚后 refresh 的修复隔离普通存储错误，不能隔离永久挂起。                 |
| `apps/core/src/jx_core/agent/runtime.py::_bounded_provider_capture`、`_mark_generation_llm_ready`、`_mark_generation_failed`     | Agent 正文成功/失败已先提交 generation、完整业务 response blob、指标与调用结果，再独立 session 至多等待 0.5 秒保存 raw capture；调用者仍等待该预算，忽略取消的 child 可能晚于预算结束。 |
| 同文件 `_finish_external_call`、`_fail_external_call`                                                                            | 决策/TTS 的调用结果与 raw capture 仍在同一调用事务；外层局部预算不等于共享异步 writer。TTS 正式 finalizer `_persist_final` 还会锁关联 ExternalCall 写播放指标。                         |
| `apps/core/src/jx_core/admin_routes.py::test_model_connection`                                                                   | 模型测试失败/cancel 路径只有局部诊断预算；成功路径仍在调用结果和审计事务内直接 await raw capture。                                                                                      |
| 同文件 `regenerate_voice_preview`                                                                                                | 成功 raw capture 与调用结果、VoiceProfile 校准及审计同事务；失败 capture 在失败标记事务内直接 await。                                                                                   |
| `apps/core/src/jx_core/postmatch.py::PostmatchService._execute`                                                                  | JudgeResult 完整业务 response blob/解析结果和 ExternalCall 成功指标同事务，其中直接 await raw capture；失败调用事务也直接 await。                                                       |
| `apps/core/src/jx_core/matches/service.py::persist_asr_capture`                                                                  | 先校验当前 callback 信封，事务内再锁 speech/关联行复核，随后直接 await capture；不能简单把这段带 ORM/session 的 coroutine 放进后台。                                                    |
| `apps/jobs/src/jx_jobs/host_tts_worker.py::process_one_host_tts`                                                                 | 主持音频任务先复核 rule/generation/task claim，再在状态汇总事务内直接 await raw capture。                                                                                               |
| `apps/core/src/jx_core/data_capture/diagnostics.py::DiagnosticWriter`、`apps/jobs/src/jx_jobs/runtime_logs.py::RuntimeLogWriter` | 已有普通运行日志队列，但未接入上述 Provider payload；本规格不把现有日志队列等同于 Provider writer。                                                                                     |
| `apps/core/src/jx_core/data_capture/content.py`                                                                                  | 权威业务与 raw capture 目前复用 content blob 存储及全局压缩名额；队列迁移须避免非权威压缩占满权威准备资源。                                                                             |
| `apps/core/src/jx_core/admin_data_routes.py::_call_view`、调用详情；`apps/jobs/src/jx_jobs/export_worker.py`                     | 现有管理员日志与导出直接读取 ExternalCall 的 legacy capture 字段和 blob 指针，需要统一兼容读取投影。                                                                                    |

AgentGeneration、JudgeResult 的完整业务 request/response blob 是受控实验数据，不能丢弃
或截断。ExternalCall 的 status、错误、关联、Token、时间和播放指标也是已提交调用结果。
Provider 适配器生成的脱敏 request/response 信封、关键原始 JSON 事件、request id
和捕获校验值才是本规格的
非状态权威研究采集。它们受 Spec 214 §7 的采集失败隔离约束，同时仍是 §2.4 的长期
业务／研究数据，不能归为普通可清理运行日志。二者不能按“都叫 blob”统一改变内容、
保留期限或权限。下文仅将新增采集的写入失败与业务结果解耦，不删除既有采集数据。

## 范围与不变量

1. 新增 Core/Jobs 复用的 Provider capture writer 实现；每个已有进程各一个实例，使用
   同一 PostgreSQL，不新增服务、队列基础设施或跨进程调度。Core 在 lifespan 创建并注入
   Agent、MatchRuntimeManager、Postmatch 和管理路由；Jobs 在已有 CLI 生命周期创建
   并注入主持 TTS worker。调用者不得按请求、比赛或音色临时创建 writer。
2. 迁移上述 raw capture 路径；权威 generation、Speech、ASR segment、JudgeResult、
   ExternalCall 结果、主持 asset/task/rule 状态和成功/失败审计仍按原事务提交。完整业务
   blob 保持现有模型、内容和事务语义。权威 flush/commit 失败仍传播，不被队列吞掉。
3. 在原权威事务内完成已有 callback/claim 检查，形成不可变的采集接受证明；仅在
   该事务成功提交后投递证明及对应 raw snapshot，不在提交后重复验证已经结束的
   当前 claim／Actor 动作。过期结果在提交前仍按现有身份规则拒绝，回滚不投递。
   入队不等待数据库、容量或后台写完；投递拒绝只产生诊断缺口。事务回滚不能留下
   可见诊断或触发后续 Agent/ASR/TTS 回调。没有 ExternalCall 行的局部 start 诊断失败
   仍报告聚合缺口，不伪造调用成功或重新调用 Provider。
4. 不改变认证/权限、Agent 发言及 ASR/TTS 重试次数、模型/音色、比赛状态机、计时、
   媒体协议或回调身份规则。本规格仅替换 raw capture 的等待与存储所有权；Spec 274
   的其他清理、失败和 stream 后容量释放继续保留。
5. 普通 journald/SystemLogEvent、研究业务日志、审计日志不迁入本队列，也不顺手重构
   已有 DiagnosticWriter/RuntimeLogWriter。此切片不声称完成 TechDesign §19 的所有
   可观测性要求。
6. 继承 Spec 214 的适配层真实采集定义、完整安全检测、脱敏允许列表及 request/response
   各 2 MiB 上限。正常容量内的采集内容、schema 和关键事件不得缩减为业务摘要。
   已持久化正文／索引长期保留；比赛调用只随该比赛显式删除，非比赛调用的历史清理
   仍需另立规格。管理员详情权限与追加式查看审计、比赛研究包范围和冻结截止点保持
   Spec 214 契约。拥塞时新采集拒收／淘汰策略属于本规格已批准的新决策。

## 独立存储与兼容读取

追加一条新 migration，建立 `provider_call_captures`；实施时根据届时 Alembic head
分配 revision，不改既有 migration，不回填或覆盖历史数据库数据。

- 以 `external_call_id` 为唯一关联，FK 引用 ExternalCall，随调用正常删除而级联删除；
  保存 capture revision、Provider schema version、captured_at、Provider request id、
  request/response blob 指针、状态/字节数/hash、脱敏 capture error。
- raw blob 继续复用现有压缩、校验和受权限控制的 content blob 格式；压缩准备在纯诊断
  自有资源内、数据库事务前完成。不得抢占权威 content preparation 的全部名额，不
  使用无界 executor 提交队列或取消后不断新增线程。Provider 的外层 capture wrapper
  保持与完整业务 blob 的身份分离；验证真实调用者的内容地址写入不会阻挡业务 blob。
- 新 capture／导出快照的 request/response blob 指针进入统一引用检查。比赛显式
  删除先收集该场旧业务／legacy／新 capture／该场导出快照的有限候选指针，正常
  级联删除所属行后，只回收全库所有这些引用均已消失的 immutable blob。其他比赛、
  非比赛 capture 或仍有效快照共享的内容必须保留，不能因“本场删除”一并删掉。
  导出快照按既有任务边界删除后也执行同一无引用回收，不单独改变保留期；兼容
  回滚构件必须保留这份新增引用检查，不能恢复只检查三个 legacy 业务表的旧清理。
- writer 只插入/比较更新 capture 独立行，不锁 generation、Speech、segment、JudgeResult
  或 ExternalCall，不改这些行的状态、错误、完成时间或完整业务 blob 指针。
  FK 插入可能取得 ExternalCall KEY SHARE，必须用真实 PostgreSQL 验证它与现有非 key
  权威 UPDATE 兼容；不能只凭“独立表”推断锁完全隔离。
- 管理员列表、详情和导出使用同一读取投影：新 capture 行优先；没有新行时保留已有
  legacy capture 读取。同一 migration 仅给 ExternalCall 追加 nullable
  `capture_storage_mode` 标识：NULL/`LEGACY` 沿用历史读取，新路径在原创建或结果事务
  中设置 `ASYNC_V1` 常量，不需要另一次数据库调用。`ASYNC_V1` 无 capture 行时投影
  为 `UNAVAILABLE`/`capture_not_persisted`；不回退读取该调用的完整业务 blob 充当
  raw Provider 响应，也不增加同步 raw 写入。此标识不改变公开 DTO 或捕获 payload 的
  `PROVIDER_CAPTURE_VERSION`。
- 保持现有公开字段、权限和 OpenAPI 契约，不手写重复 DTO。历史读取不依赖新队列
  是否开启。缺口行不能覆盖已完整持久化的 capture；完整同 revision 重复写幂等。
  列表／导出纳入资格同时识别 legacy `capture_version` 和 `ASYNC_V1`，否则 writer
  未落库的调用会被旧过滤条件排除，无法显示要求的逐调用缺口。该扩展不把 Spec 214
  上线前、两种标记均缺失的历史调用加入新日志页面。

### 导出截止点与投影冻结

异步 writer 的入队／`captured_at` 不是数据库可见时间；不能仅以调用 `started_at`
筛选后读取最新 capture。导出创建时须冻结该时刻可见的 call、capture revision、
两侧状态／blob 指针／长度／hash 及缺口。截止点之后提交的 capture 或 revision
不进入该次导出；重试读取同一冻结投影，不能等待 writer drain 后补成更完整的历史包。

为此同一追加 migration 增加 `export_provider_capture_snapshots`，唯一关联
`MatchExportItem` 与 call id，保存上述有限投影 metadata，不复制正文或身份自由 JSON。
该表属于既有导出任务的冻结输入，沿用导出保留／删除边界，不能替代或清理长期 capture。
创建服务在明确的同一 PostgreSQL MVCC 快照中，将新旧混合投影和导出项一并提交；
使用一次明确的 REPEATABLE READ 创建事务，隔离级别在第一条 SQL 前设置，不能在
依赖已经打开的认证事务中途更改，也不能提交认证事务作为快捷方式。冻结查询按既有
每场调用行数及导出总数硬上限执行，超限返回已有可操作错误，不把大正文装入任务 JSON。
失败整体回滚，不生成缺失冻结输入的可运行导出任务。Jobs 只读取这份不可变投影和
对应 immutable blob；blob 引用回收须识别快照引用，避免导出重试前被提前回收。

历史导出任务没有该快照时保持原 legacy 读取，明确标识冻结能力版本，不能把旧任务
推测回填为已冻结新投影。具体新增列／索引及 Core/Jobs 兼容读取均在本规格批准范围，
相关旧导出漂移实测单独记录于 Spec 272，不冒充新 writer 验收。

否决继续在 ExternalCall 上保存长诊断事务的方案：即使先异步，raw blob 的挂起仍可能
持有调用行锁并阻挡 TTS 正式 finalizer 或其他结果更新。“独立 blob 后短 CAS”只能缩短
这种共享锁，不能提供本规格要求的权威隔离。因此本规格包含独立 capture 表及投影。

## 有界快照、排队与优先级

以下为已实现默认值；本地合成测量与真实 staging 校准分别记录，可以下调，不能擅自放宽上限。

| 项目                | 硬上限                                                                                                                                    |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 每进程 capture 项数 | 64，含已入队、inflight 及重试保留项                                                                                                       |
| 每进程 capture 字节 | 总计 16 MiB：不可变快照/inflight/重试共享 8 MiB，编码/压缩临时 workspace 独占至多 8 MiB；不能借释放队列计数重新使用仍被 task 持有的 bytes |
| 单项                | request/response 各沿用适配器 2 MiB 上限，允许至多 64 KiB 受限包装 metadata；总项 4 MiB + 64 KiB，超过则明确缺失                          |
| 单批                | 至多 8 项、4 MiB + 64 KiB；不足一批至多等待 50 ms                                                                                         |
| writer 数据库并发   | 1；自有固定连接/压缩资源，禁止超时后无界补 worker                                                                                         |
| 单次数据库批次      | 至多 1 秒绝对预算；锁等待至多 100 ms，SQL statement 至多 750 ms                                                                           |
| 诊断落库尝试        | 最多 2 次，同一快照只重试一次；两次之间至多 50 ms                                                                                         |
| 关闭 drain          | 停止接收后共享 3 秒绝对预算，包含 flush、取消及资源收尾                                                                                   |

预算必须按真实保留 bytes 记账；不能只统计队列长度或伪称 JSON 字节等于 Python RSS。
压缩前后短暂共存、batch 引用及被取消任务仍持有的对象必须计入上述明确 workspace
及保留预算。writer 不把排队的 bytes 无界解码为大 dict；blob 写入复用既有格式/校验，
直接处理已验证的有限 bytes，避免重新复制完整原始对象。超出 workspace 直接记缺口，
不能为了释放满队列而临时突破字节上限。
不得先无界 deepcopy/编码原 dict 再拒绝；适配器在现有采集边界产生有限、已脱敏的
不可变 bytes/metadata，队列只接管此快照。队列不保留 run、session、ORM、回调、密钥、
完整业务 history、媒体或自由引用链。快照构造的 CPU/延迟也在验收范围内。
在适配器额外采集缓冲／编码前，必须从同一进程预算非阻塞预约容量；构造中的 buffers、
bytes 与接管后的 queued/inflight bytes 共用记账。没有预约则直接生成有界 UNAVAILABLE
事实，不先生成完整 bytes 再拒收；接管转移预约，拒收／异常释放，取消后仍被持有的
内存继续计入。权威业务对象不因此截断，但研究采集新增的对象／编码重叠必须计入
预算，不能利用“尚未入队”形成未记账的并发峰值。

- 错误/取消 raw capture 和缺口 metadata 优先于成功 raw capture，成功 raw capture
  优先于同 call/revision 的重复采集项。权威调用结果及状态转换永远在原事务，不进入
  可丢弃队列；该优先级不授权丢弃正常容量内的唯一研究采集。
- 满载时可先合并相同 call/revision 的重复项，再移除低优先级项为错误腾出空间；没有
  可移除项时直接拒绝并记缺口，不等待容量。有限队列不承诺保存所有 raw 错误 payload。
  此策略只适用于尚未持久化的新快照，需要本规格明确批准；不授权清理既有研究数据，
  不将正常、未超限的研究采集内容视为任意可丢弃的重复日志。
- 缺口 metadata 也必须有计数/内存上限；不能另建无界“待落库缺口 ID”集合。无 capture
  行/未完整捕获的投影本身可表达逐调用缺口，丢弃原因再由有限聚合计数说明。
- 固定服务/原因枚举聚合 accepted、dropped、persist_failed、stale、shutdown_drop、
  writer_unavailable 及 bytes/水位。向 journald 以有界频率写聚合计数，正常运维日志
  不含 payload、发言内容、凭据或 Provider 原文；不用每丢一项写一条日志的洪泛方式。
  后台日志投影显示逐调用缺口及聚合诊断，不能把缺口记成业务调用失败。

## 身份、批次失败与生命周期

1. 快照只保存可信 call id、来源、logical_call_id、capture revision 及适用的 match、
   speech、generation、attempt、decision/task、epoch、context、opportunity 关联。
   无此身份的配置测试明确使用其资源关联及空值，不伪造比赛身份。
2. 生产者在权威事务内沿用现有 current callback/claim 验证，形成接受证明，提交
   后只非阻塞投递，不要求已清空 lease／完成 task／推进 Actor 再通过旧当前检查。
   writer 使用普通 MVCC SELECT 核对
   已提交历史 ExternalCall 与其不可变关联，不复用 `_fenced_rows`、ASR 行锁 helper
   或当前 lease 校验。不得以捕获旧回调为由更新权威状态。合法入队的第一次失败
   采集在第二次尝试成功后仍须保留，不要求当前 task claim／attempt／Actor 动作
   等于历史值；同身份 STARTED→成功/失败、PLAYING→FINALIZED 不等于过期。
   原 call 删除、身份关联改变、同 revision 内容被替换的项拒收；不复活删除行。
   完整身份检查的适用矩阵必须先列入测试。
3. 每批自有 session/事务，调用者不参与。每项 capture 行采用条件更新/幂等插入；
   单项无效不会回滚已提交业务结果。批次异常只在诊断内重试一次，业务 Provider
   请求数、模型结果与 Agent/ASR/TTS 的重试和暂停逻辑不受影响。未知 commit 结果的
   重试也不能生成重复 capture 或改写完整内容。
4. 单次 batch deadline 覆盖数据库、Python 存储边界及取消等待，不能直接使用会
   无限等待 child 响应取消的 `wait_for`。同时存活的写入 task/连接/压缩任务数固定。
   若旧写入忽略取消，则隔离该 writer 并拒绝后续诊断，消费其迟到异常；不得不断
   启动替身并累积 session。尚被旧 task 持有的 bytes 仍计入预算，权威路径继续工作。
   仅隔离 task 不足以释放 FK KEY SHARE／blob 唯一键锁：writer 对独立连接有明确的
   强制关闭／失效所有权，deadline 后执行经真实 PostgreSQL 验证的本连接回收，
   不操作共享业务连接，也不将仍被旧 task 持有的连接归还池中。数据库侧同时设置
   statement、lock 及 idle-in-transaction 保护，覆盖 Python 存储边界吞取消；必须
   分别测量 task 返回、服务端事务结束及行锁释放，未证明有界锁释放不得验收。
5. 先停止业务生产者，后停止 capture 接收并在 3 秒预算内 drain，最后关闭自有资源；
   Core/Jobs 退出不用无限 Queue.join/gather。未写项如实聚合为缺口。关闭后不允许
   入队或创建新 worker，关闭幂等；迟到完成不能接管新进程、写错身份或覆盖新 revision。
   超时返回不等于 native socket/thread/连接已经物理释放，必须分别记录证据。

## 可验证切片与验收

先追加独立表及只读投影，再验证 writer，最后按 Agent→管理模型/音色→裁判→ASR→Jobs
接入真实调用者。每次只实现一个可验证切片，失败先修复，不用调用者 mock 隐藏事务问题。

- 空库升级及已有 legacy capture 数据升级通过；旧记录及完整业务 blob 不变，管理员
  分页、详情和导出新旧混合读取一致，权限/OpenAPI/contracts 保持原契约。
  覆盖 legacy／ASYNC 跨调用共享 blob、比赛显式删除、其他比赛／非比赛／冻结快照
  引用仍有效与最后引用删除，验证正文保留和回收闭环及兼容回滚清理。
- 导出创建前已有 capture、创建前入队但之后提交、之后 revision 变化、同 export
  重试均验证冻结正文／hash 一致；创建失败不得留下可执行但无快照的任务。缺口
  不随晚到 writer 被补齐，导出不等待 drain；legacy 任务明确保留其兼容边界。
- 有效、敏感拒绝、过大、队满、低优先级挤出、重复、取消、超时、关闭、late result、
  批次 commit 未知/失败均有专项；item/bytes/inflight/retry/关联集合与 task 数始终有界。
  忽略取消的写入至少重复 20 次投递，不能增长替身 task、线程或连接数。
  队满时并发最大尺寸采集须覆盖构造预约及原对象／bytes 重叠，实测峰值而非只看队列。
  Jobs 首次失败已排队→第二次成功→首次采集落库验证历史失败不被误判 stale。
  事务提交后 claim 清空／task 成败落定／Actor 已推进时，已接受证明仍能保存研究
  capture；提交前过期回调及权威事务回滚均不得投递或改变原失败语义。
- 真实 PostgreSQL 中挂起 raw payload 存储，同时从另一 session 提交 generation 失败/
  ready、TTS final、ASR segment、JudgeResult 及调用结果；证明不被 capture 长事务行锁
  阻挡。单独验证独立表 FK 与 authority UPDATE、同内容地址、诊断压缩阻塞等资源边界。
  FK／blob 唯一键锁取得后让 Python 吞取消，从另一连接执行调用更新、显式删除和
  同内容写入，验证服务端事务及锁在明确预算内结束，不能只证明固定 task 数。
- 注入 raw 普通异常、真实 SQL 错误、一次/二次失败和取消：业务结果先提交、调用者
  不等待 writer、最多一次诊断重试，原 Provider 请求数、错误和审计不变。完整业务 blob
  或权威 commit 失败仍失败并回滚，不计为诊断成功或触发不可提交的后续回调。
- 实际 Agent/manager/Actor/PG 的 503、首正文超时、截断 EOF→原两次尝试→ERROR→自然
  恢复→发言→Speech/Asset/Generation FINALIZED→FINISHED；再验证成功 LLM 不因 raw
  hang 重试。保留原错误、完成时间及身份栅栏，不能只证明 task 结束。
- 实际管理员模型 SSE、音色预览、裁判、ASR callback 和 Jobs 主持 TTS 的成功/失败
  链注入 raw hang/异常：各权威结果与公开返回正常，队列拒绝仅显示诊断缺口。
- 关闭阶段覆盖正常 drain、数据库失联、永久 Python 挂起、重复关闭与迟到完成；记录
  真实连接状态，不把合成对象的 close 次数当成物理资源释放。
- 各切片对应测试、隔离 PostgreSQL、Core/Jobs 回归、Lint/typecheck、contracts 通过；
  读投影改变后运行相关 Web 日志/导出测试和浏览器验证。生产构建只运行仓库 `pnpm build`。
- staging 对 16 MiB 默认预算测 RSS、编码延迟、CPU、DB 连接/锁、水位、丢弃量及业务
  发言尾延迟，再决定是否下调。合成洪泛不代表 50 路真实 LLM、五场真实媒体、真人
  180 秒 ASR、11 音色盲听或生产长期稳定性；这些项目当前均未在本规格执行。

## 回滚与发布边界

本规格已批准并在本地实施。生产部署继续
遵守非终态房间/比赛门禁和受保护窗口，不在真实比赛注入故障或以终止比赛绕过门禁。

本次兼容回滚构件是同一版本的 Core/Jobs，将 `PROVIDER_CAPTURE_ENABLED=false`：
停止构造 writer 和新增 raw 采集，保留新表／capture mode／导出冻结输入的读取投影及
统一引用清理。新调用的权威结果照常提交，缺口明确显示；已有 ASYNC_V1 有行／无行、
legacy 混合记录与冻结任务继续读取。不得直接退回把业务 blob 当作 raw 的旧详情／worker，
或使用不识别新增引用的旧清理代码。已追加表及数据保留，默认不 downgrade、不删除
历史数据。隔离 writer 不等于切换同步采集、模型或供应商；开关应用继续遵守发布窗口。
未经兼容补丁验证的旧软件构件不能作为本规格回滚版本。

## 已批准实施范围（2026-10-13）

- 追加 `0046_provider_capture_snapshots`，不回填历史记录；独立 capture、导出冻结
  metadata 和 `ASYNC_V1` 标记落入 ORM/统一读取投影。管理员公开 DTO 和契约不变。
- Core/Jobs 各一个可关闭的 writer；生产者在原事务验证身份和形成 proof，权威提交后
  非阻塞移交。无 writer 时只留下明确缺口，不调用旧同步持久化 helper。
- 编码预约 7 MiB workspace，固定压缩 executor 根据有限 raw bytes 预约 workspace；
  总记账维持 8+8 MiB；递归排序 key 引用有 1 MiB 子预算，超限明确 UNAVAILABLE。
  固定环形尾部、流式 bytearray 和 UTF-8 增量解码避免 tuple/deque/join/整串解码
  的额外无界复制；完整敏感扫描仍先执行。队列、构造、inflight、重试和迟到任务共用 reservation，
  普通错误至多重试一次，超时强制回收独立连接并隔离 writer，不启动替身。
  完成后清 submission 原始字节、已完成 Future 和 traceback 的引用；取消后原线程
  仍活着时保留原 reservation/workspace，实际完成后才释放。
- 独立 INSERT 在最终 MVCC 语句重验历史身份；每项 savepoint 回滚拒收项的 blob，
  同批其他合法项继续提交。已完成 lease／Actor 推进不使历史接受证明失效。
- 新导出在独立 REPEATABLE READ 事务冻结 call/raw 投影，首条 SQL 前设置隔离级别；
  无 Match FOR UPDATE，不等待 writer。每场 4,096 calls／总计 100 items 有界。
  marker=1 即使零 capture 行也不补读最新数据，旧 marker=NULL 明确保持兼容边界。
  本切片冻结 Provider JSONL／两侧原始正文及 metadata，不声称整个 ZIP、manifest
  或实时比赛状态／转录也被冻结。
- 显式比赛删除先收集至多 65,536 个唯一内容候选，再按五类全库引用回收；超限在
  删除前返回 413 `content_cleanup_too_large`，不删除其他比赛、非比赛采集或有效快照。
  导出 7 天下载到期仍是既有可下载性边界；当前没有导出删除后台任务，本规格不把
  到期标记冒充已自动回收，也不改变 raw 研究保留期。
- 音色预览异常路径确定释放未提交 submission；ASR 无接收方/无信封也关闭 capture；
  Core/Jobs 启停异常释放已经启动的 writer。自由决策的解析候选仅在权威调用提交后
  成为可返回结果，权威失败继续原重试及失败回调。

本地最终验收及真实 staging／生产执行边界在下文补充；不能将合成采集或本地
PostgreSQL 证据标作真实供应商、真人媒体或生产压力验收。

## 主体基线验收（2026-10-13，预约精度补修前）

| 门禁               | 结果与边界                                                                                                                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm test`        | Node tooling50、QA36、Web231、Core非集成789、Jobs非集成61 全通过                                                                                                              |
| `pnpm test:db`     | 严格独立本地QA库，Core176、Jobs5串行通过；总标准回归1,348，不累加专项复跑                                                                                                     |
| 六类显式故障探针   | 同代码连续两轮44/44，14.93s／16.21s；从原40项20绿20风险红修复并增4项栅栏/回滚                                                                                                 |
| 模式／投影／writer | 空库/legacy升级、9项projection PG、15项管理员混合reader PG、20项writer PG、6项导出snapshot PG通过，均已包含Core176                                                            |
| 真实连接与锁       | writer独立连接、FK非key authority UPDATE、唯一blob冲突、吞cancel后服务端事务结束及锁释放、过期单项savepoint无孤儿且同批合法提交通过；不据此声称LiveKit/ASR/TTS native全部收尾 |
| 资源释放           | 构造预预约、大map/短chunk/emoji峰值、实际固定线程取消、idle已完成submission/Future真实引用释放、至少20次late offers不扩worker通过；永久内存回归16项已包含Core789              |
| Web                | 相关日志/导出4项单元已含Web231；现有浏览器248通过/12退役skip、Storybook94通过。浏览器为合成路由边界，有既有unmocked Core代理ECONNREFUSED日志，不算实际生产混合投影端到端      |
| 构建               | 仓库 `CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build`通过，完整standalone/37静态路由；无裸next build                                                                        |
| 静态门禁           | Lint、Web/Contracts/Python类型、contracts、格式与diff检查通过；新增测试不依赖未声明的PYTHONPATH                                                                               |

本地合成profile为132个已提交权威结果，sustained32/32采集接受、flood80/100接受，
20项明确容量缺口；字节水位14,204,962B（13.547MiB）、items64，结束时队列／保留／
workspace均0。sustained编码p99 5.273ms、offer p99 0.015ms；关闭drain462.984ms。
整个测量进程RSS峰值72.547MiB、baseline峰值64.109MiB，不能表述为整个Core/Jobs
只有16MiB。结构化结果和instrumentation清单在本地忽略缓存
`profile_output/spec300/results.json`／`report.md`，未写入业务文件或提交缓存。

实现过程曾发现并修复旧同步故障夹具挂起、legacy raw字段断言漂移、catalog缺两新表和
migration导入路径依赖；这些setup/fixture失败不计产品风险。另经红转绿确认自由决策
在权威提交失败时提前返回解析值，现按原两次尝试及failed callback处理；预览提交异常
释放丢失、ASR缺接收方的buffer cycle、writer完成Future/raw对象保留、排序/尾部/编码
复制峰值和dotted凭据拒存均有保留强断言回归。profile汇总脚本的SQL/Decimal格式错误
也单独修正且owned-row finally清理，不计正式测量或业务故障。

最新生产只读预检：四服务active、live/ready200，DB仍0045，有原PAUSED比赛
`2dce08b6-b4f9-40c5-b99a-3e01eef0ba28`及1个PAUSED/2个WAITING房间，leases0。
远端旧预检migration固定值漂移不能代表实际head；候选0046未部署。没有生产migration、
重启、状态变更、故障注入或发布。按Spec298门禁保留原比赛/房间，未擅自终止以绕过。

实际后台混合投影浏览器与同版本flagoff本地兼容验收已在下节补齐。真实staging的
RSS/CPU/DB连接锁/业务发言尾延迟和MemoryHigh/Max校准、同版本flagoff后的受保护
线上验证、发布后稳定性仍待执行。flagoff不是
旧binary回滚，未经兼容补丁的旧构件不能直接部署；历史50/50供应商短探针不验收本版
50路流式并发或完整媒体。五场完整媒体、180秒真人ASR、11音色盲听及Spec299公平调度
也未由本规格完成，不能用本地合成/PG绿检替代。

## 隔离实际 Core／Jobs／浏览器补验（2026-10-13）

该轮没有修改冻结业务源码，也没有读取项目 `.env`／`api.md`。独立本地 QA 数据库
从空库升级至 0046，使用合成账号／正文；Core 明确关闭真实 Provider／LiveKit，
Web 直接运行上节 `pnpm build` 的完整 standalone，实际 HTTP proxy 指向本地 Core，
不使用接口 mock。以下是实际应用与数据库证据，不等同于目标服务器 staging 或真人媒体。

- 真实 AuthService/cookie 登录、54 条混合日志两页 50/4、来源筛选回第一页、legacy
  原始正文、ASYNC_V1 完整正文及明确缺口通过。缺口的两侧正文与 raw 指针均为空，
  `capture_not_persisted` 可查询；完整权威业务 blob 保留。真实详情访问审计落库。
- Jobs 专用合成 FINISHED 比赛新增三类调用后，总列表 57 条。浏览器通过单场导出
  UI 完成 preflight／确认／创建，真实 Core 原子冻结 seq3/context2/marker1 的三条输入。
  创建后真实 writer 补齐原 gap 并修改 live call status/error，冻结 JSONL 仍保持原
  SUCCEEDED/null 与 UNAVAILABLE 缺口，前后 hash 完全一致。
- 实际 Jobs worker claim／ZIP／item／task complete 通过，只执行一次，SUCCEEDED 1/1。
  UI 点击下载真实 HTTP 200；浏览器 cookie fetch 下载 bytes 并独立检查 ZIP／JSONL：
  2631 B，ZIP SHA-256 `f1df30df037fb6591bf344b64648bb89ad1a41fd810541e1eae5908c5c190e9c`，
  Provider JSONL 三行 SHA-256 `e0c3009f56053801c2786a3d582bbfbb27fc441bc9a0095479791de096197766`。
  未验证操作系统保存对话框，也不声称全部 Match／transcript／ZIP 字段冻结。
- 普通 USER 的真实浏览器显示“需要管理员权限”；列表、raw 详情及导出下载均 403。
  本轮实际页面无 JavaScript exception。
- 仅重启自身隔离 Core 为同版本 `PROVIDER_CAPTURE_ENABLED=false`，真实 lifespan 断言
  writer=None；浏览器再开 legacy／ASYNC complete／gap、HTTP／PG 权限和审计通过。
  历史冻结导出仍 HTTP 200／2631 B，ZIP 与 JSONL hash 完全不变。未回退旧 binary 或 downgrade。
- 两个自身浏览器及 QA Core／Web 已关闭，端口 8100／4100 无监听；保留独立合成 DB
  与忽略目录证据供复现。临时 harness Ruff／格式／编译检查通过；本轮不追加标准回归计数。

证据及逐项浏览器截图位于忽略的 `profile_output/spec300_browser/report.md`，后端
`profile_output/spec300/browser_backend_{check,flagoff_check}.json` 与
`export_e2e_state.json`。测试夹具的唯一 ADMIN 初始化冲突、探索性错误 HTTP 方法及
错误 wait 文案已与产品问题分开。另发现低影响界面文案：中文比赛数据页顶栏显示
`matchData`，截图可复现；属于既有翻译待修项，未扩本规格修改无关界面代码。

本轮生产只读 SSH：Core／Jobs／Web active、live/ready200；Core cgroup current
158,679,040 B／peak213,200,896 B，Jobs current58,683,392 B／peak152,641,536 B。
两服务 MemoryHigh/MemoryMax=infinity、nofile soft1024/hard524288；未安装 drop-in、
重启、写比赛状态或发布。瞬时健康／RSS不能替代压力校准或排除历史OOM。

## 构造预约精度补修（2026-10-13，实施前记录）

实际本地合成 OpenAIStreamingClient／SSE 按 24,000 字历史、当前固定/自由稿预算
50 路 × 6 轮返回 300 个正确业务结果，但 600 侧只有 276 个请求 COMPLETE，
24 请求及 300 响应 UNAVAILABLE，完整对为零。50 对完整 raw 加既定 metadata
预计 6,986,975 B，小于 retained 8 MiB；关键事件前 workspace=0。直接原因是每个
小侧/事件都在既有 retained 上再预约最大 2 MiB，并把临时拒收 latch 为整个响应缺口。
该结果是正常容量内的保守预约效率缺陷，不能以“300 accepted”标为完整采集通过。
另静态确认统一 7 MiB 编码预约与超过 1 MiB 的压缩 workspace 共存时，小内容或
tiny terminal 也会误拒；必须用独立强断言验证，不能冒充上述压测已证明主因。

本补修属于已批准的构造/编码预算切片，不改变拥塞优先级或任何硬上限。先在有限
workspace 中流式计数，不 deepcopy、不排序大 mapping、不先生成完整 bytes；按
可信 UTF-8 JSON 上界预约新增 retained，按含排序引用、编码重叠和 overflow fragment
的保守上界预约 workspace。大内容仍使用现有最多 7 MiB 编码预算，所有路径仍受
8 MiB retained＋8 MiB workspace、64 项、单侧 2 MiB 与完整敏感检查约束。无法取得
真实所需预算时仍按原缺口策略拒收；不能用增加进程预算或切供应商掩盖误拒。

验收增加：50 对实际预算小 payload 完整、retained 约 6.3 MiB 时小事件正常、压缩
持有超过 1 MiB workspace 时小内容/terminal 正常，以及真实 workspace 满时明确
拒收。保留敏感尾部、高 key、最大侧、取消、关闭、late result 和事务栅栏强断言；
之后重跑实际 adapter 50×6 并精确分组两侧完整率、RSS/loop lag，再跑相关门禁。
本地合成测量仍不替代目标 staging、50 路真实 LLM 或真人/媒体验收。

同一补修还发现溢出片段的真实分配超预算：业务原字符串在 tracemalloc 前构造为
`🙂`＋超过 2 MiB 的 ASCII＋`🙂`，两侧 896 KiB 片段整串 decode 后都成为四字节
Unicode。实际新增峰值 11,107,534 B，高于编码预约 7,340,032 B，连同该侧 retained
预约的记账水位也仅 9,502,720 B。输出仍为 TRUNCATED/1,835,426 B，不能用输出字节数
推导构造内存安全。改进必须使用内部 UTF-8 bytes 片段与增量 decode/JSON escape，
保留原公开 fragment schema、真实首尾内容、hash 及原按字符缩片段语义；不提高预算。
增加混合 emoji、UTF-8 边界、二次 escape 缩片段与实际峰值强断言。

边界审查还要求截断循环有进展保护：当片段已无法缩短而包装 metadata 本身仍超限时，
返回明确的 `capture_wrapper_too_large`，不能在最后一个尾部字符上反复循环。
敏感内容扫描的键名规范化也在构造预算内。新增红检确认大 ASCII＋emoji 键的
整串 `strip/lower/replace` 新增峰值 33,558,615 B，超过 7 MiB；改为 trim 边界索引和
有限的键名比较，保留完整原键／值敏感检测和带任意首尾空白的敏感键拒存语义。

### 预约补修专项结果（2026-10-13，补修源码冻结后）

新增永久专项 19 项与既有编码专项 16 项合计 35/35 通过（64.22s），不追加到标准
回归重复计数。覆盖正常 50 对、临近留存上限的小事件、压缩 workspace 共存、真实
workspace 满载拒收、emoji 溢出、高键数／深树、源大小变化、敏感键以及二次 escape
字符缩片段。header／binary_summary 包装自身超限先以 5s 强制截止复现不收敛，
现通过最低包装尺寸和无进展检查返回明确缺口；不依赖最后一字符自行消失。

| 实际额外分配探针      |        峰值 | 对应构造预约 |
| --------------------- | ----------: | -----------: |
| tiny payload          |    15,256 B |  1,576,401 B |
| emoji＋ASCII 溢出     | 5,594,668 B |  7,340,032 B |
| 超长 ASCII＋emoji key | 5,594,666 B |  7,340,032 B |
| 深 127 层只计数       |    19,904 B |    524,288 B |

编码／计数都在申请预算后运行；小内容使用 512 KiB 有限计数，再申请
`1.5 MiB + 3 × encoded ceiling` 的保守 workspace，至多沿用 7 MiB。大／深／高键
场景继续用原最大预约。所有留存、item、压缩及单侧硬限额保持原值。直接敏感扫描
的巨宽键 normalization 对照为 33,554,508→4,809 B，长首尾空白包裹 PASSWORD 的
真阳性仍拒存；完整原键和值的正则扫描继续执行。

本专项 Ruff／格式／Pyright／编译通过。完整标准回归、实际 adapter 多轮资源复测
和新片段实际数据库导出补验仍须在同份冻结源码完成，下文以最终结果为准。

### 满载计数开销补验（2026-10-14，下一补修实施前记录）

第一版预约精度修复的 quiet 对照已完成：paper 50×6 的 300 对／600 侧及 50×30 的
1,500 对／3,000 侧全部 COMPLETE，硬台账和线程／队列收尾正常。但极端最大侧输入
50×6 的 body active loop lag 从旧基线 199.972ms 增至 908.620ms，SSE 从 394.943ms
增至 533.931ms；源构造与 HTTPX 本身也占用 CPU，不能把全部 lag 归因采集。
此结果不能写成所有输入均流畅或仅报告已解决误拒收。

继续在同一构造预算切片内验证容量不足时的有限计数：适配器／事件尚需申请新增
retained，计数可在上界已必然无法申请时提前拒收；字符串长度可作为无需拷贝的
UTF-8 JSON 字节下界。正常容量的完整采集、截断和敏感检查不变；只在不存任何
raw 的容量拒收时停止无效扫描。兼容 writer 已持有自身 reservation 的冻结计数
不能误套其他项占用后的“剩余 retained”门槛。此补修不改变拥塞优先级或任何硬上限。

先用实际大正文容量拒收的访问／扫描工作量红检复现，再验证 tiny 正常、真实满载、
最大侧单路及 50 路正常容量不退化，最后重跑 quiet 对照并记录最终源码版本。

### 最终构造预算补修与再验收（2026-10-14）

新增永久预约精度测试最终 29 项，加既有编码专项 16 项，45/45 通过（60.82s）。
容量仅余 16 KiB 时，三个公开采集入口的 2 MiB 字符串由扫描全部正文变为零切片；
128×1 KiB 组合由读取 131,072 B 降为 14,336 B。容量内敏感尾部拒存、正常完整采集、
writer 已拥有 reservation 的冻结及各路径释放仍通过。固定硬上限和错误语义未变。
根代理实际 native/future/batch 引用释放的三个永久测试已纳入最终标准回归。

最终冻结源码的标准回归通过 1,378 项：tooling 50、QA 36、Web 231、Core 819＋176
数据库集成、Jobs 61＋5 数据库集成。严格数据库集成分别为 101.55s／1.81s；Core
报告 12 条既有 Alembic `path_separator` 弃用提示，无失败。Lint、类型检查和 OpenAPI
契约检查均通过。专项与反复资源案例不重复累计到标准测试总数；显式探针和实际
应用补验的最终结果分别记录。六类显式故障探针在同份最终源码连续两轮 44/44
通过（17.67s／16.81s），没有重复累计到标准回归。

Web 源码和契约没有在本构造补修中变化；此前同一 Web 构件的 Storybook 94、浏览器
248（另 12 个历史退役 skip）和仓库 `pnpm build` 37 routes／standalone 证据继续适用，
此处不声称再次执行它们。实际新片段的 Core／Jobs／HTTP 导出补验单独核验。

Darwin 静机十个新进程案例与一个独立 FD 控制补验已完成，原始数据及 Part A／B
见 `profile_output/spec300_pressure/post_available/report.md`。实际 adapter＋合成 SSE
在 paper 50×6 全部 300 对／600 侧 COMPLETE，50×30 全部 1,500 对／3,000 侧 COMPLETE，
后者实际 zlib 处理 111,305,250 B／3,000 个独立 raw SHA；两侧最大单路 30 轮也全部完整。
结束 capture 预算／队列／线程归零。该 sink 不执行 SQL，不能记为 PostgreSQL 落库。

极端最大侧的 body active loop lag 从上一补修版 908.620ms 降至 235.190ms，SSE 从
533.931ms 降至 358.665ms；拥塞缺口仍明确且业务结果正确。paper 50×30 的采集开启／
关闭峰值 RSS 为 182,419,456／159,940,608 B，CPU 为 18.399／4.345s，开启时 active
max lag 61.494ms、adapter p99 127.288ms。极端 SSE 的进程 RSS 仍接近 1 GiB，关闭采集
也高；源对象、HTTPX、allocator 和未完全隔离的存活引用仍限制归因，不能由台账归零
推断整个进程无泄漏、内存已全部归还或真实比赛无卡顿。

原十例之一 paper-off 的最终 FD 读数为 9，附近及末轮为 7；取样器当时仍打开两个
`ps` 管道。只修复 ignored harness 的取样器收尾，在独立控制中得到 FD 7、线程 0；
原 JSON 保留且不替换原 CPU／RSS 数据。这是测量竞态的证据，不隐去失败断言。
Linux 限额补验、实际 PostgreSQL 和混合 UTF-8 导出补验以各自完成证据为准。

本轮业务源码冻结 SHA256：`provider.py` 为
`0e71e44b49ecbbd25a2fcc8c2b960ad75784465ae2d39e0284cb96f4627b6209`，
`provider_budget.py` 为
`a0eda2a43a1702b0c8f66d0143ba6a8e18976c170aacbf048b7ff26ade75283c`，
`provider_writer.py` 为
`4497353bcedf6027d73c5ccbed37c4acf26cd7e3b2c52d1dfa88e4f9a9e2adf5`。

### 最终实际 PostgreSQL 压力补验（2026-10-14）

独占本地 QA 数据库、真实 `test_model_connection` route 函数、MockTransport 最大
SSE 输入，采集开启／关闭各 50×6；此模式直接调用 route 函数，不声称通过 HTTP
服务器、真实 Provider、MatchActor 或媒体验收。确认既有 head 0046／借用 ADMIN，
不迁移、不改认证，结束仅清理本次 UUID 自有行及无引用 blob。

| 维度                                       |      采集开启 |      采集关闭 |
| ------------------------------------------ | ------------: | ------------: |
| 权威 SUCCEEDED 调用／SUCCESS 审计          |      300／300 |      300／300 |
| 实际 capture 行                            |           300 |             0 |
| request COMPLETE／response UNAVAILABLE     |      300／300 |        不采集 |
| 每侧 blob kind／编码长度／chunk count 一致 |        各 300 |        不采集 |
| owned backend 采样峰值（包含 observer）    |             7 |             6 |
| idle-in-transaction 采样峰值               |             5 |             5 |
| 采样到的 lock wait／ungranted lock         |          0／0 |          0／0 |
| 整个合成进程 RSS 峰值                      | 598,884,352 B | 654,000,128 B |
| active 最大 loop lag                       |     452.548ms |     334.149ms |
| 整个 route 调用 p99                        |   1,087.219ms |     533.442ms |

最大侧拥塞的 response 全为明确 UNAVAILABLE，不记为完整采集；正常 paper 容量的
完整性由前节另验。route p99 包含数据库、连接池及合成启动屏障等，不能当成实际
业务发言尾延迟；权威事务进行中的 idle-in-transaction 采样不等同悬挂连接，也未
测量逐连接驻留时长。零锁等待
只代表采样未发现，不能证明所有瞬间都无锁。结束自有 model／call／audit 均零，
借用 ADMIN 保留，authority／writer backend 零，FD 7、capture thread 0。

首次运行严格断言失败，未记录原始断言位置；失败摘要保留。审查发现新增 QA
aggregate 错把原始 `_capture_content` canonical JSON SHA 与 freeze 后完整存储
wrapper 的 blob SHA 当作相同值。这两种 hash 职责不同，业务字段没有修改。仅修正
无效跨表示比较，保留 kind／编码长度／chunk count 强断言，并补安全位置记录后，
开启／关闭重跑全部通过；不声称已现场确认初次失败的唯一原因。详细原始证据、
源码 hash 与 instrumentation changelog 见 `profile_output/spec300_pressure/pg_final/`。
实际 blob 解压和下载校验另由 Core／Jobs 导出链完成。

### 最终混合 UTF-8 实际导出补验（2026-10-14）

仅复用已拥有的独立本地 QA 数据库，启动当前 Core 8100，通过真实 Auth、HTTP
preflight／POST export／GET／download 和实际 Jobs 完成；不启动 Web／browser、不
调用真实模型或媒体。旧 export／state／ZIP 只读核验，新增独立
`profile_output/spec300/export_utf8_state.json`，不覆盖原 fixture。

legacy 与 ASYNC_V1 response 使用超过 2 MiB 的混合中文、补充 Unicode、emoji、引号、
反斜线和换行；独立标准 JSON 计算的原始字节／SHA、片段 hash 与 schema 1／TRUNCATED
均一致，公开 head／tail 是普通字符串。真实 writer 晚补 gap 并修改 live scalar 后，
冻结 JSONL 仍保持原状态和缺口；实际 Jobs 成功且单次 attempt，下载 bytes 与数据库、
API 和 ZIP 内容校验一致。旧 ZIP 2,631 B 及原 hash 严格不变。

- 新 export：`0104f7b4-3d1a-4eb0-88a5-ace7d16ae9e7`。
- 新 ZIP：12,464 B，SHA256
  `277ead60ca5778388f572df5760f422b64e4ce31e0a05e773c8b574930340ea7`。
- 冻结／晚写后／ZIP／HTTP 下载的 Provider JSONL SHA256 均为
  `75667d1f7f80dad2dd2acba5f44beec3c677595a88d759ff0ecceea2808c8307`。

仅修 ignored 工具的四处类型／可变 state 路径命名问题，Ruff／Pyright 通过，业务
三源码 hash 不变。owned Core PID 25240 经命令归属核验后正常 SIGINT、退出 0，
PID 消失，8100／4100 均无监听。新合成 fixture 和证据保留；不声称整 ZIP／实时
transcript／Match 字段全部冻结，也不将此次补验写成一场完整比赛或目标 staging。
证据见 `profile_output/spec300/export_utf8_http_results.json` 与相应独立 state／ZIP；
临时工具在 `profile_output/spec300/INSTRUMENTATION.md` 汇总。

### 临时 Linux 限额压力补验（2026-10-14）

使用固定 cached Python 3.12.14／arm64 base digest、uv 0.12.5＋frozen uv.lock，
154 文件白名单及三业务 hash 核验后建立独立 Linux venv。原工作区／宿主 venv／
环境文件／凭据不复制、不挂载。每例独立 owned container，2 CPU、768 MiB memory
及 swap 总限额、pids 96／nofile 256，readonly／nonroot／network none／无发布端口；
固定 source bundle 含 FD 采样器收尾修正，未混入随后 PG aggregate 工具变更。

| Linux 合成案例  | 完整性                  | 进程峰值 RSS | cgroup 峰值 |       CPU／wall |                  active max lag |
| --------------- | ----------------------- | -----------: | ----------: | --------------: | ------------------------------: |
| paper 50×6 off  | 300 业务结果；不采集    |  142.164 MiB | 104.012 MiB |   0.771／1.645s | 未采到超过心跳间隔的 active lag |
| paper 50×6 on   | 300 对／600 侧 COMPLETE |  160.117 MiB | 117.789 MiB |   3.640／6.719s |                        65.301ms |
| 最大侧 1×30 off | 30 次正文控制；不采集   |  140.473 MiB | 102.285 MiB |   1.747／5.619s |                         1.072ms |
| 最大侧 1×30 on  | 30 对／60 侧 COMPLETE   |  166.711 MiB | 124.508 MiB | 12.705／18.184s |                       243.836ms |

四个正常压力案例均 ExitCode 0／OOMKilled false／memory.events oom_kill 0；采集
开启两例 errors 0、ledger／queue／thread 收尾零、FD 7、采样器已停止且未隔离。
paper／最大单路的逻辑水位分别为 8,696,551／12,625,822 B，未提升 16 MiB 上限。
进程 RSS 与 cgroup charged memory 口径不同，不将二者差额当成泄漏或全部内存归还。
off 的零采集区间或未观测心跳迟滞不意味着真实业务零延迟。

采集开销仍真实存在：paper adapter p95 140.586ms（off 15.755ms）；最大单路
capture p95 253.648ms／active lag 243.836ms，不写成所有输入无卡顿。此补验只覆盖
合成正常容量、最大单路及诊断本身，不替代实际 ASGI／Actor／PostgreSQL 并行媒体
或目标 staging；Linux 本轮未运行 50 路极端正文 OOM 场景。

冻结 bundle 的 45 项纯内存／编码回归亦通过（84.22s，JUnit 45 tests／零 errors／
failures／skips），容器 ExitCode 0／OOMKilled false。这例未采退出前 RSS 或 cgroup
memory.events，不由 inspect 推导 oom_kill 计数。重复专项不计入 1,378 标准回归。
所有临时测量证据和 source／image／lock hash、instrumentation 及 owned 容器收尾
记录见 `profile_output/spec300_linux/REPORT.md`；源数据保留，不更改宿主／生产限额。

## 2026-10-14 正常小尺寸 PostgreSQL 持续完整性补验

此前实际 PG 极端用例的响应全部明确 UNAVAILABLE，未证明容量内的长期完整采集。
本次沿用已有严格本机 QA guard 和实际 admin 路由函数／MockTransport／writer／PG，
开启与关闭采集各独立进程串行运行 50×30，合成响应 padding 4 KiB；没有使用 ASGI、
Actor、真实供应商或媒体，也不替代目标 staging。借用 ADMIN 保持不变，不迁移或
清空测试库，仅创建并清理自身 UUID 的模型、调用、审计与无其他引用的 blob 候选。

- 两模式各 1,500 个成功调用及 SUCCESS 审计。开启模式实际保存 1,500 条 capture，
  全部 COMPLETE/COMPLETE，合计 3,000 个完整采集侧；两侧 kind／stored bytes／chunk
  数量关联各 1,500 全有效，采集错误码全空。关闭模式 capture 行为零。
- 开启 accepted 1,500、水位 50 items／5,343,557B，最终 queue/items/retained/workspace
  全零、未隔离 writer。两模式 scoped cleanup 自有模型/调用/审计和 authority/writer
  连接归零、借用 ADMIN 保留、FD 7／capture thread 0。1,501 个独立 blob 候选体现
  请求去重，不能把 3,000 关联侧写成 3,000 个独立 blob。
- 开启／关闭 process RSS peak 174,292,992／166,281,216B，CPU 15.346／6.012s、
  wall 34.547／10.612s，路由 p99 201.927／137.434ms、active sampled lag
  51.253／14.885ms。路由指标含事务/连接池/屏障，不是发言尾延迟；未测 idle
  transaction 驻留时长，不由采样锁等待零推断所有瞬间无锁或无卡顿。

事后独立强断言核对上述完整性与清理。仅 metadata/chunk 验证，不解码正文或重算
全部 blob 内容 hash；真实 UTF-8 导出下载 digest 证据仍是前面的独立链路。原超载、
Linux、Darwin 证据保留，不覆盖其数值或范围。报告、原始 JSON 与 hash 清单见
`profile_output/spec300_pressure/pg_normal_report_20261014.md`。

另新增纯同步 CPU 归因证据在 `profile_output/spec300_cpu_followup/`：四种合成正文各
五次无 profiler 测量及一次 cProfile，确认完整敏感检查和重复尺寸扫描开销。保持精确
stored bytes／hash／截断与预算的减少重复扫描方案在该次补验时仅为设计，尚未写业务代码，
不将该设计、微测或本次 PG 补验计入 1,378 个标准基线用例。

七个 owned 临时容器（首轮导入失败、v2 导入、四压力及内存回归）逐项核验
owner／run／scope labels 和 stopped 状态，保存终态 inspect 后仅删除对应 name；
run label 查询剩余零。owned image／cached base／白名单／报告／独立 QA DB 保留，
未执行全局 prune／force 删除或触碰生产。清理证据在该 run 的 `cleanup-result.json`。

## 同日后续主持恢复验收的范围区分

主持身份／保护／挂起 PCM 控制及测试夹具补修见 Spec 267。本规格三份采集业务源码
hash 保持原冻结值，原 1,378 项及性能测量是当时的历史构件证据；后续标准总计
1,425 项包含新增主持与夹具检查，不把重复专项或设计计为新增通过。正常 PG 完整
采集与 CPU 报告的独立清单亦已复核，尺寸固定点优化在该次验收时未实施；后续单一切片见下文。

后续第一次数据库总回归误用保留 QA 且触发既有认证夹具 TRUNCATE 的操作失误、
27 项容量失败、共享故障夹具的自有终态化、新可销毁实例的全绿复跑及精确收尾，
已在 Spec 267 和 MEMORY 记录。原始 JSON／ZIP／日志证据保留，但不能推断原 QA
数据库中的记录仍完整存在。目标 staging、真实媒体与受保护发布边界不因此改变。

## 2026-10-14 尺寸计量后续切片（已批准构造性能范围内）

状态：已实施；32项新增回归及当前1,457项标准门禁通过，独立CPU实测完成，尚未发布。
上一主持切片已冻结，此处只处理本规格
已批准的快照构造 CPU 边界，依据独立 CPU 报告，不扩展媒体、调度或产品行为。

范围仅为 `provider.py` 内部尺寸计算与 freeze 循环，以及对应纯回归测试。首次实际
UTF-8 紧凑 JSON 计量保留；对稳定的普通 JSON 树及内部不可变事件／片段，在同一次
实际遍历中记录形状是否稳定，以及 capture metadata 对象是否恰好只出现一次。
非负普通整数 `stored_bytes` 只修正其十进制位数，保持原循环初值和逐步赋值，避免
相邻固定点改变最终规范字节。不得新增全树预扫描、body 缓存或跨调用尺寸缓存。

自定义 Mapping／容器／标量子类、重复引用的 capture metadata，以及 bool、负数、
非整数或缺少旧字段仍走原完整测量循环；不能把仅检查外层 dict 作为稳定树证明。
稳定性观测若需要内部 JSON chunk 递归参数，只作为本次遍历的局部有界状态，不进入
payload、队列、公开类型或数据库。freeze 保留独立首次测量、最低包装检查、字符
减半、无进展错误、每次变更后的实际计量与最终逐块 allocation guard；特殊输入
保留旧循环的重复读取语义。完整安全扫描、编码格式、原始／冻结 hash、错误码、
预算与释放、writer／migration 均不变。

验收：先保留至少一个公开采集路径的实际 JSON 编码读取量性能红检；用标准 JSON
实测旧循环 oracle 校验十进制边界、相邻固定点、metadata alias、特殊类型 fallback，
并验证 COMPLETE／TRUNCATED／拒存／缺失、Unicode／控制转义、多轮缩片段、来源
增长及原有预算断言。对应纯测试、Ruff、类型检查通过后，对相同四个微测 fixture
比较完整字节证明、源 hash 和无 profiler CPU／墙钟；结构扫描减少不能替代性能
实测，也不等于全部 event-loop 延迟或真实链路验收通过。

回滚只恢复本切片实施前 provider 源文件，保留之前300 writer及267主持修复。
实施前源码 SHA256 `0e71e44b49ecbbd25a2fcc8c2b960ad75784465ae2d39e0284cb96f4627b6209`；
原 CPU／PG／主持证据及旧清单保留。本切片不操作保留 QA 数据库或生产，不增加
异步线程、不修改权限、比赛计时、模型或发布门禁。

### 本切片最终实施与证据

- 业务只修改provider.py，最终SHA256
  `135abc6628e4d870daa0f2a4e43e388a4a78230d87fe263a1200c0f294c70dc7`；
  新测试SHA `7a908b8e32405a0c14d45638590d431b0f5fadf11e9bf95887f09b386febcf81`。
  budget/writer及267四份主持业务hash不变；旧0e71源码精确保存，162行focused patch
  仅含本切片，不能用整个既有Git dirty diff冒充这次改动。
- 公开64KiB普通str实际JSON piece读取6→5遍，原始65,684B、存储65,863B、规范
  bytes/hash、预约及最终释放断言不变。新增32pure覆盖独立标准JSON旧循环oracle、
  相邻固定点99/100、特殊类型、metadata alias、Unicode缩片段及实际allocation guard。
  首实施类型白名单的metaclass equality反例已通过is身份比较修复并永久回归；保留
  中间51B/1read与旧/修后451B/4read证据，不假装它是原基线失败或有已保存源码hash。
- 七fixture各五次before/after交替无profiler测量，所有70个样本status/原始及冻结
  byte/hash证明一致，ledger归零。近限ASCII墙钟中位76.444→70.570ms，escaped
  overflow115.896→76.739ms；dense小整数1980.180→1689.628ms，仍有显著同步
  编码开销。单独四fixture同observer cProfile显示_json_size正常3→2、overflow9→4。
  这是本机合成验证时长（含decode/hash/release），不是生产延迟、Linux心跳、真实
  Provider或media压力；不将累计profile时间相加预测收益。
- 当前全门禁1,457：50tool+36QA+231Web+890Corepure+61Jobspure+184CorePG+5JobsPG；
  32新项已包含890，专项重复不另加。Lint/typecheck/contracts、新测试Pyright/Ruff绿。
  原针对性83绿/3DB skip不当作3通过，最终新独立PG全184+5绿。浏览器/Storybook/
  build没有本轮重跑，无Web业务变更，旧证据保持其时间与构件边界。
- 数据库总入口仅使用新owned容器c981ca0cedbe...／loopback32769的可销毁QA，既有
  auth TRUNCATE仅影响此新实例；head0046、其它client0。部分formal fixture行仍
  PAUSED/SYSTEM_RECOVERY及WAITING/FINISHED，不能称逐行全清。精确标签/ID核对后
  正常stop(exit0,noOOM)，只rm本次stopped容器/自有匿名卷，remaining owned0。
  旧保留QA、cached image及production未操作，未migration/restart/deploy生产。

报告、原始profile／paired数据、完整标准日志、精确回滚before及本轮独立manifest在
`profile_output/spec300_cpu_accounting/`。该目录另有Spec272 Publisher启动探针，
4控制绿／7风险红仍未修复，不混入本切片或1,457标准计数。真实50LLM、五场媒体、
真人ASR／音色盲听、staging及受保护发布仍未完成；整体目标保持active。

## 2026-10-15 生产发布终态补充

此前候选／未发布段落保留为当时快照。用户明确批准正常关闭两个旧候场后，
`20261015-asr302-6004cca8`已受保护发布。正常管理员接口保留历史，维护前／停服务后
非终态房间和比赛0；服务器私有备份后升级0046、修复63个终态结束时间并安装已批准
Core/Jobs nofile设置。部署runner及完整预检exit0；独立209文件SHA、Web构建、13路由
和16静态资源核验通过，未登录的3个受保护API均401。300.188秒／11次观察健康、同PID、
无重启、cgroup OOM0及新增warning-or-higher日志0。真实短供应商／RTC／ASR链通过，
完整4v4混合媒体QA仍进行中；未把合成麦克风、短链或五分钟观察当作现场／五场压力。
详见`profile_output/workflow_candidate/release/RELEASE_REPORT.md`和独立生产收据。
