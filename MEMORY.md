## 2026-09-06 CHI 2027 Experiment Platform 论文素材（Spec 254）

- 后台管理正文不再强调配置冻结，现以灵活实验控制和完整数据管理闭环为主线。
- 中英文正文已减少功能清单式枚举，改用机制与研究用途串联；图 X 的 A–G 对照作为
  必要读图索引保留。
- 论文小节已按作者最终思路重建为“平台总述—辩论界面—后台管理”，并新增由实验批次
  管理和赛后机会级标注组成的双面板实机图、英文图注与 alt text。
- 根据作者反馈，中英文正文已统一收敛为三段：研究平台价值、实时语音与实验控制、
  数据采集管理与 coordination demand–action alignment 分析；标注图及事实边界不变。
- 新增 `docs/paper/chi2027-experiment-platform.md`：包含可直接纳入 CHI Methodology 的英文
  `Experiment Platform` 小节、英文图注、alt text、中文对照和作者整合备注。
- 新增 `PaperExperimentFreeDebate` Storybook 场景，复用实际辩手组件并按 Spec 201 呈现
  每方 `3 Human + 1 AI`、人类第 1 与 AI 第 2 的队内举手状态。
- 新增原始 1920×1080 截图与 A–G 英文标注 SVG；截图仅含笔名和演示文字，
  未使用生产账号、真实比赛或参与者数据。
- 事实边界：论文文字仅使用 PRD、TechDesign、语音 Spike 与 Spec 201 支持的功能；
  未将真人麦克风、五场并发、50 路 LLM 或 11 音色盲听写成已验收。

## 2026-08-30 Postmatch task route regression

- Production questionnaire tasks, matches, rooms, and participant relations were intact.
- `/me/postmatch-surveys/[taskId]` treated the Next.js 16 Promise-valued `params` as a plain object,
  so the browser requested `/api/me/postmatch-surveys/undefined`; Core correctly rejected that path
  with UUID validation rather than losing or hiding a questionnaire.
- The page now unwraps `params` with React `use()` and has a regression test asserting that the
  resolved UUID, never `undefined`, reaches `surveyApi.postmatchTask`.
- Targeted Vitest, TypeScript, ESLint, and the repository production build passed. The complete
  Web standalone build `x1chgpBWSryThr_jdAm95` was released after preflight confirmed zero
  non-terminal matches. Public detail routing returned 200, the unauthenticated API boundary
  returned 401, all four services remained active, and `jx-web` had zero restarts or warnings.
- Rollback Web: `/opt/jixia-web-before-postmatch-route-20260830211330`; source backup:
  `/opt/jixia-source-before-postmatch-route-20260830211330`.

## 2026-08-23 Spec 217 unified callback envelope

- User approved Spec 217. Added frozen `CallbackEnvelope` and threaded it through
  Agent decision/generation/TTS/playback/finalization, ASR segment/final/capture/
  failure, reconnect epoch and match reset callback paths.
- Late/stale Agent decisions are retained as redacted diagnostics and cannot
  change speaker selection; resumed ASR keeps speech/opportunity/context identity
  while adopting the newer connection epoch.
- Local evidence: Core 320 passed/32 skipped; Jobs 23 passed/2 deselected; Web
  184 passed; Storybook 89 passed; browser 228 passed; tooling/QA 53 passed;
  Ruff, ESLint, Pyright, TypeScript, OpenAPI contract, `git diff --check`, and
  `pnpm build` passed.
- Production source deployment is complete with protected preflight, backup,
  sync, health checks and observation. Provider-chain acceptance remains open;
  no migration was added for Spec 217.
- Follow-up deployment evidence: pre/post preflight passed with zero active
  matches; rollback backup `/opt/jixia-backup-before-0217-20260823T151553Z` was
  created; four source hashes matched; Core restart stayed at `NRestarts=0`; and
  ten 30-second health samples over five minutes were clean. The provider probe
  failed twice at ASR `wait_ready()` timeout, so external provider acceptance is
  explicitly unresolved and must not be reported as passed.

# 项目工作记忆

## 2026-09-02 Spec 248 真人自由辩论命令回归

- 生产真实双人验收中，历史 TTS 单次失败后曾出现真人举手 WebSocket 泛化
  `ValueError`；持久化诊断确认它不同于规则定义的 `HUMAN_WAIT_TIMEOUT`。
- HTTP 与 WebSocket 命令现均将已校验的 `connection_epoch` 传入 MatchActor，
  未预期命令异常会保留脱敏异常类型、SQLSTATE 和约束名，不记录堆栈或供应商内容。
- Core 路由与领域回归共 96 项通过，Ruff 通过；生产已在零活动比赛门禁下以单文件
  备份发布，Core live/ready 正常、重启计数零、远端哈希匹配。
- 新建真实双人场已自然 `FINISHED`：完成席位、设备、两段真人发言、发言中刷新重连、
  Agent 与自由辩论；真人举手成功获权并发言。双方赛后逐段标注均已提交（甲方
  20/20、乙方 18/18）。另两场自然结束与双方标注仍待完成。

本文只记录当前可执行状态、近期发布证据和未完成风险。产品与架构事实来源是
根目录 PRD、TechDesign 和实时语音 Spike；详细代码地图见
`docs/reference/current-system-reference.md`，历史过程见规格 161–170。

## 2026-08-24 Spec 228 production QA follow-up

- Spec 228 remains released: paper rule v5 is the sole enabled version and its
  frozen free-debate `starting_side` is `AFFIRMATIVE`.
- A fresh production ordinary account created a 4v4 room; one selected human
  seat replaced an Agent and the other seven seats remained unique random
  Agents. The rule snapshot contained all three ready host-audio assets.
- The headless browser could not produce physical microphone input and correctly
  did not pass the real device check. A clearly labelled synthetic QA PASS was
  then saved through the normal device-check/ready APIs to exercise later flow;
  this is not physical microphone or ASR evidence.
- The real match reached affirmative Agent theory playback. The browser test
  WebSocket later went offline and Core safely paused the match; the same period
  logged `agent_cleanup_timeout`. The warning is accompanying cleanup evidence,
  not proof that Agent finalization caused the pause. The QA match was terminated
  through the authorized MatchActor command at sequence 8, without direct DB
  updates; no post-match task was generated for the terminated match.
- The same account verified personal survey partial-draft save, completion of
  the exact five approved questions, and successful submission in production.
- Spec 229 is released. Agent and human free-debate speech-start paths now
  flush the new Speech before persisting a following opportunity that
  references it; no FK, timing or selection semantic was weakened.
- Follow-up production run kept both WebSocket and LiveKit online, completed
  both theory speeches, and entered affirmative-first free debate. The selected
  affirmative Agent then failed both playback-start commits at sequence 32.
  Persisted diagnostics prove SQLSTATE `23503` on
  `free_debate_opportunities_source_speech_id_fkey`: the transaction inserts a
  Speech and simultaneously makes the next opportunity reference it, but the
  opportunity was flushed first. The match was terminated through MatchActor at
  sequence 33. The final real PostgreSQL regression covers both Agent and human
  circular writes and passes.
- Production matches `0919e342-34ba-4f3c-93ba-0024da7cf322` and
  `74840b50-a941-48dc-bab9-7eb1dfb96565` reached natural `FINISHED`. The second
  generated exactly one strict post-match task for the QA user; reveal gating,
  14 segment answers, five overall answers, submit locking, personal-survey
  reopen/resubmit, and revision retention were verified.
- Production QA also found and released a PostgreSQL-only post-match response
  fix: refresh `PostmatchSurveyTask` after flush before reading server-generated
  `updated_at`. Repeated synthetic audio still produced intermittent
  `asr_task_failed`; one zero-time human speech required the normal early-finish
  action. Synthetic audio is not physical microphone evidence.

## 当前版本

- 当前工作版本为 `v2.1`，包版本统一为 `2.1.0`；应用已发布到远程服务器，正式实验
  创建能力已开放。DRAFT 模拟批次仍未发布为正式研究批次。
- 新建房间只支持 4v4；Web 只展示 4v4 规则，Core 独立拒绝非 4v4 创建。
- 历史非 4v4 房间、比赛、规则快照和 migration 保留读取/恢复兼容，不改写数据。
- Core/MatchActor 是状态、权限、计时和外部模型编排的唯一权威；Web 和
  LiveKit 不推进比赛状态。
- 生产实际从 `/opt/jixia-debate/apps/core/src` 和
  `/opt/jixia-debate/apps/jobs/src` 导入仓库源码。

## 2026-08-22 后台管理重构

- `paper_docs/变更.md` 已批准，后台重构拆分为 Specs 207–211；五份规格均已批准，严格按顺序实施。
- Spec 207 已完成：后台导航重组为全局资源、赛制、比赛、运行诊断和系统审计边界；新增赛制中心、裁判结果、事故、后台任务和审计入口，旧页面保留迁移访问。
- 207 验证通过：Web 单元 174、Storybook 86、Playwright 216、ESLint、TypeScript、34 页面生产构建；三个桌面视口无横向溢出，Axe A/AA 0 violations。
- Spec 208 正在实施；209 的版本 Agent/随机补位与根 PRD 旧全局 Agent/稳定补位表述存在显式冲突，必须在 209 实现前同步 PRD。

## 已发布基线

- 规格 161–163：epoch 高水位与 lease、WebSocket + LiveKit 双通道 presence、
  Core 主持音权威计时、Agent 播放后故障暂停已发布。
- 规格 164–169：状态矩阵、presence sequence 刷新、真人开始超时、发言中
  掉线 ASR 冻结/恢复、连续离线高 epoch 不续期已发布并有生产专项证据。
- 规格 170：终态迟到字幕不能重新写入 `interim_text`，已于 2026-08-21
  Core-only 发布；回滚点 `/opt/jixia-backup-before-170-20260821142353`。
  发布后四服务 active，Core/Web `NRestarts=0`，健康与 Web 为 200，
  Core/Jobs warning 为 0，非终态比赛为 0。
- 规格 200 已正式发布 v2.0/4v4 创建边界；运行回滚点为
  `/opt/jixia-backup-before-v2-20260821144920`，源码/文档回滚点为
  `/opt/jixia-backup-before-v2-docs-20260821145415`。生产 Core、Jobs、FastAPI
  和 Web 元数据均为 `2.0.0`。

## 当前回归证据

- 170 发布前：tooling 47、QA 5、Web 166、Core 197、Jobs 19、Storybook
  82、Playwright 200/200，以及 Ruff、Pyright、lint、typecheck、contracts 和
  27 页正式构建通过。
- v2.0 完整门禁：tooling 47、QA 5、Web 167、Core 198、Jobs 19、Storybook
  82、Playwright 200/200，以及 Ruff、Pyright、ESLint、TypeScript、contracts、
  Prettier、`git diff --check` 和 27 页正式构建通过。
- v2.0 发布后观察超过五分钟：四服务 active，Core/Jobs/Web
  `NRestarts=0`，Core live/ready、本机与公网 Web 为 200，Core/Jobs warning
  为 0，活动比赛为 0；匿名首页/大厅浏览器检查无错误，首页 Axe 违规为 0。
- 上述 v2.0 历史证据保留；当前 v2.1 已使用独立本机 PostgreSQL 补齐数据库门禁，
  见下节。

## 未完成验收

- 真人浏览器麦克风与 180 秒现场 ASR、噪声/蓝牙设备恢复。
- 11 音色至少 10 人真人盲听。
- 五场并发长链路与 50 路 LLM 持续饱和。

## 2026-08-24 Spec 223 reliability release

- Fixed serialized Core WebSocket sends, presence/webhook failure supervision,
  human pause/resume speech identity, LiveKit transport/playback separation,
  strict questionnaire wording, and match export provider request/response
  capture. Production Web/Core/Jobs/LiveKit are active with zero restarts.
- Production export evidence: one selected match, `SUCCEEDED`, seven `MATCH`
  provider records with request and response fields, and no runtime-log export
  surface. Runtime log page remains filter-only.
- Controlled production flow crossed both theory turns into free debate. A
  human-seat fake microphone flow reached `HUMAN_SPEAKING`, paused/resumed at
  the same speech identity, finalized, and reached `free_debate.started`.
  Both controlled matches were terminated through MatchActor afterward.
- Added a Web label for expected `HUMAN_WAIT_TIMEOUT`; it no longer appears as a
  generic service fault. The accepted rule still pauses after a full 60-second
  human-only wait when no human raises a hand.
- Release backup: `/opt/jixia-web-prev-human-wait-20260824T135531Z`.
- The final `pnpm test` rerun passed tooling 47, QA 6, Web 188, Core 347
  (33 deselected), and Jobs 25 (2 deselected); source hashes for the deployed
  Core/Jobs files matched 11/11.
- Remaining risks: physical microphone/ASR and concurrent long-chain tests;
  pure-Agent all-skip sides require a product decision before changing the
  specified human-only wait semantics.

## 2026-08-24 Spec 224 Agent Prompt side rendering

- Fixed a canonical-side mismatch in fixed Agent speeches. Initial
  `agent.preparing` events now carry the action's `AFFIRMATIVE`/`NEGATIVE` side
  and seat, rule-snapshot fallback returns the same codes, and invalid values
  fail with `agent_side_invalid` instead of silently selecting the negative
  Prompt branch.
- Local evidence: 114 focused Agent/MatchActor tests and 349 Core
  non-integration tests passed; Ruff, Pyright and `git diff --check` passed.
- The one production non-terminal match
  `705eaedf-b55f-4fe2-81ac-cd6854ae5f2a` was terminated through an isolated
  privileged MatchActor at sequence 15. Core-only backup:
  `/opt/jixia-backup-before-0224-20260824T162549`.
- The two deployed Core hashes match local. Post-release preflight passed at
  migration 0041 with zero active matches; production-side mapping assertions
  passed and six ten-second samples kept all services active, ready 200 and
  Core restart count zero.

## 规格 201/202 论文实验适配

- 规格 201 已按 v2.0 实现：预约排表与固定席位、实验自由辩论竞争、实验
  Prompt、参与者/专家标注、授权音频、评分门禁、排行榜、批次导出与停用边界
  均已落入 Core/MatchActor、Jobs 和 Web；普通 4v4 路径保持隔离。
- v2.1 当前本地证据：tooling 47、QA 5、Web 172、Core 243、Jobs 23、
  Storybook 85、Playwright 208/208（含主题来源管理端 4 视口用例）；Ruff、Pyright、ESLint、TypeScript、contracts、
  `git diff --check` 和正式 `pnpm build` 通过。
- Storybook 实验参与者、专家和管理员界面已在 1440x900 与 390x844 检查，
  没有页面级横向溢出；波形和移动端堆叠稳定。
- 独立本机 PostgreSQL 的 `pnpm test:db` 已通过：Core integration 27、Jobs
  integration 2。v2.1 专项测试真实验证了 21 个匿名账号、密码 hash 与一次性交付、
  6 队 roster、18 场正式赛与 3 场训练赛、CSV 往返、发布、预约和批次停用边界。
- 另用临时独立数据库完成 0030 空库升级、降级到 0028（实验表消失）、再升级到
  0030 及 `alembic check`；临时数据库已删除。真实 `jx-core` HTTP 冒烟验证 live/ready、
  v2.1 capability、未认证 401 和 OpenAPI 停用路由。
- 真实 PostgreSQL 赛后闭环已覆盖 6 个参与者、AI 两阶段揭示、提交锁、个人/6-of-6
  结果可见性、排行榜任务、3 个专家冻结任务，以及实验双方控制者/管理员/普通辩手/观众
  控制权限矩阵。Spec 213 后控制者定义为每方最低席位真人，不再假定一辩为人类。
- 普通比赛仍不受全局管理员角色越权影响；实验控制权限有真实数据库断言。
- C7、P01-P18/E01-E03 平台匿名映射、6 个固定 Agent 和 18+3 排表已在生产 DRAFT
  模拟批次完成；现实姓名/联系方式仍由研究负责人填写。真人麦克风/设备恢复、持续五场
  长链路和真人音色盲听仍未完成。
