# Spec 267 — 主持语音衔接与自由辩论后续阶段

- 版本：v2.0
- 状态：基础切片已实施、测试并发布；主持恢复、夹具隔离及事件sequence补修于2026-10-15通过1,564标准门禁后发布；真人首字/连续试听仍需现场设备验收
- 日期：2026-10-10
- 范围：主持音首播/衔接、自由辩论结束后的线性阶段推进
- 依赖：Spec 217、223、264、265、266

## 证据与问题

1. 生产启用的 YXH（rule `d2e24b41-47d1-4d33-bba8-ee6c54ff5078`，
   config revision 24）已包含第 7 阶段自由辩论、第 8 阶段反方四辩总结、
   第 9 阶段正方四辩总结，以及结束主持音。配置不缺四辩。
2. 生产比赛 `df919b6f-970e-4ef3-8cd7-9b6a151b13d2` 已 FINISHED，
   runtime action index 仍是自由辩论的 6；快照内的两个四辩动作未执行。
3. `MatchActor._finish_free_turn` 在双方剩余时间耗尽后直接设置 FINISHED，
   没有调用线性动作推进。本地临时模拟同样复现：后面还有 `8:1` 总结动作，
   结果仍是 FINISHED、index 0、事件 `agent.finalized / match.finished`。
4. 主持音由 Web 收到 HOST_ANNOUNCING 后才请求完整文件，随后无条件执行
   `currentTime = (duration - authoritative_remaining) / 1000`。这会将状态传播
   与加载期间消耗的时间变成首播跳过开头的偏移。effect 又依赖剩余时间，
   快照更新可能取消、重取和重设音频。Core 计时在请求前已经开始。
5. 生产前两段主持资产的第一处可测声音约在 50 ms，尾部留白约 240 ms；
   第三段开始约 290 ms。文件可解码且实测时长与 duration_ms 一致。
   这支持播放路径截头与衔接时间不足的判断，但不代替真人首字试听。

## 事实来源与偏差

PRD 要求主持结束后才放行辩手，主持与等待不占用正式发言时长。
TechDesign §6.1 要求每场比赛的服务端输出顺序承载主持、提示和 Agent 音频。
当前 Web 本地主持播放器与 Core Agent 音轨是两条独立时基，存在实现偏差。
本规格明确批准后，将主持播报收敛到现有 Core/LiveKit 音频能力；不新增服务、
供应商、外部存储或数据通道。未批准前停止该业务实现。

## 切片 A：自由辩论结束继续规则流程

- 两方时间耗尽仅结束当前 FREE_DEBATE 动作，提交当前发言结果、清理当前
  opportunity/候选队列/决策与旧 timer，再经 `_advance_action` 进入下一个动作。
- 保留规则原有顺序；YXH 执行：自由辩论 → 反方四辩（120 秒）→ 正方四辩
  （120 秒）→ 结束主持音 → 比赛结束。
- 如自由辩论就是最后一个有效动作，则继续由统一线性推进分支结束比赛。
- 真人与 Agent、纯人/纯 Agent/混合阵容、双方剩余不足三秒的耗尽分支均一致。
- 旧自由辩论回调不得触发已进入总结的阶段；不得重复归档或提前创建赛后任务。
- 不修改当前规则阶段配置、已有房间快照或已结束比赛，不复活历史漏掉的总结。

## 切片 B：主持完整播报与衔接

- 复用 Core 的 PyAV 解码与 LiveKit PCM 输出能力，把主持预生成资产预读并顺序
  放入比赛级有界输出调度；Web 在此模式只订阅媒体，不再额外播放同一主持文件。
- 按 TechDesign 收敛服务端主持/Agent 输出生命周期，复用既有模块；不另建服务。
- 默认每段主持正文前保留 800 ms 过渡静音、正文后保留 600 ms 静音。
  音频文件本身保持不变，过渡 PCM 由运行时加入；重新试听后可在规格内校准常量。
- 首段主持完整解码并从第一帧播放，避免 HTTP 请求耗时被当作正文 seek 偏移。
  同一播报期间更新快照不重新加载或重播。预读只缓存当前和下一段，容量/超时有界。
