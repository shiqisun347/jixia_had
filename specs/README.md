# v2.x 规格入口

v2.0 的产品与架构事实来源只有根目录的 PRD、TechDesign 和实时语音 Spike。

`161`–`170` 是 v1.0 已发布或已验证的历史规格，只作为回归证据，不作为
新增产品范围。v2.0 新规格从 `200` 开始。

新规格必须在这里创建，并明确对应的版本变更、验收标准和回滚边界。

## v2.0 规格

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
  缺少审计的问题；规格已批准，正在实现。
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