- v2.1 已于 2026-08-22 发布到服务器 `/opt/jixia-debate`：追加 0030 后备份位于
  `/opt/jixia-backup-before-0030-20260822011200`（另保留首次 v2.1 备份），包含源码、Web standalone 和可读
  PostgreSQL custom dump；线上 migration 为 `0030_topic_provenance`。
- 发布后 Core/Jobs/Web 使用 2.1.0，四服务 active，Core 8100 live/ready 为 200，正式域名
  `https://debate.vsagents.online/` 与 `/experiments` 为 200，能力接口返回
  `target_version=2.1.0`、`creation_enabled=false`，未认证管理接口为 401；活动比赛为 0。
- 生产 provider chain 只读探针通过：TTS 首包 251ms/完成 609ms，ASR 首个 interim 216ms、
  22 字符，LLM 首 token 335ms/完成 412ms，AI 裁判首 token 334ms/完成 13.4s，LiveKit
  连接 105ms；该证据不覆盖真人设备、并发压力或盲听。
- C1-C6 已通过 CatalogService 录入并保存规范化标题、双方立场和原始来源文本；提供的
  `CEDAR_topics.json` 没有独立 CEDAR ID 字段，故 `cedar_id` 保持为空，未按数组位置伪造。
- 2026-08-22 重新执行完整 `pnpm check` 通过；线上匿名桌面/移动路径、实验登录回跳、
  首页 Axe（0 violations）及浏览器 page/console errors 检查通过。Storybook 85/85 通过；
  全局 MSW handler 改为不会被 story override 清除的 initial handlers 后，无未处理请求警告。
- 线上首次发布保持 `PAPER_EXPERIMENT_ENABLED=false`；没有创建正式批次，也没有修改
  既有比赛记录。Web 发布窗口的 SIGTERM 日志来自人工重启，当前 `NRestarts=0`。
- 2026-08-22 早先只读 SSH 检查曾被鉴权拒绝，后续授权恢复并完成上述发布。
- 新增 `scripts/ops/preflight_v2_1.sh` 作为服务器只读发布前门禁：检查四个 systemd
  服务、Core/Jobs 2.1.0 导入路径、健康/能力接口、0030 migration 和非终态比赛数；
  不读取或输出 EnvironmentFile，不执行迁移、重启或比赛命令。
- Spec 204 已完成：生产 `SIM_20260822_V21` 保持 DRAFT，21 个匿名账号展示名均为编号，
  6 队/18 成员/3 专家及 18 正式+3 训练排表通过幂等核验；凭据仅保存于服务器 root-only
  `0600` 文件。并发探针 TTS 5/5（P95 1858ms）、ASR 5/5（P95 6258ms）、LLM 50/50
  （P95 1969ms）、LiveKit 5/5（P95 1538ms）；含 AI 裁判的完整单链路通过。最终
  preflight 四服务正常、实验开关关闭、活动比赛 0。该证据不等于真人现场验收。
- 最新增量后完整 `pnpm check`、Storybook 85、Playwright 208、服务器独立 PostgreSQL
  Core integration 27/Jobs integration 2 及匿名展示名归一单例均通过；线上首页 Axe 0
  violations，实验登录回跳与 390x844 无横向溢出复核通过。

## 2026-08-22 Spec 205 增量

- 已移除实验线上研究同意书和伦理批准环节。管理员创建批次不再填写相关字段，参与者可
  直接进入预约房间，Core 不再查询 `experiment_consents`；历史表和批次列仅为数据库兼容
  保留。平台不展示“线下签署”“已签署”等替代提示，也不记录或推断平台外流程。完整
  `pnpm check` 通过，Playwright 专项 2/2 通过；本次未配置独立测试数据库，未重跑数据库
  集成。

## 2026-08-22 Spec 206 增量

- 实验管理改为批次侧栏工作区，提供搜索/状态筛选、草稿创建/修改/删除、人员与 Agent、
  辩题与排表、发布与停用的完整生命周期；切换批次通过 keyed setup 重置未提交草稿。
- P01-P18、E01-E03 使用统一默认密码 `Jixia2026`，不强制首次改密；提供中文可读排期
  CSV 和账号密码 CSV。管理员可直接查看固定 Agent、实际 Prompt 模板，并从已有比赛场次
  进入工作台查看事件、完整请求输入和原始响应；系统日志和 Agent 管理均有直达入口。
- 用户端和管理员端实验入口始终可见；普通创建房间的标准 4v4 规则标记为“论文同款”，
  仍使用普通房间邀请/Agent 逻辑，不套实验固定排表。
- 当前验证：Ruff、ESLint、Pyright、TypeScript 通过；Core 实验单元 6、Web 172、
  Storybook 86、实验 Playwright 12 均通过；完整 Playwright 216/216 通过。已发布批次进度
  直接显示辩题、正反方队伍和中文比赛/尝试状态，并直达逐场数据与请求日志；390x844
  和 1280x720 实际渲染无页面级横向溢出，Axe 0 violations。完整 `pnpm check` 通过，
  包含 Core 244、Jobs 23、契约检查和 29 页面生产构建。数据库集成测试已更新，但本机
  没有 Docker/独立 PostgreSQL，尚未重跑 `pnpm test:db`。

## 2026-08-22 公网入口恢复

- 服务器重启后 1Panel OpenResty 容器退出，80/443 无监听，公网表现为拒绝连接。根因是
  `/opt/1panel` 在开机后被重新初始化，原 OpenResty 单文件挂载源和站点目录缺失；Docker
  将缺失的文件源创建为空目录，随后 OCI bind mount 以 exit 127 失败。
- 已保留异常空目录并从同版本镜像恢复 `mime.types`、`fastcgi_params`，用仓库
  `ops/openresty/` 的最小配置恢复 Web 3000、Core 8100 和 LiveKit 7880 三条既有路由；
  现存 Let’s Encrypt 证书有效到 2026-11-01，私钥仅复制到服务器 root-only 目录。
- `openresty -t` 通过，原容器 running 且 restart policy 为 always；80 返回 301，HTTPS
  首页、`/experiments`、能力接口均为 200。独立 `agent-browser` 已从公网渲染首页和实验
  登录页；Core live/ready、四个 systemd 服务及 1Panel 服务均正常。
- 只读 preflight 仍发现 1 场暂停的普通比赛，因此未继续应用发布，也未暂停、终止或修改
  该比赛。实验创建能力继续保持关闭。
- 用户终止该比赛后，preflight 返回 `active_matches=0`。已完成 Spec 206 正式发布：发布前
  回滚点为 `/opt/jixia-backup-before-publish-20260822154542`，包含权限 0600 的源码、Web
  和 PostgreSQL custom dump；旧 Web 另保留于
  `/opt/jixia-web.before-publish-20260822154542`。
- `pnpm build` 重新生成 29 页面 standalone；Core/Jobs 明确源码目录、migration、运行脚本
  和新 Web staging 分别同步，未同步 `.env`、数据库、音频或日志。`alembic check` 无待执行
  操作，migration 保持 0030。
- 发布后四服务 active 且 `NRestarts=0`，Core live/ready、公网首页、实验页、实验管理入口
  与能力接口均为 200；独立浏览器显示新“实验安排”导航。连续 5 分钟逐分钟检查均正常，
  Core/Jobs/OpenResty 无 warning/error，最终 preflight 仍为 0 个非终态比赛。Web 的两条
  systemd warning 是受控停止产生的 143，当前新进程 Result=success。实验创建能力继续关闭。

## 2026-08-22 论文赛制纠偏

- 本地新增独立 `paper-experiment-4v4` 规则与严格校验：正方一辩 90 秒、
  反方一辩 90 秒、自由辩论每方 360 秒且反方先开始，单次最多 30 秒；
  不含二、三辩独立陈词或四辩总结。论文正文已同步为该精确赛制。
- 用户终止暂停比赛后，发布前 preflight 为 0 个非终态比赛。生产已创建并启用
  `paper-experiment-4v4` v1；三个主持音频全部 `READY`，时长分别为 9900、
  9100 和 14100 ms。生产阶段、动作及参数查询与规格完全一致。
- `SIM_20260822_V21` 已切换到新规则，仍为 DRAFT；21 场排表完整、
  `attempt_count=0`，未将模拟批次发布为正式研究批次。
- 用户明确授权后，生产 `PAPER_EXPERIMENT_ENABLED=true`。开关变更前的 `.env`
  备份保存于服务器 root-only 路径，未读取或输出其他配置。Core/Jobs 重启后
  active、`NRestarts=0`、无 warning；Core live/ready 为 200，最终 preflight 显示
  `creation_enabled=true`、migration 0030、非终态比赛 0。
- 公网 `/experiments` 和 `/admin/experiments` 均由独立浏览器成功渲染并进入带正确
  return URL 的登录页。真人麦克风/设备恢复、真人音色盲听和五场持续长链路仍未验收，
  不得把本次服务端发布证据写成这些现场验收已通过。

## 2026-08-22 Specs 207–208 后台重构增量

- Spec 207 已完成：后台导航按概览、资源、赛制、比赛、运行与诊断、系统重新分组，新增赛制中心、裁判结果、事故、后台任务和独立审计入口；后台固定为桌面工作台。
- Spec 208 已完成：全局模型支持受限能力 Schema 与独立 API Key 轮换；音色保存校准状态并在参数变化后失效；活动引用保护覆盖单项和批量停用；管理员可直接修改普通用户密码并撤销其会话。
- 系统设置新增白名单持久化：运行日志保留天数、最长 24 小时的临时 DEBUG 和上传上限；不直接改变比赛状态或已发布快照。追加 migration 0031–0033，未修改历史 migration。
- 当前证据：工具测试 47、Web 178、Core 259、Jobs 23、Storybook 87 全通过，Ruff、ESLint、Pyright、TypeScript、OpenAPI 契约和 34 路由生产构建通过；三个桌面视口无模型页重叠，系统设置 1280×720 正常，Axe 无确定违规。
- 该阶段当时尚未执行 PostgreSQL 门禁；现已在 2026-08-23 随 0031–0036 完整迁移链补齐。此次未部署，后续进入 Spec 209。

## 维护规则

## 2026-08-22 Specs 209–211 完成审计

- 完成审计纠正了两类此前遗漏：Agent 与 AI 裁判运行时均改为消费房间冻结的赛制版本
  Prompt/模型/音色配置；训练房间只在正式场应用固定席位与一辩控制，训练场恢复普通
  房间式选座、换座、角色切换、离开和组织者控制。房间契约新增正式/训练类型供 Web
  正确展示，训练场不再错误显示或隐藏正式场控件。
- 完整门禁通过：工具测试 47、Web 181、Core 非集成 276、Jobs 23，Ruff、Pyright、
  ESLint、TypeScript、OpenAPI 契约、34 页面生产构建和 `git diff --check` 均通过。
- 临时独立 PostgreSQL 16.10 已从空库迁移到 0036，`alembic check` 无待生成操作；
  `pnpm test:db` 通过 Core 28、Jobs 2。本轮未部署。
- 生产构建可在本地启动；训练房间的正式/训练差异已由 Core、Web 单元和 Storybook
  场景覆盖，但本轮仍未把真人登录设备链路记为通过证据。

## 2026-08-22 Spec 209 赛制版本工作区

- Spec 209 已完成：赛制版本、深复制草稿、乐观锁、不可变发布快照、版本专属
  Agent CRUD、完整 Prompt 模板、模块、AI 裁判和辩题引用已落入 Core 与后台工作区。
- 普通房间和席位回填共用按 `format_version_id` 隔离的随机 Agent 候选逻辑；候选不足
  由 Core 拒绝。旧全局 Agent 页面只读，历史比赛与旧配置不重写。
- 最终验证纳入统一门禁：Core 非集成 276、Web 181、Storybook 89、Ruff、Pyright、
  ESLint、TypeScript、OpenAPI 契约、34 路由生产构建和 `git diff --check`；0034 已在
  独立 PostgreSQL 空库迁移链与 `alembic check` 中通过。本轮未部署。

## 2026-08-22 Spec 210 运行与诊断

- Spec 210 已完成：运行日志与审计日志独立导航；RuntimeLog 支持 INFO 以上持久化、
  最长 24 小时 DEBUG、有界队列、脱敏、游标/分页、保留清理、请求/追踪与赛制版本关联。
  事故、后台任务和比赛数据页补齐详情、重试及赛制版本/批次筛选。
- 验证通过：Core 非集成 272、Jobs 23、Web 181、Ruff、Pyright、ESLint、TypeScript、
  OpenAPI 契约、生产构建与 `git diff --check`。五个后台页面在三个桌面视口无横向溢出，
  Axe 0 violations；新日志缓冲和隐藏标签页暂停轮询已用隔离浏览器验证。
- migration 0035 已在独立 PostgreSQL 空库迁移链中执行，`alembic check` 无待生成操作；
  完整数据库门禁通过 Core 28、Jobs 2。未部署，下一阶段进入已批准 Spec 211。

## 2026-08-22 Spec 211 批次与训练房间

- Spec 211 已完成本地实现：批次冻结绑定已发布赛制版本；追加 migration 0036 增加版本引用、
  训练房间配额和 attempt 创建者；旧批次只做可追溯回填。正式场固定席位，训练场按版本
  随机补位 8 个 Agent，允许普通房间式选座/邀请，训练不生成正式标注任务或排行榜数据。
- 验证通过：Core 非集成 273、Jobs 23、Web 181；Ruff、Pyright、ESLint、TypeScript、
  OpenAPI 契约、生产构建和 `git diff --check`。浏览器验证批次弹窗与训练配额输入（1280x720），
  无页面级横向溢出。Radix 对话框 Axe 仅有焦点守卫的 incomplete 手工项。
- 0036 已在独立 PostgreSQL 16.10 从空库升级成功，`alembic check` 无待生成操作；
  `pnpm test:db` 通过 Core 28、Jobs 2。完整 Storybook 89/89 通过。未部署；正式发布仍须
  单独授权并遵循规格 211 第 5 节发布顺序。

## 2026-08-23 五阶段完成性审计

- Specs 207–211 均有独立规格、批准依据、验收条目和回滚边界；代码实现按赛制版本、
  运行诊断和批次发布边界落在 Core/Web/Jobs 对应模块，未新增服务或修改已发布 migration。
- 最终仓库门禁：`pnpm check` 全部通过；Tooling 47、QA 5、Web 181、Core 非集成 276、
  Jobs 非集成 23、完整 Storybook 89、完整 Playwright 216，OpenAPI 契约、Ruff、Pyright、
  ESLint、TypeScript、Prettier、生产构建和 `git diff --check` 均通过。
- 独立 PostgreSQL 16.10 从空库升级至 migration 0036，`alembic check` 无待生成操作；
  `pnpm test:db` 通过 Core 28、Jobs 2。临时数据库已停止，本轮未部署。
- Playwright 测试中的 Core `ECONNREFUSED:8000` 仅出现在无 Core 的 fallback 请求日志，
  不影响 216/216 通过，也不构成真实线上服务验收。真人设备、持续多场压力、11 音色盲听
  和正式生产 preflight 仍是发布前现场/运维验收，不在本轮完成声明内。

每个 v2.x 切片只记录最终状态、验证证据和遗留风险；详细事件序列、实现设计和
历史讨论放入对应规格或脱敏研究文档，不在此重复堆叠。

## 2026-08-23 Spec 214 第三方 API 请求日志

- Spec 214 已批准并完成本地实现：后台 `/admin/logs` 改为管理员专用“API 请求日志”，按供应商
  attempt 保存 LLM、自由辩论决策、AI 裁判、ASR 和 TTS 的脱敏请求/响应 JSON；配置测试、主持
  音频和运维探针使用同一 Schema。日志页不提供导出，比赛研究包包含目标比赛的调用 JSON。
