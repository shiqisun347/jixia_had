# Spec 262：全面浏览器测试

状态：匿名线上、认证只读巡检、本地浏览器/Storybook/tooling 门禁、Web-only 修复发布和隔离生产 4v4 完整媒体/赛后切片已完成；真实 provider 负载探针已通过，paper-experiment-4v4 尚无生产排期，线上 TTS 历史事故、管理员写入和五场真实比赛并发仍未关闭（2026-10-03）

## 范围与验收

- 安装并实际使用 Chrome CDP 和 Playwright CLI；补充 agent-browser 的探索测试流程。
- 线上逐一检查所有静态入口、历史入口、动态路由错误边界、身份边界、资源加载、控制台、语言切换、键盘、桌面与窄视口、表单校验和网络错误恢复。
- 使用仓库标准 Playwright、Storybook、Web/Core/Jobs 单测及独立测试库覆盖登录、注册、建房、席位、设备准备、比赛控制、恢复、管理与历史赛后流程。
- 生产已登录流程必须使用授权测试会话；生产新房间、比赛与资料写入需得到测试范围授权，禁止对真实比赛做终止/删除/状态修改。用户已授权独立 QA 数据，本轮仅使用合成账号和隔离房间，不触碰现有真实比赛。
- 区分真实服务、Mock、合成音频与真人证据；不能把匿名边界或 Mock 当成已登录线上功能通过，不能把有限检查当成全面通过。
- 报告保留完整数量、失败、复现证据与未执行项；QA-001 最小 Web 文案修复已完成 Web-only 发布，
  保留即时回滚副本。QA-002 指南按钮对比度修复已随当前 Web-only 构建发布，Storybook 验收通过。

## 当前证据

- `chrome-cdp`、`@playwright/cli` 和 `agent-browser` 均已安装并实际运行；证据与截图在
  `artifacts/qa-20261002/`。
- 线上 16 个入口、认证边界、资源、控制台、语言切换、无效登录、注册空表单、排行榜筛选、
  503 恢复和 390×844 窄视口已检查；公开路由网络无失败请求。
- Web Vitest 52/210、Core 非集成 392、Jobs 非集成 25、contracts 和 diff check 通过；仓库标准
  Playwright 已固定 `zh-CN`，四个目标桌面视口 232 passed、12 skipped。跳过项为已停用的历史
  实验创建/编辑场景，原测试保留并明确标记，当前重定向场景已验收。
- Storybook 11 文件、94/94 通过；设备复用请求已显式 Mock，无对应 MSW 警告，详见
  `artifacts/qa-20261002/results/storybook-final-clean.log`。
- 将仓库 Playwright 与认证配置固定为 `zh-CN`，并按当前发布行为同步历史实验入口、首页、头像、网络
  状态和房间错误 Toast 断言；在隔离 PostgreSQL + Core（head migration）下四个目标桌面视口标准
  Playwright 232 passed、12 skipped，日志为 `artifacts/qa-20261002/results/browser-standard-final-green.log`。
- Storybook 11 文件、94/94 通过；tooling 50/50、Web Vitest 210/210、contracts 通过。
- `pnpm lint`、`pnpm typecheck`、`pnpm format:check` 仍分别有既有 Ruff/Pyright/Prettier 失败，
  完整日志保存在 `artifacts/qa-20261002/results/`。
- 独立 PostgreSQL 下重新运行当前 v2.0 集成切片：Core 正式 4v4 房间服务 1 项、正式 4v4 持久化
  5 项和 Core PostgreSQL/租约 5 项共 11/11 通过；Jobs PostgreSQL 2/2 通过。正式房间集成测试
  复用 migration 预置的唯一 admin，避免旧夹具重复创建管理员。
- QA-001 已修复：排行榜 503 状态的重试按钮现在显示“重新加载”/“Reload”；本地定向 Playwright
  4/4 通过，生产 Mock 复验通过。完整证据见 `artifacts/qa-20261002/report.md`。
- QA-003 已发布：Web BUILD_ID 为 `zYyxSOk-xY_gDAzgebcSF`，远端 `server.js` SHA-256
  `53031e2acd8b88246d59443af27e9e382ff042d3bf6b2c0d7a33f250d41e3909` 与本地 standalone 构建一致。
  发布前 Web 已复制到 `/opt/jixia-web-before-qa003-20261003T0910`，未触碰 Core、Jobs、LiveKit、数据库
  或 migration。发布后四服务均 active，Core live/ready 均 200。

