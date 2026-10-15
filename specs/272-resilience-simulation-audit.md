# Spec 272 — 多场景重复模拟与风险审计

- 版本：v2.0
- 状态：已完成模拟／恢复链及显式采集风险探针；已修复项见 Spec 273/274/217，同步采集风险已按批准的 Spec 300 修复并复验，真实压力待验收
- 日期：2026-10-12
- 授权：用户要求全面、多次、意外及突发场景模拟并发现风险。

## 范围

复用真实 MatchActor、供应商适配器、已有 4v4/语音模拟器和独立测试数据库。
覆盖正常比赛、真人/Agent 组合、五场同时运行、命令重复与乱序、暂停/恢复、断线、
任务取消、超时、心跳、流提前结束、队列反压、事务失败、赛后与浏览器交互。
新增内容限于测试/模拟工具与报告，不改变生产比赛、资源、状态机或权限。

## 2026-10-15 发布后真实链路补充范围

用户已批准关闭两个旧候场房间并继续发布。发布后仅以只读聚合检查核对已安装文件、
进程资源、路由权限、数据库版本及至少五分钟稳定性；生产日志只在服务器内解析为
优先级计数，不复制消息或用户数据。实际供应商测试复用当前模型、音色和安全RTC
启动所有权，合成语音经TTS、LiveKit、ASR链路，不把短链当作完整比赛。

完整混合4v4验证使用服务器上的新建独占测试PostgreSQL、合成账号与规则、精确自有
RTC房间／音频目录，复用真实Manager、Actor、AgentRuntime、ASRRuntime和回调。
每队两席模拟真人麦克风、两席Agent，正常主持与权威计时驱动到双方四辩及赛后。
不手工伪造供应商成功／host.finished／agent.finalized事件，不在生产业务库创建比赛。
测试规格可缩短发言时间并限制生成长度，但不修改业务计时、模型或重试语义。
精确自有资源正常关闭；失败、清理异常、未覆盖项目分别记录。

实际完整媒体三轮后，前两轮自然完赛但裁判格式失败；第三轮末主持进入权威ERROR。
新增最小诊断切片：保留8席2Human+2Agent/侧、合法有限测试规则，以Agent四辩到末主持
和END为目标，真实Manager／Provider／RTC串行三轮，透明转发原主持回调，只记录
Envelope／Runtime owner／DB context及稳定事件code，不伪造成功。该切片不称完整赛。
只有取得失败原因或明确正常控制后才补全场；完整模拟麦克风使用合题正／反方论点，
并以现有普通命令验证暂停／恢复。原generic语音测试内容的失败仍保留。

若再次出现真实解码／播放异常，允许在服务器私有自有诊断目录保留精确失败的
合成Ogg基线及SHA（最多3段，每段1MiB），用于同文件复现；不保留或复制生产真人
音频、不复制到本机、不导出测试数据库。报告明确区分已回收RTC／PG／临时目录与
仍保留的有限失败基线；验证结束再清理这些自有音频。观测wrapper先记录内存入口，
透明转发原函数及原异常，不在原callback之前新增数据库查询造成观测干扰。

七次完整媒体测试后，full07取得精确SDK资源证据：stage7正常四辩完成后的Room16
disconnect在等待原生FFI结果时超过500ms清理预算，后续join明显变慢并在末主持两次
超时。此时尚未证明SDK内部原因。新增一次隔离依赖对照：当前生产SDK1.1.14与
PyPI正式候选1.1.20，只在服务器独立0700 QA目录校验官方wheel SHA并加载候选rtc；
其他依赖、业务源码、模型、权限、计时和回调保持同一冻结版本。运行完整默认4v4和
正常暂停恢复，保留精确SDK ledger、pending函数诊断、实际Judge失败形状及资源终态。
不修改生产runtime-site、systemd、仓库依赖锁，不以候选测试结果直接授权生产SDK升级。
若候选公开API不兼容或需要业务语义修改，停止相关测试并记录；正式依赖升级另写规格。

## 验收及证据

