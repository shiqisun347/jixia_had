# Spec 305 — Agent 自然发言的实时尾音保护

- 版本：v2.0。
- 状态：已上线 `20261015-spec305-24ccc540`，安装后真实浏览器尾音与每队2模拟真人+2Agent完整比赛均复验通过。
- 日期：2026-10-15。
- 来源：用户报告 AI 发言即使仍有剩余时间，最后几个字也常被吞，末尾有突然卡断感；用户确认目前观察为实时播放，尚未对比赛后回放。
- 播放设备补充：用户确认主要为电脑内置扬声器或有线音箱。
- 依赖：Spec217/267/274/303/304；PRD 历史需求、TechDesign §9 与语音 Spike。

## 1. 调查事实与边界

1. 生产仍为 Spec304 后端；大屏 Web 为 `HX2A8FaQQj1tYWmwcfx9d`。
   当前三个服务健康、Core/Jobs 未重启，不能把此次界面修改认作媒体代码修改。
2. `AgentRuntime._execute` 解码结束并等待 `AudioSource.wait_for_playout()` 后，
   设置 `natural_complete` 并提交 `agent.playback_finished`。现有 finalizer 随后关闭
   AudioSource 和 publisher Room；Agent 没有主持音频已有的 600ms 尾静音。
3. 固定 LiveKit Python SDK1.1.20 的 `wait_for_playout` 等待本地采样/时钟维护的
   Future，不是浏览器已经听完的 ACK。浏览器收到取消发布时，LiveKit JS SDK 会
   stop/detach 远端音轨。普通 `speech.finished` 本身不会重建网页音频连接。
4. 合成 Ogg 三种长度（48000/65333/144173 样本）实际 PyAV 增量解码与完整解码逐字节
   一致，最后500ms及不足20ms的最后帧保留；这只排除这些合成控制的 decoder 漏尾，
   不证明真实供应商输出或远端播放完整。
5. 既有混合比赛 QA 检查音轨有帧/非静音及源文件到 Jobs 回放一致，没有逐发言的接收端
   尾音完整性门禁；其通过不能排除实时尾音被取消发布截断。
6. 生产只读聚合18条近期Agent发言，精确关联发言结束事件：10条COMPLETED且
   `audio_truncated=false`，正文时长比配额少506–8470ms；另7条到时完成、1条到时后重置。
   查询按开始时间窗口筛选、观察时读取最新状态，不能称窗口截止时的冻结快照。
   因此不能只用发言到时解释。供应商 close 超时与旧音色纠正日志独立保留，
   尚未证明它们是本次吞尾的原因。原 Speech finish_reason 的旁支数据缺口另记，不扩展本修复。
7. 本地真实 SDK/独占LiveKit共10轮交替对照已完成：5轮无保护时本地排空后仍有
   50/30/60/40/80ms尾标记到达；最后标记比排空晚66.685–89.733ms，且晚于取消订阅。
   5轮600ms静音保护时尾标记均在排空前完成、排空后标记为0。
   接收器会继续消费取消订阅后的旧stream，本轮未测浏览器SDK立即stop/detach，
   不能把正弦Opus/PLC分类的±30ms差异直接当成真实丢字，也未复现用户所述多字缺失。
   该对照证明收尾边界早于远端尾音；若真实末句候选不能改善门禁，应停止并继续定位。
8. 另一本地真实native Source/Stream控制发现，不足10ms的最后一帧可能仍留在native
   分帧buffer中，而本地wait已返回；最大479样本（约9.98ms），补齐到20ms后收齐。
   这也不足以解释多字缺失，但说明保护需要实际PCM推出余帧，单纯sleep不能代替。

## 2. 修复范围

只修复自然完成后、关闭 publisher 前的音频收尾，不改变文本模型、TTS 模型/音色、
供应商接入、计时起点、重试规则、权限、媒体协议或数据库结构。

1. 复用既有 `AGENT_FINALIZING`：正文 PCM 排空后仍按原有回调冻结比赛发言预算、取消
   发言计时器。不要在 `_execute` 的完成回调前插入600ms等待，以免无声收尾消耗发言配额，
   将本已完整的发言变成 TIME_LIMIT。