- 主持结束以服务端输出队列排空与有效完成回调为依据；开始超时/播放故障按既有
  一次重试、再次失败恢复保护处理。总超时为保护上界，不得先于真实播放完成放行。
- 上一辩手有效收尾后才开始主持；主持尾部静音和队列排空后才放行下一辩手。
  不改变真人点击开始、Agent 第一帧有效语音入队的正式计时起点。
- 静音过渡、主持加载和播放不扣任何辩手或自由辩论时间。
- 暂停/终止须先栅栏旧媒体并取消任务；恢复由 Core 权威重建当前位置。重连观众
  订阅当前流，不触发全场重播或改变其他人的阶段。仅支持可操作的声音开启提示。
- 播放回调携带 action_key 与现有 CallbackEnvelope 身份信息，校验 match_id、
  speech_id（可空）、attempt_no、generation_id、connection_epoch、context_version；
  旧播放完成回调不得放行新动作。使用独立播报身份，不能混同 Agent generation。
- 如需增加媒体来源/播报状态 DTO，只修改 Core schema 并生成 contracts 类型。
  保留历史 asset HTTP 读取与原快照兼容；协调部署避免旧 Web 双重播报。

## 验收

1. 先复现并补测试：自由辩论结束后仍有固定发言/结束主持动作，不能提前 FINISHED。
2. 覆盖真人/Agent 最后一轮、双方不足三秒、末段自由辩论、暂停恢复、迟到回调，
   并验证总结阶段正确 speaker 与完整计时，最后只有一次 match.finished/赛后排队。
3. 主持测试覆盖首帧、前后静音、状态更新不重播、播放失败重试/保护、队列有界、
   暂停清理、恢复定位，以及主持未完成时禁止 Agent/真人开启正式发言。
4. 完整 YXH 4v4 模拟必须包含八位固定发言者与自由辩论、两个四辩、结束主持音；
   不能复用以自由辩论为末段的简化模拟代替该验收。
5. Core/Jobs/Web 定向与全量回归、类型、Lint、契约、仓库 pnpm build、专项浏览器
   门禁通过；独立 TEST_DATABASE_URL 验证提交顺序/赛后排队。
6. 专用线上 QA 完整跑过自由辩论和双方四辩；采集服务端播放/阶段事件顺序与音频
   样本，并分别试听连续、首入、暂停/重连的主持首字和辩手衔接。供应商失败须
   如实记录，失败暂停不可报告为完整完赛。

## 发布与回滚

- 用户批准后按 A/B 可验证切片实现；生产活动比赛为零时协调发布 Core 与 Web。
- 保留仓库外源码和 Web standalone 回滚点；核验 import path/hash、live/ready、
  四服务、公网页面和至少五分钟观察。没有必要不新增 migration。
- 回滚恢复对应源码/standalone 并协调重启；不改历史比赛、旧音频或规则快照。
- 未批准前只做文档与只读诊断；此次不对生产业务配置或历史数据进行修复写入。

## 实施与发布证据（2026-10-11）

- 切片 A：`MatchActor._finish_free_turn` 在双方耗尽后清理自由辩论状态并调用
  `_advance_action`；本地完整 YXH 顺序模拟通过：自由辩论 → 反方四辩 → 正方四辩
  → 结束主持音 → 单次 `match.finished`。
- 切片 B：Core `AgentRuntime` 复用 PyAV `IncrementalOpusDecoder` 与 LiveKit
  `AudioSource`，主持音前置 800 ms、尾置 600 ms 静音，队列上限沿用 200 ms；播放完成
  以 `wait_for_playout()` 后的独立 `CallbackEnvelope` 回调推进 Actor。播放失败只重试一次，
  仍失败进入既有恢复保护；暂停/终止先栅栏并清理主持任务，恢复由 Core 重建播报。
- Web 删除 HOST_ANNOUNCING 的 HTTP 拉取、seek 和本地 `<audio>` 播放，只订阅 LiveKit
  服务端音轨；历史主持音频 HTTP 路由保留兼容读取。
