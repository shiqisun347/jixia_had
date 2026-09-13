# 201 论文实验临时适配

- Version: v2.0
- Status: Implemented and deployed; human-device and sustained live-chain validation pending
- Approved by: user approval of `paper_docs/需求对齐.md` and explicit selection of v2.0
- Date: 2026-08-21
- Product source: `paper_docs/需求对齐.md`

研究同意相关产品行为已由 Spec 205 废止；历史表结构仅作数据库兼容保留。

## 1. 目的

在 v2.0 现有 4v4 平台内增加可关闭的论文实验模式，支持固定团队与席位、预约排表、实验专用自由辩论竞争、参与者和专家标注、研究导出及评分可见性门禁。

这是一项临时、隔离的产品能力，不替换普通 4v4 房间，不新增服务，不改变历史数据读取规则。普通用户仍可选择同一三阶段赛制创建普通房间；只有关联实验批次和预约场次的房间启用实验覆盖。

## 2. 事实来源与冲突规则

优先级如下：

1. 产品行为：`paper_docs/需求对齐.md`。
2. 现有平台通用产品行为：根 PRD。
3. 架构、事务和故障语义：根 TechDesign。
4. 实时语音能力：`docs/research/realtime-voice-spike-2026-07-24.md`。
5. 工程流程：`AGENTS.md` 与 `agent_docs/`。

如实验需求与普通平台行为冲突，仅在 `experiment_mode=true` 且预约绑定有效时应用实验覆盖；不能用实验分支改变普通比赛。发现无法通过模式隔离解决的冲突时停止该切片并更新本规格。

## 3. 范围

### 3.1 包含

- 实验批次、固定队伍、排表、固定席位和 attempt 生命周期。
- `P01-P18`、`E01-E03` 的批量创建或绑定能力。
- （已由 Spec 205 废止）简单的版本化研究同意。
- 实验房间自动入席、公开观战和实验权限矩阵。
- 单 Agent、人类优先、允许 Agent 跳过且不补位的自由辩论模式。
- 人类 ASR final 后决策、Agent 全文生成后提前决策、迟到结果留痕。
- 60 秒纯人类等待及暂停/恢复/重置语义。
- opportunity、举手事件、决策 attempt、分配结果和执行结果记录。
- 参与者事件标注、整场问卷、专家机会标注。
- AI 裁判结果门禁、公开与排行榜重算。
- 批次级研究导出、保留期与临时停用。

### 3.2 不包含

- 研究试运行或性能准入门槛。
- 自动 failure/mismatch 分类。
- 自助撤回或自动级联删除。
- 独立标注服务、通用工作流引擎或新基础设施。
- 永久删除实验代码或表。

## 4. 模式隔离

### 4.1 权威判定

不得从规则名称、房间标签、题目或前端路由推断实验模式。Core 仅在以下关系完整且有效时生成 `experiment_mode=true`：

```text
room -> experiment_match_attempt -> scheduled_match -> experiment_batch
```

且批次不是 `DISABLED`，attempt 指向该 room，scheduled match 属于该 batch。Match snapshot 持久化 `experiment_mode`、`scheduled_match_id` 和 `experiment_attempt_id` 的只读投影，恢复时重新校验关系。

### 4.2 普通路径

未命中上述关系时，必须继续使用现有：

- 普通创建和入房流程。
- 普通房主权限。
- 多 Agent 决策、`willingness` 排序和普通 fallback。
- 普通赛后文字审阅、评分和排行榜可见性。

实验字段不得出现在无权限普通响应中，也不得让现有客户端必须理解实验状态。

## 5. 数据模型与 migration 0029

只追加 `migrations/versions/0029_paper_experiment_adaptation.py`，不得修改 0001-0028。精确 SQL 名称可在实现时按现有命名规范微调，但下列数据所有权不得合并丢失。

### 5.1 组织与排表

