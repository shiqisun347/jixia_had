# v2.0 规格入口

当前交付以 [Spec328](328-v2-stable-repository-and-recovery.md) 为整理与复现入口，生产行为由实际已发布切片决定。历史批准不等于已发布，历史失败不拼接为通过。

| 范围 | 当前基线 |
| --- | --- |
| Core/Jobs | [307](307-mixed-human-reliability-and-conference-presentation.md) + [308](308-agent-personas-and-stage-presentation.md) / [309](309-retired-finalizer-generation-ownership.md) 已发布；迁移 0049 |
| Web 大屏 | [312](312-staging-presentation-and-simple-agent-speech-policy.md) 的独立 UI 切片；构建 J6YFijOoE7TSV9ijePYSO |
| 发言模板 | [327](327-closing-source-scope-boundary.md) 已发布；YXH275/新国辩146，后建房间冻结生效 |
| 现场投票 | [306](306-standalone-conference-showcase.md) 的现有独立 Showcase；公开可复现副本在 apps/showcase |
| 本次交付 | [328](328-v2-stable-repository-and-recovery.md)：私有备份、清理、公开初始化、干净环境验证与 GitHub 同步；状态见规格正文 |

Spec310/312 未发布的 Core/Prompt 候选已隔离。Spec313 已撤回；314、317–326 的失败/中间结果保留在各规格中，不能作为现行配置或补足验证样本。完整历史证据保存在私有归档，公开仓库仅保留可复现资源、测试、结论与风险。

以下为历史工作记录，其中“当前”“准备中”等均指记录当时。最新状态以上表、规格正文和 v2.0 发布回执为准。

<details>
<summary>展开历史规格与当时证据索引</summary>

- `327-closing-source-scope-boundary.md`：已真实仅配置发布、新房生效；同批12条10时长/12狭义内容/10联合合格，8/8四辩120秒90.47025～111.05025。原生138保存/138正常审计同事务，YXH275/新国辩146，两QA新房合计198条prompt全量冻结核对并正常关闭；130房间110比赛1582发言、0活动/租约。173源/资源/模型声音/服务PID/Web/Showcase保持、HTTP6项200，无重启。原资格SHA1c111…保留上线前身份，真实发布见`profile_output/spec327/release/RELEASE_RECEIPT.json`；两条时长失败/内容风险与RTC/现场/整场/压力未验证边界保持。
- `325-simple-fixed-speech-and-closing-task.md`：历史候选，已由后续326/327评测及327实际发布覆盖；不再是待执行安装计划，原失败与中间证据保留在规格正文和私有归档。
- `324-mingbian-fixed-speech-length-calibration.md`：唯一12条技术完成、自然stop、错误0；4时长/12窄口径内容通过、联合4/12，5短3超，未发布。24原声100,205,616字节验签及12独立解码完成；Root回收远端owned重复24文件、跳过0，保留本地原声/远端元数据。
- `323-fixed-speech-explicit-paragraph-count.md`：唯一12条完成，7时长/9内容通过、联合6/12，未部署；24原声117,162,120字节闭环，原失败保持。纯builder/transform的35项Ruff告警留档，未称该批全部工具Lint通过。
- `322-fixed-speech-preserve-opponent-boundaries.md`：唯一12条技术完成、自然stop、错误0，6时长/11内容通过，同条联合6/12，未发布。24原声114,961,781字节验签及12完整独立解码完成；Root精确回收远端owned重复24文件、跳过0，本地原声/远端元数据保留。后续323独立评测，不改本批失败。
- `321-bowen-fixed-speech-sentence-length.md`：唯一12条已完成，7时长/11内容通过，同条联合6/12；1项明确方案反写原判保持，未部署。24原声验签与12个完整独立解码已完成，远端仅自有重复音频清理由Root处理，未完成的动作不预称通过。
- `320-fixed-speech-sentence-budget.md`：唯一12条句数预算实测已完成，6时长/10内容通过，同条联合4/12；24原声验签及12独立解码完成，未部署。用户持续授权继续改进，无需再次审批，不追改旧失败。
- `319-fixed-speech-practical-qualification.md`：已按用户同批至少7/12且免重复审批授权完成两批；A联合3/12、B联合2/12，均未部署，不追加第三批或拼接。每批12次LLM/全文TTS及各24原声/12完整解码已完成；配置原子工具PG12与独审通过不代表生产安装已执行。

- `318-fixed-speech-dynamic-hard-limit.md`：已按批准完成唯一硬上限候选12组LLM/全文TTS，3时长达标/7超时/2过短，全部自然stop且技术错误0；24原声已验签/独立解码，内容窄审12无重大fatal但风险保留。未取得发布资格，线上旧配置保持，无部署/重启或追加调用。报告`profile_output/spec318/REPORT.md`。