2. 仅在 `reason == COMPLETED` 且 `run.natural_complete` 时，在现有 finalizer 的
   `_persist_final`、移除运行句柄及 `finalize_agent_speech` 回调之前，给该运行的同一
   AudioSource 顺序提交600ms的20ms静音帧并等待排空。保护不落入TTS源文件，不增加
   `played_samples`、音频正文时长或文字，沿用200ms有界source队列与原15秒收尾上限。
3. 复用当前运行登记、generation、完整 CallbackEnvelope 与 source 对象身份校验；
   每次异步 capture/排空前后重验，暂停、重置、终止后立即退役旧保护，禁止旧发言
   完成回调推进新动作。锁只围绕精确媒体检查/单帧提交，不跨 Actor 回调持有媒体锁。
   同时明确producer→finalizer的所有权交接：`_run_with_retry` 的现有 finally 会在
   `_execute` 正常返回时移除未fenced运行，不能让它提前移除仍在保护的自然完成运行。
   原producer已结束而finalizer仍等待时，登记必须继续属于该精确运行，最终由权威
   finalizer或中断清理移除；异常/取消没有成功交接时也必须保持既有可恢复清理。
4. TIME_LIMIT、错误、中断、重置与取消不走自然尾音保护，保留即时停声与既有恢复语义。
   不在 finalizer 内等待或取消正在等待 Actor ACK 的 `run.task`，避免互等。
5. 网页继续沿用 SDK 音轨生命周期；不通过延迟移除 DOM、忽略终态或弱化正式站测试限制
   掩盖服务端收尾问题。主持音频、真人麦克风及历史音源不改。
6. 保留自由辩论既有3秒举手窗口和可提前选席行为。若尾保护异常跨过窗口，不要求
   Actor.current_speech永远等于旧speech；保持旧发言精确运行/source的合法收尾与现有
   source-finalized门禁，后继正文不能在旧source尚未收尾时开始，旧回调不得推进新owner。

600ms 是复用已批准主持收尾的初始保护值，不承诺覆盖任意公网延迟；必须由下面的
接收端尾标记与实际语音对照证明有效，不能以 sender 本地排空替代。

## 3. 验收

1. 真实固定SDK/独占LiveKit对照：相同且可独立辨认的编码尾标记源，交替执行无保护/保护，记录内容样本、
   sender排空、接收端末尾标记与disconnect时间，保留每轮原结果。检查接收端实际尾音，
   不仅检查总RMS或“收到过音频”；稳态正弦可能被PLC延续，不能单独作为完整性门禁。
   有保护连续至少10轮满足固定尾标记门禁。
2. Core定向回归：正文预算在保护前冻结；保护完成前不开始后继正文；正常保护完成仅一次
   正式收尾，正文样本/时长/源文件不变；短末帧、两种阵营和固定/自由发言覆盖。
   强控finalizer首次capture/排空不完成、让producer正常done，验证精确登记仍存在且
   最后仅一次收尾；再验证交接失败、暂停及替换run只清旧owner。
3. 故障与身份回归：逐帧及排空期间暂停/重置/终止、重复取消、旧generation、旧source、
   capture阻塞/失败、排空超时；旧媒体/完成回调不得泄漏到新动作，资源按既有有界清理。
   自由辩保护跨3秒选席窗时，合法提前选席和旧finalizer晚到并存，后继source门禁不丢。
4. 至少3次受控固定末句的实际TTS→增量解码→RTC→接收端音频对照，同时保留合成源和
   接收端最后一句，区分供应商未生成与传输截断；另完成每队2模拟真人+2Agent的完整比赛，
   包含自由辩论、双方四辩、结束主持和回放，检查自然完成及到时路径。
   实际语音允许有界单调分窗对齐以核验NetEQ时间伸缩，尾部相关性阈值保持0.60/0.45；
   唯一性须对应完整1秒尾链，不能把另一处相似短音视为完整尾句。入口120ms只作声源
   存在检查，不承诺首个音素、整头或中段无失真。新判读协议、工具和强阴阳控制须先冻结
   SHA再开展新的资格录音，包含三个实际源裁掉最后可辨识160/240ms的阴性；旧固定偏移
   检查、各次失败和不适用边界原样保留，不回填为通过。