| 表 | 关键字段与约束 |
| --- | --- |
| `experiment_batches` | `id`, `code`, `title`, `status(DRAFT/PUBLISHED/DISABLED)`, `schedule_version`, `consent_version`, `created_by`, timestamps；`code` 唯一 |
| `experiment_teams` | `id`, `batch_id`, `team_code`, `agent_profile_id`；批次内 code 唯一，Agent 必填 |
| `experiment_team_members` | `team_id`, `user_id`, `participant_code`；批次内 user 与 participant code 均唯一；每队恰好 3 人由发布校验保证 |
| `experiment_experts` | `batch_id`, `user_id`, `expert_code`；批次内 user 与 expert code 均唯一 |
| `scheduled_matches` | `id`, `batch_id`, `round_no`, `match_no`, `topic_id`, `affirmative_team_id`, `negative_team_id`, `scheduled_at`, `kind(TRAINING/FORMAL)`, `status`, `schedule_version`, `effective_attempt_id` |
| `scheduled_seats` | `scheduled_match_id`, `side`, `seat_no`, `occupant_kind`, `user_id`, `agent_profile_id`；每场每侧 1-4 席唯一，引用必须二选一 |
| `experiment_match_attempts` | `scheduled_match_id`, `attempt_no`, `room_id`, `match_id`, `status`, `termination_reason`, `public_at`, timestamps；attempt_no 唯一；最多一个活动 attempt |
| `experiment_consents` | `batch_id`, `user_id`, `consent_version`, `accepted_at`；三字段唯一 |

`scheduled_matches.effective_attempt_id` 只能指向同一 scheduled match 的 `COMPLETED` attempt。训练赛与正式赛必须在数据库事实层区分，不能仅依赖名称。

### 5.2 机会与执行事实

| 表 | 关键字段与约束 |
| --- | --- |
| `free_debate_opportunities` | `id`, `match_id`, `attempt_id`, `sequence_no`, `side`, `trigger_kind`, `source_speech_id`, `context_version`, 窗口时间、decision/assignment/execution 三层枚举、冻结上下文引用、失效信息；match 内 sequence 唯一 |
| `human_hand_events` | `opportunity_id`, `user_id`, `event_type(RAISE/CANCEL)`, `server_sequence`, `accepted_at`, `connection_epoch` |
| `speaker_allocations` | `opportunity_id`, `speaker_kind`, participant reference, `reason`, `allocated_at`, `effective`；每机会最多一个有效分配 |
| `participant_annotation_tasks` | `attempt_id`, `user_id`, `status`, `due_at`, `submitted_at`, `late`, 唯一约束 |
| `participant_annotation_items` | `task_id`, `opportunity_id`, `subject_kind(HUMAN_SELF/TEAM_AI)`, `speech_id`, `position`, `stage1_locked_at`, `revealed_at`；task 内 position 唯一，任务对象唯一 |
| `participant_annotation_answers` | `task_id`, `opportunity_id`, `subject_kind`, `stage`, `question_key`, `answer`, timing/playback metadata；幂等保存键唯一 |
| `match_questionnaires` | `task_id`, 固定 Q1-Q5、可选 Q6、`submitted_at`；task 唯一 |
| `expert_annotation_tasks` | `batch_id`, `expert_user_id`, `status`, timestamps |
| `expert_annotation_answers` | `task_id`, `opportunity_id`, Q1/Q2/Q3 structured answer, `submitted_at`；每专家每机会唯一 |
| `experiment_result_overrides` | 可选，仅保存管理员对超时未 6/6 场次的公开决定、操作者、原因与时间；正常可见性从任务完成事实计算 |

现有 `agent_free_debate_decisions` 与 `external_calls` 必须扩展或通过关联表连接 `opportunity_id`，并增加：有效/迟到/技术缺失/失效状态、绝对截止、请求触发来源和请求时举手快照。不得删除 `willingness` 旧列；实验请求写 `NULL`，保证普通历史兼容。

### 5.3 现有表的最小扩展

- `rooms`/`matches` 不直接承载完整实验配置；通过 attempt 外键关系反查。
- `background_tasks.task_type` 追加实验批次导出、结果公开/排行榜重算及到期清理所需有界类型。
- `match_events`、`external_calls`、`agent_generations`、`speeches` 继续保存权威运行事实，不复制大字段。
- 所有新增研究表使用 `RESTRICT` 或软状态保护研究事实；不得因删除普通用户操作自动级联删除已完成实验数据。