- 每侧正文限制 2 MiB 并保留首尾、字节数和 SHA-256；敏感正文拒存，不保存认证头、签名、URL
  查询参数或二进制音频。采集写入由保存点隔离，失败不改变业务结果和重试语义；ASR 未消费采集
  固定最多 16 条。
- 本地门禁通过：Tooling 47、QA 5、Web 183、Core 非集成 300、Jobs 非集成 23、Storybook 89、
  Playwright 228；Ruff、Pyright、ESLint、TypeScript、OpenAPI 契约、34 页正式构建和
  `git diff --check` 通过。隔离浏览器已验证 1280x720、1440x900 的日志详情布局和内部滚动。
- Spec 214 已生产发布。发布前活动比赛为 0；root-only 数据库、源码和 Web 备份为
  `/opt/jixia-backup-before-0039-20260823T073053Z`，即时 Web 回滚目录为
  `/opt/jixia-web-before-0039-20260823T073053Z`。生产只向前迁移至 0039，运行目录保持 0755。
- 隔离临时 PostgreSQL 空库完成 migration 全链、`alembic check`、0039 字段和索引验证后删除；
  服务器没有 pytest，因此完整 PostgreSQL integration marker 仍未执行。TTS、ASR、LLM、AI 裁判和
  LiveKit 真实短链路通过，4 条 `OPS_PROBE` capture 均为 COMPLETE/SUCCEEDED 且无敏感字段。
- 线上 1280x720、1440x900 验证日志列表、JSON Drawer、内部滚动、无导出和无页面溢出；详情审计
  从 0 增至 1。发布前终止比赛的研究包含 provider schema 1 和空 `agent-calls.jsonl`，未重建历史。
  五分钟 10/10 样本四服务 active、ready 200、重启 0，最终启动窗口 warning-or-higher 为 0。
- Web 首次切换命令因受控停止返回 143 在目录切换前退出，旧 Web 立即恢复；随后以可回滚原子切换
  成功发布。真人 ASR、五场长链路和 50 路持续 LLM 仍未执行。

## 2026-08-23 Specs 207–211 生产线上测试发布

- 已按 Spec 211 完成生产 preflight、备份、追加 migration 0036、Core/Jobs/Web 发布和线上验证；
  生产 head 为 `0036_experiment_batch_format`，活动比赛数为 0。
- 四项服务 active，重启计数为 0；公网首页、实验入口、排行榜和未授权管理接口冒烟通过。
- TTS、ASR、LLM、AI 裁判和 LiveKit 短链路探针通过；服务连续观察 5 分钟无 warning 或重启。
- 备份和 Web 回滚副本保留在服务器受保护目录；未将地址、凭据、连接串、用户数据或模型正文写入记录。
- 管理员认证操作、真人设备、长链路、并发压力和音色盲听仍待现场验收，不宣称已通过。

## 2026-08-23 生产全量清库并回退 v2.0

- 用户明确选择完整清空生产数据库；Spec 212 已完成。执行前恢复点保存在服务器 root-only
  `/opt/jixia-resets/20260822T182454Z`，源码、Web 和 PostgreSQL dump 校验通过。
- 生产 Core/Jobs 已回退为 2.0.0，数据库从空 schema 初始化至 `0028_connection_leases`；
  未执行 downgrade，未恢复旧 dump。用户、Session、房间、比赛和运行日志最终均为 0。
- Core、Jobs、Web、LiveKit active，重启计数为 0；live/ready、公网页面和注册入口正常。
  v2.1 赛制中心接口已不存在，证明不存在前后端混合发布。
- v2.0 migration 自动创建的默认管理员已按全量清库要求删除；随后按用户明确授权通过受控
  交互创建唯一管理员，未记录密码。另有一个用户通过公开注册创建，未覆盖或提升该账号。

## 2026-08-23 Spec 213 赛制规则与 Agent 池

- Spec 213 已批准并完成本地实现。追加 migration `0037_rule_owned_agent_pools`；新规则直接拥有
  阶段 Prompt、音色驱动的 Agent 池、最小 Prompt 覆盖和 AI 裁判配置，新房间冻结
  `rule-config-v1`，旧 `format-version-v1` 快照继续兼容读取。
- 管理端已改为规则目录、规则工作台和阶段抽屉；Agent 页面必须先选规则，只展示该规则的池，
  Agent 身份跟随音色且只读，编辑只保留启用、模型、折叠生成参数和 Prompt 继承/覆盖。
- 论文实验四份 Prompt 已按确认稿接入：统一使用“你和另外三名队友”；正方首次立论无当前状态和
  历史；自由辩论决策 Prompt 原文保持不变。房间继续自由组队，人类占位后由规则池随机补齐，
  Agent 可担任包括一辩在内的任意席位。
- 受保护备份已在隔离 PostgreSQL 完成 dry-run、白名单恢复和幂等演练：源计数为模型 1、音色 12、
  辩题 10、规则 10、裁判 1；目标生成规则 Agent 54，用户、房间、比赛、实验、日志和排行榜均为 0。
- 最终本地证据：Tooling 47、QA 5、Web 184、Core 非集成 292、Jobs 非集成 23、恢复专项 11、
  Storybook 89、全量 Playwright 224；Ruff、Pyright、ESLint、TypeScript、OpenAPI 契约、
  正式构建和真实 PostgreSQL 生命周期测试通过。
- Spec 213 已发布：发布前 root-only 数据库/源码/Web 备份完成，生产从 `0028` 只向前迁移至
  `0037_rule_owned_agent_pools`，白名单恢复和论文规则音频生成/复核/启用成功。最终为 1 个启用
  论文规则、2 个旧版归档、4 份确认 Prompt、9 个启用 Agent；房间、比赛、批次和排行榜仍为 0。
- 生产 preflight 和 TTS、ASR、LLM、AI 裁判、LiveKit 探针通过；四服务 active、重启计数为 0、
  最近十分钟无 warning。线上管理员浏览器在三个目标桌面视口验证规则工作台、Agent、裁判和
  创建房间表单，无横向溢出或控制台错误，Axe A/AA 0 violations。隔离数据库和浏览器凭据已清理。
- 完成性复审发现实验批次仍会新建旧 `FormatVersion` 绑定，且旧排表约束仍禁止 Agent 担任一辩。
  已追加 `0038_experiment_rule_snapshot`：新批次只接收规则 ID，创建/发布冻结规则与
  `rule-config-v1`，名单和训练随机补位只使用规则私有 Agent 池，新房间不写 `format_version_id`；
  历史批次继续走旧快照。0038 同时移除 Agent 一辩约束，生成与导入校验允许任意席位。
- 审计继续修复了由此暴露的权限和生命周期耦合：正式实验每方最低席位真人拥有控制权，正方
  控制者作为组织者；等待中的实验房终止会同步终止 attempt 并把排表标为未完成。
- 0038 最终本地证据：OpenAPI 契约、Ruff、Pyright、ESLint、TypeScript、Tooling 47、QA 5、
  Web 182、Core 非集成 294、Jobs 非集成 23、Core PostgreSQL 31、Jobs PostgreSQL 2、
  Storybook 89、Playwright 224、34 页面生产构建、`alembic check` 和 `git diff --check` 通过。
  独立浏览器确认实验批次只选择规则、无 `/api/admin/formats` 请求、1280 宽无横向溢出或页面错误，
  Axe 无确定违规；Radix/Storybook 焦点守卫有 1 项人工复核。此条为发布前本地证据，后续线上状态
  以本节末尾的 0038 发布记录为准。
- 完成性审计关闭了旧 FormatVersion 产品写入口：`/admin/formats` 及其详情 URL 只重定向到
  `/admin/rules`，后台导航与概览分别进入规则和规则范围 Agent 页面；Core OpenAPI 只保留
  `/api/admin/formats` 列表 GET 和 `/{version_id}` 详情 GET，旧草稿、复制、发布及其子资源写接口
  已移除，并新增契约回归测试；四个桌面浏览器项目的重定向专项 12 项通过。
- Spec 213 的 `0038_experiment_rule_snapshot` 和旧 FormatVersion 写入口关闭已正式发布。生产
  head 为 0038，发布前后非终态比赛为 0，新增快照列存在、旧 Agent 一辩约束不存在，
  `alembic check` 无差异；关键 migration、Core 路由和 Web server 哈希与本地 staging 一致。
- 线上登录态浏览器确认旧 `/admin/formats` 重定向到规则目录，Agent 页面先选规则且论文规则
  仅显示自己的 9 个 Agent；独立 `agent-browser` 匿名会话确认登录回跳，无浏览器错误。TTS、ASR、
  LLM、AI 裁判和 LiveKit 真实探针全部通过。
- 发布中源码 rsync 曾把 `/opt/jixia-debate` 顶层权限继承为 0700，Core/Jobs 因 CHDIR 自动重试
  15 次；Web 当时尚未切换。恢复目录为 0755 后 Core/Jobs ready，再完成 Web 原子切换。随后 5 分钟
  10/10 样本四服务 active、Core ready、重启数不再增长且 0 warning。root-only 完整备份为
  `/opt/jixia-backup-before-0038-20260823T013152Z`，Web 即时回滚副本为
  `/opt/jixia-web-before-0038-20260823T013152Z`。

## 2026-08-23 Spec 215/216 批准与首批实现

- 用户已明确批准 Spec 215、216；两份规格状态均为 `Approved`，并已登记到
  `specs/README.md`。尚未标记完成，也未发布生产。
- 首批实现已把正式八席位比赛与 `experiment_mode` 解耦：运行快照新增 `formal_4v4`，正式
  4v4 使用纯布尔 `should_speak` 决策、人类优先、无 Agent fallback 的纯人类等待，以及按比赛
  种子确定性选择多个举手 Agent。历史非 4v4 保留旧快照兼容。
- HTTP/WS 快照投影和 Web 席位状态已识别正式 4v4；对方与观众仍拿不到举手队列和 Agent 决策，
  管理员可查看私有状态。每个 Agent 席位按自己的决策显示，不再共用一个聚合状态。
- 当前证据：Core 非集成测试 302 项、Web 单测 184 项通过；Ruff、Pyright、TypeScript、OpenAPI
  契约生成和 `git diff --check` 通过。新增正式 4v4 全 false 无 fallback、多 Agent true 的种子选择
  回归测试。
- 已补上 Agent 旧 LiveKit source 的同步媒体 fence、ASR 首个 PCM frame 门禁和空音频回归测试；
  0040 将正式 4v4 机会表的实验批次外键改为可空，正式比赛可持久化机会链路。当前遗留仅为
  本机 PostgreSQL/Docker 不可用导致的 migration/数据库集成门禁，以及尚未进行线上灰度和真实
  Agent/人类媒体回归；在这些证据取得前不得上线。

## 2026-08-23 Spec 215/216 线上隔离 staging

- 服务器隔离空库从零升级至 migration 0040，`alembic check` 无待生成操作。新增 PostgreSQL
  集成测试验证正式比赛在 `experiment_attempt_id=NULL` 时，可在同一机会下持久化真人举手、
  Agent 布尔决策和真人优先分配；1 项通过。
- staging Linux 环境运行 MatchActor、重置恢复、ASR 和 Agent 音频专项共 113 项，全部通过。
  临时数据库及测试虚拟环境已删除，staging 源码保留供发布前复核。
- 回调身份审计确认当前 Actor/服务已按各链路核对当前 speech、generation、decision round、
  action、Agent、context 或 opportunity；但回调接口没有在每一种回调上同时显式携带 Spec 216
  列出的全部八个身份字段。现有媒体 fence 和状态校验覆盖本次生产故障，统一回调 envelope 仍是
  发布后的协议加固项，不能将其写成已完成。
- 生产仍有一场 `PAUSED` 非终态比赛，序列为 120。按发布门禁未执行生产备份、0040 migration、
  源码/Web 切换或服务重启；必须等待该比赛自然进入终态，或取得用户对该比赛处置的明确授权。

## 2026-08-23 Spec 215/216 生产发布

### Spec 216 隐私投影增量

- WebSocket 非管理员事件已改为公共投影；候选侧的举手、Agent 决策和倒计时只通过
  viewer-scoped HTTP/command snapshot 返回。这样排队事件即使跨越对方回合，也不会
  按新状态错误授权旧队伍字段。
- 公共事件会移除 `opportunity_id`、`opportunity_generation`、`decision_round_id`、
  `generation_id`、内部音频路径和未播放 Agent 文本增量；完成事件仅保留已播放/已说出的
  正文元数据。新增核心回归测试覆盖事件类型和额外字段。
- 该增量在本机 94 个自由辩论/投影专项测试中通过。真人媒体验收与统一异步回调 envelope
  仍是明确遗留风险。

- 用户明确授权终止阻塞发布的暂停比赛；Core 管理员权威命令将比赛置为 `TERMINATED`、sequence
  121，临时运维管理员 Session 随即删除，生产非终态比赛归零。
- root-only 完整恢复点为 `/opt/jixia-backup-before-0040-20260823T125157Z`，包含源码、Web 和已校验
  PostgreSQL custom dump；生产只向前迁移至 `0040_formal_4v4_opportunities`，机会表 experiment
  attempt 外键已可空，`alembic check` 无差异。
- Core/Jobs/Web 已切换，Web 即时回滚目录为
  `/opt/jixia-web-before-0040-20260823T125434Z`。首次发布验证发现生产源码漏同步 0040 migration
  文件，数据库结构本身正确；补齐文件后 production `alembic current` 正常解析为 0040。
- 发布后深度隐私审计发现非候选方 WebSocket 的 `free.selection_locked`、`speech.started` 和
  `agent.playback_started` 仍可能取得机会、决策轮或生成标识。已最小化公开事件 payload，候选方和
  管理员保持完整字段；新增 3 组投影回归，Core 领域测试 75 项、Ruff、Pyright 通过并已热修发布，
  本地/生产文件哈希一致。
- 最终完整本地门禁：Tooling 47、QA 5、Web 184、Core 非集成 307、Jobs 非集成 23、Storybook
  89、Playwright 228；ESLint、Ruff、TypeScript、Pyright、OpenAPI 契约、34 路由生产构建和
  `git diff --check` 全部通过。Playwright 的 Core 8000 代理拒绝仅来自无 Core 的预期 fallback
  用例，不影响 228/228。
- 线上浏览器以临时管理员 Session 验证比赛数据页和 API 请求日志页，无页面错误；临时 Session
  均已删除。正确公网入口为证书覆盖的正式域名；直接使用服务器 IP 会因证书域名不匹配出现浏览器
  告警，不作为用户入口。
- 完整 5 分钟 10 样本中四服务始终 active、Core ready、重启计数 0。日志唯一 warning 是受控
  Web 停止产生的 SIGTERM 143，随后新进程立即 Ready；Core 热修后再次验证四服务、权限 0755 和
  ready 正常。
- 真实真人麦克风 ASR 重连、真实 Agent 暂停恢复及可听见旧音频比例仍需现场参与者验证，不以模拟
  或供应商探针冒充通过。回调接口尚未统一为同时显式携带全部八字段的 envelope；现有各链路通过
  speech/generation/decision round/action/context/opportunity 的相关子集拒绝迟到状态变更，统一协议
  仍是后续加固项。
- 发布工具复审已把 `scripts/ops/preflight_v2_1.sh` 的期望 migration 从 0039 修正为当前
  `0040_formal_4v4_opportunities`，并同步部署/回滚文档；否则下一次只读预检会错误阻塞发布。
- 后续只读复核确认 Core 实际监听 `127.0.0.1:8100`；服务器 `127.0.0.1:8000` 属于 1Panel，
  不得作为 Core 健康检查地址。仓库 preflight 在 8100 上通过，四服务保持 active、Core
  `NRestarts=0`、迁移 0040、非终态比赛 0；发布后观察任务完成 10/10 个 30 秒样本。
