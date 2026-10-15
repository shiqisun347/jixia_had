# Spec 301 — ASR RTC 启动所有权与恢复连接隔离

- 版本：v2.0
- 状态：用户于2026-10-14明确批准；本地实施、标准门禁和两轮原生补验完成，2026-10-15随完整流程版本受保护发布；实际短语音链通过
- 日期：2026-10-14
- 依据：Spec 272 重复故障审计、Spec 274 媒体清理及 publisher 原生取消补验、Spec 217 回调身份契约
- 授权边界：本次明确批准覆盖私有 ASR 服务身份逐连接唯一化及以下启动所有权切片；此前 publisher 授权保持其原范围。

## 问题与预期行为

修复前 `MatchAudioReceiver._start` 直接 `wait_for(Room.connect, 10s)`；超时或外部取消会取消 SDK join，未返回的局部 Room 不在 receiver owner 中。`AsrRuntime.ensure_match` 只捕获 Exception，取消还会留下未就绪 receiver。连接成功后的 Provider 构造或监听注册失败同样可能丢失局部资源。

修复前私有服务身份 `jx-asr-{match_id}` 被同场新旧连接共用。仅 shield 旧 join 后立即恢复，新连接仍可能被迟到旧 join 的重复身份替换。LiveKit 对非 resume 的重复身份 join 会断开旧参与者，因此不能把 publisher 的独立 join 机械复制到 ASR，也不能因为单个 helper 的绿检宣称恢复安全。

预期：用户点击恢复／开始发言后，只使用当前成功接管的 receiver；启动取消仍及时返回并保留原异常／超时分类。旧连接可完成 SDK 自己的 ready 后断开，但不能顶掉新连接、开始消费音轨、构造 Provider、写回 receiver 或调用比赛回调。正常连接不提前关闭，已有真人轨道完整交给当前接收器。

## 单一切片范围

1. 只改 `apps/core/src/jx_core/asr/runtime.py` 的 RTC 启动／局部清理及 `ensure_match` 的精确失败回收，新增对应测试；复用现有模块和每资源 2 秒清理预算。正常 receiver close 复用相同的独立资源关闭 helper，修正重复取消跳过后续 Room 清理的问题；不改变 speech detach／session 完成／计时语义。
2. 每次创建私有 ASR Room 使用唯一服务身份 `jx-asr-{match_id}-{uuid4.hex}`。这只影响服务参与者，真人 `jx-human-*`、观众／Agent 身份、房间名、授权 grants、JWT TTL、音频协议和 WebSocket 业务命令均保持现有行为。它是本规格需要明确批准的身份增量。
3. SDK `Room.connect` 放入独立 Task，调用者用 shield 加原 10 秒截止等待；外部取消／超时不取消该 SDK join。精确迟到完成 owner 仅持有旧 Room、SDK Task 和必要清理标识，不持有 Runtime、Manager、Actor、Session 或新 receiver。
4. Room 从创建起由局部 owner 管理。监听注册、connect、Provider 构造和交付之间任一失败／取消都进入 rollback；正常接管只提交一次 Room／Provider。保留原异常对象，原 TimeoutError 仍转为 `asr_start_timeout`，不额外重试或改变真人手动开始和第二次失败暂停规则。
5. `track_subscribed` 在成功接管前或退役后不得启动 consumer；回调核对当前精确 Room／receiver，旧监听退订或使用不保留 receiver 的窄 gate。成功后枚举已经发布的轨道，避免漏轨和重复消费。迟到旧 callback 不能关闭替换对象或恢复旧 speech。
6. `ensure_match` 覆盖外部取消，移除 registry 时比较精确 receiver 和本次实际启动所有者，不能删除并发替换项。两个请求共用 receiver 时，仅在生命周期锁前等待的请求被取消，不得清理另一个请求的正常启动；逐调用 ticket／等价代次证明实际失败所有者，不能只凭同一个 receiver 或复用 Task 判定。必要清理先独立调度，再有界等待；再次取消仍留有清理 owner。保留 `close_match` tombstone 和 runtime close 的既有关闭语义。
7. 对已返回的 Provider／Room 独立安排关闭，清理构造异常、挂起和迟到错误不能覆盖原启动错误或跳过其他资源；没有可关闭对象时不假造资源。