## 生产 QA 续测（用户确认平台条款后）

- 已创建两个独立合成 QA 账号并完成自动登录；注册时勾选并提交“我已阅读并同意 平台条款”，未记录用户名密码。
- 真实 Chrome 账号创建隔离正式 4v4 房间：UUID `aeed7572-506c-441f-b7dd-c1f9ef975ba7`，房间号 `474569`，标题为 `QA Chrome 全链路 20261003 1159`，辩题为“过程还是结果更能体现奋斗的价值”。账号选择正方 1 辩后，真实 Chrome 设备检测完成，页面显示“设备正常，已自动完成准备”；网络提示为“网络稍弱，可以确认后继续”，确认后席位状态为“已准备”，开始比赛按钮可见。
- 启动正式 4v4 时页面给出明确产品门禁提示：“正式 4v4 每方必须安排 2 名真人辩手和 2 名 Agent”。单个合成账号不能启动该房间；未通过创建第二场正式房间绕过 owner conflict，也未关闭该隔离房间。
- Playwright CLI 的生产建房尝试保留了 API 证据：已有房间的账号收到 `POST /api/rooms -> 409 room_owner_conflict`（“你已经创建其他未结束房间”）；该响应是业务约束，不是静默成功。

## 认证只读观察

- 已复用现有 Chrome `admin` 会话完成认证只读导航；用户端、历史入口、现有房间和历史比赛详情可加载。
- 建房空提交显示客户端名称/辩题校验，未创建生产房间；管理后台用户、模型、音色、辩题、规则、比赛、裁判结果、问卷、日志、事故、后台任务、审计和系统设置入口均完成只读导航。
- 事故页的历史 OPEN 记录和系统设置 79.0% 磁盘使用率作为生产健康观察记录，未在本规格内修改或关闭。

## 条款确认前的 QA 写入尝试（历史基线）

- 使用现有 admin 会话填写唯一标题的短规则 4v4 训练赛建房表单并提交；页面未离开 `/rooms/create`。
- 该会话在大厅仍显示一个已有房间的“返回房间”入口；建房服务会拒绝已有房间或活跃参与者再次建房，本轮没有离开或修改既有比赛来绕过该业务约束。
- 后台比赛列表按该标题搜索为 `0 场比赛`，公开大厅也没有该标题；未形成可运行比赛或媒体状态。
- 临时 QA 注册表单已准备合成数据，但未在平台条款前勾选或提交；该确认仍需用户在行动前明确完成。
- 已读条款证据：平台条款 `platform-terms-v1`；人类辩手说明 `human-participation-v1`，包含麦克风采集、实时 ASR、向 Agent/AI 裁判提供文字上下文和音频保存范围。

## 待补证据

- 生产建房、选择席位、真实 Chrome 设备准备、正式 4v4 启动门禁和三名追加真人席位已完成；隔离比赛已实际启动。
- 已验证真实 Chrome 麦克风发言计时、ASR 文字记录同步、LiveKit 连接/发布、主持音频阶段推进、Agent 发言/TTS 文字同步、自由辩论入口和安全暂停恢复；后续隔离比赛已自然完成并可打开赛后记录、回放和原始音轨。赛后问卷任务生成、后台写入、大屏模式和五场真实比赛并发仍待单独验收；50 路真实 LLM provider 负载已由脱敏探针复测。
- 历史 `test_auth_migration_integration.py` 与当前 v2.0 约束存在未迁移断言：混合运行时 33 项为
  28 通过、5 失败；失败包括迁移回退后续污染、旧的全 Agent 建房/启动假设和旧 admin CLI 顺序。
  该日志保留为发现证据，不将历史失败计入当前 v2.0 集成门禁。

### QA-003：英文持久化 locale 触发 React hydration 错误

- 2026-10-03 线上复现：全新会话打开 `/register` 无 React 错误；切换到 English 后重新加载 `/register`，
  控制台出现 React production error `#418`。预期的匿名 `/api/auth/me` 401 和注册页 autocomplete
  提示另行记录，不计入该问题。
- 根因是 `LocaleProvider` 的首个客户端 render 读取 `localStorage`，而服务端总是输出中文树；英文持久化
  时两棵树不一致。修复边界为首帧采用服务端/显式 locale，挂载后的 effect 再同步持久化 locale；不改
  语言内容、路由或认证行为。
- 验收：中文/英文切换和英文 reload 不再产生 React 错误，页面仍保持英文、`document.lang` 和标题同步；
  既有匿名 401 仍是预期边界。若发布失败，回滚到该次发布前的 Web standalone 目录。