## 6. 排表约束

发布动作必须在一个事务内锁定批次并校验：

- 6 队，每队 3 名人类与 1 个固定 Agent。
- 18 场正式赛，6 轮，每轮 3 场，每队 6 场。
- 单循环 15 场加 3 场配对重赛。
- 每队正反方各 3 场。
- 每人一辩恰好 2 次，尽量一正一反。
- Agent 不当一辩，二/三/四辩各 2 次。
- 正式赛使用 C1-C6；训练题与正式题不同。
- 同轮同一用户、队伍或 Agent 不得重复出现。

CSV 导入先进入 DRAFT 版本，返回逐行错误；只有无错误版本可发布。发布递增 `schedule_version` 并冻结该版本的场次与席位。已产生 attempt 的版本不得原地改写。

## 7. 预约房间生命周期与权限

### 7.1 创建和加入

- 预约成员对 scheduled match 的首次进入通过事务/advisory lock 创建唯一 attempt 和 room；并发进入返回同一 room。
- 按 `scheduled_seats` 原子创建 8 个固定 Seat 和 6 个 DEBATER RoomMember。
- 非预约用户进入公开房间时只能创建或恢复 SPECTATOR membership。
- 管理员不在排表时只拥有观察/控制上下文，不创建 Seat。
- 实验参与者不可调用普通选身份、选席、换席或离席补位端点。

### 7.2 权限

Core 根据 scheduled seat 1、暂停发起者和管理员角色校验：

- 双方一辩：开始、暂停、恢复系统暂停、重置任意当前发言、终止。
- 其他参赛者：暂停、恢复自己触发的暂停、重置自己的当前发言。
- 管理员：必要时开始/暂停/恢复/重置/终止，但不能换人或换席。
- 观众：只读。

手动暂停记录发起者；只有该发起者或管理员可恢复。系统暂停由任一一辩或管理员恢复。终止请求必须携带非空原因。

### 7.3 attempt

- 正常完成后 attempt 为 `COMPLETED` 并成为 effective attempt，不可再赛。
- `RUNNING/PAUSED` 不允许创建新 attempt。
- 异常 attempt 先终止为 `TERMINATED/INCOMPLETE`，保留全部数据，才可创建下一 attempt。
- 仅 effective attempt 生成正式标注任务、专家任务、裁判公开资格和研究主分析数据。

## 8. MatchActor 实验状态扩展

### 8.1 设计原则

继续使用一个 MatchActor 串行处理同一比赛。不得创建第二套实验 Actor。为 `MatchRuntimeState` 增加持久可恢复的实验选择状态，建议最少包含：

- `experiment_mode`
- `experiment_attempt_id`
- `opportunity_id`
- `opportunity_generation`
- `selection_deadline_mono/remaining_ms`
- `human_wait_deadline_mono/remaining_ms`
- `selection_phase`: `COMPETING | HUMAN_ONLY_WAIT | ALLOCATED`
- `agent_effective_status`: `WAITING | DECIDING | RAISE | SKIP | TECHNICAL_MISSING`
- 冻结/重启语义所需的 pause origin

单调时钟只用于进程内计时；数据库事件/状态必须保存可恢复的剩余时间与服务端 wall-clock 时间。

### 8.2 机会形成

- 首次机会：主持音频开始形成反方 opportunity 并启动决策；主持结束后启动 3 秒截止。
- 人类对手：对方发言开始即开放人类举手；ASR final 到达才请求决策；发言结束时启动 3 秒截止。
- Agent 对手：对方生成全文完成事件触发决策，即使仍在播放；发言结束时启动 3 秒截止。
- 一方时间耗尽：下一机会仍形成，但 holder 可与上一发言方相同。
- 每次机会仅创建一个稳定 `opportunity_id`；等待和暂停不重复创建。

### 8.3 选择算法

截止命令进入 Actor 后按以下顺序一次性决定：

1. 有人类举手：选择 `hand_queue[0]`。
2. 无人类且 Agent 在截止前有效 `true`：选择该 Agent。
3. 其他情况：进入 `HUMAN_ONLY_WAIT`，Agent 有效 UI 为跳过。