实施复审细化：`close_match`／runtime close 在原 registry 锁内宣告启动退役；
receiver 的直接 close 也在等待 lifecycle lock 前宣告。SDK join 完成后、构造 Provider
及接管 Room 前校验该标记，旧轨道 gate 同样拒绝已退役启动。保留 `_closed`／speech
detach 在 lifecycle lock 内的原时点、tombstone 和既有 `asr_receiver_closed` 错误，
不以取消 SDK join 实现退役。此项用于保全条目 5–6 的终态关闭语义。

## 明确不扩展的范围

- 不改认证／权限策略、比赛状态机、计时起点、重试次数、模型／供应商或 CallbackEnvelope 字段；不静默切换 Provider。
- 不修改已安装 SDK、私有 FFI ready／unsubscribe 协议或既有 migration，不新增服务、全局无界任务集合、SDK 依赖升级或生产准入／拒绝政策。
- 不把永久不返回的 join／close、SDK 内部异常、进程 shutdown、pending 队列字节硬限或全局 retired 预算写为已解决；这些仍需后续独立验收。
- 不改 `scripts/ops/provider_chain_probe.py`，也不执行其真实供应商入口；它的相邻风险单独记录。

## 备选方案与选择

保留固定身份并等待旧 join 及已确认 disconnect 完成后再建连，可以避免直接身份碰撞，但必须把等待和新 join 共同限制在原 10 秒内。永久 pending／未确认 close 会持续占住恢复窗口，普通 registry 移除还会丢失跨 receiver 屏障；需要增加协调状态并覆盖所有直接 start 入口。本规格选择逐连接唯一私有身份，使旧 join 不影响新连接，保留现有恢复预算和错误语义；不以绕过审批为理由引入长期串行等待。

## 验收

1. 精确原源码保存 SHA 和聚焦回滚副本。原实际 receiver／runtime 强红覆盖 connect／监听／Provider 构造失败及取消后 registry 残留；正常交付控制通过，setup／teardown 错误单列，不放宽断言。
2. 实际 SDK Python＋自有 FakeFFI 覆盖 receiver 自有超时、实际 Manager 外层 timeout、外部取消，迟到成功／失败、SDK join 未被取消、ready→disconnect、订阅／SDK task 终态归零。所有自有 fake 回收在产品断言之后，不冒充应用回收。
3. 正常及旧轨道回调、已发布轨道、精确替换 receiver、终态 close、重复取消和清理挂起／迟到异常有直接断言。新旧同场服务身份不同，现有真人 identity parser／grants 不变；旧 callback 不调用 Provider／消费／比赛回调。
4. 可行时新独占本地 native／LiveKit 测试验证旧构件取消故障，候选迟到正常 join 后 disconnect，以及新旧连接重叠时恢复连接保持可用。保留强红对照和至少两轮候选观察，实际 FD／线程与 Python 队列分开报告；未送 PCM 不称实际发言通过。
5. 对应 ASR／Manager／身份／恢复集成、全量单元／隔离新数据库回归及 Lint／类型／契约检查通过。无 Web 业务变更不冒称新的浏览器、构建或真人现场验收。

## 回滚与发布边界

只恢复本切片前 ASR runtime 精确源码
`cdf26a076c65479aa858cffd61e7bc99f9b3bcbaee001f1f0a2da37b56ebe44f`，副本位于
`profile_output/spec301_asr_implementation/runtime_before.py`。该基线包含已按 Spec274
独立修复、17 项验证通过的正常 close／迟到 socket 回收和录音异常引用清理。
保留此前 publisher／Host／capture 修复，回归测试和诊断证据单独保存。
原 `acb26d74...` 仍为启动故障历史对照，不作为本切片直接回滚目标。
无新数据库迁移、历史数据改写或清理。