- `317-fixed-speech-length-conflict-removal.md`：批准的14组LLM/全文TTS已完成，候选未通过、未上线。旧120秒四辩仅28.85/20.93秒；新12条7时长达标/5超时/0偏短，窄复核2确定内容错误。28原声/14全文解码与保护通过，138模板计划未写生产；后续动态明确上限见318，已完成且未取得发布资格。

- `316-precompetition-server-housekeeping.md`：完成不停服资源整理，667已备份音频＋571缓存回收约3.01GiB，可用4.77→7.77GiB/内存末轮918MiB；用户确认遗留后关闭3个Codex＋2个开发代理。8生产服务/173源/模型配置/Showcase保持，最终6项HTTP＋9项观察全200；未部署/重启/停赛。

- `315-match-offline-fallback-material-bundle.md`：已完成指定YXH每队2真人＋2Agent配置与通用素材只读打包；用户要求排除本场具体内容。81载荷、八席/模型/API/Prompt/赛制/头像和10段主持OGG＋WAV，ZIP及实际解压校验通过；不含辩题、发言、辩手录音或密钥，生产写入和模型调用均0。
- `314-same-model-prompt-structure-and-length-validation.md`：已按批准完成同模型隔离评测，候选未通过、未部署。12筛查择唯一D后64原样执行，共76SPEECH/76TTS；152原声验签/76完整解码及64全文独审完成，52固定31早/6中/15超、FREE4超、5条length，内容澄清后16fatal/13含0。模型/预算/173旧Core/670配置保持，生产写0；报告见profile_output/spec314/REPORT.md，后续新方案另审批。
- `313-single-model-speech-qualification.md`：已撤回、未执行；用户要求沿用现有模型。保留历史单Plus离线提案，不再等待或推定批准，不继承Spec311旧V9和生产操作授权。
- `312-staging-presentation-and-simple-agent-speech-policy.md`：已批准，20261017-spec312ui-c09c2769已独立发布Web，五分钟/短语音/最终18展示及173源/完整配置历史复核通过，整项未完成。开场欢迎/主持提示、无主持节选、原头像上方绿色大状态与淡色席位完成门禁；新两场原时长2真人＋2Agent结构/浏览器/归档/清理通过，但三条原尾音门槛失败，整体验收阻断。已发布独立UI切片，173旧Core及670模板保持；Core312补位/兼容与585新模板暂不发布，新模板仍有时长/内容阻断。跨站自动全屏按用户允许暂不实现。
- `310-quality-first-debate-and-stage-refinement.md`：已批准，按用户17:25限时切片于2026-10-17约17:15发布已验收Web V4b/Linux03，安装后核验中。原头像始终保持、绿色中心/红蓝席位、大屏字号分布及三个观众投票入口完成；模型/670模板/Core保持。十一版与最小文本候选仍有内容阻断，Prompt不安装，完整质量目标未完成。
- `309-retired-finalizer-generation-ownership.md`：已随Spec308于2026-10-17受保护发布；精确generation/finalizer所有权，状态机/计时不变，真实PG两交错及完整normal/recovery混合赛通过。原失败与安装后浏览器QA余项保留，不冒称全部验收完结。
- `308-agent-personas-and-stage-presentation.md`：已于2026-10-17发布，schema0049、Web hY-mZF-_0jzWG3V9sQjgt。音色互换、28头像、670个性化槽及展示更新完成；最终同源2437后端检查、两场原时长2真人＋2Agent混合赛、服务/公网五分钟观察通过。逐Agent原阈值尾句覆盖通过，3条原尾NCC失败保留；内容仅局部改善。首轮安装后浏览器仅完成一声且总失败，QA撤销等待循环已复现，完整浏览器复验与Git交付未完成；最新质量/头像修正由已批准Spec310实施，候选仍在真实验证。

- `307-mixed-human-reliability-and-conference-presentation.md`：已实施、完整测试并上线
  20261016-spec307-e290fab2/schema48。真人ASR连续收音/有界恢复、文字同步、弱网容错、
  应急安全自动继续、固定四Agent席位/男女声及赛事大屏均完成。最终同源发布前三场＋
  安装后三场原时长2真人＋2Agent混合赛自然完赛，两次180秒和实际辩手浏览器专项通过；
  后端1742/标准纯2206分别通过且有重叠。125安装源/49migration、五分钟健康及历史保持
  复核通过；人工断网中断与历史失败保留，物理设备和更大并发未冒称验收。