5. 运行与变更相称的Core完整单元/独立PostgreSQL回归、Lint和严格类型检查；如不改Web、
   契约或Jobs，不把旧Web构建/其他套件重复运行当作尾音证据。
6. 用户批准后按此前上线授权发布最小Core切片：活动比赛/房间保护、冻结源SHA、兼容源码
   备份、安装SHA与健康检查、安装后受控尾音验证。只读调查本身不重启服务或修改历史数据。

## 4. 回滚

无migration、配置或历史音频改写。保留对应旧Core源码/安装SHA；需要回滚时在无活动
比赛/房间的既有发布保护下恢复该Core切片，按systemd依赖恢复并核验服务。
保留当前已上线的大屏Web，不运行整栈旧publisher覆盖界面或主持资源。
任何回滚不删除比赛历史、测试失败原件或回放音源。

## 5. 证据与参考

- `profile_output/spec305/decoder-tail-control.json`：仅合成decoder控制，3项通过。
- `profile_output/spec304/tail-audio-ro/`：本轮只读线上脱敏聚合与服务检查，时间范围以收据为准。
- `profile_output/workflow_candidate/spec304/audio-tail-investigation/local-rtc-tail.json`：
  固定SDK真实RTC10轮对照；自有容器已删除、消费任务/stream已关闭、最终FFI订阅0。
- 同目录 `native-decoder-tail.json`、`REPORT.md`、`FINAL_RECEIPT.json`：
  native不足10ms余帧与解码控制、调查结论、源码前后SHA和清理冻结收据。