禁止实验模式调用普通 fallback，不按 `willingness` 选跳过者，不按 seed 强制选 Agent。

`HUMAN_ONLY_WAIT` 中第一名合法人类举手立即分配；60 秒超时发出系统暂停。恢复重启完整 60 秒并保持同一 opportunity 与 Agent 跳过状态。

### 8.4 截止与迟到

Actor 的截止命令是唯一生效点。截止后到达的 ASR final 仍触发仅记录用途的决策任务；截止后决策回调只更新日志的 late 字段，不提交改变 Actor 的命令。若回调仍进入 Actor，Actor 必须按 opportunity、generation、context 和 deadline 拒绝为 `STALE/LATE`。

### 8.5 获权后

- 人类保持现有 `HUMAN_READY_TO_START` 与 60 秒开始超时；实验恢复保留原人并重启完整窗口。
- Agent 进入 `AGENT_PREPARING`，但播放开始不得早于上一发言/主持结束后 1500 ms；生成时间计入这段间隙。
- Agent 正式生成/TTS 仍按一次重试、二次失败暂停；不得重新选人。

## 9. 暂停、恢复、重置和失效

所有回调校验 `match_id`、`speech_id`、`attempt_no`、`generation_id`、`connection_epoch`、`context_version`，机会回调再校验 `opportunity_id/opportunity_generation`。

- 发言完成后的 3 秒窗口暂停：保存剩余窗口与候选，恢复继续，不重调模型。
- 人类发言中暂停且无 final：不请求决策，恢复业务发言，清空旧举手。
- 纯人类等待暂停：恢复重启 60 秒，Agent 保持跳过。
- Agent 对手被重置或需重新生成：旧全文、提前决策、候选和举手失效；生成号和上下文版本递增。
- 人类未完成内容删除；Agent 调用保留并标记失效。
- 一辩/管理员的 privileged reset 与普通用户 self reset 在 Core 明确区分。

失效回调保存为 `STALE_INVALIDATED`，绝不能推进比赛或回写有效机会结果。

## 10. Agent Runtime

### 10.1 Prompt

实验模式使用 `paper_docs/需求对齐.md` 第 10 节确认稿，模板版本写入外部调用快照。必须填充 `POSITION`、`STANCE`、`SEAT`、题目双方立场、剩余时间和完整历史。

不向模型提供 `HUMAN_HAND_STATE`。对方为 Agent 且全文已生成时，完整生成文本按普通历史输入，不标注未播放比例；后台单独记录时点。

### 10.2 参数和解析

- 模型 `qwen3.7-plus`，`temperature=0.75`，`top_p=0.9`，`enable_thinking=false`。
- 决策 `max_tokens=32`，只接受对象且只有布尔 `should_speak`；不要求 `willingness`。
- 正式发言纯文本流，动态 token 上限。
- 决策和正式发言使用独立请求。
- 完整历史超出上下文上限时暂停，不摘要或截断。

普通模式保留现有解析和参数。实验与普通解析必须由显式模式选择，不能用“字段缺失时猜测”。

### 10.3 失败

连接、首 token、流停顿各 3 秒；非法 JSON 同样失败。立即重试一次，但绝对选择截止不延长。两次失败写 `TECHNICAL_MISSING`，进入纯人类等待，不暂停、不换供应商。

## 11. HTTP、WebSocket 与可见性

### 11.1 路由边界

建议新增独立模块而非继续扩大通用 route：

- Core：`experiments/routes.py`, `experiments/service.py`, `experiments/schemas.py`。
- 管理端：批次、队伍、排表校验/发布、CSV、attempt 控制、进度、导出、公开 override。
- 参与者端：我的预约、进入预约、同意、标注任务、问卷、提交。
- 专家端：任务、冻结机会读取、答案保存/提交。

普通 room/match command 仍经现有路由，但 service/Actor 做实验权限和状态校验。

### 11.2 Snapshot 分层

构造 Match snapshot 时先得到公共状态，再按 viewer context 添加：

- 本方人类/管理员：本方举手队列、Agent 状态、机会截止/等待状态。
- 对方/观众：不得包含上述键，而不是返回空值或伪值。
- 管理员诊断：另走管理员端点查看原始决策 attempt 与失效/迟到原因。