- `306-standalone-conference-showcase.md`：独立于现有系统的会议全屏展示与后台控制，
  已更新 showcase.vsagents.online，20261017-award-split-8694fe66；大众两卡左2/3上下排奖项与大队名/姓名、
  右1/3比分或得票比，“正方”宋体突出；49布局/28其他阶段/公网实际3票核对通过。
  大众“胜方队伍”与比例条、最佳得票/总票数保留，
  49结果/28其他阶段对比与公网当前大众空态通过，仅两前端文件修改，正式数据/原系统保持。
  仅00候场增加远程重连、超时/素材失败自动恢复、
  保留启音权限且不补播，56接口/恢复专项/完整页面与公网通过；3s有界退出补修已验证，其他阶段不启用恢复。
  有界服务端推送、每次新探测与实际播放确认、
  暂停/断线恢复及迟到响应防补播，53接口/310s停轮询驻留/延迟响应/完整页面与公网保活通过；原生后台限制见§22。
  点评图1.75MB→53KB并缩小，
  只读原系统同步8个最新头像与4音色/增益，44接口/42布局/四真实音频/公网源SHA通过，原系统保持。
  结果来源改为最大标题，
  大众蓝色/评委灰绿标签及奖项来源明确，63布局/14最终对比度尺寸与公网实际结果页通过。
  点评中心以生成的透明话筒/笔记插图
  替换大“评”圆圈；7尺寸与后台预览/公网实际点评页通过，外层蓝白及其他逻辑保持。手机页已移除“返回现场大屏”；
  扫码投票正反二选一、最佳辩手八选一，
  服务端拒绝平局/多选/旧排序，后台和大众结果每票计一票，最高同票并列。历史原始票保持。
  保留958b蓝白、0页四声音、启音后后台播放及投票可见性策略；43接口、42布局/历史样式、手机
  必选互斥/单票请求/防重、四真实WAV与后台唤醒通过。正式票/阶段/口令、原系统源码/服务/代理保持。
  受控hidden不称系统真实切窗/人耳验收，产生新制票后不常规回退旧计分代码。

v2.0 的产品与架构事实来源只有根目录的 PRD、TechDesign 和实时语音 Spike。

- `271-tts-voice-compatibility-recovery.md`：修复百炼 3.1 TTS 历史错误音色导致 Agent
  恢复后无法发言，保留冻结快照并使用显式兼容映射；已实施、测试并发布，真人现场试听仍按规格边界待验收。

`161`–`170` 是 v1.0 已发布或已验证的历史规格，只作为回归证据，不作为
新增产品范围。v2.0 新规格从 `200` 开始。

新规格必须在这里创建，并明确对应的版本变更、验收标准和回滚边界。

- `305-agent-audio-tail-delivery.md`：已上线20261015-spec305-24ccc540，1,569项Core、发布前完整混合比赛及浏览器受控缓冲3组尾音改善通过并独审；安装后3组浏览器尾音改善及16次发言完整混合赛复验通过。复用AGENT_FINALIZING冻结正文预算后保护同一source，原失败及理想网络无改善结果保留；经用户明确授权正常结束两个旧房并保留历史。

- `304-match-handoff-context-and-conference-recovery.md`：已实施、复验并上线20261015-spec304-8cd36502；主持固定longanwen_v3.1、显示“龙风月瑶”。2202标准测试、发布前三场及安装后一场真实混合完整比赛通过，122安装文件与6赛制/46主持资产核验通过。五分钟运行指标正常；原审计因19条发布前既有TERMINATED记录缺结束时间未通过，备份逐项比对确证未新增，原失败及遗留风险保留。用户追加的大屏顶栏精简已单独上线Web构件HX2A8FaQQj1tYWmwcfx9d，Esc退出及七尺寸展示验证通过，后端保持原版本。

- `303-complete-flow-rtc-and-judge-contract.md`：已实施、完整复验并上线20261015-spec303-29073b57。SDK1.1.20、Agent暂停精确退役/旧回调丢弃、裁判既有评分契约；1614标准、发布前3场＋发布后1场真实媒体/实际归档及五分钟观察通过，物理/压力边界明确。

- `302-asr-protocol-retirement-epoch.md`：用户明确批准后，已修复 ASR 调用退役、迟到 socket／错误／capture 及重复取消清理；并按已批准 Spec217/274 补齐 Session 回调隔离、abort 后连接恢复及旧暂停标记清理。2026-10-15标准1,564项、同场完整服务／PG至颁奖、浏览器248与Storybook94通过；用户批准正常关闭两处旧候场后已受保护发布，209安装文件及Web构建匹配、0046升级和终态元数据修复完成。实际短语音链通过，完整混合媒体QA失败且继续定位，证据见真实媒体QA报告。

