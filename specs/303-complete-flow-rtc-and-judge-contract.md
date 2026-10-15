# Spec 303 — 完整流程 RTC 依赖、Agent 退役记录与裁判输出契约

- 版本：v2.0
- 状态：已实施、完整复验并上线；20261015-spec303-29073b57，发布前3场＋发布后1场完整媒体/归档与五分钟观察全部通过
- 日期：2026-10-15
- 依据：PRD §5／§12.2、TechDesign §7–8／§11.2／§15、已批准 Spec217／267／272／274／301／302
- PRD 工作树删除保持不变，仅只读 HEAD 确认稿作为继承需求来源。

## 问题与实测边界

生产已发布 `20261015-asr302-6004cca8`。现有1,564标准检查及Web门禁通过，
但真实完整混合4v4尚未通过，不能据此宣称完整流程版本已验收。

1. SDK1.1.14 下 full07 正常四辩结束后的原生 disconnect 等待超过500ms清理预算，
   后续主持join14.146秒失败、Agent连接／发布11.382秒，末主持两次13.7秒超时，
   权威ERROR／agent_pipeline_failed。精确SDK任务、Room归属及原异常保留。
2. 同一业务和测试源码、模型、计时、LiveKit服务器下，隔离SDK1.1.20的full08达到
   FINISHED，19个publisher连接均返回、末主持3439ms成功，普通Human／Agent暂停恢复
   检查通过；最终owner／pending0。仍有3个SDK disconnect预算取消，不能宣称全部
   生命周期问题已解决或仅凭一轮证明长期稳定。整体工具exit1，非完整验收通过。
3. full08在完整流程最后检查发现Speech未全部满足FINALIZED／RESET；实际异常状态
   未采且owned DB已清理，不能回填。静态代码显示Agent普通pause取消generation，
   但旧Speech记录的终态回收仅覆盖reset／system.recover，需真实PG强回归先复现。
   不存在名为INTERRUPTED的Speech枚举，不放宽门禁来隐藏未终态记录。
4. full08第一条裁判回复JSON完整、四个顶层字段及8席均存在，1081tokens低于1600；
   expression给出14／16，超出PRD和Core的10分上限，_parse_result拒绝。正常重试的
   最终结果与AWARDS返回未在异常前持久化为receipt，不宣称裁判通过。不能归为截断。

证据：`profile_output/workflow_candidate/real_media_qa/RESULTS.md`、full07／full08
原收据与精确冻结源码。隔离候选Mac SDK下45项现有检查通过；12项ASR检查先因原
fixture强制1.1.14而阻断，仅复制fixture改为候选版本断言后12通过，产品断言未改。
原仓库fixture和失败保留，不把这57项当新版SDK全量标准门禁。

## 三个顺序可验证切片

### A. 固定并验证官方 RTC SDK

- 将Core的`livekit`依赖从1.1.14固定为官方正式1.1.20，并同步uv.lock及准确版本断言。
  依赖修改前保留原pin与wheel／模块SHA；不升级其他依赖或LiveKit服务器。
- 复用现有SDK join独立owner、shield、唯一私有identity、精确迟到关闭与callback栅栏。
  不撤销这些保护，不直接修改SDK源码或把普通取消传入SDK connect。
- 不更改音频协议、PCM采样率、200ms队列、grants、模型、音色、重试和权威计时。
- 当前官方候选映射RustSDK0.9.2／FFI0.12.80，含原生关闭顺序、引用环及重协商修复；
  这只是选择依据，实际完整复验决定能否发布。

### B. 普通 Agent 暂停后的旧 Speech 精确退役

- 先用真实Manager／Actor／独占PG复现：Agent开始播放→普通pause→原恢复事件→
  新generation完成，逐行确认旧Speech状态、身份和是否进入上下文，不手工修fixture行。
- 若确认遗留STARTED／FINALIZING，在现有权威事务边界精确处理被撤销的旧Agent
  Speech为既有RESET终态，保留历史行、结束时间及generation关联。只有当前比赛和
  旧Speech／generation身份匹配时操作；不能让迟到清理更改恢复后新Speech。
- 真人暂停继续保留同一Speech和ASR段；不修改真人语义、Match状态机、事件payload、
  倒计时起点或恢复条件。不扫描或批量修复历史生产发言。
