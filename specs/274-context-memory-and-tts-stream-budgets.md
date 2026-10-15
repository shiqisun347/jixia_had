# Spec 274 — 上下文内存预算与 TTS 流收敛

## 2026-10-14 ASR aborted-provider recovery supplement

实施前范围：继承已批准的旧生产者退役、有界取消和精确资源所有权规则。真实 Receiver、
Session 与 Spec302 Protocol 已复现：abort 原取消预算返回后，旧 socket factory 仍挂起，
新发言共用旧 Provider 的锁，不能开始识别或消费新 PCM。只加回调身份栅栏无法恢复。

- 保持现有 abort 取消预算。若精确旧 speech task 在预算后仍 pending，权威后续发言
  使用同配置的新 ASR connection；正常合作取消保留原连接复用，不增加重试或切模型。
- 以生命周期锁和精确旧 connection/session 快照保护替换，等待后不能撤掉较新的资源。
  退役旧 Provider 复用已有独立有界关闭与旧生产者终态后尾部回收；不阻塞新发言，
  不等待自身 task，不重复读取 receiver 的新字段。RTC Room 与媒体连接保持原所有权。
- 不扩大到正常 pause/resume 的新策略，不改变主持、计时、权限、Actor 转移、DTO 或
  migration。Spec217 的不可变 session 回调隔离必须同时成立。

验收：保留精确 e785 Runtime/8687 Protocol 的真实锁阻塞强红；在未释放旧 factory
时验证同一 Room 上的新 session 能进入新 Provider 并消费 PCM，同时旧结果不能
发布或处置新录音。正常合作取消应复用连接；所有测试自有 task/连接独立收尾。
再运行相关 ASR/恢复、同场完整服务流程和标准门禁。回滚只恢复这一 Runtime 切片，
保留 302 Protocol、301 RTC 和既有其它修复；不清数据、不退 migration。

- 版本：v2.0
- 状态：本地实施、回归及原生补验完成，2026-10-15随完整流程版本受保护发布；永久pending／shutdown和真实媒体压力仍待验收
- 日期：2026-10-13
- 授权：用户要求继续全面发现并修复比赛稳定性风险，并明确允许进行必要改进。
- 依据：Spec 272/273 故障注入审计及残余风险复核。

## 目标

为长赛程、多并发和供应商持续输出建立硬边界，避免请求体、内存、音频文件或事件记录
无限增长。比赛状态仍由 Core/MatchActor 权威推进，不以截断上下文或 TTS 超时推进错误的
比赛阶段。

## 范围与验收

1. Agent LLM：运行时 `max_tokens` 始终不超过 32768；generation 参数中的同名字段不能
   覆盖按发言时长计算的硬上限。历史上下文按字符预算保留最近完整条目，保留当前发言所需
   的结构化字段，并为截断增加可诊断标记。
2. Core/Jobs TTS：握手和整段任务使用绝对截止时间；heartbeat、非目标事件或持续音频不能
   无限续期。发送、接收事件记录有界采样；音频字节超过预算时中止并清理临时文件/连接，
   返回稳定 `tts_audio_too_large` 或等价错误。
3. 比赛运行时 registry、Presence、订阅队列和异常 WebSocket 握手必须有清理路径；终态
   Actor 延迟淘汰，关闭时清空回调/订阅引用；Judge、赛后文字响应和导出音频读取同样受
   记录数、字符和逐项媒体预算限制。
4. 为上限、历史截断、heartbeat、静默接收、发送阻塞、长音频和取消清理增加故障注入测试；
   不读取或记录密钥、完整真实发言或供应商原始响应；LiveKit webhook 验签前的请求体也
   必须有界。
5. 运行 Core/Jobs/Web 相关测试、类型、Ruff、契约、Storybook、浏览器和生产构建；不注入
   生产、不修改生产比赛数据。

## 非目标

- 不更换供应商、模型、音色、媒体协议或比赛计时语义。
- 不修改历史 migration；不把截断上下文写回历史比赛记录。
- 不以静默切换供应商或删除失败记录掩盖 Provider 故障。

## 回滚边界

仅回滚本规格新增的上下文预算、TTS 截止/采样、容量获取和应用共享注入、Agent/Judge/
后台测试资源及诊断清理、退役 Provider WebSocket 关闭逻辑和对应测试；保留 Spec 273
的流收敛、生产数据、比赛记录和历史迁移。

## 完成证据（2026-10-13）

### 取消与排队预算补充验收（2026-10-13）

现有取消清理范围继续覆盖统一 `LlmCapacityLimiter`，不改变模型、优先级、比赛状态机或
重试语义。合成复审确认：等待模型名额时取消会遗失已经取得的全局名额；两次独立的
`wait_for` 会把 TechDesign §8.2 的最多 3 秒排队放大至约 6 秒。