- 本轮本地收尾门禁：release-state QA 2 项、目标文件 Ruff、全仓库 lint、Pyright、OpenAPI
  契约、TypeScript 和 `git diff --check` 均通过。真实麦克风/真人 Agent 媒体验收与统一八字段
  callback envelope 仍未获得现场证据，不能标记为已验收。
## 2026-08-24 Spec 218 Agent finalization recovery

- Production evidence for match `d1ad792d-ead3-4d85-9913-a513723c6c87` showed
  `agent.finalizing` followed by a failed background task and no `agent.finalized`.
  The match stayed blocked until a later recovery. The failure was not surfaced
  as an authoritative Actor transition.
- Added a 15-second finalization timeout, stable `agent_finalization_failed` /
  `agent_finalization_timeout` error reporting, redacted diagnostics, and stale
  late-finalization handling. Added exception and timeout regression tests.
- Local evidence: Agent/match 32 passed; Core 322 passed/32 skipped; Ruff,
  Pyright, Web lint/typecheck, OpenAPI contract and production build passed.
- Production sync is intentionally pending: preflight currently reports one
  non-terminal match. Do not restart Core or terminate that match to release.
- 2026-08-24 follow-up: user explicitly authorized termination. The match was
  terminated through a privileged `MatchActor` command at sequence 29, then
  backup `/opt/jixia-backup-before-0218-20260823T163420Z` was created. The two
  changed Core files matched local SHA-256, Core restarted at `NRestarts=0`,
  preflight passed with zero active matches, and ten 30-second post-release
  samples were healthy with all four services active. The prior ASR provider
  readiness timeout remains unresolved separately.

## 2026-08-24 Spec 219 ASR LiveKit human identity

- Reproduced the reported first-speech pause path from source inspection: the
  current LiveKit token identity is `jx-human-<match_id>-<user_id>-<epoch>-...`,
  but `MatchAudioReceiver` only accepted the obsolete `user-<uuid>` format.
  Every PCM track was discarded, so speech finalization reached
  `asr_empty_audio`; after the retry budget, Core correctly entered the
  user-visible error pause.
- Added current identity parsing with legacy compatibility and regression tests
  for valid, malformed, and Agent identities. Local ASR tests (13), match
  domain/recovery tests (91), Ruff, and `git diff --check` pass.
- Spec 219 was released after the normal zero-active-match preflight and the
  user's standing termination authorization.
- Release attempt after user authorization was blocked by one existing
  production match (`2a8f80f1-6ae2-4249-9e33-d9c9ed556753`) in
  `ERROR/RECOVERY_REQUIRED` at the negative Agent first speech. Its durable
  event chain ends at `agent_finalizing` followed by
  `agent_finalization_failed`; the match must be handled through an
  explicitly authorized MatchActor termination or recovery before Core can be
  restarted. A 24-hour journal scan found this incident plus the earlier
  generic background-task errors, and no `asr_empty_audio` entries.
- User has provided standing authorization for the assistant to terminate
  production matches when required by an approved release or recovery flow;
  termination must still use the privileged `MatchActor` command and be
  recorded with the match id and resulting sequence.
- Production release then completed: backup
  `/opt/jixia-backup-before-0219-20260824T000000Z`, deployed ASR runtime hash
  matched local, Core restarted with `NRestarts=0`, post-release preflight
  passed at migration 0040 with zero active matches, and 10/10 observation
  samples passed over five minutes. No new Core errors appeared.

## 2026-08-24 Spec 220 AI debate questionnaires

- Rule-level post-match questionnaire toggle is persisted and frozen in
  `rule-config-v1`; only normal `FINISHED` matches create one idempotent task per
  human participant. Experiments and `TERMINATED` matches are excluded.
- Added personal versioned AI experience survey, participant draft/submit APIs,
  structured per-speech `HUMAN_SELF`/`TEAM_AI` ratings and tags, admin version
  editing/publishing, identity-bearing CSV/JSON export, and audit records for
  views, publishing, and exports. Personal resubmission snapshots prior submits
  into an immutable revision table.
- Local evidence: Core questionnaire/model tests 16 passed; target Ruff checks,
  Pyright, Python compileall, Web TypeScript, ESLint and OpenAPI contract checks
  passed; `git diff --check` passed.
- Remaining gate: `alembic check` could not connect because local PostgreSQL at
  `127.0.0.1:5432` was not running. No production deployment performed.

- Production release completed after the local gate: the sole `ERROR` match was
  terminated through privileged MatchActor (`TERMINATED`, sequence 23), temporary
  admin session revoked, backup `/opt/jixia-backup-before-0220-20260824T022347Z`
  created, migration 0041 applied, and Core/Jobs/Web/LiveKit restarted cleanly.
  Post-release preflight passed with zero active matches; public home and survey
  page returned 200, admin API unauthenticated returned 401, key hashes matched,
  and ten 30-second observations stayed live/ready 200 with `NRestarts=0`.

## 2026-08-24 Agent first-theory transition hotfix

- Reported symptom: after the first theory speech, the match could not enter
  free debate. Production evidence showed the negative Agent speech reached
  `agent.playback_started` and `agent.finalizing`; persistence had completed,
  but the `finalize_agent_speech` callback transaction failed. The speech stayed
  `FINALIZING`, blocking the next authoritative action.
- Added one bounded 50 ms retry around that finalization callback. A second
  failure still reports `agent_finalization_failed`; cancellation behavior and
  MatchActor timing remain unchanged.
- Regression evidence: Agent voice and match-domain tests 104 passed; Ruff and
  Pyright passed. Production backup is
  `/opt/jixia-backup-before-agent-finalization-retry-20260824T024248Z`.
  Core was restarted successfully, all services were active, live/ready were
  200, migration `0041_ai_debate_questionnaires` was present, and zero active
  matches remained. Runtime hash:
  `d6c043154cb2392354fe304c66616ad5ab1b3df7da9c5fe9eb405dc790558623`.
- Residual risk: no fresh live debate was run after deployment; validation is
  regression coverage plus production preflight. Real microphone/ASR and live
  human/Agent media acceptance remain separate field checks.

## 2026-08-24 Recurrence: negative theory finalization

- Production match `ceea7ef0-96fa-4276-879d-c0dfc9ad0266` was inspected directly
  in PostgreSQL and Core journal. The event chain ended at sequence 14
  `agent.finalizing` for negative Agent seat 1, then sequence 15
  `match.error(agent_finalization_failed)`. Current state is `ERROR /
  RECOVERY_REQUIRED`, paused from `AGENT_PREPARING`; this is why the UI never
  reaches the next stage.
- The generation and AgentAudioAsset were `FINALIZED`, the Speech row had the
  complete 411-character text but remained `FINALIZING`, and no `agent-{speech}`
  MatchFile existed. The same signature appears in all four recorded
  `agent_finalization_failed` incidents. LLM/TTS provider calls were already
  `SUCCEEDED`; the failure is in the final MatchActor finalization transaction.
- Local source fix: make Agent MatchFile persistence idempotent by updating an
  existing row under lock, and add a sanitized exception type to the diagnostic
  record. Target tests remain green: 114 passed, Ruff, Pyright, and
  `git diff --check` passed.
- This recurrence is not yet deployed. The current production ERROR match must
  be recovered or terminated through its existing MatchActor before restarting
  Core; do not create a second Actor against the same match.

## 2026-08-24 Recurrence fix release

- With the user's explicit terminate-and-release authorization, Core was stopped
  before recovery. An isolated `MatchRuntimeManager` rehydrated the one ERROR
  match and submitted privileged `match.terminate`; the durable result was
  `TERMINATED`, sequence 16. No direct DB state mutation was used.
- Backup: `/opt/jixia-backup-before-finalization-idempotency-20260824T20260824T030911+0800`.
- Deployed idempotent Agent MatchFile finalization and sanitized exception-type
  diagnostics. Local and production hashes match:
  `runtime.py` `d30c451c8f75c407f40a04d94938afc0159e6eaefd2430165c9bcf78e4546429`;
  `matches/service.py`
  `44dfec9cb4e4eb00680653691a9dbb5cc7df538beb4306de339e00cd38e8f528`.
- Production preflight passed at migration 0041 with zero active matches; Core,
  Jobs, Web, and LiveKit were active, Core live/ready returned 200, and Core
  `NRestarts=0`. No new finalization or background-task errors appeared after
  restart.

## 2026-08-24 Definitive free-debate transition fix

- The production recurrence was finally localized to the `_commit` handler for
  `agent.decision_started`: it added a `FreeDebateOpportunity` and its
  `AgentFreeDebateDecision` children in one transaction without an explicit
  flush. SQLAlchemy could order the child insert first, causing
  `23503/fk_agent_free_debate_decisions_opportunity`; the failed finalization
  transaction then made the UI appear to pause after negative theory.
- `matches/service.py` now flushes the opportunity before inserting decisions.
  This is the root cause and replaces the earlier provisional `MatchFile`
  diagnosis. A PostgreSQL integration regression exercises the exact manager
  commit path.
- The runtime also records redacted pipeline exception type/SQL constraint and
  retries `start_agent_playback` once after a bounded 50 ms delay. A second
  callback failure still pauses through `agent_pipeline_failed`.
- Local evidence: 116 Agent/match tests passed; Ruff, Pyright, and
  `git diff --check` passed. The integration test is skipped unless an isolated
  `TEST_DATABASE_URL` is supplied.
- Final production E2E match `ddaf46a8-5517-4b45-989c-5051af64aa6c` passed both
  theory finalizations and entered free debate with decision/hand events; the
  no-candidate 60-second human wait paused normally and was then terminated
  through a privileged MatchActor. Final preflight: migration 0041, zero
  active matches, all four services active, live/ready 200, and zero restarts.

## 2026-08-24 Full-flow regression and home navigation conflict

- Added a deterministic full Actor lifecycle regression covering both Agent
  theory speeches, entry into formal free debate, one Agent turn per side, and
  normal `FINISHED / MATCH_FINISHED` completion. This closes the gap left by the
  earlier production E2E, which intentionally stopped at the 60-second
  human-only wait.
- Production browser inspection reproduced that unauthenticated visitors see
  the 实验安排 navigation item. The root PRD says the user navigation is fixed to
  首页 / 比赛大厅 / 使用指南 / 我的页面, but approved Spec 206 says 实验安排 is
  always visible. The user's report resolved the conflict in favor of removing
  the protected experiment workspace from public navigation; `/experiments`
  remains available through direct and account-specific entry points.
- Full-flow evidence now includes 330 Core non-integration tests and two
  formal-4v4 PostgreSQL integration tests against a freshly migrated isolated
  database; the temporary database was dropped after the run. The deterministic
  lifecycle covers both Agent theories, one free-debate Agent speech per side,
  and normal `FINISHED / MATCH_FINISHED` completion.
- Spec 221 was released as a Web-only change after a zero-active-match preflight.
  Backup: `/opt/jixia-web-before-0221-20260824T094340`. Production browser
  inspection confirmed the public header no longer exposes 实验安排 while the
  protected direct route still reaches login. Ten 30-second samples kept all
  four services active, live/ready passing, and restart counters at zero.

## 2026-08-24 Spec 222 strict debater questionnaire

- Replaced the participant annotation and rule-controlled post-match survey
  with the user-approved questions: HUMAN_SELF single-choice reason plus
  multi-select goals, TEAM_AI locked pre-speech suitability plus single-choice
  post-reveal team-need match, and exactly five 1-5 overall experience items.
- Removed the new-flow seven-point rating, generic tags, three summary text
  areas, and optional sixth question from the participant UI. Historical rows
  and the nullable `q6` storage field remain readable and unchanged.
- New ordinary tasks use `postmatch-v2-strict-2026-08-24`; new experiment tasks
  use `paper-v2.1-strict-2026-08-24`. Personal-page defaults use the same five
  items; an older published default is archived on first read and existing
  responses remain bound to that old version.
- Core validates question types, exact choices, all speech items and all five
  overall scores on submit. Core and Web both reveal AI text only after the
  pre-speech answer draft is saved successfully, including the HTTP response
  projection. The saved pre-speech answer is immutable through Core and remains
  revealed and disabled after a browser refresh.
- The exact-question follow-up removed the duplicate description beneath the
  HUMAN_SELF `其他` option. New strict experiment requests no longer send Q6,
  and Core rejects a non-empty legacy Q6 for strict tasks while retaining the
  nullable historical column for read compatibility.
- Final strict-text review removed UI-only suffixes from the HUMAN_SELF Q2 and
  TEAM_AI Q1 labels, and restored the exact `赛后辩手体验问卷` section title;
  input types and the saved answer values are unchanged. Follow-up target
  verification passed: Core survey tests 7/7, Web strict post-match tests 2/2,
  TypeScript, ESLint and `git diff --check`.
- Evidence: Core 335 passed/33 deselected; Web 186 passed; Ruff, ESLint,
  TypeScript, OpenAPI contract and diff checks passed. Browser checks at 1280
  and 390 px found no overflow, no legacy controls, correct AI reveal behavior,
  and no confirmed axe A/AA violations. Storybook regression is 89/89; the
  strict annotation page was also checked at 1280 and 390 px with 25 overall
  radio options, no Q6 text, no textarea and no horizontal overflow. The
  added PostgreSQL Q6 rejection assertion was not run because this workspace
  has neither Docker CLI nor an independent `TEST_DATABASE_URL`. Production
  release has not been run.

## 2026-08-24 Spec 222 exact wording follow-up

- Rechecked both strict participant questionnaire surfaces against the final
  user-supplied Chinese wording. HUMAN_SELF Q2 now visibly joins each option
  title and explanation with the approved full-width colon in both the
  experiment annotation page and the ordinary post-match survey page; stored
  answer values and historical records are unchanged.
- Tightened the ordinary post-match two-stage projection: AI speech is revealed
  only when Q1 contains one of the approved choices, and Core rejects a Q2
  draft without such a valid pre-speech answer. This closes the prior
  `{"q1": null}` key-presence bypass without changing valid drafts.
- Verification: Core survey tests 8/8, Web tests 186/186, Ruff, target ESLint,
  Web TypeScript and OpenAPI contract checks passed. Storybook now includes the
  dedicated HUMAN_SELF wording scenario and passes 90/90; Prettier and final
  diff checks also pass. No production release was performed for this wording
  follow-up.
- A final service-boundary audit closed two validation gaps without changing
  question text: strict speech submissions now reject `null` Q1/Q2 values, and
  personal `submit=false` saves may contain a valid partial five-question draft
  while `submit=true` still requires all five answers. Survey, post-match and
  experiment regression tests passed 17/17 with one independent PostgreSQL case
  skipped; Ruff, service-module Pyright and diff checks passed.

## 2026-08-24 Spec 225 independent-value Agent Prompts

- The user rejected and withdrew the independent-value Prompt proposal before
  release. Its business-code and test changes were reverted.
- The paper-rule decision and speech text again matches the Spec 213 approved
  wording, and the legacy experiment Prompt version is restored to
  `paper-v2.0-2026-08-21`.
- No production release occurred, so no production rule, room or match snapshot
  was affected by the withdrawn proposal.

## 2026-08-24 Spec 226 human speech start reliability

- The repeated “human has speaking rights but cannot start” symptom had three
  independent causes left after Specs 166/167/168/219/223: Web could send an
  old `expected_sequence` while an event-triggered snapshot refresh was still
  pending; ASR startup ran before MatchActor idempotency; and one unexpected
  runtime exception escaped the WebSocket command loop and closed the business
  connection.
- `speech.start` now validates and deduplicates in the serialized MatchActor
  before a bounded ASR pre-commit startup. Startup/commit failure cleans the
  attempted ASR session, rolls back to `HUMAN_READY_TO_START`, preserves the
  full 30-second allowance and permits retry. Duplicate successful message IDs
  do not start ASR twice.
- Web synchronously tracks the highest event sequence, disables commands while
  its cached snapshot is behind, refreshes before send, and then rechecks the
  current socket/epoch/sequence. Synchronous send failure settles immediately.
  Unexpected Core command errors are sanitized per command without destroying
  the WebSocket.