- 4v4 每队两真人两 Agent 完整模拟至少 100 轮；已有故障场景至少重复 20 轮。
- 五场同时运行的虚拟模拟与 50 路本地限流突发明确标注为模拟，不能写成真实媒体压力。
- 新增适配器故障注入覆盖响应头停滞、正文前后心跳、截断、取消和反压；记录实际结果。
- 运行 Core/Jobs/Web 回归、隔离 PostgreSQL 集成与专项浏览器测试；阻断和失败均如实记录。
- 每个确认问题附复现方法、期望/实际、严重性、影响及建议；区分实测故障、代码风险及
  未执行项目。使用合成内容，不读取密钥到报告，不输出用户发言或供应商原始响应。
- 更新 MEMORY.md 和本规格；PRD 当前路径缺失时只测 TechDesign/既有规格行为并记录缺口。

## 回滚及生产边界

只读生产健康/脱敏诊断；不在真实比赛注入故障、重启服务或制造磁盘耗尽。
独立数据库以新建测试库或隔离 PostgreSQL 进程运行，迁移升降只在此边界执行。
测试报告/脚本可直接移除，不改变业务数据，不需要生产回滚。

## 2026-10-12 执行证据

- `formal_4v4_match_simulator.py --rounds 100`：100 轮、1000 次发言、300 个过期回调，
  暂停/恢复全部收敛；仅覆盖内存状态机和合成事件，不代表真实供应商或媒体压力。
- `five_human_match_simulator.py --rounds 100`：100 轮、500 次发言、400 个过期回调，
  非法 UUID 与内部 timer 故障收敛；timer 场景产生的 `internal timer failed` 是故障注入的
  预期诊断，不是未处理异常。
- 以上两类模拟各并发 5 份再次运行：500 轮/5000 次 4v4 发言和 500 轮/2500 次五真人
  发言均完成；另将 31 个可靠性场景重复 25 轮，775/775 通过。
- 50 路本地 `LlmCapacityLimiter` 突发完成 50/50，观测最大模型并发 4，释放后仍可取得
  新 lease。该项是本地限流模拟，不是 50 路供应商压测。
- Core 非集成回归 437 passed、Jobs 25 passed、Web 单元 219 passed；专项浏览器
  260 项中 228 passed、12 skipped、20 failed。失败均依赖本地 Core `127.0.0.1:8000`
  或真实音频元素，当前环境没有隔离 PostgreSQL/Core，故未把它们计为产品通过。
- `pnpm test:tooling` 发现发布门禁失败：`preflight_v2_1.sh` 固定 `0044_rule_audio_generations`，
  当前 Alembic head 为 `0045_postmatch_showcase`；Web lint 另有 2 个 React Compiler 错误和
  1 个既有 Hook 警告。上述均为发布阻断或发布前风险。
- `contracts:check`、Ruff、Pyright 和仓库 `pnpm build` 通过。Storybook 94 项中 86 通过、
  8 失败，失败落在房间准备按钮/真实音频元素故事；需要在 Core/fixture 修复后重跑，不能
  把构建通过误写成交互回归通过。

## 已确认的适配器风险

1. **高：LLM 首正文预算可被心跳无限延长。** `OpenAIStreamingClient` 对每一行重新
   `wait_for`，注释/空白行被忽略但仍重置首 token等待。合成心跳每 5ms 发送一次时，外部
   120ms 保护先超时，适配器没有返回 `llm_first_token_timeout`；供应商保持连接时可能让
   比赛长期停在 Agent 思考中。
2. **高：ASR 活跃发送阶段没有总时限。** `FunAsrConnection` 在 sender 未结束时使用
   30 秒 `recv` 超时，持续 `heartbeat=true` 会不断重置该时限；永不结束的音频流因此不会
   进入 `asr_audio_timeout`。合成心跳+无限音频源在 120ms 外部保护下可复现不收敛。
3. **中高：LLM 截断 EOF 被接受。** 合成 SSE 发送一个正文事件后直接 EOF（没有 `[DONE]`）
   返回 `success`，而文档要求流中断应失败；可能把不完整发言写入记录并进入 TTS。

以上三项只记录为风险，未在本规格中修改生产代码；应另建规格补充“总截止时间、心跳不
重置首正文预算、SSE 正常结束标志和回归测试”，再实施、测试和发布。

## 未执行项目（2026-10-12 快照，后续验证见下节）