- 全局和模型名额获取共用一次绝对截止预算；registry 锁等待也计入该预算。
- 任意一层获取后，等待另一层时取消或失败，必须归还已经取得的名额并原样传播取消。
- 忙模型的排队者不能预占全局名额，令其他尚有模型容量的请求在全局实际空闲时也被
  拒绝。可通过先取得模型名额再等待全局名额的最小调整实现，仍共用绝对排队预算。
  回归须覆盖跨模型隔离、全局等待取消后的模型归还及双层硬上限。该切片不等同于完整
  比赛轮转或角色优先级调度验收，后者仍需独立规格及真实压力验证。
- Agent 和赛后裁判在并发排队失败、客户端关闭失败或取消时必须同步归还名额、尽力关闭
  临时 spool 并有界尝试客户端清理，不能因清理异常跳过容量归还。底层 close/FFI 若忽略
  取消，实际资源回收仍需等待其后续完成；不将有界等待宣称为物理 FD 必然立即释放。
- 已退役的 Provider WebSocket 在关闭超时、关闭失败或关闭被取消时，若底层提供公开
  transport abort 能力，应同步中止该连接后再传播取消/退出，避免等待 keepalive 才释放
  socket。正常关闭不强制 abort，不中止仍用于新任务的连接；不提供该能力的测试接口或
  native/FFI 资源继续采用有界等待并消费迟到结果。使用本地真实 WebSocket 握手且故意不
  响应 Close 的服务器验证 FD 关闭、取消传播和正常关闭路径，不调用真实供应商。
- Agent 与裁判使用同一冻结 ModelProfile 的稳定 ID 作为模型名额键；不能因一处使用名称、
  另一处使用 ID，把同一模型拆成两个容量池。回归必须覆盖同池竞争与取消后的重新获取。
- 管理员的模型连接测试也是同一 Core 的 LLM 调用，必须取得应用共享 limiter 的全局与
  ModelProfile ID 名额，不能在活动比赛已达硬上限时额外发起 Provider 请求。成功、失败、
  容量拒绝、构造失败及取消均应归还名额并有界关闭客户端，再保存诊断；Provider 关闭
  故障和诊断异常不能覆盖原失败或有效结果；诊断等待期间的新外部取消仍须传播。
  复用同一 limiter，不新增独立容量池或调整身份权限、
  错误码、模型选择与重试次数；角色优先级仍是独立调度验收，不能宣称本切片已完成。
- 自由辩论决策的成功/失败/取消诊断持久化不能推迟已结束 Provider 调用的名额释放与
  客户端关闭；取消时先清理 Provider，再执行有界脱敏诊断，保留取消传播和旧回调栅栏。
- Agent 正文的 LLM stream 返回后同样须同步归还容量并有界尝试关闭客户端；后续 LLM
  诊断、TTS 合成完成、播放和播放后诊断不能占用已经结束的 LLM 名额。真实 `_execute`
  的多场合成回归应证明：四场同模型文本已完成、媒体仍在处理时，第五场可以调用 LLM，
  而实际 stream 尚未结束时模型硬上限仍有效。不改变 TTS、首音频计时或权威推进顺序。
- Agent 正文的纯 ExternalCall start 诊断须在取得 LLM 名额前有界执行；TTS start 和
  finish 的纯诊断同样使用至多 0.5 秒等待，不能无限阻塞首音频或 decoder EOF。
  不把 `_create_generation`、`_mark_generation_llm_ready`、失败/终态 generation 等权威
  事务当成可丢弃诊断。取消有界诊断时外层立即传播取消并消费迟到结果，诊断稍后完成
  不得令已退役的正文任务重新调用 Provider；调用前复核现有 run/媒体栅栏。
- 退役 attempt 的 TTS 子任务若不响应取消，`reset_agent` 仍须在既有清理预算内结束，
  不能因 `wait_for` 等待取消完成而无限挂起。每个已退役 decoder、AudioSource 和 room
  均须发起清理，不因等待另一个资源耗尽总预算而全部跳过；先保留现有媒体栅栏、停止
  输入和清空队列，再有界等待独立资源清理，消费迟到异常。资源异常不覆盖原 Provider
  失败或跳过其他清理，外部取消仍传播；不把超时返回写成 native 资源已物理释放。
  同步 `finish_input`/`clear_queue` 的普通异常也须隔离并留下稳定脱敏诊断，覆盖公共
  Agent/Host fence、reset 和正式 finalizer 的 `_close_run`，不能因清队列失败阻断
  已提交暂停事件的广播，或跳过任务取消及其他资源关闭。以真实 Actor/manager 的
  暂停提交与广播路径、公共 Host reset 和正式 Agent 清理回归验证；不吞掉权威事务
  失败，不改变媒体栅栏、finalizer 对 spool 的所有权或比赛状态/计时语义。