- 本地验证：Core `422 passed, 76 deselected`；Jobs `25 passed, 2 deselected`；Web
  `52 files / 214 passed`；定向 Ruff、TypeScript、ESLint、Pyright、生产构建和
  `git diff --check` 通过。`pnpm contracts:check` 仍受本机 pnpm 忽略依赖构建脚本阻塞，
  手工 OpenAPI 导出/生成与比较无差异。
- 线上发布：零活动比赛预检通过；Core、Web 备份分别保留在
  `/opt/jixia-spec267-final-20261010233800` 及其 `web-live-old`；Core 四个文件哈希
  与本地一致，Web standalone 构建标识 `8LwdMp0i1PJetz_WCQt1I`，完整 1102 文件哈希
  校验通过后原子切换。
- 线上核验：`jx-core`、`jx-jobs`、`jx-web`、`jx-livekit` 全部 active，Core live/ready
  200，migration `0044_rule_audio_generations`，`active_matches=0`，四服务
  `NRestarts=0`；10 次、每 30 秒健康采样全部通过。公网 `/debate` 返回 200，未登录边界
  正常，浏览器页面没有本地 `<audio>` 元素。
- 尚未宣称真人设备试听、真实比赛的首字/衔接和供应商短链路通过；这些仍是发布后的现场验收项。

## 2026-10-14 主持暂停恢复补修切片

此次补修落实已批准切片 B 的「独立播报身份」「旧播放完成不得放行新动作」及
「保护总超时不得先于真实播放完成」，不新增产品流程、模型、协议或正式发言计时。
原发布及 Spec 300 的历史验收证据保留，不推断为本补修后的最终回归。

### 已复现缺口

- 同一比赛替换主持任务后，抵抗取消的旧任务 `finally` 无条件按 match id 删除登记，
  可以删除仍在播放的新任务句柄。
- Manager 的主持回调仅检查 action/context 和非空 generation。同 action 的恢复后，
  旧 generation 完成会误放行下一位，旧失败会把恢复中的比赛改为 ERROR。
- 主持从文件首帧重新播放；Actor 恢复保护仍按暂停前剩余长度计算。60 秒文件加
  原 800/600ms 静音的模拟调度为 61.4 秒，而暂停后仅余 1 秒时保护窗口为 31 秒。
- 初播的 duration＋30 秒保护也未覆盖长文件的两次完整尝试。每次既有播放预算为
  `max(10s, duration + 12s)`，必须将一次重试及有界清理纳入保护上界。

### 范围与不变量

1. 复用现有 Core 模块增加纯主持时间预算 helper。每次播放预算、两次尝试、50ms
   重试间隔和静音/媒体缓冲不变；初播与恢复保护统一覆盖完整资产的两次既有播放
   预算及每次至多 3 秒的清理，并保留既有 30 秒启动／派发余量。恢复时主持展示
   剩余值也按完整重播长度重建。
   这仅修主持保护，不改变真人手动开始或 Agent 首个有效 PCM 的正式计时起点。
2. `HostAudioRun` 保存当前尝试身份。注册句柄、match/action/generation/attempt/context、
   适用的空身份、媒体 fence 和关闭状态必须一致；在连接、解码、排空等 await 后及
   每次重试/回调前重新检查。被 fence 的旧任务不能自行解除 fence 或启动第二次播放。
3. 旧任务只能清理自己持有的媒体及 spool；退出时仅按对象身份删除自己的登记，
   不能删除、关闭或重建替换任务。每次清理沿用有界资源所有权，不累积替身任务。
   暂停栅栏和重置入口不能先无限等待旧主持 PCM 持有的媒体锁；挂起的 native
   capture 须先失去回调资格，再按已有取消／清理预算收尾。底层吞取消的物理资源
   释放能力须如实记录，不能把有界等待声称为强制终止 native 工作。
4. Manager 先做乐观身份核验，并在数据库 await 后复核。完成/失败命令携带私有身份，
   在既有 Actor `pre_commit` 串行窗口再次核验，覆盖「检查时合法、入队后过期」竞态。
   实际事务等待 Match 行锁后还须按锁定行的 context 与当前登记身份复核，避免
   等待锁时已替换的播报结果落库；保持既有锁顺序。不匹配时不提交状态/事件、
   不重试真实播放、不公开内部 envelope。