WS 事件同样逐连接投影，不得把私有 payload 广播后依赖前端隐藏。

### 11.3 幂等与并发

- 创建房间、发布排表、举手/取消、答案保存/提交、结果公开均要求幂等键或数据库唯一约束。
- 每题保存采用版本或 `updated_at` 条件，旧响应不得覆盖新答案。
- 提交和锁定同事务完成；提交后 PATCH 返回冲突。

## 12. 参与者标注与专家标注

### 12.1 任务生成

effective attempt 正常结束后，在结束事务提交后创建幂等后台任务：冻结 opportunity 上下文、生成 6 个参与者任务、生成/补充 3 位专家任务、启动裁判。

终止 attempt 不生成正式任务。训练任务全流程生成但标记 `TRAINING`。

### 12.2 防后见之明

- 参与者 AI 评价分两阶段，阶段一提交锁定后才能读取实际发言内容。
- 专家机会 payload 只含形成时冻结的过去上下文；不含实际选择、后续发言、结果或他人答案。
- 冻结内容不可被赛后 display text 编辑回写。

### 12.3 UI 依赖

在 Web 依赖中固定许可兼容版本的 SurveyJS Form Library 与 wavesurfer.js，并更新 lockfile。jsPsych 只参考设计，不作为首选运行依赖。所有页面在现有 Next.js 应用内实现。

## 13. AI 裁判、门禁与排行榜

- effective 正式 attempt 结束后立即沿用 `PostmatchService` 启动裁判。
- Postmatch API 在返回 judge/result 前调用 experiment visibility policy。
- 管理员立即可见；单个参赛者提交自己的完整任务后可见；公众在 6/6 后可见。
- 24 小时未 6/6 不自动公开；管理员 override 必须审计。
- 门禁必须覆盖详情、历史摘要、排行榜查询和任何聚合接口。
- `leaderboard_worker` 只纳入已公开的 effective 正式 attempt；6/6 或管理员 override 后入队重算。
- 训练赛和非 effective attempt 永不进入排行榜。

公开比赛文字/音频沿用现状，门禁只限制裁判结果、胜负和由此派生的排名信息。

## 14. Jobs 与导出

复用 PostgreSQL 有界任务队列：

- 批次研究导出：生成需求列出的 9 个数据文件与 `manifest.json`，使用冻结 cutoff，逐文件计数与 SHA-256。
- 结果公开：幂等检查 6/6 或管理员 override 后标记并入队排行榜。
- 到期清理：按最终伦理配置处理音频、匿名数据和身份映射；默认 dry-run 报告，实际删除必须管理员显式启用并审计。

导出默认匿名，不包含用户名、真实姓名、密码、token、供应商密钥、内部路径或未脱敏错误。CSV 使用现有公式注入防护。

## 15. Web 信息架构

- 参与者：首页/个人页增加“我的实验预约”，显示下一场、队伍、固定席位、状态和待完成标注。
- 等待房：复用设备检测；实验模式隐藏身份/席位/换席，只显示固定阵容和准备状态。
- 比赛页：在固定席位卡的举手位置显示等待决策/决策中/举手/跳过；队内私有。
- 赛后：正常结束自动进入事件标注，再进入整场问卷；完成后显示结果。
- 专家：专用机会标注工作台，过去上下文固定，主内容区域稳定，支持保存恢复。
- 管理员：批次向导、排表表格与校验、预约/attempt 状态、完成进度、异常与导出。

不得将页面做成营销落地页；核心任务直接作为第一屏。复用 Button、Toast、ConfirmDialog、Drawer、Avatar、Badge、表格与现有色彩系统。动画仅 transform/opacity 并尊重 reduced motion。

## 16. 实施切片

实现必须按以下可独立验证顺序推进，不允许一次性合并全部范围：