- `301-asr-rtc-startup-ownership.md`：ASR 局部启动资源、SDK join 独立 owner、轨道回调及取消 registry 回收，并将私有服务身份逐连接唯一化，防止迟到旧 join 挤掉恢复连接；明确批准后实施，标准1,531项与实际ASR原生三场景各两轮通过，2026-10-15随完整流程版本受保护发布。正常 close 先按 Spec274 修复并保留回滚基线；真实媒体／永久 pending 等仍独立验收。

- `269-agent-provider-transient-failure-hardening.md`：对 Agent 供应商首 token 前的瞬时流中断增加有界退避、稳定错误分类和前端可操作提示；保持一次重试后暂停语义，已实施、测试并发布。

- `270-bailian-access-model-selection.md`：依据百炼官方接入文档，对 OpenAI 兼容与 DashScope SSE、Qwen 3.0/3.1 Audio 及 Qwen 3.7 Flash 快照进行脱敏对比；固定文本模型快照，保留已验证的生产适配器与 3.1 音色，已实施、测试并发布。

- `272-resilience-simulation-audit.md`：重复多场景、并发、故障注入及实际 Actor/Provider/PostgreSQL 恢复链测试；原40项显式采集／导出冻结探针20绿20红，批准Spec300后44项连续两轮全绿；已修复项见 Spec 273/274/217/300，真实压力待验收。

- `273-provider-stream-convergence-and-release-gates.md`：修复 Provider 流心跳/截断不收敛、迁移 head 发布门禁和 React Compiler lint；实施与门禁完成，2026-10-15已受保护发布。
- `274-context-memory-and-tts-stream-budgets.md`：上下文/TTS 硬预算、共享容量、退役清理，以及 Provider 诊断与权威事务隔离、幂等失败清理；完整异步 writer 已按批准 Spec 300 实施。补齐共享 publisher 启动所有权和 SDK join 取消保护，原生旧构件两个 self-SIGTERM 对照与候选两轮收敛留证；2026-10-15已受保护发布，永久 pending／shutdown 和真实媒体压力未关闭。
- `299-match-fair-llm-capacity-scheduling.md`：补齐 TechDesign 的按比赛轮转和活动比赛优先调度；设计草案待批准，尚未实施，不能用 Spec 274 的共享硬上限代替该验收。
- `300-bounded-provider-diagnostic-writes.md`：继承 Spec 214 研究采集契约，新增独立 capture 表／导出投影冻结、有界异步 writer、兼容读取、构造与字节预算、连接锁释放和关闭语义；已批准并本地实施，追加0046。最终构造补修标准回归1,378、44项故障探针连续两轮、实际PG／Core／Jobs Unicode导出及临时Linux限额补验通过；此前浏览器混合日志与同构件flagoff兼容证据保留，范围及性能局限见规格。后续尺寸计量补修32新增及当前1,457标准检查通过，七fixture配对字节证明一致；dense编码仍有同步开销。2026-10-15已随完整流程候选受保护生产发布；完整真实媒体与归档音频验收仍未闭合。

## v2.0 规格

- `268-conference-staging-and-postmatch-showcase.md`：会议现场候场与二次启动、管理员应急抽屉、观众大屏，以及赛后评委点评、观众排名投票、颁奖和后台展示；同时压缩首页首屏资源并补齐静态缓存，已实施、测试并发布，真人现场完整流程仍需现场验收。

- `267-host-audio-handoff-and-free-debate-continuation.md`：基础主持衔接／四辩总结切片已发布；补齐主持恢复身份、保护预算、旧计时器及挂起 PCM 控制入口、事件sequence顺序，修复自有测试夹具容量泄漏。补修于2026-10-15通过1,564标准门禁后发布；真实媒体与真人设备试听按实际证据验收。

- `266-agent-provider-stall-recovery.md`：修复 Agent 取消后复用陈旧 TTS 连接，以及 TTS 失败导致 LLM 文本队列永久阻塞；已完成本地回归并发布生产。

- `265-unrestricted-4v4-member-composition.md`：所有赛制恢复双方各 4 席、任意真人/Agent 比例，包含纯真人和纯 Agent；取代 Spec 261 固定 2+2 限制，拆分 Agent 能力与基础就绪，已于 2026-10-10 实施发布并通过本地与线上健康核验。

- `264-rule-management-and-host-audio-lifecycle.md`：重设计赛制规则 CRUD、规则生命周期与主持音频 generation，修复音频成功后状态不汇总、TTS worker 配置静默阻塞和审核入口错置；已批准实施并于 2026-10-09 完成生产发布。2026-10-13 补齐 Jobs 当前 generation 和独立测试库回归，两轮集成通过。