5. 仅主持保护回调补齐跨暂停/恢复的身份核验；已入队的旧保护不能误暂停新播报。
   主持保护提交失败后的应急回调同样携带并核验原保护身份，不能通过裸错误命令
   绕过栅栏。保护身份更新与 Actor 事务回滚一致，不泛改其他阶段 timer 或状态机。
6. 不新增 migration、公开 DTO、外部依赖、队列基础设施；不改既有音频资产、规则或
   历史比赛，不处理真实非终态房间，也不触发生产重启/发布。

### 验收与回滚

- 公共 start/reset/fence 下覆盖取消抵抗、替换句柄保留、旧成功/失败/重试、排空后
  fence，以及 generation/attempt/context/空身份不符时的拒绝。
- 实际 Manager/Actor 覆盖旧回调、已经排队的旧回调和旧保护；状态、sequence、
  提交事件不变，新回调只放行一次，权威提交失败仍回滚并保持有效保护。
- 初播、晚暂停后恢复、长文件第二次完整播放均验证预算覆盖；未知历史时长保留
  可操作且有界的失败保护，不以剩余时长代替完整文件。
- 运行对应 Core 测试、独立 PostgreSQL 事务验证、Ruff/Pyright、标准回归和契约检查。
  模拟 PCM 不是设备试听或五场真实媒体，生产继续遵守受保护发布门禁。
- PostgreSQL 夹具须仅收尾本次精确 UUID 的合成房间／比赛，关闭异常也不能跳过
  终态化和自有活动容量归零检查；不能依赖后续认证测试的全表清空掩盖顺序依赖。
  含认证 migration fixture 的数据库总回归会执行 TRUNCATE，只能使用新建可销毁
  实例，不能复用保留证据和 ADMIN 的 QA 库。
- 补修无数据库变更；源码回滚恢复补修前构件及其已记录的主持恢复风险，不删除
  历史数据或修改资产。线上回滚也必须在受保护窗口执行。

### 补修实施与本地验收证据（2026-10-14）

- 已修复替换任务登记所有权、完整播报身份及 await 后复核、Actor 串行与 Match
  行锁／flush 后的旧回调拒绝、旧保护和提交失败 fallback 的身份栅栏。私有身份
  只在内部 MatchCommand typed 字段中传递，实际 PG 公开事件 JSON 序列化通过。
- 初播和恢复共用完整资产预算。60 秒资产保护为 180.05 秒：两次原 72 秒播放预算、
  每次至多 3 秒外层清理、50ms 重试及既有 30 秒启动余量。主持静音、媒体缓冲、
  模型／音色、正式真人手动开始与 Agent 首个有效 PCM 的计时规则不变。
- 挂起主持 PCM 不再阻塞暂停栅栏／重置入口的共享媒体锁；每个主持 run 仍串行发送。
  迟到返回只清理该旧 source，旧 copy 真实线程结束后只删除 generation/attempt
  专属 spool，不影响新 run。没有修改共享 publisher 或其他 Agent 媒体锁。
- 原 8 项产品强红、实际行锁 3 项强红、旧保护 fallback 1 项强红均已复测通过。
  原挂起帧日志保留 6 项直接产品断言、2 项掩盖断言的 fixture 收尾错误及 4 个 warning，
  不写成 8 项干净红检；改正收尾后 8 种中断组合全绿。
- 34 项主持纯模拟、310 项相关组合及 8 项实际 PG 专项通过；共享夹具仅终态化自己的
  精确 Room/Match UUID 并保留行、seed 与采集证据，5 项关闭异常检查通过。
  主持 8 项与成员组合 28 项正序／反序各 36 项通过，消除对认证全表清空的顺序依赖。
- 最终标准计数为 1,425：工具 50、QA 36、Web 231、Core 非 DB 858、Jobs 非 DB 61、
  Core PG 184、Jobs PG 5。新 5 项夹具检查后单独完整复跑 Core 非 DB；其余不变组件
  采用本轮 pnpm test 结果，不重复计算专项／组合。Lint、Ruff followup、全仓类型、
  新测试 Pyright、契约及 git diff --check 通过。Web 无本轮业务变更，此前 Storybook／
  浏览器／build 证据保留，未重复报告为本轮重新运行。