- Agent 失败 generation 及已有 ExternalCall 失败结果仍须按原规则事务提交，但可选
  Provider capture/blob 的诊断写入不能令该事务回滚、终止现有重试/暂停回调或跳过资源
  清理。先提交原失败标记，再在独立自有 session 中有界保存纯诊断，并仅修改对应失败
  调用的 capture 字段；不吞掉权威失败事务本身的错误，不新增 Provider 重试或改变
  状态机。必须以实际 AgentRuntime/manager/Actor/PostgreSQL 的单次 capture 写入异常
  回归证明仍按既有两次尝试进入 ERROR/RECOVERY_REQUIRED、保持原错误和历史关联。
  权威失败提交后的生命周期清理须幂等：已 FAILED 的 generation 保留首次错误和
  完成时间，不被随后 `agent_cancelled` 覆盖；已 FINALIZED/CANCELLED 的结果也不
  复活或改写。若仍有 STARTED 的关联调用须收敛，使用该 generation 已提交的原
  失败原因。通过实际 ERROR→恢复→正常发言→FINISHED 的三类 Provider 故障链
  验证旧失败记录保持原错误，新 attempt 和最终 Speech/音频正常持久化。
- Agent 成功 LLM 的 generation PLAYING、完整业务 response blob、Token/延迟和已有
  ExternalCall SUCCEEDED/时间字段仍在原权威事务提交，不能因纯 Provider capture
  存储失败或挂起而重试已经成功的 LLM。仅将可选原始 capture 拆为提交后的独立
  session 和至多 0.5 秒等待；诊断事务不锁住权威 generation 行、不改写状态字段，
  入口验证 generation/call 关联及已成功调用。实际底层 savepoint 回滚造成的 ORM
  expired 属性访问也须通过隔离消除。权威 response blob 或提交失败仍原样传播，
  不被视为可丢弃诊断；取消仍传播，迟到捕获不能覆盖退役或新 attempt 的权威状态。
  使用真实 PostgreSQL 和实际 SSE/runtime 回归证明成功流只请求一次、业务文本仍
  持久化，并分别记录底层存储异常、捕获边界异常和挂起。
- 共享 `persist_provider_capture` 的 savepoint 错误屏障仅隔离可选诊断写入。
  在屏障前显式 flush 既有权威修改，使创建 savepoint 时的业务约束/写入失败原样
  传播；诊断回滚后显式 refresh 已持久化的 ExternalCall，避免后续读取已过期属性
  触发 MissingGreenlet、回滚成功结果或产生重试。flush、refresh 和外层提交失败
  都不得吞掉。真实 PostgreSQL 分别覆盖 request/response、普通存储异常/SQL 错误、
  前置及外层权威约束失败，并通过实际管理员 SSE 成功路径证明结果和成功审计保留。
  保持返回值、capture 错误标记、权限、审计和 Provider 调用语义，不扩展新服务。
- 取消、全局满、模型满、跨两层排队和随后重新获取容量都必须有合成回归证据；
  不把这一切片标作 50 路真实供应商或生产发布通过。
- 该补充切片的回滚仅恢复 limiter 获取/共享注入、Agent/Judge/后台测试客户端与诊断
  清理、退役 WebSocket 关闭及对应测试；不修改数据库、媒体协议、模型参数或其他
  已验收的预算逻辑。

### 先期取消与容量清理证据（2026-10-13，后续补充见下节）

- limiter 将 registry、全局和模型等待纳入同一截止时间；模型等待取消时归还全局名额。
  六项回归覆盖取消、两层预算、registry 等待及释放/取消竞态。
- Agent 正文在排队失败、构造失败、重复取消与 spool 关闭失败时清理客户端、文件和播放
  任务；Judge 同步归还容量后再有界关闭客户端，关闭失败不覆盖真实结果或原始错误。
  Agent/Judge 使用同一冻结 ModelProfile ID，回归证明同池竞争不能绕过模型限制。
- 自由辩论决策先释放容量与关闭客户端，再执行有界完成/失败/取消诊断；start 诊断在获取
  名额前执行。诊断独立限时 0.5 秒，取消子任务并消费迟到异常；Provider 的 3 秒快速决策、
  一次重试、取消传播和旧回调身份保持原有语义。
- 客户端 close 达到上限时取消并消费其后续结果；底层实现不响应取消时仍可能延迟回收。
  诊断失败或超时留下稳定告警，诊断记录可能缺失，但不能因此长期占用 Provider 名额。
- 组合专项 `89 passed`；聚合 `pnpm test` 中 Core 非集成 `539 passed, 76 deselected`、
  Jobs `50 passed, 2 deselected`、Web `231 passed`、tooling Node `50 passed`、QA `36 passed`。
- 在独立 `jx_debate_qa_experiment` 数据库完整运行 Core PostgreSQL 集成：
  `76 passed, 539 deselected`；Lint、类型、契约、新增回归定向 Pyright 与格式检查通过。
- Jobs 的旧主持 fixture 缺 generation，扩大集成时被现有栅栏拒绝；按 Spec 264 只修正
  测试及独立数据库约束，独立 `jx_debate_qa_jobs` 两轮集成各 `5 passed`，正文非集成
  仍 `50 passed`。确认旧任务下一次轮询收敛为 FAILED；没有为测试放宽生产栅栏。