- `263-voice-synced-rule-agent-management.md`：补齐音色驱动的赛制 Agent 同步，独立保存各赛制启停选择；已实现、回归与独立数据库验证通过，并完成生产发布。

- `261-human-4v4-match-operations.md`：收敛为双方各 2 真人 + 2 Agent 的正式比赛，新增管理员大屏展示和现场事故控制台；已完成线上发布与稳定性观察。

- `260-bailian-qwen-model-voice-config.md`：切换北京百炼 `qwen3.7-flash` LLM，固定 Qwen Audio 3.1 TTS/ASR，并登记未校准备用音色；已完成。

- `255-speech-control-strip-coherence.md`：修复英文 Speech 控制组因重置文案过长而在
  1280×720 下换行的问题，并统一紧凑桌面控制栏；已完成生产发布、浏览器验证和五分钟
  稳定性观察。

- `256-admin-i18n-completion.md`：补齐 admin、标注与赛后问卷的中英文界面，并同步英文品牌；
  已完成生产发布、浏览器验证和稳定性观察。

- `254-chi-experiment-platform-section.md`：为 CHI 2027 Methodology 生成实验平台小节、
  中文对照、自由辩论标注图，以及实验管理与赛后标注双面板图；已完成并验证。

- `245-deepseek-model-switch.md`：将启用中的 Agent/AI 裁判模型资源切换为 `deepseek-v4-flash-0731`；历史冻结快照保持不变，待受保护生产会话执行。

- `244-unified-hand-queue-display.md`：自由辩论候选面板统一显示真人与 AI 的服务端权威名次；已实现本地版本，待发布。

- `243-cross-room-device-check-reuse.md`：同一账号同一浏览器在 24 小时内跨房间复用服务端校验的设备检测结果；已实现本地版本，待发布。

- `242-guide-workflow-redesign.md`：使用指南按角色和完整比赛闭环重设计，修正当前 4v4
  规则、自由辩论顺序、设备/暂停操作及独立赛后问卷说明；已实现本地页面，待验证发布。

- `200-v2-baseline-and-4v4-scope.md`：v2.0 与正式 4v4 产品基线，已发布。
- `201-paper-experiment-adaptation.md`：论文实验临时适配已正式发布，生产能力开关已开启；
  DRAFT 模拟批次未发布，正式开赛仍需完成真人现场验收。
- `202-v2.1-experiment-closure.md`：v2.1 实验闭环修复，停用、训练题、CSV 往返和
  参与者入口收敛；实现、本地 PostgreSQL、浏览器、构建、服务器发布和服务端
  provider chain 验收已完成，正式批次仍待外部素材及真人现场验收。
- `203-experiment-topic-provenance.md`：补齐实验辩题原始来源与 CEDAR 元数据，追加
  migration 0030 和实验排表发布校验；已在线发布，C1-C6 已录入来源。
- `204-experiment-simulation-readiness.md`：固定 C7、六个 Agent、匿名账号/队伍映射，
  完成 DRAFT 模拟批次及生产供应商并发验收；已完成，正式招募仍待真人现场验收。
- `205-remove-online-research-consent.md`：完整移除平台内研究同意书与伦理批准环节，保留
  历史数据库结构兼容；已完成。
- `206-experiment-admin-workspace.md`：重设计实验管理工作区，补齐草稿创建、修改、删除和
  发布后停用的清晰生命周期交互；已完成。
- `207-admin-foundation-and-global-resources.md`：后台框架、运营概览、全局资源和统一 CRUD；已完成并验证。
- `208-global-resources-and-settings.md`：模型、音色、辩题、用户和系统设置的资源生命周期；已完成并验证。
- `209-format-workspace-and-versioned-agents.md`：赛制版本工作区、规则、版本专属 Agent、Prompt、AI 裁判和模块；实现与独立 PostgreSQL 门禁完成，未部署。
- `210-operations-diagnostics-and-match-data.md`：比赛数据浏览、运行日志、事故、后台任务和审计日志；实现与独立 PostgreSQL 门禁完成，未部署。
- `211-batches-migration-and-release.md`：实验批次、训练房间、历史迁移和分阶段发布；已完成生产发布、线上服务/浏览器冒烟和供应商链路验证，正式真人现场验收仍待完成。
- `212-production-reset-to-v2.0.md`：生产应用已回退 v2.0 并完整清空数据库；之后已按用户授权创建唯一管理员账号。
- `213-rule-workspace-agent-pools-and-resource-restore.md`：赛制规则工作台、规则私有 Agent 池、
  阶段 Prompt 继承/覆盖、AI 裁判最小配置和资源白名单恢复；0038 实验批次规则快照及旧
  FormatVersion 写入口关闭已正式发布并通过线上服务、数据库、浏览器与供应商链路验证。