第一次数据库总入口误用了保留合成证据的本机 QA 库；既有 auth migration fixture
会在每例前后 TRUNCATE。该次 Core 157 绿／27 容量失败原日志保留，生产未触碰。
此可避免的操作失误已记录，不能声称原 QA 数据完整保留。后续使用新建、标签与 ID
核验的独立可销毁 PostgreSQL 实例；正式全量 Core 184／Jobs 5 全绿。该实例检查
head 0046、其他客户端连接为零、自有 fault 房间全终态后正常停止并仅移除自己的
容器与匿名卷；共享镜像和旧 QA 库未在收尾中修改。部署文档补齐两个破坏性测试入口。

四个补修业务文件最终 SHA256 及真实补修前构件、专项红绿日志、定向 diff、临时
工具与数据库收尾清单在 `profile_output/spec300/host_recovery_scope267_report.md`
和本轮独立 manifest。旧 Linux bundle 的三个源文件 hash 与补修前实证完全一致，
可作本地源码回滚基线，原 bundle 未改。

仍有边界：已进入 native capture 的一帧不能撤回；持续吞取消的 native 调用／copy
可能在有界权威返回后存活，重复故障尚无新增全局退役准入政策；共享 publisher 在
connect/publish 中途失败前的局部 Room/source 回收仍未实证。没有把这些写成资源
强制归零或所有输入无卡顿。目标 staging、真实完整混合 4v4／五场媒体／50 路真实
LLM／真人 ASR／音色盲听与受保护生产发布均未由本补修完成。

## 2026-10-14 完整流程事件连续性补修

继承本规格已批准的自由辩论进入／主持交接／四辩继续范围，落实既有权威事件连续
且按生成顺序提交的契约，不新增状态、计时规则或决策行为。当前 canonical 4v4
双方各2真人2Agent，同一Actor六个正常／故障恢复场景都走到两位四辩和FINISHED，
随后连续事件强断言失败：主持进入自由阶段先生成decision再生成opened，却反序
返回；复用初始opportunity时构造但未返回started，消耗一个未持久化sequence。

精确范围仅 `matches/domain.py`：先生成并返回opened再生成decision；复用已有
free_debate.started时不调用_event生成无用事件。保留事件类型／payload、主持／
计时起点、hand与Agent选择规则、所有状态变更及数据库提交后广播的边界。
不靠前端容忍缺号／排序掩盖服务端问题。

验收保留六场同一比赛的连续sequence强断言，并验证候场二次开始、人工开始、
八个固定发言、十二自由回合、反四／正四总结和单次FINISHED；两轮trace及相关
Actor／规则／恢复／真实新独占PG门禁另行记录。未执行真人ASR／真实TTS媒体不称
通过。原完整domain `7a7381f471478cadd175bd0af9dd98d4bb7e0756a31be271e6488a5bc387cd63`
保存在 `profile_output/workflow_candidate/domain_before.py`，回滚只恢复本序号切片，
保留之前主持／ASR修复、旧数据和migration；生产依既有非终态门禁发布。

事件连续性候选 `7f29dc1303214d3fb414fcc348275e1ddb0aff69b2177b4b3b475b48ab54debf`
已本地通过同一场六场景连续两轮（12场、240次合成发言），以及202项相关回归。
新独占PG服务测试也完成候场／权限／二次启动、真实主持及ASR身份栅栏、20次发言
事务、双方四辩、单次FINISHED和赛后评委／8席投票／重复拒绝／颁奖。
独立只读审查确认本delta只改变事件sequence及对应server timestamp分配顺序，
没有改变业务状态、计时、类型或payload。证据在 `profile_output/workflow_candidate/`。
该轮ASR Runtime为e785，不能替代后续217/274候选的完整门禁；模型／PCM／RTC
为合成边界，不宣称真人、真实TTS或生产已通过。

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