- 20 轮合成容量复核累计取消 1,000 个等待任务后重新获取 1,000 个名额，超限仍拒绝；
  0.2 秒跨两层预算实际约 0.201 秒。上述验证没有真实 Provider 请求，不能替代 50 路
  真实供应商压力或现场音频验收；未执行生产发布。

### 跨模型、媒体尾部与后台测试补充证据（2026-10-13）

- 复现全局先获取会让忙模型的等待者占住其他模型可用的名额：实际默认 3 秒预算下，
  三轮空闲模型请求均因等待者占位失败。现在先等待模型，再等待全局，两层仍共用
  一个截止时间；全局等待取消/失败只归还已经取得的模型名额。limiter 当前 10 项
  回归通过，保留硬上限、取消竞态和 registry 预算验证。
- Agent 正文 stream 返回即同步释放容量并有界关闭客户端，再处理原 LLM 诊断、TTS
  和播放尾部。真实 `_execute` 的四场媒体尚未完成/第五场请求合成复审，改前在四类
  尾部重复 20/20 拒绝；改后六个五场场景重复三轮共 18/18 通过，未完成 stream 的
  硬上限仍有效。Agent 清理当前 17 项回归通过，播放时序及回调身份没有改动。
- 本地真实 WebSocket 服务器握手后故意不响应 Close。改前五种 Core TTS/ASR/Jobs
  关闭路径在外层 2 秒返回后 FD 仍开着，约 22.2 秒才因 keepalive 关闭；固定库版本
  的 close 被外层取消绕过了其自身 abort。共享 helper 在异常、超时或取消时中止已
  退役 socket 的 asyncio transport，正常关闭不 abort，替换连接不受影响。
  原样 2 秒常量的五路径并发复核于 2.0017 秒全部 FD=-1/CLOSED，观测在 fixture
  最终清理前；21 项回归验证超时、取消、正常关闭、替换保护及迟到异常消费。
- 应用只创建一个 limiter 并注入 Agent、Judge 和后台模型测试；即使未启动 Agent，
  Judge 和后台仍使用同池。后台真实 route 与合成 Provider 的复审证实此前容量满仍
  发起 stream、构造失败/取消/close 挂起会遗留 STARTED。修复后先获取稳定模型 ID
  名额，拒绝不构造 Provider；所有路径先归还容量，再有界关闭，最后保存诊断。
- 后台失败诊断使用原 session/route task 的 0.5 秒预算，不创建迟到数据库子任务；
  普通诊断失败或超时保留原 503/error code，新外部取消仍传播。成功诊断保留原有
  审计语义。27 项后台回归覆盖上述路径、真实 HTTP、诊断失败/挂起及取消。
- 聚合 `pnpm test` 通过（补充诊断切片前 Core 592）；最终 Core 非集成全量复跑为
  `598 passed, 76 deselected`。Core 独立 PostgreSQL `76 passed`，Jobs 独立 PostgreSQL
  `5 passed`、非集成 `50 passed`；Web 231、tooling Node 50、QA 36 均通过。
  最新验证组合为 1,046 项，不把重复专项或历史浏览器测试重复计入这个总数。
- 最终代码的 Lint、Web/Contracts/Python 类型、OpenAPI contracts、新增测试定向
  Pyright、Ruff 格式及差异检查通过。末次取消捕获的小改动另经定向类型/Ruff验证。
  本轮前端未改，没有重复已有 Storybook 94、浏览器 248/12 skipped 与构建基线。
- 验证无真实 Provider 调用或生产写入。HTTP/native/FFI close 忽略取消时仍可能延迟
  物理回收；取消/失败诊断写入失败可留下 STARTED 和稳定告警，但不占容量。
  比赛轮转与角色优先级尚未实现，已形成待批准 Spec 299；真实 50 路、五场媒体、
  180 秒真人 ASR、11 音色盲听和受保护发布仍待完成。

### 诊断事务与生命周期补充证据（2026-10-13）

- 实际 PostgreSQL 故障证明：失败 raw capture 曾回滚 generation/ExternalCall 失败结果；
  成功 capture 的 savepoint 回滚曾触发 MissingGreenlet 并重试有效 LLM。Agent 成败
  权威提交与可选捕获已分离，后者有界等待 0.5 秒，不锁 generation；完整业务 blob
  仍原事务提交。共享 helper 在错误屏障外 flush、诊断回滚后 refresh，权威错误仍传播。
- runtime 故障 PostgreSQL 14 项、成功捕获 13 项、共享 ORM helper 9 项、恢复链 3 项
  均通过。503/首正文超时/截断 EOF 均按原两次尝试失败、自然 3 秒恢复、正常发言并
  FINISHED；取消清理幂等，保留原错误和完成时间。场景只含一个 Agent 动作和合成媒体，
  不替代完整真人/真实供应商比赛。
- start/finish 诊断有界且不持有已结束 LLM 名额；retired TTS/native 清理先脱离旧快照，
  独立启动每个有界关闭。取消抵抗探针约 3.002 秒返回，仍可有底层任务未完成，不宣称
  native 物理资源全部回收。attempt 清理新增 20 项通过。