- Evidence: focused Core tests 20 passed; Core non-integration 355 passed;
  repository tests passed (tooling 47, QA 6, Web 190, Core 355, Jobs 25);
  Storybook 90 passed; live-match browser 12 passed and full browser 232 passed;
  Ruff, ESLint, TypeScript/Pyright, contracts and production build passed.
- No independent PostgreSQL URL or Docker was available, so integration tests
  were not run. Physical microphone and real provider startup remain release-
  site verification. Spec 226 has not been released to production.

## 2026-08-24 Spec 227 free-debate Prompt save and speech template

- The confirmed free-debate speech Prompt could not be saved because Core
  derived its required variables from the decision Prompt set and therefore
  incorrectly required `SIDE_REMAINING_MS` and `OPPONENT_REMAINING_MS`.
- Free speech now requires role variables, `MAX_SPEECH_SECONDS`,
  `TARGET_CHAR_COUNT` and `DEBATE_HISTORY`; both remaining-time variables are
  still recognized when an administrator chooses to include them.
- The ordinary formal-4v4 default and paper-experiment rule now share the exact
  user-confirmed speech constant. The decision Prompt remained byte-identical;
  its SHA-256 stayed
  `4ac43ed2e3d6a9daf8cf8e1a288e778f231cb7971c10441c5a6fafb424ae0a3e`.
- Evidence: Prompt/paper-rule 21 passed; repository tooling 47, QA 6, Web 191,
  Core 366 and Jobs 25 passed; Storybook 90 and full browser 236 passed; the
  focused save flow passed all four viewports; Ruff, ESLint, TypeScript/Pyright
  contracts, production build, Prettier and diff checks passed.
- PostgreSQL integration was unavailable because there is no independent test
  URL or Docker. No production release or paper-rule ensure was performed;
  deployment must create/update the production rule version explicitly, while
  existing room and match snapshots remain unchanged.

## 2026-08-24 Specs 226/227 production release

- The sole release blocker was an abandoned paper-rule match
  `05de98ae-4ed2-4dc2-a57d-40a493870d7d` in `ERROR` at sequence 41. Under the
  user's standing authorization it was terminated through the privileged
  MatchActor endpoint and reached `TERMINATED` at sequence 42; no direct
  database state edit was used.
- Root-only rollback backup
  `/opt/jixia-backup-before-0226-0227-20260824T101605Z` contains the affected
  Core source, prior Web standalone and a verified PostgreSQL custom dump.
  Four Core files and the Web runtime source were synchronized by exact path;
  `/opt/jixia-debate` remained `0755`.
- An initial unbounded Linux Web build exhausted host resources, caused the
  Core PostgreSQL advisory-lock connection to be lost, and Core exited cleanly
  by design. The build was stopped and Core restored. The first Web artifact
  also revealed that the production source tree lagged the deployed Spec 221
  navigation and would reintroduce `实验安排`; that artifact was rolled back
  before final acceptance. The final build included the approved
  `site-header.tsx`, ran in a transient systemd cgroup with a memory hard limit,
  and was switched from a new standalone directory.
- Paper experiment rule v4 is enabled after 3/3 host-audio assets became
  `READY`; versions 1-3 are archived. Persisted decision Prompt SHA-256 is
  unchanged at
  `4ac43ed2e3d6a9daf8cf8e1a288e778f231cb7971c10441c5a6fafb424ae0a3e`.
- The deployed MatchActor deterministic simulation completed affirmative and
  negative theories, opened free debate, selected and finalized one Agent turn
  per side, and finished at sequence 35 with `FINISHED / MATCH_FINISHED`.
  Production TTS, LLM, judge and LiveKit probes passed; the ASR readiness probe
  timed out and remains a real-device/release-site risk rather than a pass.
- Final browser verification showed no `实验安排` public navigation, no
  horizontal overflow, and correct login redirects for protected pages. Final
  preflight passed at migration 0041 with zero active matches. Ten 30-second
  samples kept Core, Jobs, Web and LiveKit active, live/ready at 200, restart
  counters at zero, and added no warning-or-higher service logs.

## 2026-08-24 Spec 228 affirmative-first free debate

- User approved changing the first free-debate opportunity from the negative
  side to the affirmative side. The new requirement supersedes only the
  negative-first clauses in Spec 201; fixed speeches, competition timing,
  human priority, Prompts and frozen historical snapshots are unchanged.
- The paper rule description, host copy and `starting_side`, plus the admin new-
  rule default, now use `AFFIRMATIVE`. Paper-rule ensure/preparation compares
  against that canonical draft; restore continues to preserve historical
  backed-up rule semantics.
- The complete deterministic MatchActor flow passed both theory speeches,
  affirmative first free-debate Agent selection and speech, negative reply,
  and `FINISHED / MATCH_FINISHED`. A separate compiler assertion preserves an
  explicit historical `NEGATIVE` starting side.
- Prompt hashes are unchanged: decision
  `4ac43ed2e3d6a9daf8cf8e1a288e778f231cb7971c10441c5a6fafb424ae0a3e`,
  speech `be38ff2874939758cf9397ca4f37bf96391c790e3cd65713bcb2ee80e6f7ba92`.
- Verification passed: tooling 47, QA 6, Web 191, Core 366, Jobs 25, browser
  236, Storybook 90, production build, lint, TypeScript/Pyright, contracts,
  formatting and diff checks. PostgreSQL integration tests were not run
  without an independent `TEST_DATABASE_URL`.
- Spec 228 is released. Preflight passed at migration 0041 and zero active
  matches. Rollback backup:
  `/opt/jixia-backup-before-0228-20260824T132000Z`; immediate Web rollback:
  `/opt/jixia-web-before-0228-switch-20260824T141443Z`.
- Paper rule v5 is the sole enabled version, uses `AFFIRMATIVE`, has 3/3 host
  assets `READY`, and retains the approved decision/speech Prompt hashes. Older
  versions are archived and historical snapshots were not rewritten.
- Production Web source lacked three already released Spec 220 survey pages and
  the matching `surveyApi` client. A 34-route artifact was rejected before
  switch and a follow-up build failed on the missing export. After restoring
  those approved sources, the accepted Linux build passed TypeScript and
  contained all 37 routes. This source drift and the memory-constrained build
  should be included in future release preflight checks.
- The deployed full Actor simulation passed both theories, affirmative first
  free turn, negative reply and `FINISHED / MATCH_FINISHED` at sequence 35. One
  earlier in-memory attempt saw a playback state conflict; a minimal trace and
  complete rerun passed on the identical deployed hash without database writes.
- Public browser smoke passed navigation, overflow and protected-route checks.
  Final preflight passed; ten 30-second samples kept all four services active,
  health at 200 and restart counters at zero, with no warning-or-higher logs
  after the final service start.

## 2026-08-25 production resource cleanup and Spec 230 proposal

- A read-only ownership audit covered systemd working directories, OpenResty
  references, mounts, symlinks, open files, Docker container/image/volume
  references and exact directory sizes. Production PostgreSQL, match audio,
  pending archive tasks, build dependencies, current Web/runtime directories
  and the current Spec 229 rollback were retained.
- Removed seven unreferenced historical Jixia rollback directories, ten
  unreferenced anonymous PostgreSQL integration-test volumes created on
  2026-08-04, and their now-unused `postgres:16.14-alpine` image. Named CPA
  volumes, its stopped container and the unrelated unused ChatGPT image were
  deliberately left untouched. Root filesystem usage fell from 36% to 33%,
  approximately 1 GB reclaimed; deleted resources are not recoverable.
- A second data-tree audit retained all production/research audio, host assets
  and exports inside their retention window. It removed three unreferenced
  failed `.ogg.part` files older than the PRD's 24-hour limit plus the stray
  `--full-page`, `.DS_Store` and AppleDouble metadata files from the runtime
  checkout. No expired `.part` file remains; these deletions are not
  recoverable.
- Post-cleanup, Core/Web/Jobs/LiveKit remained active with zero restarts, Core
  live/ready returned 200/200, `/opt/jixia-debate` remained 0755, and rollback
  `/opt/jixia-backup-before-0229-20260824T163719Z` remained present.
- Production match `74840b50-a941-48dc-bab9-7eb1dfb96565` proves a restarted
  human Speech received a new 30-second allowance but its second deadline was
  discarded by the old Speech-only idempotency key. It also proves ASR failure
  counts leak across free-debate opportunities sharing action key `3:0`, and
  participant survey writes have no corresponding audit actions.
- Spec 230 defines attempt-scoped deadlines, per-logical-turn ASR retry and
  immutable failed Speech semantics, plus atomic redacted participant survey
  audits and a clean brand-new-account production acceptance flow. The user
  approved it on 2026-08-25.

## 2026-08-25 Spec 230 implementation and pre-release verification

- Deadline commands now have a unique committed-start identity and carry
  `speech_id` plus `start_sequence`; stale callbacks cannot finalize a later
  human or Agent start. Runtime snapshots persist the optional start sequence,
  so no database migration is needed and old snapshots remain readable.
- Free-debate ASR attempts and failure budgets are scoped by the allocated
  opportunity. The first failure retains an immutable `FAILED` Speech,
  invalidates its speculative opportunity and returns the same human to a
  full-time retry with a new Speech. The second failure pauses and recovery
  starts a third distinct Speech without transferring the allocation.
- ASR mutation boundaries validate Speech, attempt, connection epoch, context
  version, opportunity and opportunity generation. Late ASR finalization does
  not change a `FAILED` Speech.
- Participant personal/post-match draft and submission writes now append
  redacted audit actions in the same transaction. Locked and invalid requests
  add no successful audit.
- An isolated PostgreSQL database reached migration `0041` through an SSH
  tunnel and passed all five formal 4v4 integration tests. The temporary
  database, role, tunnel and remote source directory were removed afterward;
  production data was never used.
- Verification passed: tooling 47, QA 6, Web 191, Core 368, Jobs 25, isolated
  PostgreSQL 5, Storybook 90 and browser 236; lint, Ruff, TypeScript/Pyright,
  contracts, production build and diff checks passed. The first browser run
  collided with a concurrently running Next build lock; the required serial
  rerun passed all 236 tests.
- Read-only production preflight before release passed at migration 0041 with
  zero non-terminal matches, `/opt/jixia-debate` mode 0755, live/ready 200/200,
  and Core/Web/Jobs restart counters at zero.
- Spec 230 is deployed. Only `matches/domain.py`, `matches/service.py` and
  `survey_service.py` were synchronized, and their local/remote SHA-256 hashes
  match. Rollback backup:
  `/opt/jixia-backup-before-spec230-20260825T1405Z`. A release-command quoting
  mistake briefly created `/apps` containing only those three backup copies;
  ownership/time/content were verified, the exact stray directory was removed,
  and the correct rollback was recreated before any production source sync.
- A brand-new production account created a formal 4v4 room and entered both
  theories and affirmative-first free debate. The flow passed an Agent turn,
  a human hand raised during Agent playback and human priority allocation. The
  human free Speech reached `TIME_LIMIT` at exactly the authoritative deadline;
  its synthetic browser media then failed ASR, the `FAILED` row remained
  immutable and the same user received a full retry. The subsequent 60-second
  human-start timeout paused as designed; the QA match was terminated through
  the UI under standing authorization. This is not claimed as a natural-
  `FINISHED` or physical-microphone acceptance run.
- The same new account saved and submitted the personal AI survey. Production
  database evidence shows `SUBMITTED` plus redacted
  `participant.survey.personal_draft_saved` and
  `participant.survey.personal_submitted` rows containing only status/version
  detail keys. Post-match survey UI could not be accepted because a terminated
  match must not generate that task; its save/submit/audit behavior is covered
  by isolated PostgreSQL integration. A real-device natural-finish run remains
  the explicit operational follow-up.
- Final preflight again passed with migration 0041, zero non-terminal matches,
  health 200/200, runtime mode 0755 and restart counters zero. Temporary test
  database/role/tunnel/remote source, browser credentials/cookies and synthetic
  media were removed; the local sensitive files were moved to Trash.

## 2026-08-25 Spec 230 extended production flow evidence

- Two new production accounts created and joined formal 4v4 match
  `461dc960-b3c6-47ee-8860-8b5f88615614` on opposite fourth-debater seats.
  The match completed both theories, affirmative-first free debate, repeated
  alternating Agent turns, human-only waits and human turns, then reached
  natural `FINISHED / MATCH_FINISHED` at sequence 547 with both side clocks at
  zero. No SQL state edit or QA termination was used.
- Expected safety pauses were exercised rather than hidden: all-Agent skip led
  to a 60-second human-only wait and `HUMAN_WAIT_TIMEOUT`; a missed retry start
  led to `HUMAN_START_TIMEOUT`; a second ASR failure led to
  `ERROR / RECOVERY_REQUIRED`. Owner recovery preserved the allocated speaker
  and restarted the documented full window.
- Production Speech evidence retained failed attempts as immutable `FAILED`
  rows, created distinct retry Speech IDs and attempt numbers, and successfully
  finalized a recovered controlled-audio turn before the state machine
  continued. Agent opportunities continued across unique committed start
  sequences without stale deadline interference.
- Normal finish generated one strict post-match survey task per human. Both are
  `SUBMITTED` on `postmatch-v2-strict-2026-08-24`; both accounts also have a
  `SUBMITTED` personal AI survey response on the current published version.
  Production audits contain the four submission actions plus draft actions,
  with status/version-only details and no answer content.
- The browser microphone source was controlled Chinese audio. This is valid
  deployed integration evidence, not physical-microphone evidence; no physical
  device acceptance is claimed.
- The run intentionally exercised ASR error recovery and included missed human
  start windows, so it does not meet Spec 230 acceptance item 8's stricter
  requirement of no `match.error` and no operator-induced timeout. Natural
  finish and both questionnaire flows are proven; a clean physical-device run
  remains the explicit residual acceptance gap.

## 2026-08-25 clean full-flow production acceptance and follow-up cleanup

- Public UI registration created two more new accounts. They created/joined
  formal room `948778`, occupied opposite fourth-debater seats, completed
  device preparation and started match
  `dd4ab26e-ed75-42df-b4d7-c66009889d83` without privileged shortcuts.
- Both theories completed and affirmative-first free debate ran through 31
  Agent speeches. A final all-Agent skip entered affirmative human-only wait;
  the human immediately raised a hand and completed the remaining 3.3 seconds
  through a controlled Chinese MediaStream. ASR finalized 17 characters and
  the match naturally reached `FINISHED / MATCH_FINISHED` at sequence 539 with
  both clocks zero.
- Database evidence has one finish event, no pause/error event and 32
  `FINALIZED` Speech rows with no failure. Both strict post-match tasks and both
  personal AI survey responses are `SUBMITTED`. All four participant
  draft/submit audit action types are present with status/version-only details.
- This run satisfies Spec 230's clean synthetic end-to-end acceptance: there
  was no `match.error`, operator timeout, recovery or direct database mutation.
  The device probe used Chromium's deterministic microphone and the final
  Speech used controlled Chinese audio; a physical microphone remains outside
  the evidence and is not claimed.
- A renewed production reference audit removed six unreferenced failed audio
  fragments older than 24 hours, obsolete `/opt/apps` and `/opt/node_modules`
  Next build remnants, and 63 broken symlinks to a deleted temporary
  PostgreSQL source tree. Two younger `.part` files remain under retention;
  unrelated Docker resources were retained. Services stayed active, health was
  200/200, restart counters remained zero and `/opt/jixia-debate` stayed 0755.
# 2026-08-29 Web questionnaire surface audit

- Production read-only audit found both questionnaire APIs and route bundles
  deployed, with migration `0041_ai_debate_questionnaires`; recent finished
  matches generated tasks only when the frozen rule snapshot enabled them.
- The production `/me` source lacked the two questionnaire links while the
  local source had them, demonstrating a mixed/old Web standalone deployment.