- 未启动隔离 PostgreSQL，因此集成测试和依赖真实 Core 的浏览器场景未执行完整闭环。
- 未在生产注入网络、供应商故障、服务重启、磁盘耗尽或并发比赛；未把本地模拟结果写成
  真人语音、真实五场压力或长期供应商稳定性结论。

## 2026-10-13 跨模块验证补充范围

现有百轮/多场模拟只覆盖 Actor 内存语义；适配器、运行时和 PostgreSQL 的独立绿检不能
代替同一条实际故障链。本补充仍仅增加测试夹具与报告，不修改业务代码或比赛失败语义。

- 使用显式独立本地 TEST_DATABASE_URL、合成账号/4v4 规则/模型/内容；禁用 dotenv，
  不访问或注入生产。数据库迁移/清理只在该测试库内执行，串行运行。
- 先验证实际 AgentRuntime、Provider 适配器、manager/MatchActor 和 PostgreSQL 的
  Provider 超时/截断 EOF→已有重试→错误/暂停→正常恢复路径。只模拟 HTTP transport
  与 LiveKit/decoder 边界，保留实际业务事务、回调及身份栅栏；明确媒体为模拟。
- 注入一次失败诊断事务错误，观察 Provider 原错误、任务异常、Actor 与数据库是否
  收敛；不得把任务退出或诊断失败当成通过。权威事务与纯诊断必须分开记录。
- 注入一次实际比赛事件提交失败，验证事务未提交时 Actor/数据库 sequence、Speech、
  MatchEvent 与广播均未推进，随后正常命令可重试。
- 确认的业务缺陷先记录复现和对应规格边界，不能在测试文件中放宽产品断言、增加
  重试或绕过身份/事务检查。该补充不替代真实供应商和五场媒体压力验收。

## 2026-10-13 跨模块执行证据

- 新 runtime 故障 PostgreSQL 14 项覆盖失败 capture 异常/挂起、真实适配器首正文超时/
  截断、实际事件 INSERT 失败。事务失败时 Actor/数据库 sequence、Speech、事件与
  广播未推进；同一命令正常重试和 duplicate 行为保留。
- 新恢复链 3 项使用实际 AgentRuntime、manager/Actor 和 PostgreSQL：503、首正文
  超时、截断 EOF 各经两次尝试 ERROR、自然 3 秒恢复倒计时，随后正常发言并持久化
  完整文本/音频资产到 FINISHED，旧错误保留。每场正式 4v4 每队两 Human 两 Agent，
  只验证一个 Agent 动作；HTTP/decoder/LiveKit source 合成，不是真人全场验收。
- 新成功捕获 13 项、共享 helper ORM 9 项、迟到结果 11 项 PostgreSQL 全部通过；
  确认缺陷按 Spec 274/217 修复后保留强断言。实际暂停广播窗口证明旧任务退出仍由
  后续权威 reset 接管，不能靠丢失 registry 引用假装清理完成。
- 冻结代码后标准 pnpm test、专属本地库串行 pnpm test:db 共 1,182 项通过；最新
  Lint、类型、契约与格式通过，证据及边界见 MEMORY.md。初始本地 PostgreSQL VM
  停止造成的连接拒绝只计环境阻断，启动既有 VM/测试库后实际重新执行并通过。
- 未在生产注入故障、清理真实比赛或重启服务；真实供应商、真人音频和长期压力仍未
  验收。Spec 299/300 草案待批准，没有用本测试规格授权新的调度政策或数据表。

## 2026-10-13 剩余同步 Provider capture 探针范围

继续验证 TechDesign §19 的非权威诊断隔离，仅增加显式运行的 `scripts/qa/probes/`
风险探针和报告，不写 Spec 300 尚未批准的业务代码、writer、模型或 migration。

- 管理员模型测试、音色预览/主持任务、ASR capture 回调、赛后裁判复用实际业务路径和
  PostgreSQL；模型／裁判保留实际 SSE 适配器，预览保留实际 TTS 适配器；主持任务
  注入合成 TTS client，ASR 只调用实际 manager capture callback，未接入完整 ASR 连接。
  合成传输／音频内容与 capture 存储挂起／异常，记录权威业务结果、
  原错误、审计及资源状态，不把 limiter 名额释放或任务退出等同于用户调用成功。