当前先在独立合成环境完成实施与验证，生产部署仍按既有受保护门禁执行；不在真实比赛注入取消故障、重启或替换连接。完成后更新 MEMORY、状态及证据清单。整体目标未完成，目标 staging、真实五场媒体／50 路 LLM／真人 ASR／音色盲听仍独立验收。

## 实施前证据（2026-10-14）

- 本次批准前另做实际 SDK／自有 LiveKit 的身份重叠机制测试：同身份迟到 join
  断开当前参与者（DUPLICATE_IDENTITY），不同身份两连接均保持；两 fresh child
  各观察至少 20 秒，Python 订阅／task 终态归零。此为机制证据，非 ASR 候选修复
  或完整发言验收；冻结报告位于 `profile_output/spec301_identity_overlap/ACTUAL_REPORT.md`。
- 以下 `acb26d74...`、23 项 15 红／8 绿及两次 self-SIGTERM 均为此前冻结历史
  对照。批准时正常 close 已先按 Spec274 修复；RTC 启动和身份仍未改，剩余启动
  14 项强红必须在本切片继续保留并修复。

- 精确 `acb26d74...` 原源码保存于 `profile_output/spec274_asr_startup/runtime_before.py`，尚未改业务。
- 实际 receiver／runtime 首5例4产品红/1正常控制绿；3个局部Room清理断言及active启动取消后registry断言失败，setup/teardown无错。
- 实际SDK Python＋FakeFFI首11例7产品红/4正常控制绿：三种取消/超时入口×迟到成功/失败各残留1订阅，成功无ready/disconnect；外部和Manager取消还残留未关闭receiver。原强红、警告与guard终态单独留证。
- 追加并发等待者取消控制当前通过：取消B不影响A的SDK join／registry，A正常ready移交，再正常close/EOS归零；不能用捕获所有BaseException后盲删receiver破坏它。
- 单start中订阅事件和inventory同轨道重复消费、同对象/同publication无重新发布的迟到重复投递，以及正常close重复取消跳过Room三项追加产品强红保留。它们与尚未实施的预期改动分开记录。
- 当前两个新文件整轮共23项：真实receiver/runtime11项8产品红/3正常绿，实际SDK/FakeFFI12项7产品红/5正常绿，setup/teardown全通过；首批或单独controls不重复加。
- pure/SDK层native/DB/network/Settings全guard。随后新internal网络／本地测试LiveKit原生补验：精确原ASR receiver.start与实际ensure_match两fresh child在外部取消后均由FFI callback真实self-SIGTERM、exit-15，wall16.847/15.573秒，无父deadline kill、ready均0。350ms只延Pythoncallback投递，未送PCM或构造Provider，不是生产历史事故确定归因。
- 新clients/server/空network均精确ID/labels正常收尾，缓存镜像保留。身份重叠场景尚未原生补验，原构件强红不能冒称候选已经修复；全部业务仍acb原SHA。详细设计、官方duplicate identity引用、实际结果PartA/B在同目录 `design_review.md`／`BASELINE_REPORT.md`。

## 实施与验收结果（2026-10-14）

- 最终唯一业务改动为 ASR runtime，SHA
  `e7857d3094e81ca7df1f79b31978e540dab6bc8e3655261d010e348dabd5f8f9`。
  cdf 基线与 startup-only patch 单独保存；publisher／Host／capture／ASR protocol
  八个相邻源码 SHA 及十五个非启动方法 AST 保持不变。无 migration、模型／认证／
  比赛状态机／计时修改，不修改已安装 SDK。