- 已FINALIZED发言保持正式记录；正在由正式finalizer持有spool／完成事务的generation
  必须按既有所有权规则处理，不可由旧退役清理抢占或删除其正式资产。
- 普通暂停撤销并将在恢复时重开的旧FINALIZING发言也只退役Speech记录为RESET；
  不改变finalizer／generation／正式资产所有权。正式收尾仍可持久化generation和资产，
  旧业务回调继续丢弃，不另设绕过Actor／context的新晚到结果通道。
- 若复现证明其他合法状态，记录实际业务原因再更新本切片；不凭猜测扩大实现。

B 切片已取得独占 PG 强红→绿：相同探针 `79675ca1…` 对旧 Manager `c95e7e90…`
的普通播放暂停、held finalizer 和正常历史编辑控制均红，候选 `e464465f…` 三控均绿。
随后真实 formal4v4 `FREE_DEBATE` Action 控制复现既有 late helper 将 RESET 回写
FINALIZED 并增加 context；同探针 `88cbdfe1…` 加入 RESET 立即丢弃后，最终 Manager
`56d5b952…` 保留旧 RESET/context，正式 generation/Asset/file 持久化完成，新 Speech
正常 FINALIZED，原及重复旧 callback 均不影响新发言。未重排未证实死锁的锁顺序。
这些 PG 控制使用真实 Room/Manager/Actor/原 callback/自然约三秒恢复，Provider、codec
和 RTC 为明确有界合成边界；自由子集以正常房主 terminate 收尾，不计完整真实比赛。
最终 runtime owner/pending 0，独占 PG、音频与上传源目录正常回收。新增 21 unit 与
3 个 PG 用例覆盖身份、终态、正式 finalizer、真人同 Speech 和迟到自由 callback；相关
本地 188 项、Ruff/Pyright 通过。精确源、SHA、原失败、exit 与逐行状态在
`profile_output/workflow_candidate/real_media_qa/spec303-b/`、`agent-pause-pg-01` 至
`agent-pause-pg-09-free-candidate/`；完整流程及发布验收仍由后续最终版本复验决定。

### C. 裁判请求遵循既有评分量表

- 在现有裁判请求中明确既有JSON字段、双方稳定ID及评分上限：立论30、反驳25、
  证据20、协作15、表达10，个人0–20。请求约束与现有解析校验复用同一量表，
  不修改分值、冻结用户Prompt或历史快照。
- 双方身份使用既有side枚举，辩手使用8个稳定participant_id；继续原ID去重／全集
  校验，不把合法JSON等同合法评分或身份对应。
- 针对当前模型先做隔离的合成完整历史请求对照，核验结构化输出参数的实际兼容性；
  只采用实测支持的方案，不静默切模型、供应商或接入点。
- 保留原解析校验和一次正常重试；不钳制越界分数、不伪造结果、不绕过裁判失败。
  评分失败仍不改变已完成比赛状态。快速自由决策的非关键格式问题另记，不顺手重构。

## 验收

- 57项连接／取消／迟到owner强回归在正式候选环境运行，版本断言真实匹配。
  标准Core／Jobs及独占PG回归、Lint／类型／契约重新通过；无Web变更时复用原Web包。
- 普通Agent pause强PG红→绿，旧Speech为精确终态、新Speech正常FINALIZED，旧回调
  不能改变新发言；真人pause同Speech、reset和系统恢复控制均通过。
- 裁判越界JSON强失败控制保留；修复后的当前模型合成完整历史请求、真实完整场的
  判据输出都满足既有范围，不靠增大token预算解释已证实的1081token有效JSON。
- 至少3场独立真实混合4v4：8次固定发言、双方Human／Agent自由发言、双方四辩、
  末主持、单次FINISHED、连续sequence、正常Judge重试／有效结果、8席投票及重复
  拒绝、AWARDS；至少一场覆盖普通Human和Agent暂停恢复、一次自然限时收尾。
- 每轮失败也先保存Speech状态计数、Judge终态／attempt、AWARDS及清理聚合再断言，
  不能让测试断言擦掉证据。原full08门禁不放宽，确认实际状态后再核对业务修复。