- 在第二个真实 session 检查原权威行是否被 capture 事务锁阻挡；观察有界，外部取消
  后清理测试资源。保持权威 flush/commit 失败对照，不以捕获错误掩盖业务失败。
  ASR 同时覆盖 Match/Speech 行锁及实际暂停命令的提交/广播；已有 segment 结果
  不因诊断失败丢失，迟到身份仍须拒绝。
- 沿用专属本地 `jx_debate_qa_agent_faults` 及显式 TEST_DATABASE_URL，禁用 dotenv，
  串行运行数据库探针，不访问生产/真实 Provider，不打印凭据、payload 或真人内容。
- 产品期望仍为非权威 capture 不阻挡权威成功/失败。未修复风险用保留强断言的红检
  表达，不添加 xfail 或放宽期望；探针显式执行并独立报告，不混入默认回归通过总数。
  后续 Spec 300 批准并修复后，须以同一探针通过证明闭环并接入适当回归门禁。
- 追加导出冻结的测试-only 风险核查：实际创建导出与 Jobs 研究包读取，在截止点前
  创建调用、之后提交／更新合成 capture，观察内容和哈希是否漂移；保留截止点前已有
  capture 和截止点后新调用的控制。仅改显式探针，不改导出、保留或采集业务规则。

## 2026-10-13 剩余同步采集实测证据

显式探针位于 `scripts/qa/probes/`，只运行于独立本地 PostgreSQL，未修改本轮业务
代码、migration、生产或供应商配置。以下红检保留了采集不能阻挡业务结果的强断言。

| 路径               | 独立最终运行                             | 已确认风险与对照                                                                                                                                                                                                                                                             |
| ------------------ | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 管理员模型连接测试 | 6 项：2 通过、4 风险失败，7.19s          | 实际 SSE 已成功；request/response 捕获挂起 0.75s 后调用仍 STARTED，成功指标及审计未提交，另一 session 200ms 行锁超时 55P03。取消释放锁但丢失已观察成功结果；SQL 捕获错误对照保留成功／审计。                                                                                 |
| 赛后裁判           | 15 项：7 通过、8 风险失败，5.94s         | capture 边界异常替换成功／503；挂起 0.8s 后 Judge RUNNING、call STARTED；取消后已取得结果回滚；实际 UPDATE 遇到 55P03。正常／Python／SQL 存储错误各成功和 503 对照保留完整业务结果／原两次错误；权威 SQL flush 失败传播并回滚。                                              |
| Jobs 主持音频      | 6 项：4 通过、2 风险失败，4.15s          | 真实 manifest/claim/worker，合成有效 100ms Ogg。挂起约 0.9s 不完成，Rule/Generation/Asset/Task/Call 五类行锁均超时。取消释放锁，但任务仍 RUNNING、lease 未清空、call STARTED，音频文件未关联资产、原错误未提交。普通存储错误对照保留成功与原 tts_provider_failed／重试语义。 |
| ASR capture 与暂停 | 3 项：2 通过、1 风险失败，最终夹具 3.80s | Match/Speech 行锁均真实 55P03，暂停观察 0.35s 仍等待。Actor 与 DB 均 RUNNING/sequence 0，广播为 0，没有未提交状态暴露；已提交 final segment 保留。SQL 存储错误及取消对照释放锁，实际暂停随后提交／广播 PAUSED。                                                              |

Core 音色预览追加 **6 项：3 通过、3 风险失败，4.57s**。实际路由／QwenTtsConnection／
音量处理与合成 WS 输入 100ms Ogg：挂起时有效预览文件已发布（解码约 106.5ms），
但 call STARTED、Voice NOT_CALIBRATED、审计未提交。取消后文件仍在，连接关闭
边界与两个并发名额释放，权威成功未提交。另一 session UPDATE call 触发 55P03，
VoiceProfile 行锁实际可取得，不能误称其持有音色行锁。正常／Python／SQL 存储错误
对照均成功校准并写审计。