- Added `assertQuestionnaireSurface` to `scripts/build-web.mjs` and regression
  coverage in `scripts/build-web.test.mjs`; deployment guidance now requires a
  single atomic standalone build and route/link checks.
- Verification: build-web tests 12/12, Web TypeScript, ESLint and `pnpm build`
  passed before the production Web-only switch.
- A first Web-only switch was rolled back immediately after home and personal
  AI page returned 502 because the local build used Core rewrite port 8000.
  Rebuilt with `CORE_API_ORIGIN=http://127.0.0.1:8100` and atomically switched
  on 2026-08-29. Backup: `/opt/jixia-web.before-survey-surface-20260829T072838Z`.
  Final checks: `jx-web active`, `NRestarts=0`, `/opt/jixia-web` 0755,
  home/AI/post-match pages 200, Core live/ready 200, unauthenticated survey
  APIs 401. No Core, Jobs, database, match or permission state changed.

## 2026-08-29 Spec 232 release

- Added bounded `decision_reason` to free-debate Agent decisions and strict
  Prompt/parser/persistence handling. Added `ALL_AGENT_SKIP_RANDOM` allocation
  only when a side has no human participants and all valid Agent decisions
  skip; human-priority behavior is unchanged.
- Migration `0042_agent_decision_reason` is production head. Core and Web were
  restarted/switched with no non-terminal matches; all services active and
  restart counters zero. Home and both questionnaire pages remain HTTP 200.
- Verification: Core 368 passed (36 deselected), focused tests 144 passed, Web
  191 passed, Ruff/Pyright/TypeScript/contracts and production build passed.
  Physical microphone and authenticated end-to-end decision capture remain
  unclaimed.

## 2026-08-29 Spec 233 zero-time queue diagnosis

- Authenticated production snapshot for match
  51abd716-7103-4204-bdf8-7b7e51191278 showed FREE_SELECTING, affirmative
  holder with 8684 ms remaining, negative at 0 ms, and a negative human in
  hand_queue. This confirms a stale cross-side queue entry.
- Added Spec 233 and a minimal domain fix: zero-time sides cannot raise
  (positive remaining time remains eligible); queued human entries are filtered
  to the current holder side before selection and when a turn closes.
- Verification: free-debate domain tests 14 passed; match-domain plus
  free-debate regression tests 103 passed; Ruff passed. Production has not
  been restarted or mutated in this turn.

## 2026-08-29 Spec 233 production release

- The affected match was confirmed TERMINATED / MATCH_FINISHED before release;
  no active match was interrupted.
- Synced the Core domain fix and Spec 233 to /opt/jixia-debate with a
  timestamped rollback copy, preserving /opt/jixia-debate mode 0755.
- Restarted jx-core only. jx-core, jx-jobs, jx-web and jx-livekit are active;
  Core live/ready returned 200 and jx-core NRestarts remained 0.
- Production domain.py hash:
  a26ce340865d72d6d981ebad0d3350d63e7eaa7b7152fe8a06690fc780f37c64.

## 2026-08-29 homepage visual refresh

- Replaced the old SVG node network on the home Hero with the existing
  versioned anime illustration `jixia-debate-hero-v3-anime.png`, which shows
  two distinct teams with human and visually consistent-but-different Agents.
- Updated the shared brand lockup to use `logo-new.jpeg`, larger mark, exact
  `稷下·争鸣` name, and `多人多智能体实时语音交互平台` subtitle. Home copy now
  uses the approved slogan and supporting line.
- Verification: `pnpm lint`, `pnpm typecheck`, and
  `CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build` passed. Browser checks at
  1280px and 390px showed no horizontal overflow and the Hero image loaded.
- A new image generation call was not made because no image API credential is
  present in the environment; no credential from `api.md` was used.

## 2026-08-29 reference-layout refinement

- Matched the supplied reference layout by adding a four-item capability strip
  below the Hero: 多元协作、实时语音、公平竞技、成长体系. Existing live-room,
  three-step, and ranking sections remain intact below it.
- Kept the versioned anime Hero asset and exact webpage-rendered Chinese copy;
  no text was baked into the image.
- Verification: Web lint/typecheck and the repository `CORE_API_ORIGIN=...
  pnpm build` completed successfully. A 1280px browser screenshot confirmed
  the intended two-column Hero and four-column capability strip.

## 2026-08-29 homepage visual v4

- Generated `output/imagegen/jixia-debate-hero-v4.png` with the configured
  compatible Image API and `gpt-image-2`; the prompt required a bright anime
  SaaS illustration, two mixed human/Agent teams, consistent robot language,
  and a central opposing audio waveform.
- Switched the Hero to the v4 asset, enlarged the brand lockup typography and
  subtitle, matched the reference button hierarchy, and increased capability
  cards to a consistent 168px minimum height.
- Verification: Web lint/typecheck and production build passed; 1280px browser
  screenshot reviewed. No credential was written to the repository.

## 2026-08-29 homepage visual v5

- Generated `output/imagegen/jixia-debate-hero-v5.png` with a brighter,
  integrated studio composition: standing and seated humans plus standing and
  seated Agents actively debating on both sides.
- Switched the Hero to v5, removed the hard image card treatment, applied a
  light blend into the page canvas, increased brand typography, and aligned the
  reference button hierarchy and capability-strip height.
- Verification: Web lint/typecheck, production build, and a 1280px browser
  screenshot passed review. The local dev server remains available on port
  3000 for visual review.

## 2026-08-29 hero edge integration

- Converted the v5 hero artwork to a versioned RGBA asset with a transparent
  white/pale background and switched the page to it.
- Added a radial CSS mask and multiply blend on `.jx-hero-visual` so the
  generated art fades into the light page canvas rather than showing a hard
  rectangular image boundary.
- Verification: 1280px browser screenshot reviewed; Web lint/typecheck and the
  required production build passed.

## 2026-08-29 hero full-frame display

- Fixed the v5 transparent Hero display cropping by replacing the fixed `3:2`
  cover frame with the source artwork's native `1693:929` ratio and
  `object-contain` sizing.
- Verified desktop and 390px mobile screenshots: all team members and Agents
  remain visible, with no horizontal overflow. Web lint/typecheck and the
  production build passed.

## 2026-08-29 homepage spacing tune

- Reduced capability cards to a 136px minimum height and tightened padding.
- Increased the brand subtitle to 0.9rem, moved the public lobby action into
  an explicit second grid row, and shifted the desktop Hero visual left by
  1rem while preserving mobile flow.
- Verification: 1280px and 390px screenshots reviewed; Web lint/typecheck and
  production build passed.

## 2026-08-29 homepage spacing tune 2

- Reduced capability cards to a 112px minimum height with tighter spacing.
- Increased the brand subtitle to 1rem with darker color and heavier weight.
- Shifted the desktop Hero visual left by an additional 1rem; mobile remains
  unshifted to prevent overflow.
- Verification: 1280px and 390px screenshots reviewed; Web lint/typecheck and
  production build passed.

## 2026-08-29 brand lockup typography

- Increased the vertical gap between `稷下·争鸣` and the platform subtitle to
  `0.32rem` while keeping the two-line group vertically centered with the Logo.
- Changed the interpunct in `稷下·争鸣` to the same dark ink color as the wordmark.
- Verification: 1280px browser screenshot reviewed; Web lint/typecheck passed.

## 2026-08-29 brand lockup typography 2

- Shifted the two-line brand copy upward by `0.2rem`, increased its line gap to
  `0.48rem`, and enlarged the platform subtitle to `1.08rem` for stronger
  alignment and readability.
- Verification: 1280px browser screenshot reviewed; Web lint/typecheck passed.

## 2026-08-29 homepage visual production release

- Published the complete Web standalone build containing the homepage visual
  refinements and transparent Hero asset to `/opt/jixia-web` via a timestamped
  staging directory and atomic directory switch.
- Preserved rollback copy `/opt/jixia-web.before-home-v5-<timestamp>` and set
  the runtime directory to mode 0755 with `jixia:jixia` ownership.
- Verification: `jx-web` active, restart count 0, local home/asset 200, and
  public `https://debate.vsagents.online/` home and asset both 200. Core,
  Jobs, and LiveKit were not changed.

## 2026-08-29 human speech transient query resilience release

- Added Spec 234. Live match pages now keep an already-entered match mounted
  when snapshot, room, or current-user refreshes briefly fail; initial entry
  failures still show the entry error boundary.
- Human raw-audio file persistence in the normal `speech.finished` commit is
  idempotent, matching the late-ASR path and avoiding duplicate `(match_id,
  file_key)` inserts.
- Verification: match Web tests 18/18 passed, Core Python compile and targeted
  service tests passed (15 passed, 5 PostgreSQL-dependent skipped), production
  Web build passed. Published with rollback copy
  `/opt/jixia-backup-before-234-20260829205912`; all four services active,
  Core readiness 200, public home and `/me/ai-experience` returned 200. The
  remote preflight script was stale (expected migration 0041); direct read-only
  check confirmed production head `0042_agent_decision_reason` and zero active
  matches.

## 2026-08-29 debate participant visual hierarchy

- Enlarged live debate seat cards: avatars are 56/64px, names and seat labels
  use stronger type, and status/type badges have clearer contrast.
- Added framed affirmative/negative stance blocks with larger line height and
  widened desktop team columns; retained compact columns below 900px to avoid
  mobile overflow.
- Verification: debate layout and live-match tests 33/33 passed, Web lint and
  TypeScript passed, Prettier passed, and the production Web build passed.

## 2026-08-29 stance panel refinement

- Reworked affirmative/negative stance copy into a dedicated "阵营主张"
  panel with a stronger type scale, colored accent rule, layered surface, and
  improved line height for long positions.
- Verification: debate layout and live-match tests 33/33 passed, Web lint,
  TypeScript, Prettier, and production build passed. This refinement is built
  locally and has not been deployed without an explicit release request.

## 2026-08-29 participant type badge sizing

- Increased the human/AI identity badges to `text-xs` with larger padding and
  stronger line-height while leaving card geometry and runtime behavior intact.
- Verification: debate layout and live-match tests 33/33 passed; Web lint,
  TypeScript, and Prettier passed. Not deployed without a release request.

## 2026-08-29 participant type badge production release

- Rebuilt and published the enlarged human/AI identity badges in the Web
  standalone bundle.
- Verification: production build passed; `jx-web` active after restart;
  public home and debate routes returned 200 and the logo asset returned 200.
- Rollback copy: `/opt/jixia-web-before-badge-20260829212500`.

## 2026-08-29 centered stance copy

- Centered stance text horizontally and vertically, reduced the panel padding,
  and limited visible copy to two lines for a shorter, denser panel.
- Verification: debate layout and live-match tests 33/33 passed; Web lint,
  TypeScript, and Prettier passed. Not deployed without a release request.

## 2026-08-29 admin brand overflow fix

- Fixed the compact admin sidebar brand rules: constrained the brand width,
  reduced the wordmark size, removed the desktop-only transform, and hid the
  long platform subtitle in compact mode. This prevents the brand from
  overlapping the admin page title/header.
- Verification: admin shell tests 2/2 passed; Web lint and TypeScript passed.
  An existing Prettier warning remains in the untouched `jixia-logo.tsx` file.

## 2026-08-29 admin brand overflow production release

- Published the compact admin brand overflow fix in a complete Web standalone
  build.
- Verification: production build passed; `jx-web` active; public home,
  `/admin`, and `/me` returned 200.
- Rollback copy: `/opt/jixia-web-before-admin-brand-20260829214000`.

## 2026-08-29 debate brand subtitle

- Restored `多人多智能体实时语音交互平台` in the compact debate header while
  keeping it hidden only for the admin sidebar via the scoped `admin-brand`
  class, preventing the previous global compact rule from hiding it on debate.
- Verification: admin/debate tests 16/16 passed; Web lint, TypeScript,
  Prettier, and production build passed. Not deployed without a release request.

## 2026-08-29 debate brand subtitle production release

- Published the debate header subtitle version in the Web standalone bundle;
  admin compact branding remains scoped and non-overflowing.
- Verification: production build passed; `jx-web` active; public home, debate,
  and admin routes returned 200.

## 2026-08-30 postmatch survey staged flow (unreleased)

- Added Spec 235 for the confirmed per-match workflow: overall experience
  first, transcript/speech review second; personal AI survey remains separate.
- Added optional `stage=overall|speeches` to the postmatch save API. Overall
  answers are persisted before transcript review unlock; speech submission
  still uses the existing strict validation and AI hindsight protection.
- Added Core gate for formal experiment participants: an earlier FINISHED
  match in the same batch with an enabled postmatch snapshot blocks entry until
  that user's task is SUBMITTED. Training and disabled/legacy snapshots are
  unaffected.
- Web postmatch page now hides transcript and per-speech questions until the
  overall stage is completed; tests updated for the two-step flow.
- Verification: Core survey tests 8/8 passed, Web postmatch tests 2/2 passed,
  Web TypeScript passed. Not deployed; full browser flow and production build
  remain required before release.

## 2026-08-30 postmatch survey staged flow production release

- Production Core received the staged postmatch save flow and formal-batch
  next-match survey gate; Web received the matching standalone build.
- Preflight: `jx-core`, `jx-jobs`, `jx-web`, and `jx-livekit` active; Core
  `/health/live` and `/health/ready` passed; no migration required.
- Browser smoke: unauthenticated `/me/postmatch-surveys` rendered the login
  boundary (200, not 404); `/me/ai-experience` also returned 200.
- Rollback copies: `/opt/jixia-backup-before-spec235-20260830002854` and
  `/opt/jixia-web-before-spec235-20260830002854`.
- Remaining acceptance: authenticated end-to-end questionnaire completion and
  a real scheduled formal pair should be exercised before the next experiment;
  no live match was interrupted during release.

## 2026-08-30 separate overall postmatch survey link

- Removed the overall experience form from the speech review page and added
  `/me/postmatch-surveys/overall/[taskId]` as a task-bound standalone form.
- Speech review now presents only the transcript annotation workflow and links
  to the overall form until the server confirms its completion.
- Verification: Web production build includes the new route; postmatch tests
  2/2 and TypeScript passed. Web standalone was synced and `jx-web` restarted;
  public survey routes returned 200.

## 2026-08-30 postmatch overall route final sync

- Final release verification initially caught the newly added dynamic overall
  route missing from the deployed standalone bundle (404). Rebuilt with the
  repository `pnpm build` script and synced the complete standalone/static/public
  output before restarting Web.
- Final online checks: `jx-web` active; `/me/postmatch-surveys`,
  `/me/postmatch-surveys/overall/test`, and `/me/ai-experience` all returned
  200; unauthenticated browser snapshots showed the login boundary.
- Final rollback copy: `/opt/jixia-web-before-spec235-final-20260830005632`.

## 2026-08-30 separate experience survey production release and history reset

- Spec 236 released: single-match postmatch tasks now contain only speech
  annotations and remain the required next-match gate; AI debate experience
  remains an independent account survey at `/me/ai-experience`.
- Removed the obsolete task-level overall form; its legacy route redirects to
  the independent experience page. Existing legacy `overall` answers remain
  readable.
- With explicit authorization, production history was cleared after a custom
  PostgreSQL dump: matches, rooms, match-linked data, experiment schedules and
  tasks, audit logs, leaderboard snapshots, and related runtime/export records.
  Users (21), rules (14), questionnaire versions, topics, models, and voices
  were preserved. Post-restart system logs are new runtime entries only.
- Final online checks: all four services active, Core ready passed;
  `/me/postmatch-surveys` 200, `/me/ai-experience` 200, legacy overall route
  307 redirect; postmatch/experiment tests 8 passed, 1 skipped and Web
  postmatch tests 2 passed with TypeScript/build verification.
- Database backup: `/opt/jixia-backup-spec236-20260830014055.dump`.
- Rollback copy: `/opt/jixia-web-before-debate-brand-20260829215000`.

## 2026-08-30 postmatch transcript review and finish prompt release