1. 数据模型、migration、实验模式判定与只读管理 API。
2. 排表生成/校验/发布、账号绑定、预约与固定房间创建。
3. 实验权限和固定等待房 UI。
4. opportunity 数据层与单 Agent 竞争状态机。
5. 人类/Agent 不同决策触发、Prompt、迟到和失效留痕。
6. 暂停/恢复/重置/故障矩阵。
7. 参与者标注与问卷。
8. 专家标注。
9. 评分门禁、排行榜与批次导出。
10. 临时停用、保留期、全量回归和真实链路验收。

每个切片必须先更新 OpenAPI，再生成 contracts，随后实现 Web；不得手写并行 DTO。

## 17. 验收

### 17.1 自动化

- migration：空库升级、v2.0 现有库升级、约束、回滚到 0028 后再升级。
- Core domain：机会形成、截止、人类优先、Agent true/false/失败/迟到、60 秒等待、同方连续、暂停和重置。
- Core service：事务后 Actor/广播、权限矩阵、过期回调、并发唯一房间、任务幂等。
- API/WS：本方、对方、观众、管理员字段矩阵和越权拒绝。
- Jobs：effective/训练/终止过滤、6/6 公开、排行榜、导出 manifest 与脱敏。
- Web：固定入席、Agent 状态、自动保存/恢复/锁定、两阶段揭示、结果门禁、错误和空态。