- `214-provider-api-request-logs.md`：将后台运行日志收敛为管理员专用的第三方 API 请求日志，
  记录供应商适配层脱敏原始 JSON、重试分组、关键流式事件和比赛导出；需求访谈与 GitHub
  调研、本地实现和生产发布已完成；0039 migration、真实 provider 短链路、线上浏览器、详情
  审计、比赛导出与五分钟稳定性通过，完整 PostgreSQL pytest 套件仍待补。
- `215-interruption-media-reset-and-asr-empty-audio.md`：生产发现的 Agent 暂停媒体清理超时与
  人类恢复后 ASR `EmptyAudio` 修复规格；实现、隔离 staging 验证和生产发布完成；真实 Agent
  暂停恢复与真人麦克风重连仍需现场验收。
- `216-formal-4v4-free-debate-competition.md`：将布尔决策、人类优先、纯人类等待、私有席位
  状态和确定性 Agent 选人统一应用于所有正式 4v4；实现、隔离 staging 验证、隐私投影修复和
  生产发布完成；真实媒体链路仍需现场验收。
- `217-unified-async-callback-envelope.md`：为 Agent、ASR 和媒体异步回调定义统一身份信封、
  迟到/重复回调处理和兼容迁移方案；已完成本机实现与全量门禁，生产发布待受保护部署会话。
- `218-agent-finalization-failure-recovery.md`：修复 Agent 发言收尾后台任务失败或
  卡住后永久停留在 `AGENT_FINALIZING` 的问题；已完成生产发布与五分钟稳定性观察。
- `219-asr-livekit-human-identity.md`：修复 ASR 接收器无法识别当前 LiveKit 真人身份，
  导致一辩结束后 `asr_empty_audio` 并暂停比赛的问题；已完成生产发布与五分钟稳定性观察。
- `220-ai-debate-questionnaires.md`：规则控制的赛后 AI/自身发言问卷与个人 AI 辩论感受问卷；
  已生产发布；当前严格题目版本和参与者页面已通过线上浏览器核对。
- `221-public-home-navigation-scope.md`：将受保护的实验安排从公共主导航移除，保留参与者
  直达与个人页入口；已完成生产发布和浏览器验证。
- `222-strict-debater-questionnaire.md`：按确认稿收敛自身/AI 发言标注和五道赛后体验题，
  保留历史问卷兼容；已生产发布并核对为正好五题、25 个五级选项且无开放文本框。
- `223-human-speaking-and-full-flow-reliability.md`：修复人类 WebSocket/LiveKit 发言、
  Agent/ASR 回调与暂停恢复竞态，补齐自由辩论阶段和导出验收；已生产发布并完成受控全流程验证，
  真实物理麦克风与并发长链路仍不宣称通过。
- `224-agent-prompt-side-rendering.md`：修复固定 Agent 发言缺少标准阵营码、导致正方 Prompt
  错误落入反方 `POSITION/STANCE` 分支的问题；已完成生产发布与正反方映射验证。
- `225-independent-value-agent-prompts.md`：所有 Agent 继续使用相同 Prompt，将决策收敛为
  独立判断非重复新增价值的提案；用户已撤回，未发布，代码已恢复 Spec 213 确认稿。
- `226-human-speech-start-reliability.md`：系统修复真人获权后开始发言的 sequence 栅栏、
  ASR pre-commit 幂等边界和 WebSocket 单命令故障隔离；已完成生产发布、全流程状态机模拟、
  浏览器和五分钟稳定性验证，真人麦克风/ASR 现场验收仍未通过。
- `227-free-debate-prompt-save-and-speech-template.md`：修复自由辩论正式发言 Prompt 因
  非必要剩余时间变量而无法保存的问题，应用用户确认稿并保持决策 Prompt 不变；已正式发布，
  生产论文规则 v4 已完成主持音频生成、完整性检查和启用。
- `228-affirmative-first-free-debate.md`：将新发布正式 4v4（含论文实验）规则的自由辩论
  首次机会改为正方，历史房间和比赛快照保持不变；已完成本地实现、完整 MatchActor 流程及
  全量门禁和生产发布，论文规则 v5 已启用并通过线上完整流程及稳定性验证。
- `229-agent-playback-cleanup-and-online-full-flow.md`：持续在线生产复验确认自由辩论 Agent
  与真人开始发言均存在 Speech/下一轮机会循环外键落库顺序错误（PostgreSQL 23503）；
  已修复并正式发布，完成真实 PostgreSQL 回归、两场生产自然完赛及两类问卷提交验证。