- 发布后全新 Playwright 会话复测 `/register`：英文切换并 reload 后 `lang=en`、标题为
  `Jixia · Live Human–AI Debate`，控制台无 React #418；仅有预期的 `/api/auth/me` 401 和浏览器
  autocomplete verbose 提示。局部回归：LocaleProvider 3/3、smoke 7/7。

### QA-004：生产音色目录存在启用但失败的音色

- 认证只读巡检 `/admin/voices` 发现 `博闻 Agent · yunhuanhuan_v3.1` 当前标记为“启用 / 失败”；
  其余配置包含已就绪和待校准状态，不能据此声明所有 TTS 音色可用。
- `/admin/models` 显示 Qwen 3.7 Flash（`qwen3.7-flash`）已启用，Base URL 为北京百炼 OpenAI 兼容地址，
  API key 仅显示末四位。未读取、修改或记录密钥。
- `/admin/settings` 当前磁盘使用率为 79.3%，距离 80% 新比赛告警阈值较近；事故页有 200 个查询结果，
  其中多个 LiveKit FFI `OPEN` 记录，均未在本轮关闭或修改。

### QA-005：历史 TTS 供应商失败后静默回退到 edge-tts

- 事故页存在 `TTS provider failed, falling back to edge-tts` 的 `OPEN` 事故，记录为 52 次、影响 52
  场比赛、0 名用户（2026-10-01 03:05:01）。这证明曾发生供应商失败后的供应商切换。
- 按工程契约，TTS 失败不能静默切换供应商或模型；在该事故核查完成前，真实 TTS、主持音频和比赛媒体
  流程不能标记为通过。本轮只读展开事故详情，未保存处理状态或修改生产记录。
- 当前代码检索未发现 `edge-tts` 回退实现；Core Agent voice 单测 26/26、Jobs TTS 单测 1/1 通过，
  但这些本地证据不能关闭历史线上事故，也不能替代真实供应商链路验收。

### QA-006：真实 Chrome 设备检测通过，但网络质量需要人工确认

- 真实 Chrome 中选择正方 1 辩并点击设备检测，设备检测完成后页面显示“网络稍弱，可以确认后继续”；确认后席位变为“已准备”，并显示“设备正常，已自动完成准备”。未出现浏览器麦克风权限阻塞，也未产生 React 控制台错误。
- 该证据只证明浏览器采集和设备检测状态机可完成，不证明正式比赛中的真人发言、LiveKit 发布、ASR 转写或 TTS 播放已通过。设备录音试听和后续多真人席位仍待补测。

### QA-007：正式 4v4 启动门禁按规格拒绝单真人房间

- 真实 Chrome 隔离房间开始按钮可见，但点击后提示“正式 4v4 每方必须安排 2 名真人辩手和 2 名 Agent”。当前只有一个合成真人席位，因此比赛未启动；没有修改既有真实比赛，也没有通过关闭房间绕过门禁。
- 同一合成账号再次建房时，生产 API 返回 `409 room_owner_conflict` 和“你已经创建其他未结束房间”。该错误已由 Playwright CLI 的请求/响应证据复核，属于可理解的业务拒绝；短赛规则尝试因此未形成第二个生产房间。
- 结论（已由 QA-009 续测补充）：正式 4v4 的席位约束和错误边界通过；多真人实时比赛、ASR/TTS、主持音频和恢复保护已有隔离切片证据，赛后归档与压力测试仍未完成。

### QA-008：未登录邀请入口保留 return_to 并阻止匿名入场

- 未登录 Playwright CLI 访问隔离房间邀请路径 `/join/474569` 后，页面重定向到 `/login?return_to=%2Fjoin%2F474569`，没有创建访客成员、占用席位或触发设备检测。
- 该入口边界通过；登录后的实际入场和多真人席位流程仍待补测。

## 回滚与数据边界

仅安装用户级工具、技能和本地测试产物；不修改 migration、生产配置或历史业务数据。
QA-001 和 QA-003 均为 Web-only 原子发布；QA-001 远端即时回滚目录为 `/opt/jixia-web-before-qa-fix-20261002T031917Z`，
QA-003 远端即时回滚目录为 `/opt/jixia-web-before-qa003-20261003T0910`；
测试产物保存在忽略的 `artifacts/qa-20261002/`，不记录 Cookie、密钥或个人业务记录。

## 事实冲突