- 六处公共 fence/reset/正式清理的同步 finish_input/clear_queue 复用异常屏障。新 10 项
  回归证明实际 Actor 暂停提交后仍广播并执行原恢复清理，正式 finalizer 与 Host reset
  继续关闭其他资源，外部取消仍传播。run/spool 的权威接管栅栏另按 Spec 217 验收。
- 最新冻结代码全量：Core 非集成 684/独立 PostgreSQL 126，Jobs 非集成 50/集成 5，
  Web 231、Node 50、QA 36，共 1,182 项通过；不重复统计专项。Lint、类型、契约、
  格式与差异检查通过。没有生产发布，本轮前端未改，未重复此前浏览器/构建基线。
- 完整异步 Provider capture writer 仍缺失，其他部分调用者还直接等待诊断；新增
  Spec 300 草案要求独立 capture 表和有界 writer，待批准，未借本规格实施新表/队列。
  Spec 299 公平调度、真实压力与现场音频、staging 内存校准及受保护发布仍待完成。

### 既有预算验证证据

- LLM 请求硬上限、`max_tokens` 覆盖保护和最近完整历史预算已实现；上下文截断会留下
  可诊断的系统标记，不修改历史发言记录。
- Core/Jobs TTS 增加握手与整段绝对截止时间、16 MiB 音频预算和 128 条事件采样上限；
  超时、持续 heartbeat、超大音频和异常连接都会清理连接/临时文件并返回稳定错误；
  Provider socket 的 close/discard 也有界，避免取消路径被不响应的 close 永久拖住；
  Agent/Host 的 decoder、AudioSource、LiveKit room 关闭也采用有界清理，避免 native/FFI
  关闭阻塞比赛 Actor 或重试路径。Jobs 对导出包和生成音频的 SHA-256 改为 1 MiB 分块，
  避免哈希阶段创建第二份整文件内存副本。
- Presence 终态 tombstone、取消回滚、ASR 建连/回调总截止、生命周期锁和队列满收敛已覆盖；
  ASR segment/capture 回调在事务内复核 match、speech attempt、epoch、context、机会代次及
  task/call 关联，过期结果不会写入数据库。赛后/文字响应使用列投影与有界记录/字符预算，
  Jobs 导出逐项读取音频并限制 16 MiB/文件、64 MiB/场，Voice Preview 并发限制为 2，
  LiveKit webhook 验签前拒绝超大 body，避免异常输入放大 Core/Jobs RSS。
- 内容 blob 读取限制为 8 MiB 压缩体、16 MiB 解压体和 16 个分块，并通过增量 zlib 输出
  拒绝解压炸弹；延迟实验轮次栅栏限制为 4096 并按比赛终态清理；等待 Speech 行的完整
  Agent 文本栅栏限制为 128 条。观众投票改用异步流式聚合，避免投票行数直接复制进 Core
  RSS。ASR track/speech、Agent/Host media 和赛后 Judge 关闭均使用有界取消等待并消费迟到
  异常；终态 Agent/Host 启动有比赛级 tombstone，不能在 close_match 后重新建立媒体会话。
- Core 非集成 481/481、Core PostgreSQL 集成 76/76、Jobs 33 通过/2 跳过、Web 222/222、
  Storybook 94/94、浏览器 248 通过/12 跳过/0 失败；tooling 50/50、QA 13/13、
  contracts、lint、typecheck、Ruff、Pyright 与生产构建均通过。
- 所有验证使用合成故障和隔离 PostgreSQL，未部署或修改生产；未覆盖真实供应商压力、真人
  音频盲听及生产变更窗口。

### Spec 300 后续闭环（2026-10-13）

用户另行批准 Spec 300 后，所有新生产 raw capture 已迁入独立共享 writer，替代本规格
过渡期的同步或局部有界等待；legacy helper 仅保留历史兼容/专项证据。六类实际业务／
PostgreSQL 探针连续两轮44项全绿，原成功/失败/审计/锁与导出正文hash断言保留。
完整权威blob、提交失败、Provider重试及模型策略不改变；新表、拥塞策略和冻结投影的
授权、资源验收、兼容降级及未执行边界以Spec300为准。此结果不补齐Spec299公平调度。

## 2026-10-14 Publisher 启动自有资源补修切片

状态：已本地实施，应用及原生对照、标准门禁和新独立数据库回归通过，尚未发布。继承本规格已授权的退役资源及异常握手清理，
不扩展固定pending/retired准入或满额拒绝政策。上一尺寸计量切片已冻结且全1,457绿。
当前SDK／媒体库版本、线上状态机、重试次数和播放时钟不变，不修改安装的SDK文件。