- [LiveKit AudioSource官方实现与语义](https://docs.livekit.io/reference/python/livekit/rtc/audio_source.html)。
- [百炼Qwen-Audio-TTS服务端事件](https://help.aliyun.com/en/model-studio/qwen-audio-tts-server-events)：
  供应商任务完成与浏览器播放完成不是同一边界。
- [LiveKit项目中的类似末词截断报告](https://github.com/livekit/livekit/issues/4737)：
  第一手SIP场景报告，仅作为调查线索，不作为本系统Web端根因证明。
- [WebRTC NetEQ官方说明](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/audio_coding/neteq/g3doc/index.md)：
  自适应缓冲会加速或减速音频，缺包时也可能生成补偿音频；单一全段时间偏移的波形
  比较不具备时间伸缩不变性。具体录音的因果仍需实际计数和原PCM支持。

## 批准记录

用户明确选择“批准 Spec 305，修复、验证后上线”。沿用上述范围、真实末句改善门禁及
现有生产发布授权；不需要就已批准的实现、隔离测试或符合门禁的发布重复请求确认。

## 发布前实施进展

- 最终候选 Runtime `57a13e7a…`：45新增专项、482项Agent相关纯测试通过；自然尾保护、
  producer/finalizer交接、精确registered对象及source保护、异步持久化后退役重验均已实现。
- Service `f4d6f470…`：自由辩论后继真人启动前补前源Speech精确DB状态门禁，沿用
  `asr_finalization_pending`。先由真实ASR receiver准入路径复现红，再验证拒绝提前启动、
  旧发言收尾后以最新context开始；11新增PG及23既有专项通过。
- 10对真实编码尾标记：无保护0/10、候选保护10/10，10对均改善；全部同源，固定阈值，
  自有容器、任务、stream与FFI清理通过。采用native取消订阅截止近似，不冒称浏览器或实体试听。
- 首轮实际百炼三固定末句配对：同源无保护与保护尾部均完整，改善0，工具如实
  `exit1/no_improvement_evidence`，另有2个早段相关性检查失败。原结果与Ogg/PCM保留于
  `profile_output/spec305/remote-tts-01/`；不能作为本轮上线资格，继续浏览器播放边界调查。
- 完整Core首轮1319纯测试有2个失败：新增准入检查读取了旧固定启动夹具缺失的
  `free_source_speech_id`，caller在SDK join前直接AttributeError。仅补合法None字段，
  原超时、迟到join与清理断言不变，原单文件12项通过；完整第二轮另存，不覆盖首红。
- 私有最小Core发布工具已独立审阅，30项真实临时目录控制和严格静态检查通过，
  包含双文件回滚、源SHA竞争、报告失败后启动与重新开放后的活动保护；目前尚未执行发布。
- 完整Core第二轮1,569项唯一测试通过（1,319纯测试、250独立PG），失败、错误与跳过均为0；
  315源文件前后SHA一致，独占PG已清理。Ruff、严格Pyright通过。
- 同一冻结候选完整混合比赛通过：每队2模拟真人+2Agent，15次发言、双方四辩、自然结束、
  到时路径、裁判评分、颁奖和实际Jobs回放均通过；不等于实体麦克风或正式全时长压力验收。
- 另两种近期主要音色longyingtao/longanlang各3固定末句同源对照，12次native接收尾部均完整，
  改善0，仍为`no_improvement_evidence`；早段相关性原失败保留，不放宽门禁。
- 线上10条自然完成且未截断的20–118秒原始Agent音源只读核验：独立FFmpeg解码样本数
  与各自资产的正文PCM样本数全部精确一致，未发现这些长音源的增量解码漏尾。
  未下载比赛音频/正文、未调用供应商或改写数据；不能据此推断用户实际听到的尾音完整。
- 真实浏览器HTMLAudioElement/SDK stop-detach对照正在执行；使用既有3条公开测试末句音源，
  不改网页SDK生命周期、不保留旧srcObject、不新增供应商调用。有效改善门禁仍未解除。
- 浏览器第一轮在第二段订阅超时：录音工具对captureStream轨调用stop会结束共用的SDK
  底层receiver，已用同SDK本地控制复现红并仅修私有工具清理，业务Web未改。旧红保留；
  独立线上只读检查该轮自有房残留0。
- 浏览器第二轮六段实际录音及资源清理完整，但固定时间偏移的波形检查仍为failed。
  分窗复核发现末1秒存在约64ms伸缩，某保护组末120ms实际相关性0.9465，单一全段
  偏移却造成尾帧反相；不能把该误判或局部改善数当成通过。继续验证有界单调对齐和
  缺尾阴性控制，原阈值及失败收据保留，不以任意时间变形掩盖真实缺词。
- 13:56:39 UTC生产复查三服务健康、无重启、122安装源及Web不变；仍有暂停比赛和
  非终态房间，未停服、未关闭任何现有房间。


### 最终候选浏览器证据（发布前）

冻结协议 `spec305-bounded-tail-onset-v3` 和35项强阴阳控制；新独立处理器按累计PCM
坐标检查receiver末1秒、完整主链包络、anchor/partial及actual/matched首120ms，
含128sample边界及相邻块。所有其他原门禁保持，正文外时钟异常另行保留诊断，
不宣称全句无损。第06轮仍保留原整段时钟检查失败；第07轮保护尾音均完整，但
1条首段presence锚超界，整轮仍failed，原始录音与只读后续定位保留。

新策略 `QUALIFICATION_POLICY_V3.json` SHA `2faaf965…` 在新录音前冻结；
`browser-tail-buffered/actual-browser-08/` 实测六段400ms目标缓冲，实际累计计数证据
全部通过。3组同源无保护均缺尾，保护后3组尾部完整，6条有限首段presence均通过；
全段采集counter也连续，SDK实际取消订阅/清srcObject、185/122/3源前后审计、子进程、
自有房间及媒体owner清理全部通过。原固定偏移判读仍failed且未改写；独立v3资格
`TAIL_QUALIFICATION.json` SHA `9897e51b…` 为passed，独立原PCM逐分数/NCC/计数/
证明区间核验 `INDEPENDENT_FINAL_QUALIFICATION_REVIEW.json` SHA `4094f366…`。
此证据只证明受控接收缓冲条件的尾音改善，不确证生产症状唯一原因、全句保真或实体音箱试听。

代码和完整回归独审：`release/INDEPENDENT_CODE_REVIEW.json` SHA `7a5d9e9a…`。
生产当前仍为Spec304；暂停比赛766345和候场034835尚未关闭，不能忽略已有活动保护
直接停服。结束现有暂停比赛不可恢复，需对这两个明确目标单独授权，历史保留。


### 实际发布

用户明确批准结束暂停比赛及两个指定旧房、保留历史后发布。正常管理员接口两次200，
目标比赛及房间TERMINATED，历史计数保持，本次60秒管理会话已精确撤销。终态房间
仍保留旧ADMIN SPECTATOR租约；经独立审阅，使用正常终态WebSocket重连并关闭完成
142→143精确释放，事件及历史计数保持，发送比赛命令0、直接租约改写0。

私有发布工具严格四阶段空闲检查全部通过（非终态房/比赛/租约均0），正式安装两个
冻结Core文件，原112房/95比赛/1336发言和schema0047前后相同。双旧源码备份位于
`/opt/jixia-release-backups/20261015-spec305-24ccc540`；Web构件HX2A不变，三服务
active/0自动重启，live/ready及Web健康通过。原启动器参数替换语法错误发生在安装前，
未启动业务工具；仅修启动器并本地compile后重启，原失败收据保留。

安装后只读核验122源前后匹配、原Web及schema不变。发布完成后终态浏览页又建立1条
租约，非终态房/比赛仍0；不能将此新阅览连接误记为发布前活动，也不强行清理。
终态事件连接无TTL且不会自动结束，属于保留的既有运维限制，尚未变更协议或业务。
实际installed-browser-01及每队2模拟真人+2Agent完整比赛已启动，结果完成前不记通过。


### 安装后最终验收（2026-10-16 北京时间完成复核）

- `installed-browser-01` 使用实际安装的Runtime/Service、同一冻结2faaf策略：3组无保护
  真缺尾→3组保护尾完整，6条有限首120ms存在性通过，全部采集块连续；实测缓冲
  344.9–397.2ms，SDK实际取消订阅、185/122/3源双审计、FFI/任务/自有房清理通过。
  TAILQUAL `0be2eb50…`，F独立原PCM/计数/证明窗复算 `9d57bf94…` 通过。
- 安装后完整混合赛 `mixed-installed-01` 自然FINISHED，230405ms，双方各2模拟真人+
  2Agent，实际16次发言全部FINALIZED（固定8+自由8），8个Agent生成FINALIZED，
  双方四辩、结束主持、裁判、AWARDS及实际Jobs归档通过。26源=16发言+10主持，
  6,629,976样本，回放解码与正文源差0。6自然收尾650–707ms，2次到时仍走原取消路径。
  权威错误0；测试PG/音频/RTC房已清理，Agent/Host/ASR/Actor/任务owner和新增pending均0。
  有2条既有 `tts_connection_cleanup_timeout`，未阻止完赛，原警告完整保留。
- C独立真实服务器安装复核 `INDEPENDENT_INSTALLATION_REVIEW.json` SHA `f8da4c23…`：
  122源、新双SHA、旧双备份/700目录、SDK1.1.20、Web构件、四阶段历史/schema/空闲门禁、
  三服务与健康均正确；不把后续终态阅览租约1记为0。
- 最终生产只读15:59:21UTC（23:59:21北京时间）：三新PID稳定、0自动重启，live/ready/Web200，
  122源前后匹配，非终态房/比赛0，自有测试RTC房0；终态历史浏览仍有1正常新租约。

发布前和安装后各完成一场混合比赛，不冒称五场压力、50路LLM、180秒实体麦克风、
11音色盲听或实体音箱验收。受控缓冲下尾音改善成立，未证明所有网络/设备的唯一故障原因。

安装后混合独审 `e3537f94…` 31项通过，原件SHA稳定；主持断连及终局回调的中间取消观测保留，最终FINISHED及资源归零符合验收。完整交付链见 `release/FINAL_DELIVERY.json`。