工作区 PRD 被删除，Git HEAD PRD仍写全人类/全 Agent 支持；当前已发布 Spec 261 要求每方 2 真人 + 2 Agent。本规格测试按当前发布行为分别记录，保留冲突；不据此改写状态机或权限。


### QA-009：条款确认后的完整生产媒体切片

- 用户明确确认平台条款后，使用三名新增隔离合成账号补齐当前 QA 房间的正方 2、反方 1、反方 2 真人席位；四名真人均通过虚拟媒体设备检测并显示“已准备”。
- 房间 `474569`（match id `73331c7d-924c-48e0-86b6-3ab637d5fa53`）满足正式 4v4 的每方 2 真人 + 2 Agent 门禁并成功启动。真实 Chrome 房主端进入“麦克风已开启 / 实时发言中”，服务器计时推进，首轮真人发言出现同步文字记录；Playwright 虚拟媒体账号依次完成真人发言。
- 页面在回合间显示“Host audio is playing”，随后显示“Agent is speaking”和“语音正在实时播放，文字记录同步更新”，可见多句 Agent 文字记录；比赛继续推进到自由辩论。
- 故意让一名合成真人不在 60 秒内开始发言，验证比赛进入“比赛已安全暂停”；房主点击“申请恢复”后，三秒恢复倒计时完成，发言席重新开放并成功开始发言。
- 该切片证明生产端从席位门禁到实时人声、ASR、主持音频、Agent/TTS UI 和恢复保护的连续路径；后续隔离比赛已经完成并验证赛后记录/媒体归档，但不扩展为问卷任务生成、后台变更或压力测试通过。


## Client-disconnect safety observation

- After the media slice, the three synthetic Playwright clients were closed. The production match detected the missing current speaker/participants and entered `比赛已安全暂停` rather than advancing on a stale client or losing timing state.
- The owner UI exposed both `申请发言` and `申请恢复`; the transcript retained the completed free-debate entries. The isolated QA match was left paused for evidence and was not terminated or deleted.


## 2026-10-03 revalidation and remaining boundary

- `@playwright/cli` 0.1.22 and `chrome-cdp` 0.2.0 are installed; Chromium binaries are present for Playwright. The Chrome CDP skill is present, but the user Chrome instance does not expose `DevToolsActivePort`, so real Chrome was driven through its native browser control surface while Playwright CLI handled isolated browser sessions.
- Formal simulator tests passed 9/9. Independent PostgreSQL integration passed Core formal room/persistence tests 6/6 and Jobs PostgreSQL tests 2/2 using a separate local database `jx_debate_qa`; no production database was used.
- Repository Web build script `node scripts/build-web.mjs` passed, including TypeScript, route generation, standalone artifact checks, questionnaire route presence, and public research-surface checks. The standalone build served all 16 public/protected route probes with HTTP 200.
- Real Chrome toggled the isolated paused match into 大屏展示 and back to the normal control view. The authenticated account's `/me/postmatch-surveys` page correctly showed no available task, while `/me/ai-experience` rendered the five required rating questions and save/submit controls without submitting data.
- `pnpm test:browser` could not start in this environment because the workspace's pnpm preflight rejected ignored dependency build scripts (`ERR_PNPM_IGNORED_BUILDS`); this is an environment/tooling gate, not a Playwright assertion failure. The earlier green browser artifact remains the authoritative full-suite result, and the current build/route/simulator checks above were rerun.


## Deterministic full-course and reliability evidence

- The formal 4v4 simulator CLI completed two rounds with 4 human and 4 Agent participants, 20 completed speeches, six stale callbacks rejected, and pause/resume verified; JSON evidence is `results/formal-4v4-simulator-20261003.json`.
- The voice reliability simulator completed 31/31 scenarios, including ASR retry budgets, Agent/TTS failure convergence, free-debate races, timeout boundaries, pause/resume states, duplicate commands, and late callback rejection; JSON evidence is `results/voice-reliability-simulator-20261003.json`.
- A five-round formal 4v4 simulator run completed 50 speeches across 4 human and 4 Agent participants, verified pause/resume, and rejected 15 stale callbacks. This is deterministic local evidence, not a production concurrency or 50-request LLM load test.
- A local `LlmCapacityLimiter` gate probe admitted 50/50 concurrent synthetic leases, observed a maximum of 50 active leases, and rejected the 51st lease with `llm_capacity_full`. It does not replace a real provider load test.
- Targeted post-match, survey, experiment, format, rule, prompt, and paper-experiment tests passed 29/29 and 48/48 respectively.
- Core formal room/persistence integration was rerun against an independent PostgreSQL database: 6/6 passed. Jobs PostgreSQL host-TTS integration was 2/2 passed. The local PostgreSQL container was stopped after verification and its data volume preserved.
- The complete `test_experiment_integration.py` loop passed 1/1 against a separate PostgreSQL database, covering experiment batch lifecycle, roster/schedule, annotation/questionnaire persistence, and post-match task creation without production writes. A production read-only count confirmed zero non-terminal matches after QA.