Spec272新增实际SDK/FakeFFI与真实业务helper探针11项4控制绿/7风险红，明确未发起
真实网络/native。Room/source等直到helper成功return才赋run，启动异常丢失owner；
SDK1.1.14 connect被取消还遗留永久订阅。[上游issue784](https://github.com/livekit/python-sdks/issues/784)
另报告原生ready握手缺失后SIGTERM，但它不是本机native复现，不将此转写为本机已证故障。

### 精确范围与不变量

- `_connect_publisher` 创建Room后即建立局部owner；返回的Source/Track等逐步归属。
  成功返回仅一次移交原Room/Source，helper不提前关闭；异常不交付任何媒体资源。
- 公共`Room.connect`使用独立任务，调用者等待以shield或不传递取消的wait实现。
  外部取消/既有超时只退役本次启动，不取消SDK建连，不自行发私有FFI ready／退订。
  若SDK尚未完成，其自有完成回调保留Room和Task所有权；结束后只对该旧Room发起
  公共disconnect，不创建Source/Track、不发布音轨、不播放PCM、不调用比赛回调。
  正常SDK ready顺序不由应用重建。结束失败也消费原SDK结果并收尾局部资源。
- connect完成后的构造／publish错误及取消，所有已返回资源先独立调度有界关闭，
  随后才等待。复用既有每资源0.5秒预算，不以wait_for等待取消抵抗清理而无限挂起；
  第二次外部取消不撤掉尚未开始的关闭任务，迟到错误必须消费。同步清队列的清理
  BaseException在本rollback窄边界隔离，不能覆盖原启动失败或跳过别的资源。
- 保留原错误对象、错误分类与取消传播，不额外重试，不改变Host/Agent callback身份。
  失败或退役只清本次精确资源，旧任务不得关闭替换连接。Track无公开close，沿Source
  关闭／Room断开及释放本地owner引用，不猜测私有handle dispose或伪造unpublish。
- 不新增全局无界任务集合或新的服务/线程/SDK依赖；每次已开始建连保持原SDK waiter
  和精确迟到owner回调，不复制run/Actor/session或业务上下文到清理任务。

### 验收与边界

1. 永久pure tests保留原强断言，覆盖connect/source/track/options/publish失败、取消与
   重复取消、清理构造异常／同步BaseException／挂起及迟到异常，成功移交不提前关。
2. 以真实SDK Python方法＋自有FakeFFI验证应用路径取消时SDK join未取消；迟到成功
   完成ready后才disconnect，迟到失败及正常路径订阅/task终态归零。原裸SDK取消
   探针保持原红证据，不冒称安装SDK已被修复或以放宽原断言转绿。
3. 对应Host/Agent生命周期、旧身份／回调、模型容量与实际PG恢复链回归，类型/Lint/
   契约检查。可行时以独立本地原生RTC/测试服务器子进程补验，全部用合成凭据与资源，
   避免影响生产或真人发言；FakeFFI close计数不等于实际FD/线程/socket已经归零。

本切片证明已返回局部资源有清理者、调用取消不破坏SDK连接握手；不能证明永久
挂起native join／close物理回收、SDK内部初始化异常、shutdown保留、单个pending
原生订阅的字节/时长硬限或全局retired准入。SDK1.1.14内部队列仍可能在未完成join时
接收全局事件，固定数量也不能单独证明队列有界。新策略需后续明确规格/批准，不能
因为此切片绿而宣称整体稳定性或真实五媒体/50LLM已验收。

回滚只恢复本切片前runtime精确源码
`f914530a24b74e57c030dace43bd139fdf316d8cc8a3a132fc01c9f10cca54fa`，保留之前267/300
及其它模块。基线保存`profile_output/spec274_publisher_startup/runtime_before.py`。
不回退migration、不清历史数据，不触生产部署/重启。本切片结束后更新MEMORY及状态。

### Publisher 启动切片实测（2026-10-14）

- 仅共享runtime启动helper及私有清理函数变更，最终SHA `bd9b2eb3b47af8ae9e5ff1eb876b7909f2842a0ae87c99615ad1816e1ee6c75c`。
  旧精确f914构件和聚焦patch分别保存在`profile_output/spec274_publisher_startup/`。
  启动错误保留原异常并清理局部资源，成功只移交一次；SDK join独立shield，迟到owner
  只关闭精确旧Room。没有改SDK、状态机、公开DTO、模型、迁移或生产配置。
- 永久新增启动14与实际SDK/FakeFFI8共22项绿；精确旧构件分别保留5产品红/1正常绿、
  5产品红/3正常绿，setup/teardown均无失败。SDK迟到成功/失败各五次重复收敛，不能
  改写为长期pending容量验证。相关Host/Agent生命周期/身份145绿，独立复审22绿。
- 新独占internal网络／本地LiveKit1.13.5／缓存Linuxarm64 SDK1.1.14实际原生补验：
  裸SDK和精确旧helper取消后ready均0，FFI callback各实际self-SIGTERM，子进程exit-15，
  墙钟16.689/15.891秒。候选两个独立子进程exit0，各观察至少20秒，SDK join不取消，
  ready→connect完成→disconnect各一次，订阅/队列、SDKjoin/listener Python任务终态0。
  没有Source/Track/publish/PCM或Settings/DB/Provider调用，回调仅延迟Python投递350ms，
  native请求和完成未伪造。v2总入口exit1包含两个预期旧强红，原数据不改成全绿。
- 首客户端仅浅mount路径IndexError，未启动SDK child；原console/preflight/入口源码和
  prepared manifest保留。v2与repeat使用新handle／输出，独立目录记录，不覆盖首失败。
  原生finalFD两轮21（导入前8、native初始化后18），没有physical归零证据；线程及即时
  RSS为null，不能用Python线程1代替native线程。所有新clients/server/空internal网络
  按精确ID/label正常收尾，共享镜像保留，未碰旧QA、生产或真实比赛。
- 当前非DB标准1,290绿：tool50+QA36+Web231+Core912+Jobs61；pnpm lint/typecheck/
  contracts全exit0，新test Ruff/Pyright绿。本轮前端未改，未重新执行历史browser/
  Storybook/build。数据库总入口只允许新建可销毁实例，结果核验后另记，不复用保留QA。
- 报告、逐文件PartB和独立清单在`profile_output/spec274_publisher_startup/`；本轮原生
  两次迟到正常完成只关闭应用取消切片，未关闭永久pending/globalquota/shutdown/
  SDK内部异常或真实媒体验收风险，完整目标仍在进行中。

最终当前可运行标准回归**1,479项通过**：前述非DB1,290，加Core独立PG184、Jobs PG5。
新独占容器`d6860594...`仅loopback随机端口，fixture强制的`jx_debate_qa_agent_faults`
与哨兵experiment库均在新实例；auth TRUNCATE未触旧QA或生产。head0046、测试clients
及idle-in-transaction均0；测试留下8TERMINATED/3PAUSED/1WAITING rooms及对应matches，
不称所有fixture逐行清空。随后正常stop/rm -v精确标签/ID的新实例和自有卷，exit0/
noOOM，容器不存在，无force/prune。全部标准工具会话终态，无新增Web或生产发布。

后续只读枚举发现ASR独立`MatchAudioReceiver._start`仍直接wait_for取消SDK connect，
timeout／外部取消时局部Room接管存在缺口；该ASR路径本轮尚未原生复现或修复。
应用publisher两轮绿不能扩展成所有Core建连安全，需另立对应强断言和精确补修切片。

## 2026-10-14 ASR 已接管接收器关闭补修

状态：实施前精确补充，继承本规格已批准的 ASR 退役资源、有界取消与异常隔离范围。
这是正常 receiver.close 的独立切片，不实施待批准 Spec301 的 RTC 启动／identity 增量，
不把绕过新身份批准的部分结果替代完整恢复方案。

原 acb 构件的实际 receiver.close 重复取消强红已经保存：Provider close 抵抗取消时，
调用者退出后 Room.disconnect 没有发起。同步 close factory 错误及 speech／track
取消等待也可能跳过后续资源。close 已同步脱离字段，再次 close 因 closed 返回，
不能依靠第二次调用补做丢失的资源关闭。只修复这个已接管资源快照的所有权缺口。

### 精确范围

- 保留 `_lifecycle_lock`、`_closed`、speech detach／录音处置及精确旧 task／Provider／
  Room snapshot。正常 close 不取消当前调用者自身，幂等 close 不重复关闭同一资源；
  await 后不读取替换字段，不能关闭新资源。
- 先对精确旧 speech／track 任务发出原取消请求，随后在第一次 await 前独立安排
  各任务等待及每个 Provider／Room 有界关闭，并立即注册结果消费者。保持取消先于
  Provider／Room 请求的动作顺序；等待旧任务不能阻止发起其他资源清理。
- 所有关闭复用原每资源 2 秒／任务取消 2 秒预算；调用者重复取消只结束其等待，
  不撤掉已调度且尚未进入的关闭工厂。同步工厂异常、清理失败／挂起和迟到异常
  各自隔离，诊断只保留稳定资源／异常类，不覆盖原外部取消。
- close-only 普通录音清理异常不能丢掉已保存的旧 speech task 或跳过其他资源；
  保留 `_detach_speech`／abort／pause 的其他语义，不删除历史媒体文件或额外音频数据。
- 提前关闭 Provider 时，尚未结束的旧 speech／track 任务可能在取消抵抗 factory 返回后
  继续写入同一旧连接的 socket／capture。首次关闭开始时若精确旧任务仍 pending，
  其实际终态后再对同一旧 Provider 做一次幂等有界关闭；完成通知只保留旧资源／任务
  与 match UUID，不引用 receiver 或替换字段。正常旧任务已结束时不重复关闭。该收尾
  不等待永久任务来阻塞调用者、不改变协议类或关闭后重用策略，也不宣称永久 pending
  的物理回收已完成。
- 不修改 `_start`、SDK connect、registry、service／真人身份、音轨协议、模型、
  CallbackEnvelope、重试次数、比赛计时／权限／状态机、migration 或生产。

### 验收与回滚

1. 独立永久关闭测试对原构件先留强红／正常控制；覆盖 speech／track／Provider／Room
   等待期间取消、重复取消、清理工厂构造失败、录音清理错误、挂起／迟到异常消费、
   已完成任务、自身调用、幂等与精确替换保护。测试夹具的最终清理不算应用回收。
2. 当前两个 ASR startup 测试文件不放宽断言；原 normal close 相关项应收敛，其余
   未批准／未实施的启动及 identity 风险保持强红。明确全量是否仍红，不能择绿宣称全部通过。
3. 相关 ASR／speech／Manager／回调与类型、Ruff、契约检查。无当前 Web／Provider／
   native 改动，不以 pure close 计数冒称实际 FD／线程全部释放或真实发言已通过。

回滚只恢复本切片前 ASR runtime 精确 acb 源码，保留 publisher bd9 与此前其它修复；
before 及 close-only diff 在 `profile_output/spec274_asr_close/`。本切片不进入生产，
结束后更新状态／MEMORY。永久 pending、shutdown、SDK 内部异常和 Spec301 完整
建连隔离仍独立待验收；已批准的 normal close 收敛不会关闭那些问题。

### ASR 正常关闭切片结果（2026-10-14）

本切片已本地实施。close-only 最终 SHA `cdf26a076c65479aa858cffd61e7bc99f9b3bcbaee001f1f0a2da37b56ebe44f`；
独立 17 项通过：原 acb 的 14 项为 5 产品红／9 控制绿，实际 Protocol 迟到 factory
两项为 1 红／1 控制绿。中间 401e 的录音 unlink 异常屏障残留旧路径，可实证删除
替换接收器的新音频，另留 1 项产品强红；最终只在该异常分支清掉四个录音引用。
所有 setup／teardown 正常，最终整轮 0.37 秒，源码前后相同，原断言与失败日志保留。

正常 Provider 关闭一次；尚未结束的精确旧生产者终态后最多再关闭一次，释放晚建
socket／capture／reservation。此回收不阻止退役 run／finish 请求，不宣称永久 pending
或物理资源归零。本切片没有运行完整门禁／DB／native／生产；最终门禁随之后新批准
Spec301 的独立构件执行。报告、逐文件 Part B、旧／新源码与聚焦 patch 在
`profile_output/spec274_asr_close/`。Spec301 于同日用户明确批准，从 cdf 基线继续，
不能把批准前剩余启动强红改写为已解决。

## 2026-10-15 完整流程候选最终验收

已本地完成；最终Receiver a7b3、Protocol8687、domain7f29，标准1,564通过（Node50+QA36+Web231+Core996+Jobs61+CorePG185+JobsPG5），Lint／类型／契约exit0，561冻结文件前后一致。同场真实Room/Manager/独占PG链20发言完成双方四辩、single FINISHED、评委发布、8席投票／重复拒绝及AWARDS；模型／PCM／RTC为合成边界。六场景两轮12场／240合成发言及浏览器248／Storybook94通过，12项既有legacy浏览器跳过保留。

Spec217/274补齐精确Session＋不可变Envelope、callback await返回／错误后撤销、旧录音保护、pending abort后同配置新Provider、旧pause intent同步清理。完整流程fixture10个自有HOST_TTS曾污染后续Jobs，已用真实wrong-UUID→own-UUID对照确认；仅finally按自有UUID＋kind＋own rule白名单清理，不改Jobs业务、原测试或强断言。最终新fixture6eb1，全量fresh190通过及正常精确收尾。

发布包／资格、PartA/B、源码回滚及原始失败见 profile_output/workflow_candidate/release/RELEASE_REPORT.md 与相关证据目录。Web同次构建在服务器独立原生Node进程13关键页面/API＋16静态资源全200，临时进程／目录终态。未关闭真实房间、迁移、重启或切换生产。两个已知WAITING房间仍在；Spec283要求明确取消决定后才能维护发布。Goal保持进行中，真实完整混合媒体／现场及生产QA待下一窗口；不把本候选当作五场／50路LLM／11音色或真人ASR验收。

## 2026-10-15 生产发布终态补充

此前候选／未发布段落保留为当时快照。用户明确批准正常关闭两个旧候场后，
`20261015-asr302-6004cca8`已受保护发布。正常管理员接口保留历史，维护前／停服务后
非终态房间和比赛0；服务器私有备份后升级0046、修复63个终态结束时间并安装已批准
Core/Jobs nofile设置。部署runner及完整预检exit0；独立209文件SHA、Web构建、13路由
和16静态资源核验通过，未登录的3个受保护API均401。300.188秒／11次观察健康、同PID、
无重启、cgroup OOM0及新增warning-or-higher日志0。真实短供应商／RTC／ASR链通过，
完整4v4混合媒体QA仍进行中；未把合成麦克风、短链或五分钟观察当作现场／五场压力。
详见`profile_output/workflow_candidate/release/RELEASE_REPORT.md`和独立生产收据。