- 独立 SDK Task 用 shield 保留原 10 秒预算；迟到 owner 只关精确旧 Room。
  弱监听、精确 Room gate、接管前 inventory 和当前 publication 去重阻止旧回调。
  失败 ticket 仅由取得 lifecycle lock 的真实失败调用持有，等待者取消不清 primary；
  registry 精确比较保护 replacement。终态关闭先宣告启动退役，再等待原锁。
- 原 23 项 startup／SDK 强断言全部收敛，另六项身份／并发／弱引用／重复取消测试
  在精确 cdf 基线上六产品红、候选全绿。独立复审发现并保存首候选非连接阶段
  TimeoutError 误分类两红，以及 dbe 三类终态 close 迟到接管三红；最终六边界项绿。
  相关整轮 113 项、独立 46 项与十二项补验绿，不重复计入标准总数。
- 最终标准 **1,531 项通过**：Node50、QA36、Web231、Core pure964、Jobs pure61、
  Core PG184、Jobs PG5。`pnpm test/lint/typecheck/contracts:check/test:db` 全 exit0，
  SHA 前后不变，无跳过新增强断言。PG 仅新独占可销毁实例，auth TRUNCATE 不触旧库。
  head0046／clients及 idle-in-tx0；自有 fixture 状态分布如实留存，实例正常移除。
- 实际 LiveKit1.13.5／SDK1.1.14／冻结 ASR 业务，receiver-cancel、runtime-cancel、
  同 Runtime 同场 recovery-overlap 各两 fresh child，六项全 exit0。旧 join 均
  ready→SDK connect 返回→public disconnect；四取消例只延 Python callback350ms，
  两重叠例延旧请求 dispatch 来制造当前→迟到旧的真实服务器顺序。当前连接均继续
  保持3.053秒，所有案例20秒观察无 self-SIGTERM 或 parent deadline kill；最终 FFI
  subscriber／queue、SDK join／listener Python task0。overlap只构造／关闭一个当前
  未开 socket 的真实 FunAsrConnection；旧连接无 Provider／consumer／业务回调。
- FD8→18→21，观测主 Python task1，native threads／即时 RSS 未测；不能宣称物理
  资源全归零。没有 PCM、真实 ASR 服务或真人发言。自有 client／server／空 internal
  network 精确 IDs／labels 正常收尾、无 OOM，镜像保留；7dc／dbe准备副本未原生运行。
- 完整报告、Part B 与当前清单在 `profile_output/spec301_asr_implementation/`；原生
  raw／独立核验在 `profile_output/spec301_asr_candidate_native/`。旧 publisher102／
  ASR启动65／身份机制43／close58清单保留，不覆盖成当前通过。

本规格本地验收完成，目标 staging、受保护生产发布、真人 ASR／五场媒体／50 路 LLM／
音色盲听仍未完成。迟到 ASR socket factory 的退役 API 请求、永久 pending／shutdown／
全局 retired 预算和 Track replacement 的原生 EOS 仍为独立风险；不由本规格关掉整体目标。

## 2026-10-15 生产发布终态补充

此前候选／未发布段落保留为当时快照。用户明确批准正常关闭两个旧候场后，
`20261015-asr302-6004cca8`已受保护发布。正常管理员接口保留历史，维护前／停服务后
非终态房间和比赛0；服务器私有备份后升级0046、修复63个终态结束时间并安装已批准
Core/Jobs nofile设置。部署runner及完整预检exit0；独立209文件SHA、Web构建、13路由
和16静态资源核验通过，未登录的3个受保护API均401。300.188秒／11次观察健康、同PID、
无重启、cgroup OOM0及新增warning-or-higher日志0。真实短供应商／RTC／ASR链通过，
完整4v4混合媒体QA仍进行中；未把合成麦克风、短链或五分钟观察当作现场／五场压力。
详见`profile_output/workflow_candidate/release/RELEASE_REPORT.md`和独立生产收据。