## 2026-10-03 isolated production match completion

- Reconnected the three synthetic participant sessions after the earlier safety pause and used the authenticated Chrome owner to request recovery. The three-second recovery countdown completed and the match returned to `进行中` in free debate.
- The isolated match then reached `已结束` / `本场辩论已完成` without manual termination. The post-match record loaded with 29 speech entries, context version 25, and a `查看赛后记录` link.
- The post-match record exposed the transcript download, complete speech history, an Opus replay download, and a READY human raw audio track (89.4 seconds). A read-only production DB check confirmed the match is `FINISHED` with context version 25, 29 READY media files plus a READY replay, a succeeded `POSTMATCH_AUDIO` task, and a pending 24-hour `TRANSCRIPT_AUTO_ARCHIVE` task. The room snapshot is `formal-4v4-standard` with `postmatch_questionnaire_enabled=false`, no enabled JUDGE module, and `judge.enabled=false`; therefore no AI judge result or single-match questionnaire is expected for this ordinary formal rule.
- The authenticated owner confirmed the existing human text on the isolated post-match page; the control became disabled as `我的文字已提交` and the page showed `你的文字已经提交`. After a reload and an additional wait, the AI judge area still reported `评分任务尚未创建`.
- All three synthetic participant accounts were checked at `/me/postmatch-surveys`; each page showed `There are no single-match post-match questionnaires`. The independent AI experience questionnaire rendered its five rating questions and save/submit controls; no personal questionnaire was submitted.
- This closes the isolated end-to-end match and archive/media observation for the ordinary formal rule. The separate `paper-experiment-4v4` rule is enabled with post-match questionnaire support, but production has no scheduled experiment matches; its batch lifecycle and judge/questionnaire path remain a separate unexecuted surface. The production TTS fallback incident, admin mutation checks, and five-match concurrent business-flow test remain open; the 50-request LLM provider probe is recorded below.

## 2026-10-03 provider-chain production load revalidation

- 修正 `scripts/ops/provider_chain_probe.py` 的 ASR 就绪竞态：探针现在先并发送入 PCM，再等待会话 ready；本地探针测试 2/2 通过。之前的 5 路 ASR 超时是探针未喂首帧造成的，不是供应商结果。
- 使用临时远端探针文件（运行后删除，未改线上源码）复测生产适配器：TTS 5/5、ASR 5/5、LLM 50/50、LiveKit 5/5 全部成功，探针 exit 0；聚合延迟分别为 TTS p50/p95/max 10693/10834/10834ms，ASR 15121/15178/15178ms，LLM 1839/2479/2722ms，LiveKit 293/557/557ms。生产 ExternalCall 只记录脱敏的 OPS_PROBE 聚合状态，未输出密钥或原始响应。
- 隔离测试发现 Python LiveKit FFI 在多个 `Room` 同时析构时会触发 Rust Tokio non-unwinding panic（exit 134），即使 5/5 连接成功；单路无此现象。探针已改为 LiveKit 创建/销毁串行，以避免探针清理崩溃；该改动只影响运维探针，不改变应用服务的房间并发模型。四项服务复核均为 active。
- 该结果关闭“真实 provider 调用完全未测”的缺口，但不关闭 QA-004 启用失败音色和 QA-005 历史 TTS fallback 事故；线上 TTS 事故仍需独立运维处置后才能宣称全量语音供应商健康。

## 最终复核（2026-10-03）

- provider probe、赛后/问卷/实验、规则与模拟器相关定向回归共 81/81 通过；敏感信息扫描无结果，`git diff --check` 通过。
- 生产临时探针及输出已清理；四个 systemd 服务保持 `active`，`NRestarts=0`。

## Standalone Playwright revalidation（2026-10-03）

- `node scripts/build-web.mjs` 通过后，直接启动该 standalone Web（不经 pnpm 依赖安装预检），用 Playwright CLI 对同一四个桌面项目完整执行 248 项：236 passed、12 skipped、0 failed。
- 12 个 skipped 仍是代码中明确停用的历史实验 UI 场景；本次不是把 `pnpm test:browser` 的启动失败误报为测试失败。`pnpm test:browser` 仍会在浏览器启动前被 pnpm 11 `ERR_PNPM_IGNORED_BUILDS` 阻断。