四文件一次串行组合复验为 **30 项：15 通过、15 风险失败，13.54s**，与各独立最终
运行分类一致。这里的 15 次失败不是 15 个独立根因，均为同步研究采集耦合权威事务的
不同结果／取消／锁场景。没有将控制组通过或资源取消算作风险修复，也未混入之前
1,182 项标准回归通过总数。

加入预览后，五文件 **36 项连续两轮串行复验，每轮均 18 通过、18 风险失败**，
耗时分别 13.24s／13.81s，失败场景一致。72 次执行不新增 72 个独立测试，也不证明
真实供应商／真人媒体稳定性；这是合成存储挂起和真实事务锁风险的可重复证据。

夹具问题已与产品风险区分：裁判 synthetic HTTP client 所有权明确后保留 close 强断言；
主持 synthetic 用户名缩短至合法长度，PCM PTS/time_base 补齐后音频严格 100ms；ASR
使用正式 HUMAN_SPEAKING、合法 ASR call kind、完整信封与实际 Human user ID。早期
夹具错误不计产品故障或通过项。预览夹具复用专属测试库的唯一 ADMIN，不改变其认证
数据或删除借用账号；首次第二 ADMIN 唯一约束错误仅为 setup 失败。最终失败均落在
业务结果／行锁／暂停强断言。合成连接 close 状态不代表生产 native socket 物理回收。

Spec 214 已批准研究采集的安全、长期保留、权限与导出边界；本轮只对齐 TechDesign
遗漏的明确例外，原 HEAD PRD blanket 禁止仍记为来源漂移，未恢复其缺失工作树文件。
Spec 300 继承该契约，独立表／writer／新增拥塞拒收策略仍待批准，未借用本测试授权实施。

## 2026-10-13 导出冻结风险探针

- `test_export_capture_freeze.py` 最终 4 项：2 控制通过、2 风险失败，4.09s；同一
  夹具修正后的前轮也为 2／2，4.64s。调用实际 `create_export`、capture helper、
  immutable blob 与 Jobs `_match_package`，只 hash `agent-calls.jsonl`，排除 manifest
  生成时间和 ZIP 时间戳；合成内容、每操作 1.2s 与 SQL timeout，仅清理 own rows。
- 截止点前已完成 capture 可读且重复 hash 相同；截止点后新调用被排除。已确认
  旧调用的 capture 在截止点后提交，旧导出仍出现晚到正文；更新请求／响应各自
  immutable blob 指针后，同一导出重读的请求正文、响应正文和研究内容 hash 都变化。
  不是原 blob 被修改，也不是归档时间字段产生差异。
- 初轮风险项被宿主／Colima PostgreSQL 约 60–70ms 时钟差的夹具断言阻挡，未算
  产品结果；合成 legacy capture 时间改用同一 PG `clock_timestamp` 后实测上述风险。
  未把该测试环境时钟差误判为生产事故。Ruff／format／Pyright／diff 均通过。
- Spec 214 未细分“任务开始”是创建还是 Jobs claim；当前真实 Core／Jobs采用
  `export.created_at`，探针沿用该现有截止点，只断言正文，不指定无正文索引是否应出现。
  当前仅按 call.started_at 过滤并加载最新 blob；只加 captured_at 过滤也不能保存
  原 revision 的内容。待批准 Spec 300 已列导出创建时的 metadata 投影冻结及兼容读取。
- 最终六文件合并串行复验 **40 项：20 控制通过、20 风险失败，14.86s**，分类与
  先前独立／重复结果一致；资源清理无错误，Ruff、format、全目录 Pyright 与文档
  格式／diff 检查均通过。20 个失败场景包括采集事务阻塞／取消结果丢失及两个导出
  冻结问题，不等于20个独立根因；既有1,182标准绿检仍为先前冻结业务代码的基线。

## 2026-10-13 Spec 300 修复后重复探针

- 用户明确批准 Spec 300 后接入独立 capture 表、有界共享 writer 和导出 Provider
  投影冻结。六个显式探针保留原权威结果／错误／审计／正文／hash／行锁强断言，
  挂起／SQL／取消改注入实际独立 writer；不通过放宽断言、xfail 或取消请求掩盖失败。