运行：

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm contracts:check
pnpm test:storybook
pnpm test:browser
pnpm build
```

数据库集成必须使用独立 `TEST_DATABASE_URL`。专项浏览器测试使用 `node scripts/run-playwright.mjs test ...`。

### 17.2 真实链路

真实麦克风、ASR、Agent LLM、TTS、裁判和三场并发必须执行并如实记录串音、状态泄漏、录音丢失及延迟。这里没有研究试运行和性能准入阈值，但发现功能错误、数据污染或权限泄漏时仍不得上线。

## 18. 上线与停用

### 18.1 上线

- migration 先行，实验入口默认关闭。
- 管理员创建并验证批次后显式发布。
- 发布前验证 C1-C6、训练题、6 个 Agent、18 个参与者和 3 个专家绑定。
- 首个正式批次开始后不得修改其 Prompt 版本、排表版本或实验选择策略；修订必须新建 batch/version。

### 18.2 临时停用

- 将批次/全局实验开关置为 disabled，禁止创建和发布。
- 隐藏预约、专家和管理导航，但保留管理员只读和导出。
- 活动 attempt 必须先结束或终止。
- 普通三阶段赛制、普通房间、普通裁判和排行榜继续工作。
- 不 drop 表、不回滚 0029/0030、不删除历史数据。

## 19. 回滚边界

在尚无实验数据且 migration 刚部署失败时，可回退应用并将 migration 降回 0028。产生任何实验数据后，不允许通过 downgrade 删除表；回滚仅关闭实验入口并回退应用到能忽略新增表/字段的版本。

永久删除实验能力、执行保存期删除或改变参与者伦理承诺必须另立规格。

## 20. 已知发布阻塞

- C7、双方规范化立场与来源已经录入；提供的 CEDAR JSON 没有独立 ID，故 `cedar_id`
  合法保持为空，不使用数组位置伪造。
- 真实姓名/联系方式对照及初始密码交付只能由研究负责人在线下受控完成；平台匿名账号、
  固定 Agent、队伍和排表已经准备完成。

## 21. 实现与验证状态

### 21.1 已实现

- migration 0029、实验批次/队伍/排表/固定席位/attempt 数据模型与管理 API。
- 固定实验房间、实验权限、公开观战、单 Agent 人类优先竞争、迟到决策留痕，以及暂停/恢复/重置失效语义。
- 实验专用决策与发言 Prompt、严格输出解析、Agent 全文提前决策和敏感 snapshot/WS 投影。
- 参与者两阶段事件标注、整场问卷、专家冻结上下文标注及授权音频波形播放。
- AI 裁判结果门禁、排行榜纳入规则、管理员公开 override、进度、导出和保留期任务。
- 管理员傻瓜式批次设置界面、参与者实验入口、固定等待房和专家工作台。

### 21.2 自动化与线上证据

本地与线上证据截至 2026-08-22：

- `pnpm lint`、`pnpm typecheck`、`pnpm contracts:check`、Ruff、Pyright 和 `git diff --check` 通过。
- `pnpm test` 通过：tooling 47、QA 5、Web 172、Core 243、Jobs 23；数据库集成另由
  独立 PostgreSQL 执行，Core 27 项、Jobs 2 项通过；最新匿名展示名迁移单例另行通过。
- Storybook 10 个文件、85 项测试通过，包含可访问性检查。
- Playwright 全量 208/208 通过；新增主题来源管理端用例覆盖 4 个视口，实验参与者、专家和管理员页面另在 1440x900 与 390x844 下完成截图检查，无页面级横向溢出。
- 仓库生产构建通过；`pnpm db:revision:check` 返回无待生成升级操作。

线上已完成只读预检、0030 migration、备份、Core/Jobs/Web 2.1.0 服务重启和 HTTPS 验收。
服务端 provider chain 单链路验证 TTS、ASR、LLM、AI 裁判与 LiveKit 成功；短并发验证
TTS 5/5、ASR 5/5、LLM 50/50、LiveKit 5/5。这些结果不替代真人设备、持续五场长链路
和真人音色盲听验收。

### 21.3 尚未完成，不能视为已验收

- migration 0029/0030 已在独立 PostgreSQL 测试库升级并通过集成与 revision 检查，且 0030 已在线发布；产生正式实验数据前仍需完成真实批次发布前检查。

## 22. 生产赛制纠偏（2026-08-22）

生产 DRAFT 模拟批次曾错误绑定通用 `formal-4v4-standard` 规则。该规则包含二、三辩陈词
和四辩总结，自由辩论起始方与时长也不符合本规格第 5 节，因此不得用于论文实验。

纠偏要求：

- 新增独立 `paper-experiment-4v4` 规则，不修改任何已启用规则或历史 migration。
- 规则只能包含正方一辩立论 90 秒、反方一辩立论 90 秒、自由辩论每方 360 秒且反方先、
  结束四个节点；自由辩论单次发言最多 30 秒。
- 实验准备脚本必须按完整阶段语义校验规则，不能只检查 `side_size=4`、状态或 rule key。
- 仅允许无 attempt 的 DRAFT 批次切换到新规则；已有比赛快照和历史比赛不得改写。
- 新规则的主持音全部生成、试听审核并启用后，才能用于创建或发布实验批次。
- 普通用户可以选择该规则创建普通房间，但只有关联实验批次和预约场次的房间进入实验模式。

验收需覆盖纯模板断言、准备脚本拒绝错误规则、生产规则阶段查询、DRAFT 批次绑定、Core
健康和零非终态比赛。正式实验功能开关仍遵循独立发布门禁。

生产发布证据：

- 发布前 preflight 确认四项服务正常、migration 为 `0030_topic_provenance`、
  非终态比赛为 0；发布使用已有的源码和 PostgreSQL 备份回滚点。
- `paper-experiment-4v4` v1 的三个主持音频均为 `READY`，规则已审核并启用。
  生产查询确认两次一辩发言各 90 秒，自由辩论每方 360 秒、
  `starting_side=NEGATIVE`、`max_speech_seconds=30`，且无其他发言环节。
- `SIM_20260822_V21` 仍为 DRAFT，已绑定新规则；21 场排表完整且
  `attempt_count=0`。模拟批次没有被发布或冒充正式研究批次。
- 用户明确授权正式发布后，生产 `PAPER_EXPERIMENT_ENABLED=true`。Core/Jobs
  重启后 `NRestarts=0`，Core live/ready 为 200，preflight 显示
  `creation_enabled=true`且非终态比赛为 0；公网实验入口和管理入口均可正常渲染。
- P01-P18/E01-E03 平台匿名账号、6 个固定 Agent、C1-C7、6 队 roster 和 18+3 排表已
  在生产 DRAFT 模拟批次完成；真实姓名/联系方式对照和初始密码交付仍须线下完成。
- 真人麦克风与设备恢复、持续五场长链路和真人音色盲听尚未验证；服务端单链路及
  5 TTS、5 ASR、50 路短 LLM、5 LiveKit 并发探针已经通过。
- 在以上部署输入和真实链路验证完成前，不发布正式实验批次；这不增加研究试运行阶段。