- `230-human-asr-retry-deadline-and-survey-audit.md`：修复 ASR 错误恢复后 deadline
  幂等键复用导致 `HUMAN_SPEAKING / 0 ms` 卡死、自由辩论跨机会共享失败次数及参与者问卷
  缺少审计的问题；已实施、测试并发布，物理麦克风与无故障现场验收仍按规格边界待执行。
- `234-human-speech-transient-query-failure.md`：真人发言收尾期间的临时查询失败保持比赛页面，
  并使真人音频文件落库幂等；已实现并完成生产发布。
- `237-postmatch-transcript-and-finish-prompt.md`：单场赛后任务提供服务端渐进解锁的双方完整
  文字记录，页面按阶段审阅，并在比赛正常结束后提醒真人参赛者填写；已完成并正式发布。
- `240-human-audio-playback-controls.md`：保留比赛页全局静音，并新增按比赛保存的全部真人及
  指定真人本机静音；已正式发布并完成五分钟稳定性观察，真实双麦克风现场验收待执行。
- `241-free-debate-postmatch-annotation-chat.md`：将单场赛后标注限定为自由辩论发言，采用
  正反方气泡记录、自动保存与自动推进、已标注气泡编辑及服务端未来内容隐藏；已完成本地
  实现、Core/Web/Storybook/契约/构建与桌面移动端视觉验证；已正式发布并完成线上健康、权限
  边界和稳定性观察。
- `247-match-operations-recovery-and-cleanup.md`：统一管理员全局观战、异常比赛恢复/终止、
  单场与批量安全删除和跨页全选；已发布，并完成生产全部比赛、房间及比赛文件清理。
- `249-human-asr-finalization-race.md`：修复自由辩论真人结束发言后 ASR 关闭错误与缓冲 final
  竞态导致的 speech reset，并防止 Core 重启从自由选择窗口复活旧发言人；已发布，生产健康检查通过。
- `250-human-asr-failure-convergence.md`：消除 nullable UUID 被序列化为 `"None"` 的定时任务
  崩溃路径，分类 ASR 失败并使内部定时未知异常进入可恢复状态；已发布并通过健康检查。
- `251-five-human-reliability-simulator.md`：新增纯内存五真人状态机模拟器，覆盖 50 次合成真人
  发言、迟到 final、非法 UUID 和定时任务异常；不连接数据库、LiveKit 或外部 provider。
- `252-web-bilingual-interface.md`：Web 中英界面切换；基础设施、全局导航、比赛关键状态、认证、
  公开大厅与排行榜已完成本地验证，其他资源与实验工作台仍在逐页迁移，尚未发布。
- `253-i18n-surface-completeness.md`：收敛英文模式混杂的中文 Web UI，先覆盖实时辩论页，
  再按页面域完成审计与迁移；正在实现。
- `258-voice-reliability-simulator-expansion.md`：扩展五真人模拟器，覆盖 ASR/Agent 故障、
  自由辩论竞态、暂停恢复矩阵、超时边界和幂等性边界；纯内存 MatchActor 验收工具已完成，
  真实供应商、LiveKit 和物理设备仍不在模拟器验收范围。
- `259-production-snapshot-rollback.md`：按用户授权将生产 Core/Jobs/Web 回撤至 `b3ef871`
  论文提交快照；数据库保持 `0043`，完整 Web standalone、首页 v5 插画和服务健康检查已验证。
- `275-terminal-room-cleanup-and-production-memory-guards.md`：终态房间清理与生产内存护栏；2026-10-15已受保护发布，终态元数据修复及Core/Jobs有效nofile限制通过，未安装未经实测定额的内存限制。
- `276-streamed-experiment-export.md`：实验批量导出改为 SQL 流式写入 ZIP，并增加行、文件和批次总量护栏；本地实现完成，待受保护发布。
- `277-bounded-late-experiment-opportunity-lookups.md`：迟到 Agent/ASR 回调按 source speech
  有界查询自由辩论机会，保留旧 JSON 数据兼容条件并避免 Python 全量扫描；同时将启动时
  终态房间校正改为集合更新；本地实现完成，待受保护发布。
- `278-survey-admin-streaming-exports.md`：管理员个人/赛后问卷展示与导出改为 SQL 流式响应，保留原有 JSON/CSV 字段和排序；本地实现完成，待受保护发布。
- `279-bounded-match-export-materialization.md`：研究导出单场 speeches、events、provider calls
  与批次项查询增加 sentinel 行上限，超限失败闭环；赛后参与者列表完成风险审计，分页另立规格；
  本地实现完成，待受保护发布。