- 实际Jobs执行自有POSTMATCH_AUDIO任务并核对可解码归档，不能把QUEUED写作完成。
  各runtime owner／新增pending0，自有RTC／PG／音频回收；原有限失败基线单独标记。
- 合成麦克风与物理麦克风分开；不将三场串行当五场并发、50路LLM或11音色盲听。

## 发布与回滚

全部必要门禁通过后按既有用户上线授权走正常受保护发布；先检查非终态房间／比赛／
租约，服务器本地备份，不在真实比赛注入故障。不追加migration或改变0046兼容模块。
精确备份旧SDK包／业务切片及依赖锁；回滚只恢复这些兼容切片，保留历史、schema和
capture读取兼容性。出现新的范围或PRD冲突停止相关实现并记录，不以提前放宽门禁发布。

## 实施与首场验证证据（2026-10-15，仍未发布）

A依赖正式固定1.1.20且uv.lock仅livekit包升级，57强生命周期检查通过。B普通播放、正式finalizer持有与合法历史编辑漂移真实PG红→绿；既有自由辩论late helper增加RESET丢弃，真实FREE action旧版本回写FINALIZED/context+1、最终候选保持RESET/context不变，两轮原finalizer均完成Generation/Asset且文件存在，新Speech正常FINALIZED。未重排原锁；静态逆序风险尚未证明同pause可达死锁。C严格schema采用当前端点实际支持方案，复用量表/个人上限，保留1600token和一次重试，30专项及两次实际API对照通过。最终源567项冻结，标准1614通过、Lint/type/contracts通过；生产仍Spec302。

full10最终候选第1场实际RTC/ASR/TTS自然FINISHED，8次固定发言、双方Human/Agent自由发言四组合、普通Human/Agent暂停恢复、双方四辩和末主持均完成，Judge首次SUCCEEDED、8席投票与重复拒绝、AWARDS、连续sequence/单FINISHED。正规POSTMATCH_AUDIO worker首次SUCCEEDED/回放READY：24输入、462720B，输入/回放48k解码样本均5810112（121044ms）；全owner/pending0、自有RTC/PG/音频回收。第二/第三最终候选完整场及保护发布仍待完成。full09虽FINISHED/有效Judge，但归档工具错误检查inet文本/32而未调用worker，整体失败保留；工具按真实独占PG改host(inet_server_addr())、13强边界检查通过，不把失败改写成功。

## 最终验收与发布

标准1614、Lint/类型/契约通过；full10/11/12发布前三场，full13实际部署版本发布后暂停恢复复测均完整passed，四份实际Jobs回放READY且输入/输出采样差0。209安装SHA独立匹配、SDK1.1.20、同Web构件，300.073秒/11采样健康200、PID一致、重启0、guard0、无新增服务warning。服务器自有PG/RTC/音频/候选stage与唯一失败synthetic baseline精确清理，生产86比赛/102房间/1203发言历史及服务器回滚备份保留。首发布Web Requires依赖遗漏、精确旧源/SDK恢复与补齐Web启动后第二发布成功收据均保留。最终报告：profile_output/workflow_candidate/spec303/RELEASE_REPORT.md。物理mic/正式最长时长/五场压力/50LLM/11音色盲听未称通过。

## 用户追加连续重复模拟（2026-10-15）

用户要求每队2Agent/2人检查反复完赛，已授权测试。追加只测试的5场同进程/同事件循环串行完整短赛，1/3/5场暂停恢复、2/4正常，保留原所有媒体/评分/归档/清理断言和全局任务基线。业务与生产配置不变；结果待本批实际终态，计划profile_output/workflow_candidate/repeated_4v4_20261015/PLAN.md。不把每场重建Manager说成长驻Manager复用或五场并发。

追加测试已终态：同进程/事件循环/SDK五场连续5/5完整passed，1/3/5两类实际暂停恢复，五份正规Jobs归档采样差0、跨轮pending/每场owner0，独占资源与工具目录回收。当前RSS223.7→277.4MiB、FD20、线程13→15，增长原因未确认，保留待核查风险，不写成已证明泄漏或无泄漏。生产源/配置/服务未变。报告profile_output/workflow_candidate/repeated_4v4_20261015/REPORT.md。