## 生产实验排期前置盘点（2026-10-03）

- 生产只读查询显示 P01-P18/E01-E03 共 21 个实验账号均不存在，实验批次为空，`ScheduledMatch` 总数为 0。
- 因此运行现有 `prepare_experiment_simulation.py` 会新增 21 个账号并生成 21 场排期；本轮没有执行该写入操作。独立 PostgreSQL 的 `test_experiment_integration.py` 1/1 已覆盖同一批次、阵容、排期、标注、问卷和赛后任务闭环。
- 复测摘要日志保存在 `artifacts/qa-20261002/results/browser-standalone-rerun-20261003.log`。

## 线上实验开关门禁复核（2026-10-03）

- 生产 `.env` 的 `PAPER_EXPERIMENT_ENABLED=false`；P01-P18/E01-E03、实验批次和排期均为空。
- 该开关是正式研究招募前的发布门禁，文档要求完成真实身份对照、真人设备/持续长链路/盲听验收后才开启。本轮不擅自改生产配置、不创建研究账号或研究排期；实验 JUDGE/问卷线上执行因此保持未执行状态。

## 实验关闭状态的线上边界复测（2026-10-03）

- Playwright CLI 匿名访问 `/experiments` 收到 HTTP 307 并重定向 `/lobby`，随后受保护入口引导到登录；没有暴露实验排期或创建控件。
- 线上 `/api/experiments/capabilities` 返回 `{"creation_enabled":false,"history_readable":true,"target_version":"2.1.0"}`。
- 匿名访问 `/api/admin/experiments/batches` 返回 401 `not_authenticated`；没有执行任何生产写入。

## 实验浏览器测试范围复核（2026-10-03）

- 当前 Playwright 四视口套件中的 12 个 skipped 来自 `apps/web/tests/experiments.spec.ts` 中明确标记为 `legacy UI retired` 的三组旧创建/编辑/参与者流程（3 场景 × 4 视口），不是运行失败。
- 当前产品行为是实验入口关闭时重定向大厅、管理员入口重定向比赛与数据；这两个现行行为均已通过生产匿名 Playwright 复测。旧实验管理流程不应在关闭的线上入口强行恢复。

## 2026-10-04 公网匿名回归

- Playwright CLI 0.1.22 复测 `/`、`/login`、`/register`、`/lobby`、`/leaderboard`、`/guide`、`/experiments`、`/terms` 和 `/join/474569`。
- 普通路由均按预期加载；`/experiments` 因关闭实验能力重定向 `/lobby`，匿名邀请入口进入登录边界。
- 唯一控制台错误是预期的匿名 `/api/auth/me -> 401`，没有其他 console error/warning；无写入、表单提交或凭据留存。证据：`artifacts/qa-20261002/results/production-anonymous-smoke-20261004.md`。

## 2026-10-04 生产稳定性复核：Core OOM 重启

- 生产 Core 本机 `/health/live` 和 `/health/ready` 均返回 200；Web 首页关键 hero 图片仍为 `jixia-debate-hero-v5-transparent.png`，SHA-256 为 `b3103903d127304e6664c59d457c2f2ddb51432e8d3223e40461fc8bbc54dc02`。
- 复核 systemd/journal 发现 2026-10-03 19:50:16–19:50:18，Linux OOM killer 终止 `jx-core`，记录进程 anon RSS 约 1.48 GiB；systemd 于 19:50:23 自动重启，当前 `NRestarts=1`。这不是本轮测试造成的写入或手动重启。
- 重启后 50 秒只读观察中，Core `MemoryCurrent=116,813,824` bytes、`NRestarts=1` 保持稳定，内存可用约 956–997 MiB；该 OOM 仍是未关闭的生产稳定性风险，不能把当前 live/ready 200 写成长期无故障。
- OOM 前 18:30–19:51 的生产数据库只读聚合没有新比赛，也没有 ExternalCall 记录，暂不能把该峰值归因于 provider 压测或 QA 比赛；根因需单独做内存剖析。
- 同一 OOM 前窗口还有两条 `internal_exception`，均紧邻媒体 Range `206` 请求（19:06:11）；日志已脱敏且没有供应商原始响应，当前只能记录为媒体回放异常观察，未修改生产数据。