- 原六文件 40 项的 20 个风险失败均已闭环；追加 Jobs/ASR 过期及权威回滚四项，
  当前 44 项。最终同一代码连续两轮专属本地 PostgreSQL 串行复验均 **44 passed**，
  分别 14.93 秒／16.21 秒，无 fixture 清理错误。重复两轮只计 44 个独立用例，
  不计作 88 个不同场景或真实比赛，也不混入标准回归数量。
- ASR 已提交 segment 不受采集故障影响，真实 Actor 暂停可提交及广播；裁判保留完整
  业务响应和原失败重试；预览/模型测试保留公开成功与审计；主持任务/资产/generation
  状态和 lease 先提交，再投递已接受 proof。过期／回滚结果不投递，已结束 claim 的
  历史首次失败采集仍合法，不重调 Provider。
- 导出 Provider JSONL、两侧原始正文及 hash 在晚到 capture／指针变化及重试后保持
  截止点内容，gap不补读最新数据。此证据不覆盖整个 ZIP/manifest 或比赛实时状态
  和所有转录的冻结一致性。
- writer 的默认目录标准回归、native 连接/锁释放、内存及最终门禁证据另见
  [Spec 300](./300-bounded-provider-diagnostic-writes.md)。真实50路LLM、五场完整媒体、
  180秒真人ASR、11音色盲听和生产长期稳定性未在本规格完成；Spec 299仍待批准。

## 2026-10-14 Publisher 启动所有权／SDK 取消补验

新增隔离探针11项：**4控制通过、7风险强断言失败**，有意保留exit1，不混入标准
绿检。使用当前已安装LiveKit1.1.14实际Python `Room.connect`／`publish_track`，
FFI与业务媒体边界使用精确自有fake；没有真实网络、原生RTC初始化、Settings、
数据库或供应商调用。原SDK及业务源码未修改，目标是查明已有遗留而非宣称媒体验收。

- SDK建连取消一次：订阅0→2→取消后1，未建立handle时disconnect后仍1；五次
  取消累计1/2/3/4/5，五个孤儿队列均maxsize0并继续各收到16条合成全局事件。
  证明Python事件订阅未退出、无人消费队列可以累积，**不证明原生FD/线程泄漏**。
- SDK普通ConnectError及publish错误／取消的三个控制，临时/永久订阅按对应正常
  错误分支归零；不能由这些局部控制推断应用已断开连接。
- 真实共享`AgentRuntime._connect_publisher`在connect/source/track/publish错误及
  publish取消共五例，初始化的局部Room/source均没有发起自有清理；未返回的资源
  尚未赋给Host／Agent run，现有run finally无法接管。正常移交对照无提前关闭，
  caller随后各关闭一次。这里证明缺少cleanup调用，不是物理native释放测量。
- 首轮harness超时未产出完整结果，不计产品红/绿；独立安全导入后再跑1.675s完整
  收敛，期望断言未放宽。finally仅退订本次fake自有队列、恢复原FFI singleton，
  pending任务/自有订阅均0；原始超时与二次日志、11条检查点全部保留。

报告及PartB/hash在`profile_output/spec300_cpu_accounting/publisher_audit/`。
自有启动失败rollback可作为既有Spec274资源清理的后续单一切片，先补范围/验收。
固定pending/retired publisher准入及满额拒绝行为尚未明确批准，需新规格或明确
补充批准；本补验不擅自私改SDK、增加媒体协议或假定disconnect能取消未结束的
native connect。上述7红在该冻结探针时尚未修复，后续应用补修证据见下节。

### 2026-10-14 应用启动补修与原生取消闭环

Spec274已落实共享Host/Agent启动owner和独立SDK join切片；精确旧helper对照保留产品
强红，永久新22项和相关145项绿，完整非数据库1,290项及Lint/类型/契约绿。
SDK自身文件没有修改，原裸SDK取消订阅风险仍成立；不能将应用shield的收敛改写为
安装SDK全面安全，或用五次迟到完成控制证明永久pending队列有界。