- Released Spec 237. A participant's postmatch task now includes every finalized
  speech from both sides with stage, side, seat, speaker, sequence, and timing
  metadata. Only the participant's own speeches and same-side Agent speeches
  are annotatable.
- Core owns the hindsight boundary: prior context is visible, the current speech
  unlocks only after its saved `q1`, and future speech text stays hidden. Drafts
  cannot answer future speeches, submit `q1` and `q2` in one first request, or
  overwrite an answer that already unlocked text.
- Rebuilt `/me/postmatch-surveys` as a phase-grouped transcript review. The current
  phase and adjacent context open by default; affirmative/negative, human/AI,
  seat, order, time, progress, future locks, save failures, and the separate
  `/me/ai-experience` link are explicit. Desktop and 390px screenshots passed;
  mobile width was exactly 390/390 with no horizontal overflow.
- The live match page now queries only the signed-in user's task status after
  `FINISHED`, shows a dismissible questionnaire prompt, and retains a visible
  pending-task entry. Changing `match_id` remounts the live page so dismissal
  state cannot leak into the next match. The existing Core next-match gate remains
  authoritative.
- Verification: Core/Jobs non-integration suites passed 372/25; targeted Core
  passed 10 with one database integration item skipped because local Docker and
  `TEST_DATABASE_URL` were unavailable; targeted Web passed 18. Full typecheck,
  OpenAPI contract check, Web lint, and production build passed. The full Web
  suite still has two unrelated stale homepage-copy expectations, and whole-repo
  Ruff still reports three unrelated long lines outside this change.
- Production: all four services active; Core live/ready passed; Alembic is
  `0042_agent_decision_reason`; active matches remained zero; public survey pages
  returned 200 and unauthenticated survey APIs returned 401. Core hashes and Web
  build ID matched local output, and no post-switch warning or automatic restart
  was observed.
- Rollback: database `/opt/jixia-backup-spec237-20260830031347.dump`, source
  `/opt/jixia-debate-before-spec237-20260830031347`, Web
  `/opt/jixia-web-before-spec237-20260830031347` (plus the first Spec 237 Web
  bundle at `/opt/jixia-web-spec237-first-20260830031347`).
# 2026-08-30 Spec 238

- 赛后问卷改为单场任务工作区：列表 `/me/postmatch-surveys`，详情 `/me/postmatch-surveys/[taskId]`。
- Core 新增用户隔离的单任务读取接口；提交任务可重新打开编辑，旧答案快照写入审计详情。
- 详情页桌面双栏（左侧阶段化记录、右侧问题面板），移动端单列；独立 AI 体验问卷保持分离。
- 验证：Core 非集成 371 通过，Web typecheck/lint、契约检查、生产构建通过；专用列表测试通过。全量 Web 仍有两条既有首页文案断言失败。
# 2026-08-30 Spec 239

- LLM-facing complete debate history is serialized as grouped JSON (`stage` + `message`) by `experiments.prompts.render_history`.
- Agent custom/experiment prompts, runtime context, and post-match judge snapshots use the serializer; empty content is `""`.
- Prompt version: `paper-v2.1-json-history-2026-08-30`.
- Verification: 19 targeted Core tests, Pyright and Ruff passed.

# 2026-08-30 Spec 240

- Confirmed that remote human microphone audio is already delivered through
  LiveKit while Core ASR subscribes independently.
- Added local match audio controls that retain global output mute and can mute
  all or selected remote humans without muting Agent or host audio.
- Human tracks are classified from Core-minted `jx-human` identities against
  room seats; reconnect identities inherit the user-level setting. Choices are
  stored per match in `sessionStorage`.
- Verification: 22 targeted unit tests, 20 debate Story tests, 12 match
  Playwright cases, TypeScript, ESLint, production build, and a 390px browser
  overflow/screenshot check passed. Full Web unit tests remain 196/198 because
  two unrelated homepage tests still assert obsolete hero copy.
- Remaining risk: a physical two-human LiveKit microphone run is still needed
  to validate same-room acoustics; no Core, ASR, API, contract, or database
  behavior changed.

## 2026-08-30 Spec 240 production release

- Released the complete Web standalone build `IAAoYiEL2x-YLldsl3aE8`; no Core,
  Jobs, LiveKit, API, migration, or database change was required.
- Read-only production preflight passed with all four services active, Core
  live/ready 200, migration `0042_agent_decision_reason`, and zero non-terminal
  matches. Production source hashes for the audio policy, LiveKit page, and
  debate layout matched local files.
- Public home, debate, postmatch survey, and AI experience routes returned 200;
  the deployed SSR bundle contains the new human-audio controls.
- Eleven 30-second samples over five minutes kept all services active, Core
  live/ready and public Web at 200, `jx-web NRestarts=0`, and no warning-level
  Web logs after the new process started.
- Rollback Web: `/opt/jixia-web-before-spec240-20260830185617`. Affected source
  backup: `/opt/jixia-source-before-spec240-20260830185617`.
- Physical two-human microphone/acoustic validation remains an explicit field
  acceptance item and was not represented as passed by browser simulation.

## 2026-08-31 Spec 241 local implementation

- Postmatch annotation now targets only finalized `FREE_DEBATE` speeches from
  the participant and same-side Agents. Non-free-debate speeches remain visible
  context but do not count toward progress or submission.
- Core persists per-speech draft/confirmation state with a revision fence,
  hides future speech metadata and retained out-of-scope answer keys from the
  participant projection, supports idempotent saves, and keeps submitted-task
  edits auditable with the prior answer snapshot.
- The single-task workspace is an always-expanded affirmative/negative chat
  transcript with a separate question panel. Single choices auto-save, human
  multi-choice drafts save immediately and advance only on confirmation, AI
  speech text unlocks after Q1, completed bubbles are editable, and final task
  submission remains explicit.
- Verification passed: Core non-integration `375 passed` (survey targeted
  `13 passed`), Web `200 passed`, Storybook `92 passed`, Web ESLint and
  TypeScript, OpenAPI contract check, Storybook static build, and repository
  production build. Desktop and fresh-load 390px screenshots had no horizontal
  overflow; axe reported zero violations in both viewports.
- Independent PostgreSQL integration was not run because this workstation has
  no `TEST_DATABASE_URL`, local PostgreSQL client, or Docker daemon. The browser
  evidence used a fictional Storybook task, so authenticated real-task save,
  refresh, edit, final submit, and next-match gate remain release checks.
- Spec 241 is implemented but not deployed to production.

## 2026-08-31 Spec 241 production release

- Production preflight passed before release: `jx-core`, `jx-jobs`, `jx-web`,
  and `jx-livekit` active; Core live/ready 200; Alembic head
  `0042_agent_decision_reason`; non-terminal matches `0`.
- Created rollback backup `/opt/jixia-backup-before-spec241-20260830163629`.
  Its source and Web copies are present and `pg_restore --list` validated the
  PostgreSQL custom dump. No migration was required.
- Synced the Core survey service/routes and the complete Web standalone/static/
  public output from the same local production build, then restarted in the
  required order `jx-core -> jx-jobs -> jx-web`. Local and remote SHA-256
  hashes matched for both survey Core modules, Web `server.js`, and `BUILD_ID`.
- Online checks passed: public home, `/me/postmatch-surveys`, and dynamic task
  route returned 200; unauthenticated survey list/detail APIs returned 401 and
  preserved login return paths; all four services remained active.
- Five-minute post-release observation passed 11/11 samples: live/ready 200,
  all service restart counters `0`, and no warning-or-higher Core/Jobs/Web
  logs. Spec 241 is released to production.
- Release limitation remains explicit: no authenticated production task was
  created or modified. Real participant completion, revision conflict, and
  next-match gate remain field acceptance checks; the local PostgreSQL
  integration suite was unavailable because no independent `TEST_DATABASE_URL`
  or Docker daemon exists on the workstation.

## 2026-08-31 Spec 242 guide workflow redesign

- Rebuilt `/guide` around the current v2.0/v2.1 participant workflow with
  debater-first role tabs, standard 4v4 timing, operation accordions, pause/
  audio guidance, separate postmatch and AI-experience survey links, and a
  desktop quick-reference rail.
- Corrected obsolete copy: affirmative starts free debate, fixed speeches are
  90 seconds in the current paper rule, and free debate is six minutes per
  side with a 30-second single speech limit.
- Verification: Web typecheck, ESLint, full Web tests (200), Storybook build,
  repository production build, desktop screenshot, mobile 390px screenshot
  and role/accordion browser checks passed. Mobile `scrollWidth` equals the
  390px viewport. Not deployed.
# 2026-09-01

- Fixed Web match presentation: `HUMAN_READY_TO_START` now says "轮到你发言了" only when the
  authoritative `current_speaker_user_id` matches the logged-in user; other viewers see the
  current side/seat instead. Empty IDs no longer count as a match. Web tests: 201 passed.

- Implemented Spec 243 locally: browser-scoped, server-validated device check credentials.
- Added `browser_device_checks` migration/model, 24-hour TTL, HttpOnly cookie issuance,
  cross-room reuse endpoint, and current-browser-only invalidation.
- Web room preparation automatically attempts reuse before microphone probing; normal
  room-level `DeviceCheck` and `ready()` remain authoritative.
- Checks passed: Web tests 200/200, Core room tests 16/16, Ruff, Pyright, TypeScript,
  OpenAPI contract check, Python compile and `git diff --check`.
- Production deployment and PostgreSQL integration tests remain pending.

- Spec 243 released 2026-09-01 after active-match gate cleared. Backup:
  `/opt/jixia-backup-before-243-20260901014827`; database migrated to
  `0043_browser_device_checks`; Core/Jobs/Web/LiveKit restarted in order.
  Public `/`, `/guide`, and `/me/postmatch-surveys` returned 200; all services active,
  restart counts zero, and Core/Jobs had no warning-or-higher logs during the observation window.

- Spec 244 implemented locally: debate candidate cards now use the server's unified
  `team_hand_queue` rank for both humans and Agents. Humans show `第 N 名` (selected adds
  `将发言`); Agents without a rank retain `决策中`/`AI 已申请，排序中`/`跳过` state labels.
  Web tests 201 passed; production release pending.

- Spec 244 production release completed 2026-09-01 after zero-active-match preflight.
  Web standalone was deployed from the local production build with its original
  `apps/web`/`node_modules` layout. An initial path-only sync briefly caused Web 502
  due to broken pnpm links; the service was restored from backup and then replaced
  using the complete standalone archive. Final checks: all four services active,
  Core live/ready 200, public `/`, `/guide`, and `/debate` 200, restart counts zero,
  and no Core/Jobs warning-or-higher logs in the final window.

- Spec 245 prepared 2026-09-01: enabled model resources can be switched idempotently to
  `deepseek-v4-flash-0731` with `scripts/ops/switch_llm_model.py`; historical frozen match
  snapshots and TTS/ASR remain unchanged. Core catalog/health tests (18) and Ruff passed.
  Production database write was not executed because no protected production session was
  available in the local workspace; run dry-run, provider probe, then explicit `--apply` on
  the server before restarting Core/Jobs.

- Spec 245 production release completed 2026-09-01: backed up `/opt/jixia-debate` (443MB) and
  PostgreSQL custom dump (27MB, `pg_restore --list` verified), switched the sole enabled model
  resource from `qwen3.7-plus` to `deepseek-v4-flash-0731` (90 Agent references), and restarted
  `jx-core`, `jx-jobs`, and `jx-web`. Production preflight passed at migration `0043_browser_device_checks`,
  active matches 0; Core health and public `/`, `/guide`, `/login` returned 200. Full provider probe
  was attempted but timed out waiting for ASR readiness; this remains an external voice-chain gap,
  not a model-switch success claim.

- Spec 245 follow-up: production probes showed the current Alibaba Bailian MaaS endpoint/key
  returns HTTP 403 for `deepseek-v4-flash-0731` and `deepseek-v4-flash`, while `qwen3.7-plus`
  returns 200. Standard DashScope endpoints returned 403/401 as well. DeepSeek was rolled back;
  model resource and experiment error text now use the verified Qwen model, and Core restarted
  healthy. DeepSeek requires provider-side model authorization or a dedicated endpoint/key.

- Spec 245 final production state: a newly supplied Alibaba Bailian workspace key was tested
  without logging or repository persistence. It returned 200 for `deepseek-v4-flash-0731` on
  both the existing Beijing MaaS endpoint and the standard domestic compatible endpoint. The
  encrypted model credential, resource name/config ref/model ID were rotated atomically; the
  temporary plaintext file was removed. The exact Core SSE client passed with first token 1038ms
  and completion 1068ms. All services and health checks passed with zero active matches.

## 2026-09-01 Spec 246 Web static artifact integrity

- Production `/debate` was failing because its HTML referenced missing chunk
  `2lro1mez7c89w.js`; browser network verification showed the chunk returned 404.
- Root cause was a mixed Web standalone deployment: HTML/server output and `.next/static`
  were not from the same complete build. Core, DeepSeek LLM, TTS and LiveKit were not the
  direct cause of the page-load failure.
- Added a build guard that rejects any HTML reference to a missing JS/CSS artifact, plus a
  regression test. Local build completed successfully and the targeted tooling suite passed
  13/13.
- Uploaded a complete standalone archive from the same build, preserved the prior Web directory
  at `/opt/jixia-web-before-246-20260901152652`, atomically switched `/opt/jixia-web`, and
  restarted only `jx-web`.
- Production verification: `jx-web` active with `NRestarts=0`; local `/debate` and `/` 200;
  the formerly missing chunk 200; fresh public browser session rendered `/debate` and had no
  failed requests except expected unauthenticated `/api/auth/me` 401.
- No Core/Jobs/database changes were made. DeepSeek remains enabled. Existing historical
  paused matches were not modified.

## 2026-09-02 Spec 247 match operations recovery and cleanup

- Added global administrator virtual-spectator access without room-capacity, seat, or audio-publish changes. HTTP snapshots, commands, LiveKit token reads, host audio, and WebSocket entry now share the administrator membership boundary; match-page commands grant administrators privileged control.
- Added on-demand MatchActor restoration and administrator controls for resume, recovery, speech reset, and forced termination. The workbench now exposes persisted action/error state, active task and connection-lease counts, and safe controls.
- Unified match bulk deletion with the single-match runtime/file cleanup path. Match selection supports current-page and current-filter cross-page selection; requests are submitted in bounded 500-item batches and return partial-failure counts.
- Verification passed: Core administration/match/WebSocket/recovery tests 113; model/runtime tests 56; Web tests 201 plus the 502-item batching regression; Ruff, ESLint, TypeScript, OpenAPI contracts, `git diff --check`, and production build.
- Production release stopped Core/Jobs/Web before destructive work. Per explicit user choice, no new database backup was created. The cleanup removed 16 matches, 20 rooms, and 288 match/export files, leaving matches, rooms, leases, and active match tasks at zero. It preserved 43 users, one model, 12 voices, 10 topics, and 73 audit rows.
- Core, Jobs, and Web are active with zero restarts; Core ready and public `/debate` return 200. Fresh browser verification rendered the login boundary and loaded current static assets. Independent LLM and judge probes passed; the full provider-chain probe still had one ASR readiness timeout, so the remaining external ASR risk is not reported as resolved.
- Final gate cleanup replaced the workbench native alert with the shared toast, replaced the postmatch unsaved-selection native confirm with `ConfirmDialog`, moved the guide viewport baseline to the route root, and synchronized the ORM metadata whitelist with the already deployed `browser_device_checks` table. Root `pnpm test` passed Tooling 50, QA 6, Web 202, Core 375, and Jobs 25; lint, Pyright/TypeScript, contracts, build, and `git diff --check` passed.
- Production browser review found and closed one remaining acceptance gap: the administrator match filter now exposes all eight lifecycle states and readable badges. The final complete Web standalone was atomically released with rollback `/opt/jixia-web-before-247-filters-20260902023612`; administrator browser verification showed zero matches, cross-filter select-all zero, and all eight state options. Core/Jobs/Web are active with zero restarts, Core ready and local/public Web return 200, and no warning-or-higher logs appeared after the new Web instance completed startup.