- `280-bounded-participant-postmatch-surveys.md`：参与者赛后问卷列表增加有界分页与游标，
  列表默认摘要投影、详情单任务读取，批量加载比赛/席位/语音并增加每场、整页和文本预算；
  本地实现完成，待受保护发布。
- `281-livekit-container-preflight.md`：发布门禁兼容受控的 LiveKit Docker 容器部署；严格校验
  容器名、running 状态和镜像前缀，保留 systemd 路径并 fail-closed；本地实现完成，待受保护发布。
- `282-release-preflight-status-diagnostics.md`：发布预检输出非终态比赛按状态计数，保留零非终态
  门禁并提升暂停/运行/恢复问题的现场诊断能力；本地实现完成，待受保护发布。
- `283-terminal-state-repair-runbook.md`：新增默认只读、应用时对非终态房间/比赛
  fail-closed 的终态时间戳和连接租约修复脚本；本地实现完成，待受保护窗口执行。
- `284-bounded-experiment-index-pages.md`：实验批次与参与者预约接口采用每页最多 50 条的
  稳定游标分页，预约当前页的最新 attempt 改为批量查询，旧数组响应保持兼容；本地实现完成，待受保护发布。
- `285-bounded-annotation-task-pages.md`：参与者与专家标注任务列表采用每页最多 50 条的
  稳定游标分页，单页关联对象改为批量查询并保留旧数组响应；本地实现完成，待受保护发布。
- `286-bounded-experiment-postmatch-payloads.md`：实验赛后标注物料化增加聚合预检、sentinel
  行、发言文本和冻结 JSON 逻辑字节预算，超限稳定失败并回滚事务；本地实现完成，待受保护发布。
- `287-bounded-postmatch-audio-replay.md`：赛后音频回放对运行时快照、语音行、源文件与
  ffmpeg 临时输出增加 sentinel、数量、字节和路径上限；本地实现完成，待受保护发布。
- `288-annotation-response-payload-bounds.md`：在任务分页之上限制单页标注项目、答案和冻结
  JSON 载荷，先做数据库逻辑 UTF-8 字节预检，并防止 Web 任务游标重复或无限追页；本地实现
  完成，待受保护发布。
- `289-bounded-postmatch-audio-cleanup.md`：修复赛后 ffmpeg 取消后的子进程残留，按 JSON
  逻辑字节限制运行时快照，校验主持/辩手音频可信根，并将输入预算对齐可配置的 90 分钟
  规则上限；本地实现完成，待受保护发布。
- `290-annotation-request-payload-bounds.md`：限制参与者标注答案请求的键数量和逻辑 JSON
  字节，避免超大 POST 进入事务；本地实现完成，待受保护发布。
- `291-bounded-experiment-history-aggregation.md`：为批次/预约兼容 helper 与 Web 全量便利层
  增加页数、条数、JSON 字节和响应形状护栏；重复或不前进游标 fail-closed，不静默截断历史；
  本地实现完成，待受保护发布。
- `292-web-annotation-history-aggregation-guard.md`：为 Web 参与者/专家标注任务的四页自动聚合
  增加数组响应校验、累计 100 条任务和 64 MiB UTF-8 JSON 预算；超限或异常响应 fail-closed，
  不返回部分历史；本地实现完成，待受保护发布。
- `293-bounded-annotation-history-helpers.md`：为 Core 参与者/专家标注历史兼容 helper
  增加 200 页/10,000 条聚合上限，并校验降序游标重复与不前进；超限或异常游标不返回部分
  历史；本地实现完成，待受保护发布。
- `294-history-cursor-and-questionnaire-budget-guard.md`：为 Core 批次/预约兼容 helper 增加
  解码后的游标单调性校验，并在参与者标注预检中计入问卷 `q6` UTF-8 字节；本地实现完成，
  待受保护发布。
- `295-runtime-cgroup-telemetry-audit.md`：只读审计 Core/Jobs cgroup 当前/峰值内存、swap、
  `oom_kill` 与 pids，区分 systemd nofile hard/soft 和进程实际限制；本地实现完成，待受保护发布。
- `296-runtime-guard-path-hardening.md`：cgroup 符号链接循环、路径解析和越界异常统一降级为
  `unavailable`；本地实现完成，待受保护发布。
- `297-resume-agent-decision-fence.md`：Agent 发言暂停恢复时清除旧决策轮次与手队列，并将
  原先伪通过的 Bug 4 模拟改为真实断言；本地实现完成，待受保护发布。
- `298-nonterminal-room-release-gate.md`：发布预检统计并阻断 `WAITING/PAUSED` 等非终态房间，
  避免只看比赛状态而遗漏房间级实时连接，真实 shell 行为回归覆盖字段异常拒绝放行；本地实现完成，待受保护发布。

</details>