实际原生测试在新internal网络、本地测试LiveKit、独立Linux子进程与合成凭据运行，
无外网默认路由且不发布主机端口。350ms仅延迟真实ConnectCallback的Python投递。
裸SDK及精确旧helper均在native回调自行SIGTERM、exit-15（约16/15秒）；候选两个
独立子进程各20秒以上存活，实际SDK完成ready后public disconnect，FFI订阅/队列和
SDKjoin/listener Python任务终态0，没有音源/音轨/发布或比赛业务回调。至此上游
SIGTERM报告有本机原生对照证据，仍不是生产历史事故的确定归因。

首客户端在入口路径计算时失败、未启动SDK child，日志保留；v2总入口exit1含旧强红，
候选repeat独立exit0。不将总入口exit1误称候选失败，不将旧强红改为通过。
候选最终FD21，高于导入前8及原生初始化后18，原生线程／即时RSS没有观测值；不能
声称native物理资源归零或真实音频通过。新owned服务、clients和空网络已按ID/label
正常清理；旧QA与生产未触碰。数据库、发布及永久pending/shutdown验收依然独立记录。
完整报告／PartB／清单：`profile_output/spec274_publisher_startup/`。

后续当前标准共1,479项通过（非DB1,290＋新独立CorePG184＋JobsPG5），数据库总入口
只作用于新独占可销毁实例，最终正常收尾。ASR独立RTC入口仍直接wait_for取消SDK
connect，是新确认的静态风险，尚未在ASR原生复现或修复，不由publisher绿检关闭。

### 2026-10-14 ASR 原构件继续诊断

实际ASR receiver/runtime11项8产品强红/3正常绿，实际SDK Python/FakeFFI12项7强红/
5控制绿，共23项15失败/8通过，setup/teardown无错。覆盖局部启动错误、receiver／
实际Manager内外超时和取消、轨道重复消费、重复close取消；共享receiver等待者取消
控制仍绿，候选不得通过盲pop/close破坏它。不能把上一构件1479标准绿检扩展到含新
强红测试的当前套件。原始阶段／精确source/test hash与fixture回收分别记录。

新internal网络、本地LiveKit、独立Linux child及合成凭据补验实际ASR原构件：
receiver.start／ensure_match外部取消后各真实FFI self-SIGTERM、exit-15，wall16.847/
15.573s；父deadline未kill，350ms只延ConnectCallback Python投递。取消后ready0、
遗留订阅1；Provider/trackconsumer/PCM/Settings/DB均0。没有生产事故归因或同identity
重叠原生结论。新自有client/server/空network正常收尾，不碰旧QA/生产，镜像保留。

最佳恢复方案及精确scope已形成Spec301：私有ASR服务身份逐连接唯一，旧join独立
收尾不挤掉新恢复，原10s／retry／human identities不变。该身份增量await用户明确
批准，async问题已发；当前未业务修改。报告与PartB/hash在spec274_asr_startup/。

### 2026-10-14 Spec301 批准后闭环

上述原构件23项15红／8绿与两次 self-SIGTERM 为冻结历史对照。用户明确批准
Spec301 后，从已按274完成的 normal close cdf 基线实施独立 join owner、唯一私有
身份、弱轨道 gate／去重、逐调用失败 ticket／精确 registry 回收及终态关闭接管栅栏。
最终 e785 构件原23强断言全绿，新增身份6项和复审边界6项绿；修复过程中实际录音
路径残留、非连接 Timeout误分类、三类关闭竞态各原失败日志保留。

当前完整标准1,531项绿（非DB1,342＋新独占PG189），Lint／类型／契约全 exit0。
ASR实际receiver.start／ensure_match／同Runtime恢复重叠各两轮原生补验六 child
全 exit0，无SDK join取消／self-SIGTERM／父deadline kill；旧join ready后仅断精确
旧Room，两次恢复连接在迟到旧join以后各保持3.053秒。最终SDK Python订阅／queue／
join／listener task0；每例20秒观察未送PCM、不调用真实供应商。FD/native物理局限
与全部自有资源正常收尾独立留证，不称真人发言通过或生产事故已确定归因。

本地301切片闭环，完整报告／PartB／清单在spec301_asr_implementation及
spec301_asr_candidate_native；没有目标staging／生产发布。退役Provider API请求、
永久pending／全局预算／shutdown／真实五媒体／50LLM／真人ASR／音色盲听仍待处理，
整体目标继续，不标完成。