# 2026-09-02 真人三场完整验收收束

- 第三场 `f39dc1de-3caf-45b5-86ae-b6c779732479` 已由两名隔离浏览器真人玩家自然完成并进入 `FINISHED`。实际覆盖等待房间重连、暂停/恢复、固定发言、自由辩论举手/获权/开麦/提前结束、刷新重连和赛后标注。
- 三场比赛最终状态均为 `FINISHED`：`89cc7b84-30e7-468c-ac7c-6ed1eadac012`、`ed17eb2e-9267-48e5-92b0-07dc1adebf85`、`f39dc1de-3caf-45b5-86ae-b6c779732479`。另一个历史遗留 `PAUSED` 比赛已通过管理员强制终止控制链路转为 `TERMINATED`，未直接 SQL 改状态。
- 三场双方赛后问卷均为已提交：甲方 `20/20`、`18/18`、`20/20`；乙方 `18/18`、`20/20`、`18/18`。浏览器问卷列表和提交后的只读页面均已核验。
- 最终 `ssh baidu-181 'cd /opt/jixia-debate && bash scripts/ops/preflight_v2_1.sh'` 全部通过：`jx-core`、`jx-jobs`、`jx-web`、`jx-livekit` active，Core live/ready 200，迁移 `0043_browser_device_checks`，`active_matches=0`。
- 本轮自动化会话曾被浏览器守护进程回收；通过认证保管库恢复后继续，未将密码内容输出。临时密码文件已精确 unlink；未读取或写入 `api.md` 凭据、`.env`、Cookie、Authorization 或供应商原始响应。

## 2026-09-02 Spec 249 真人 ASR 终结竞态

- 根因之一是竞赛制自由辩论中 `speech.finish` 已进入 `SPEECH_FINALIZING` 后，ASR provider
  关闭错误仍会走通用失败重试并提交 `speech.reset`；这会清空当前 Speech 并让同一真人重新获权，
  同时使随后到达的缓冲 final 变成迟到回调。
- 该竞态现在保留失败 Speech 和错误码，但不在已结束的自由辩论发言上重置 MatchActor；固定发言
  的既有一次重试/第二次暂停语义不变。
- `system.recover` 现在把竞赛制 `FREE_SELECTING` 视为选择窗口，清除残留 speaker/speech 字段并
  原样恢复选择状态，避免重启后复活上一位真人。
- 定向 Core 测试 108 个及 Ruff 通过。生产仅同步两个 Core 源文件并重启；live/ready 通过，
  `NRestarts=0`，启动后无错误级日志。未直接修改比赛数据库；真人现场链路仍待复验。

## 2026-09-02 Spec 250 真人 ASR 失败收敛（本地）

- 修复 nullable/非法 UUID 回调解析，避免 `"None"` 或 malformed UUID 逃逸为未捕获 `ValueError`。
- MatchActor 内部及离线定时任务现在捕获未知异常并提交脱敏 `system.error`，收敛到
  `ERROR/RECOVERY_REQUIRED`，不再产生无人处理的 Task exception。
- 新增 ASR 低基数错误分类；配置/协议错误立即进入恢复，音频/瞬态错误保留失败 Speech 并执行
  有界重试；自由辩论 finalizing 竞态仍禁止静默 reset。
- Core 全量测试 `378 passed, 36 skipped`，定向新增回归与 ASR 测试通过，Ruff/Pyright 通过。
- 已在只读预检确认 `active_matches=0` 后，仅同步两个 Core 源文件并重启 `jx-core`；live/ready 200，
  `NRestarts=0`，启动后无 warning/error 日志，未修改生产数据库。
- 复现验证：非法 UUID、内部定时异常、自由辩论 finalizing 后迟到 ASR failure 三个场景连续各运行
  10 轮（30/30 通过）；Core 全量仍为 `380 passed, 36 skipped`。线上 `/debate` 可访问，但当前
  浏览器会话未认证，未创建线上比赛；生产预检仍为 `active_matches=0`、无 warning 日志。

## 2026-09-02 Spec 251 五真人可靠性模拟器

- 新增 `scripts/qa/five_human_match_simulator.py`，以五个稳定合成用户驱动真实 MatchActor 的
  start/finish/finalized 命令，并注入迟到 final、非法 UUID 和内部定时提交异常。
- 100 轮压力运行完成 500 次真人发言，拒绝 400 个旧 final；所有比赛自然 FINISHED，非法 UUID
  后 Actor 可响应，定时异常均收敛到 RECOVERY_REQUIRED。工具不连接数据库、LiveKit 或 provider。
- 新增自动回归 2 项并通过；Ruff、Pyright、`git diff --check` 通过。浏览器/合成媒体五会话模式
  尚未实现，不能据此宣称五台真实设备或外部 ASR 压力通过。

## 2026-09-04 Spec 252 Web 中英界面切换（本地进行中）

- 添加 `next-intl` 的 `zh-CN` / `en` 类型化消息目录和浏览器本地偏好。语言切换维持原 URL，
  不读取服务端状态、不重连实时服务、不影响比赛计时；首次按浏览器语言选择，默认中文。
- 全局头部、紧凑比赛头部、后台壳层、认证导航与表单、Toast/确认框、比赛关键状态、公开大厅和
  排行榜已使用本地化消息。姓名、辩题、文字记录、规则、Prompt、模型/音色及诊断仍按原文展示。
- 本地验证：Web TypeScript、目标 ESLint 与完整 Vitest 均通过（51 files / 205 tests）。受 pnpm
  当前忽略原生构建脚本的环境策略影响，未在本轮运行完整 `pnpm build` / 浏览器门禁；其余低频
  资源、实验和赛后工作台尚未完成逐页迁移，规格保持 `implementing`，未发布。

- 后续增量已使用仓库 `scripts/build-web.mjs` 完成完整 standalone 构建并发布 Web-only 产物；线上
  非终态比赛为 0，Core/Jobs/LiveKit 未改动。公开浏览器确认英文首页首屏、导航、认证入口、能力卡、
  空状态、流程与排行榜壳层即时切换，URL 不变。最终 `jx-web` active、`NRestarts=0`，`/` 与
  `/debate` 均 200。首次最终 staging 曾在 rsync 尚未写完全部依赖时启动并自动恢复；随后确认模块到位、
  手动重启并清除失败计数。低频页面仍待迁移，不报告为全站完成。

## 2026-09-05 Spec 253 辩论页国际化优先切片（本地）

- 实时辩论页的状态、队伍面板、文字记录/抽屉、音频控制、网络诊断、恢复提示、确认弹窗、进入失败与赛后问卷提示均已迁移到类型化 `Debate` / `Match` 消息。
- 辩题、规则和阶段名称、姓名、实时文字记录、以及服务端返回的恢复原因继续原样展示；不会被机器翻译。
- WebSocket 与 LiveKit 音频会话的 effect 不依赖 locale：语言切换只重绘 UI，不会主动重连比赛实时服务。
- 本地验证通过：TypeScript、目标 ESLint、辩论相关 Vitest 39 项，以及 `CORE_API_ORIGIN=http://127.0.0.1:8100 node scripts/build-web.mjs` 生产构建。尚未对本切片进行线上发布或已登录比赛页浏览器验收。

## 2026-09-05 Spec 253 真人模拟与 Web 发布

- 五真人内存 MatchActor 模拟完成 10 轮：50 次发言完成、40 个迟到回调拒绝，非法 UUID 与内部定时器异常均安全收敛；Core 定向回归 `96 passed`，Web 辩论相关回归 `39 passed`。该结果不替代五台真实设备、LiveKit 媒体或真实 ASR 压力验收。
- 使用完整 standalone 产物发布 Web-only 国际化切片；未修改 Core、Jobs、LiveKit、数据库或比赛状态。旧 Web 运行目录保留为 `/opt/jixia-web-before-253-20260905`。
- 发布后 `jx-web` active 且 `NRestarts=0`；公网 `/`、`/debate` 与 Core live/ready 均为 200；本次启动窗口内未出现新的 jx-web error-level 日志。

## 2026-09-05 Spec 253 英文正式赛制阶段名

- 英文模式下，正式 4v4 标准阶段按人工审校术语显示；例如 `正方一辩立论` 显示为 `First Affirmative Constructive`，`自由辩论` 显示为 `Free Debate`。当前阶段、文字记录和复制的文字记录使用同一映射。
- 这是纯 Web 展示映射，不改数据库中的阶段名、主持词、Prompt 或 MatchActor 状态。未注册的自定义阶段保持原语言，避免未经审核的自动翻译。
- 本地 TypeScript、目标 ESLint、41 项辩论相关 Vitest、生产构建和差异检查通过。Web-only 原子发布后，`jx-web` active 且 `NRestarts=0`，公网 `/debate` 与 Core ready 均返回 200；回滚目录为 `/opt/jixia-web-before-253e-20260905`。

## 2026-09-06 线上 Core 假活与登录恢复

- 线上复现：公网页面 HTML 正常，但 `/api/auth/me`、条款和登录请求均为 OpenResty `502`；服务器 `jx-core` 进程显示 active、监听 8100，却对本机请求返回 empty reply，近两小时无新请求日志。判断为 Core 长时间运行后的事件循环/进程假活，不是账号凭据或 Web 登录表单问题。
- 受控重启 `jx-core` 后恢复：Core `/health/live`、`/health/ready` 为 200，未认证 `/api/auth/me` 与公网同源接口按预期返回 401，登录页 200。独立浏览器提交合成无效账号，实际 `POST /api/auth/login` 返回 401，确认登录链路已恢复且非网络失败。
- `jx-core`、`jx-web`、`jx-jobs`、`jx-livekit` 均 active，Core/Web 重启计数为 0。未读取或修改 `.env`、数据库、Cookie、Authorization 或任何密钥。后续可追加 Core 健康探针/服务级 watchdog，自动处理“active 但无响应”状态。

## 2026-09-06 Spec 255 Speech 控制栏协调性（本地）

- 将英文 `Reset abnormal speech` 改为 `Restart speech`，中文 `异常重置` 改为
  `重新开始发言`，确认框同步使用“重新开始”语义；底层 `speech.reset` 命令和权限不变。
- 移除 Audio/Speech/Match 冗余微型标题，仅压缩同时出现的结束/重新开始按钮间距。
  Storybook 英文界面在 1280×720、1440×900、1920×1080 下三组控制区均为 46px 高，
  两个 Speech 按钮保持同排。
- TypeScript、目标 ESLint、40 项 Vitest、新增英文 Storybook interaction 和生产 Web 构建通过。
  Web 全量单测 205 项通过、2 项失败，均为既有 `use-match-command` 错误文案预期未随当前
  国际化实现更新；同文件定向测试不属于本切片。
- 两次生产只读 preflight 均因 `active_matches=2` 停止，聚合状态为 `RUNNING=1 PAUSED=1`。
  未同步生产文件、未重启服务、未修改比赛或数据库；待非终态比赛清零后发布 Web-only 产物。
- 用户再次要求上线后复查，活动比赛已降为 `active_matches=1`，仅剩 `PAUSED=1`；发布门禁再次
  无副作用停止。该暂停比赛需由有权限的管理员恢复完成或明确终止，发布流程不会代为更改比赛状态。
- 暂停比赛处理完成后，最终生产 preflight 以 `active_matches=0` 通过。完整 Web standalone
  已原子切换，回滚目录为 `/opt/jixia-web-before-255-20260906182055`；未修改 Core、Jobs、
  LiveKit、数据库或 migration。`server.js` 与 `BUILD_ID` 的本地/远端 SHA-256 一致。
- 公网 `/debate` 在独立浏览器正常渲染登录边界，HTML、JS、CSS、图片和 RSC 请求均成功；唯一
  401 为预期的未登录 `/api/auth/me`。线上构建块包含 `Restart speech`，不含旧文案。
- 五分钟 11/11 采样中 Core live/ready 与 Web `/debate` 始终为 200，`jx-web` active、
  `NRestarts=0`，新进程启动后无 warning-or-higher 日志；最终 preflight 再次通过。

## 2026-09-06 线上论文截图测试赛

- 通过公网界面创建 6 个英文显示名测试用户，与两侧各 1 个真实 Agent 组成正式 4v4。房间名为 `test room`，房间号为 `951518`，辩题为“过程还是结果更能体现奋斗的价值”。
- 六个独立浏览器会话经 LiveKit 设备探测和界面准备流程进入比赛；未直接修改数据库或跳过 MatchActor 状态。自由辩论中已实际触发 Agent 发言、人类与同队 Agent 并发举手、人类优先选中。
- 已在 `docs/paper/figures/` 保存 4 张 1672×941 PNG 候选图；其中 01/03 为对方 Agent 正在发言时同队人类与 Agent 同时举手，02/04 补充展示候选队列与人类优先选中。测试赛最后通过房主界面暂停，便于后续回看或恢复。

## 2026-09-08 Spec 256 管理、标注与赛后问卷国际化发布

- 管理后台、实验管理工作区、参与者/专家标注页、单场赛后问卷与 AI 辩论体验问卷的固定 UI
  已完成中英文呈现；业务数据、辩题、姓名、文字记录、Prompt 和模型资源保持原文。
- 英文品牌为 `JX-Debate`，副标题为 `Multi-person, multi-agent live voice debate platform`；
  中文保留“稷下·争鸣”和“多人多智能体实时语音交互平台”。
- 本地通过 TypeScript、目标 ESLint、差异检查，以及管理/问卷/i18n 定向 Vitest
  `21 files / 45 tests`；完整 standalone 构建生成 37 条路由。
- 生产发布前预检发现 1 场非终态比赛。按用户明确授权，使用运行中 Core 的管理员终止接口
  经 MatchActor 终止为 `TERMINATED`；一次性管理员会话已撤销，未直接 SQL 修改比赛状态。
  复检后 migration 为 `0043_browser_device_checks`、`active_matches=0`，预检全部通过。
- 完整 Web standalone 原子发布，只重启 `jx-web`，回滚目录为
  `/opt/jixia-web-before-256-i18n-20260908020256`。Core live/ready 和线上关键路由均为 200，
  四项服务 active；独立浏览器确认英文首页品牌、导航及 admin/标注/问卷未登录边界。
  启动后五分钟观察没有新的 Web warning-or-higher 日志。
- 风险：本次生产浏览器未使用管理员或实验参与者凭据，已认证数据页依赖本地组件测试、类型检查
  和生产构建验证；未读取或修改生产用户问卷、标注或其他业务数据。
# 2026-09-10：论文对齐赛后问卷（Spec 257）

- 依据 `sigconf-authordraft.tex` 附录，将整体赛后体验问卷更新为五个独立构念：协调感、赛况理解、团队帮助、额外负担、继续组队意愿；五题统一使用 1–5 同意度选项。
- 新版本为 `postmatch-v3-paper-2026-09-10`。保留 `human_self`/`team_ai` 事件级发言标注与历史问卷版本，不修改 migration 或历史答案。
- Core `test_surveys.py` 13 项通过，Ruff 通过；Web TypeScript 检查和完整 standalone 构建（37 路由）通过。
- Web-only 原子发布完成，回滚目录：`/opt/jixia-web-before-257-questionnaire-20260910130510`。公网 `/me/ai-experience`、`/debate` 返回 200；Core live/ready 和 `jx-core`、`jx-web`、`jx-jobs`、`jx-livekit` 均 active，`jx-web` 当前重启次数为 0。
- Core 问卷定义随后在只读 preflight（`active_matches=0`）通过后同步，远端哈希与本地一致；仅重启 `jx-core`。Core live/ready、四服务状态和公网问卷/辩论页面复验通过。Core 回滚目录：`/opt/jixia-debate-before-257-questionnaire-20260910130619`。
