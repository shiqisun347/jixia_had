# 2026-10-11 Spec 270 百炼接入与模型选型发布

- 按百炼官方文档核对：北京业务空间专属域名是生产推荐入口；3.0/3.1 Audio
  音色不能混用。DashScope 原生 SSE 可用，但 `qwen3.7-flash` 不能走纯文本
  `/text-generation`（20/20 返回 400），需多模态接口；现有 OpenAI 兼容链路无需
  增加另一套运行时编排，因此保留。
- 脱敏候选对比：`qwen3.7-flash` 与 `qwen3.7-flash-2026-07-15` 在 OpenAI 兼容和
  DashScope 多模态 SSE 均 20/20；固定快照的 DashScope 首 token p95 422ms、完成
  p95 2038ms，优于别名版本，已将唯一启用文本模型固定为该日期快照。
- Audio 对比：TTS 3.1 `yuxiaoyun_v3.1` 3/3，首音 p95 348ms；TTS 3.0
  `longanhuan_v3.6` 3/3，首音 p95 275ms；ASR 3.1 与 3.0 均 3/3。3.0 虽快，
  但音色体系不同且当前正式音色已按 3.1 校准，暂不切换，避免模型/音色不匹配。
- 发布前无进行中比赛；配置备份为 `/opt/spec270-202610111505`，旧模型为
  `qwen3.7-flash`，不改 migration、不改比赛冻结快照。发布后启用模型为
  `qwen3.7-flash-2026-07-15`，四服务 active，Core/Web 健康检查 200。
- 发布后探针：LLM 5/5（首 token 185–371ms），TTS 3.1 首音 207ms/完成 539ms，
  ASR 3.1 首 interim 112ms/完成 5401ms，评委 LLM 317ms 首 token/5650ms 完成。
  未宣称真实麦克风、五场压力或完整现场比赛通过。

# 2026-10-11 Spec 269 Agent 供应商瞬时失败收敛

- 线上 `324550` 的根因是两次 LLM 流在首 token 前收到空响应并在约 10 秒内连续失败；
  LLM 直连、TTS 直连和服务器资源均正常，因而按供应商瞬时流中断处理，没有切换模型或供应商。
- 已实施并发布：空 SSE 在首 token 前结束时归类为稳定的 `llm_first_token_timeout`；保留
  TechDesign 规定的“一次完整重试、第二次失败暂停”，并在两次尝试之间增加 500ms 有界退避；
  Web 对首 token 超时、流中断和供应商失败分别给出可操作提示。没有新增 migration。
- 本地证据：Agent/LLM 31 项、MatchActor/重置 111 项、Core 非集成 424 项通过；Web 全量
  217 项通过；Pyright、Ruff、TypeScript、ESLint、OpenAPI 检查和 `node scripts/build-web.mjs`
  生产构建通过。
- 发布回滚副本：`/opt/spec269-202610111344`，包含旧 Core Agent 文件、旧 Web app 和旧依赖。
  线上 Core/Web 文件哈希与本地构建一致；四服务 active，Core/Web `NRestarts=0`，live/ready
  与公网首页均 200。
- 发布后脱敏探针：LLM 5/5 通过（首 token 229–399ms），TTS 首音 229ms/完成 565ms，
  评委 LLM 完成 6652ms，LiveKit 603ms。ASR 旧探针的 10 秒 `wait_ready` 仍会超时；使用
  实际“先送 PCM 再等待 ready”的会话方式 83ms ready，记录为探针节奏遗留，不归因于本次
  Agent 失败修复。未执行真实麦克风或完整现场比赛验收。

# 2026-10-11 Spec 268 会议现场与赛后展示发布

- 已实施并发布候场二次启动：房间“进入候场”只建立 `START_PENDING_RUNTIME` 比赛页；管理员/组织者在比赛页第二次点击“正式开始比赛”后才进入 `START_COUNTDOWN`，旧 runtime-start 调用保留兼容路径。
- 已新增主持人/管理员应急控制台，复用暂停、恢复、重置、终止和大屏模式；观众仍只能观看和接收音频，不能执行比赛控制。
- 已追加 migration `0045_postmatch_showcase`，线上 head 为 0045，`postmatch_showcases`、`postmatch_judge_entries`、`audience_votes` 均存在。赛后提供评委点评、胜方/最佳辩手、一次性观众投票、八席完整排名、前三颁奖、全部名单和后台阶段切换页面。
- 首页首屏图已增加 WebP（约 428KB，PNG 仅作回退），去掉 PNG preload，WebP 返回 `public, max-age=31536000, immutable`；线上首页 200，WebP 200，`Content-Length=438000`，公网首字节约 0.29 秒。
- 发布前线上已有一场 `PAUSED` 比赛；迁移、Core 重启及发布后仍为 `PAUSED:1`，未终止或改写。四服务 active，发布切换时 Web 曾短暂自动拉起 3 次，之后连续 5 次 Core/Web 健康采样通过且当前稳定；Web 回滚目录 `/opt/jixia-web/releases/web-live-20261011014637`、`web-live-20261011014850`，Core 旧快照 `/opt/jixia-spec267-final-20261010233800`。
- 本地通过 Python compile、Ruff、MatchActor 113 项、赛后 9 项、YXH 4v4 模拟、Web TypeScript、定向 ESLint、OpenAPI contract 和生产构建；浏览器已核对后台、投票和首页 WebP source。真人现场完整候场/开赛/投票仍需设备和多账号现场验收。

# 2026-10-11 网页卡顿服务器诊断

- 服务器资源不是当前根因：运行约 10 天，load average 约 0.17–0.32（2 vCPU），可用内存约 836–843MB，根分区使用率 63%，四个生产服务均 active 且 `NRestarts=0`；Core 本机 `/health/live`、`/health/ready` 约 1–5ms，Next 首页本机约 1–3ms，服务器自身经公网域名访问首页约 12–40ms。
- 访问侧链路出现明显抖动：本机到 `180.76.181.30` 的路由走 `utun6`，一次采样 RTT 约 563ms、HTTPS 首字节约 2.1–2.5s；随后同一路由恢复到 RTT 约 15ms、首页 HTTPS 总耗时约 54–81ms。服务器网卡错误/丢包、监听队列溢出和 OpenResty 错误日志未见异常。该现象指向访问侧 VPN/隧道或跨地域线路，而非服务器持续拥塞。
- 冷启动页面另有首屏放大因素：`jixia-debate-hero-v5-transparent.png` 约 1.93MB，浏览器冷上下文实测传输约 9.1s，并成为 LCP；页面总传输约 2.41MB。HTML、JS、CSS 已由 OpenResty gzip，静态 JS/CSS 使用长期 immutable 缓存。
- 大厅房间查询约每 5–6 秒一次；最近 10 分钟 Core 约 19 个完整请求，未发现持续请求风暴。服务器上仍有历史 `codex resume` tmux 会话，当前 CPU 约 0.3%，不能据此判定为网页卡顿根因，后续不要未经确认直接终止。
- 本次仅做只读诊断，未重启服务、修改代理配置、改数据库或改变比赛状态。待处理优先级：先让访问端绕过/优化 `utun6` 隧道并复测；再把首页大图转为更小的 WebP/AVIF 或按需加载；最后评估大厅轮询改为可见页/退避策略。
- 2026-10-11 01:03–01:06 CST 复测：首页、`/debate`、`/lobby` 大多数请求为 50–110ms，偶发一次约 844ms；服务器负载 0.12–0.17、TCP 监听无溢出、5 秒 TCP 重传/超时增量很低。诊断期间 `docker logs` 会让 dockerd 短时读盘/占 CPU，属于检查命令本身的副作用，不是线上 Web 根因；已停止继续用该方式采样。

# 2026-10-10 服务器磁盘清理

- 生产根分区清理前可用约 2.2G、使用率 95%；完成两轮清理及历史备份压缩后可用约 15G、使用率 62%，共回收约 13G。
- 处理了 systemd journal 旧归档、未使用 Docker 镜像、npm/uv/pip/Node 下载缓存、过期测试/浏览器缓存、开发依赖与构建缓存、旧 QA/阶段 Web 目录，以及已被 Spec265 回滚包替代的 Spec264 完整备份。
- `/root/_archived_20260929` 的历史发布备份先生成 `/root/_archived_20260929.tar.zst`，通过 zstd 校验和逐文件比对后才删除原目录；最新 Spec265 完整回滚包、数据库、历史比赛、业务音频和当前运行制品均保留。
- journald 约 474M，策略为最多 4G、至少保留 2G 空间、运行时最多 128M、最长 30 天。未读取或写入密钥、`api.md` 或 `.env` 内容。
- 四服务 active、重启次数均为 0；Core live/ready、本机 Web 与公网首页/创建页均 200；数据库迁移仍为 `0044_rule_audio_generations`，活动比赛为 0。

# 2026-10-10 比赛真人无法发言诊断

- 线上唯一活动比赛 `d7925a79-9b56-41f5-bf6f-9539a69c4f8f` 检查时为 `PAUSED / RECOVERY_REQUIRED`，暂停来源为 `AGENT_PREPARING`；真人在负方 2 号席，当前轮到正方 1 号 Agent，尚未创建任何真人 Speech。
- 事件序列显示 15:28 恢复后进入 Agent 准备，16:01:31 连接离线，16:02:31 因 `PLAYER_OFFLINE_TIMEOUT` 暂停，16:04:32 重新上线但仍需显式恢复；未直接修改比赛状态。
- 该轮 LLM/TTS 外部调用从 15:28:37/38 开始但没有首结果，直到暂停时才以 `agent_cancelled` 结束。服务器到百炼两条解析地址的 TCP/TLS/未认证 HTTP 探测约 10–55ms，排除供应商网络整体不可达；源码虽声明 10 秒流式超时，但线上调用未在该预算内收敛，需后续专项修复。
- 客户端公网链路表现为间歇性抖动：首次采样 RTT 35–973ms、HTTPS 总耗时 0.7–4.2s；随后 30 次采样恢复为 0% 丢包、平均 14.5ms，说明更像访问侧瞬时网络/代理波动而非服务器持续拥塞。服务器网卡错误/丢包为 0，本地 Web 约 1–7ms，四服务均 active。
- 故障专项回归：Core Agent 超时/取消 4 项、真人发言/WS 21 项、掉线/恢复/状态 22 项，Web 发言命令/连接 8 项均通过；生产四服务 active、`NRestarts=0`、live/ready 与公网首页/创建页 200。当前比赛仍保留 `PAUSED / RECOVERY_REQUIRED`，未用测试命令改写线上状态。

# 2026-10-10 Spec 265 自由成员组成实施发布

- 用户要求 YXH 等所有赛制允许纯真人、纯 Agent 及任意混合比例；新房间仍为双方各四席。
- 发现全局 `ensure_formal_human_agent_mix` 强制每方 2+2，create_room 直接拒绝纯 Agent；
  Spec 261 限制与 PRD 原有自由成员组成及本次要求冲突，已在 Spec 265 记录替代范围。
- 已按批准实施：取消每方固定 2+2，保留八席完整及实际参赛者资源/设备校验；创建支持纯真人空席、
  自由入席有限随机补位和管理员纯 Agent 专用房，Web 创建与指南同步，OpenAPI 已更新。
- 本地 Core 418 passed/76 skipped，Jobs 25 passed/2 skipped，Web 214 passed；Ruff/Pyright/TS/ESLint、
  contracts、脚本/QA 与 standalone build 通过。新增 PostgreSQL 集成测试因本机独立数据库未启动未执行。
- 2026-10-10 零活动比赛预检后受控发布；备份 `/opt/jixia-backup-before-spec265-20261010053654`；
  四服务 active，live/ready、公网首页与创建页 200，连续 5 次采样通过且 NRestarts=0；迁移仍为 0044。
- 线上 11 套非归档规则无 Agent 数量 blocker；音频审核等其他条件保持。未执行真实麦克风、盲听或压力验收，
  回滚保留规则、房间、generation 和历史文件，不降级数据库。

# 2026-10-09 Spec 264 赛制规则与主持音频实施发布

- 用户已批准 Spec 264 并完成实现：规则生命周期/结构校验/readiness blockers、阶段 CRUD、音频 generation 与
  manifest/审核栅栏、Jobs 任务收敛与 heartbeat、Web 目录归档和主持音频面板均已落地。
- 仅追加 migration `0044_rule_audio_generations`。生产发布前活动比赛为 0，数据库 head 已核验为 0044；
  备份为 `/opt/jixia-backup-before-spec264-20261009195240` 和 `/opt/jixia-web-before-spec264-web-20261009200000`。
- 旧数据修复工具先 dry-run（无异常）再 apply，关联 43 条资产；当前 generation 为 6 个 `NOT_REQUIRED`、
  5 个 `SUCCEEDED`，YXH 的 5 个待处理片段已由 Qwen 3.1 Jobs 成功完成，历史失败任务保留作诊断。
- 生产 `jx-core`、`jx-jobs`、`jx-web`、`jx-livekit` active；Core live/ready、公开首页与 `/admin/rules`
  均返回 200，发布后 `preflight_v2_1.sh` 通过且无进行中比赛。Jobs heartbeat 报告 `host_tts=true`、
  `qwen-audio-3.1-tts-flash`、WebSocket 已配置。
- 本地证据：Core 392 passed/48 skipped，Jobs 25 passed/2 skipped，Web 214 passed，Spec 264 PostgreSQL
  集成 5 passed，工具/QA 50+6 passed；Ruff、Pyright、TypeScript、ESLint、OpenAPI 和 standalone build 通过。
- 未执行外部供应商盲听或压力验收；后续若回滚使用上述备份恢复兼容制品，保留追加 schema/generation/文件/审计，
  不 downgrade 数据库。

# 2026-10-01 百炼 Qwen 模型与音色配置

- 生产配置已在无进行中比赛（`FINISHED=20`、`TERMINATED=60`）时切换：启用的模型资源为 `Qwen 3.7 Flash` / `qwen3.7-flash`，OpenAI 兼容地址为北京工作空间地址；API Key 重新使用服务器 `LLM_KEY_ENCRYPTION_KEY` 加密写入数据库，日志和文档不含密钥。
- Core/Jobs 的 `ASR_WS_URL`、`TTS_WS_URL` 均为 `wss://llm-88h5fmsp127fpced.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`；模型分别为 `qwen-audio-3.1-asr-flash-streaming`、`qwen-audio-3.1-tts-flash`，工作空间为 `llm-88h5fmsp127fpced`。同一新 Key 已写入两份受保护 EnvironmentFile 的 `ASR_API_KEY`/`TTS_API_KEY`，服务重启后四服务 active，Core `/health/live` 和 `/health/ready` 均为 200。
- 保留正式使用的 `yuxiaoyun_v3.1`、`xiaxiaochen_v3.1`、`anmingyuan_v3.1`、`huozhuoshi_v3.1`、`yunhuanhuan_v3.1` 和主持人 `xuyanchu_v3.1`；按百炼官方 `qwen-audio-3.1-tts-flash` 音色列表增加 `qiaoxiaojiao_v3.1`、`wenhuaiqing_v3.1`、`anxiaolan_v3.1`、`xieshurou_v3.1`、`baianran_v3.1`、`xunanchuan_v3.1`，均为 `DISABLED/NOT_CALIBRATED`，未自动创建正式 Agent 映射。
- 服务器配置和数据库变更备份在 `/root/_archived_20261001-qwen-config`，含 `core.env`、`jobs.env` 和 `jx_debate.dump`；不回退 migration、不删除历史比赛数据。
- 脱敏直连验证通过：12 个正式/备用音色 TTS 全部 `passed`；Qwen LLM 首 token 272ms、完成 344ms；AI 裁判 `passed`（8 个参赛者，完成 6677ms）；Qwen ASR 首个 interim 215ms、最终 285ms、识别非空 21 字。仓库 provider_chain_probe 原先的 ASR 预检会在送入音频前等待 ready，因而单独报告超时；按实际适配器先送音频的验证通过。
- 依据：[Qwen-Audio-TTS 音色列表](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list)、[实时语音合成](https://help.aliyun.com/zh/model-studio/realtime-tts-user-guide)、[Qwen-Audio ASR Streaming Java SDK](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-java-sdk)。

# 2026-10-01 回撤至 b3ef871 论文提交快照

- 按用户明确要求，将生产 Core、Jobs、Web 切换到 `b3ef871dbb4c52f8b2de076c6b5971ac5762dc3f`（`chore: snapshot paper submission version`）。数据库保持不变：head 仍为 `0043_browser_device_checks`，比赛为 `FINISHED=20`、`TERMINATED=60`，无非终态比赛。
- 从该提交独立归档并安装锁定依赖；仓库 `pnpm build` 生成完整 Web standalone，BUILD_ID 为 `N6aGOaHLG2s4yoJfu7Kj5`。线上 `server.js` SHA-256 为 `30b3d3d89abbd3ab4b1958f986b133f74f82330eae698dca02b4fb26b0aa6495`，首页插画 `jixia-debate-hero-v5-transparent.png` SHA-256 为 `b3103903d127304e6664c59d457c2f2ddb51432e8d3223e40461fc8bbc54dc02`，与本地构建一致。
- Core/Jobs 共 133 个源码文件逐一 SHA-256 与目标提交匹配；Core runtime SHA-256 为 `36ae24b3f59bc1f3da2667e510064c9049475339cd8e475f066e665699380b5c`。未同步 `api.md`、`.env`、密钥或未提交工作区改动。
- 回撤前完整备份保存在服务器 `/root/_archived_20260929/jixia-backup-before-b3ef871-20261001133552`，包括当前 Core/Jobs 源码、migration 和 Web 运行目录；数据库未做 downgrade。四服务 active、Core/Web `NRestarts=0`，Core live/ready=200，公网首页与 `/debate`=200，未认证 `/api/auth/me`=401；连续采样保持稳定，成功启动后 warning-or-higher 日志为 0。
- 验证：目标提交 Core 382 passed/36 deselected，Jobs 25 passed/2 deselected，Web standalone build 通过；Web 单测有 2 个既有 `use-match-command` 文案预期失败（实际文案为“比赛连接暂时不可用，请刷新后重试。”），不影响生产构建。浏览器自动化连接本轮不可用，首页 HTML 与插画 HTTPS 请求及 SHA-256 已完成线上核对。
- 2026-10-01 线上复核：无缓存首页与普通首页均引用 `jixia-debate-hero-v5-transparent.png`，公网图片 SHA-256 与服务器一致；root HTML 返回 `s-maxage=31536000`，若用户浏览器保留旧 HTML 需硬刷新或使用无痕窗口验证。

# 2026-10-01 服务器 Core 回撤

- 线上 Core 因 `agent/runtime.py` 的 `IndentationError` 反复重启，健康接口不可用；数据库只读核对显示当前 head 为 `0043_browser_device_checks`，比赛状态仅有 `FINISHED=20`、`TERMINATED=60`，没有非终态比赛。
- 按用户请求执行受控回撤：先停止 Core/Jobs/Web，保留损坏文件为服务器 root-only 现场副本 `runtime.py.rollback-failed-20261001122421`，将 Core 运行时恢复为服务器 2026-09-30 的可编译副本 `runtime.py.bak-1790710203`（SHA-256 `bd6673ab5594959f2e405c1a983476209854b54c1a7bde690b494b25a4ee4909`）。随后补回同一 `DEPLOY_STAGE` 中被故障发布漏删的 8 个实验/论文规则模块；其哈希与本地仓库一致。
- 未修改数据库、migration、Web standalone 或 LiveKit。回撤后四个 systemd 服务 active，Core live/ready=200，公网首页和 `/debate`=200，未认证 `/api/auth/me`=401；迁移仍为 `0043`，比赛计数未变。
- Core 成功进程自 12:31:05 起保持运行，随后采样 live/ready/Web 均为 200；启动后的 Core/Jobs 无 warning-or-higher 日志，Web 仅有回撤停服务时的预期 SIGTERM 记录。`NRestarts=384` 是故障循环的历史累计值，成功启动后未继续增加。

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

## 2026-10-11 Spec 267 主持音服务端衔接与自由辩论后续阶段

- 已实施并发布 Spec 267。自由辩论双方耗尽后经统一线性推进，YXH 会继续反方四辩、
  正方四辩、结束主持音，最终只产生一次 `match.finished`。
- 主持音现在由 Core `AgentRuntime` 使用 PyAV 增量解码和 LiveKit PCM 输出；每段前置
  800ms、后置 600ms 静音，200ms 有界音频队列排空后才提交独立播放完成回调。回调带
  `match_id/action_key/attempt_no/generation_id/context_version`，旧回调不能推进新动作。
  Web 已移除 HOST_ANNOUNCING 的本地 fetch/seek/`<audio>` 播放，保留历史 HTTP 读取路由。
- 本地证据：Core 422/76 deselected、Jobs 25/2 deselected、Web 214；定向 Ruff、Pyright、
  TypeScript、ESLint、生产构建和 `git diff --check` 通过；服务端主持音 mock 探针验证
  800/600ms 过渡、解码帧、一次重试和完成回调。`pnpm contracts:check` 因本机 pnpm 忽略
  依赖构建脚本失败，手工 OpenAPI 导出/生成比较无差异。
- 线上证据：备份 `/opt/jixia-spec267-final-20261010233800`；Core 四文件哈希与本地一致；
  Web standalone 构建 `8LwdMp0i1PJetz_WCQt1I`，1102 文件哈希校验后原子切换。预检确认
  migration `0044_rule_audio_generations`、`active_matches=0`；四服务 active、Core
  live/ready 200、10 次每 30 秒采样通过、四服务 `NRestarts=0`；公网 `/debate` 和未登录
  边界冒烟通过，页面无本地 `<audio>`。
- 遗留风险：尚未完成真实真人设备的首字/连续试听、暂停重连现场验收，也未把供应商短链路
  误报为通过；这些需要后续现场证据。

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

## 2026-10-01 Spec 261 真人 4v4 比赛与现场运维

- 新建房间继续只接受正式 4v4；Core 创建接口拒绝 `is_all_agent`，启动前由 `ensure_formal_human_agent_mix` 强制正反双方各 2 名真人和 2 名 Agent。历史实验/非 4v4 数据和接口保留只读兼容。
- 比赛页管理员可切换“大屏展示”：浏览器全屏时固定比赛主画面、隐藏操作栏和文字侧栏，退出后恢复控制视图；管理员权限不再依赖历史实验控制器字段。
- 后台比赛工作台收敛为现场控制台，实时轮询运行中的比赛，提供暂停、恢复、恢复保护、重置当前发言、强制终止和大屏入口；暂停动作走 MatchActor 并写审计日志，OpenAPI 合同已更新。
- 普通用户/管理员导航不再显示论文实验、实验预约、研究问卷入口；`/experiments` 和 `/admin/experiments` 显式回到大厅/比赛管理，历史实验页面和后端接口未删除。
- 新增 `scripts/qa/formal_4v4_match_simulator.py` 与 Core 测试：每轮 4 真人 + 4 Agent 完成固定发言和自由辩论，覆盖暂停/恢复、迟到 ASR 回调拒绝和终态收敛；两轮 20 次发言通过。
- 证据：Core 非集成 `392 passed, 36 deselected`；Web 全量 `208 passed`（修复指令失败 Toast 后）；Web TypeScript、目标 ESLint、OpenAPI 合同、standalone 生产构建通过；agent-browser 本地验证 `/experiments -> /lobby`、`/admin/experiments -> /admin/matches`，并用隔离 Mock 数据验证现场控制台显示“立即暂停 / 强制终止 / 重置当前发言 / 大屏展示”。
- 限制：未使用真实管理员凭据进行公网比赛页操作，未做真人噪声、五场压力或 50 路模型负载；这些仍是独立验收项。

## 2026-10-01 Spec 261 真实数据库与运行时复验

- 启动仓库自有 PostgreSQL 16 测试容器，执行当前 head migration；真实 Core 以 `APP_ENV=test` 启动在本地 8101，`/health/live`、`/health/ready` 返回 200，未登录 `/api/auth/me` 正确返回 401，随后已停止 Core 和数据库容器并保留测试卷。
- 正式 4v4 PostgreSQL 持久化集成 5/5 通过；新增房间服务集成 1/1 通过，实际走 `CatalogService`、`RuleService`、`AuthService`、`RoomService.create_room/join_room/select_seat/save_device_check/ready/start_room`，四名真人完成入席与设备准备，四个 Agent 席位保留，房间进入 `START_PENDING_RUNTIME`。
- 5 轮正式 4v4 MatchActor 回放完成 50 次发言、15 个迟到回调拒绝，暂停/恢复每轮通过；Spec 258 语音可靠性模拟 31/31 场景通过。
- 数据库复验暴露并修复旧赛后问卷提交后可重新打开的问题：`save_postmatch_task` 现在稳定返回 `survey_locked`，正式集成回归恢复为 5/5。该接口仍仅保留历史兼容，普通 Web 入口已移除。
- 全量 integration 集合仍包含历史 migration/all-agent 论文实验测试，不作为本规格验收门禁；当前失败项是旧测试对已删除 all-agent 工作流和迁移降级顺序的假设，不影响新的正式 4v4 集成用例。

## 2026-10-01 Spec 261 线上发布

- 在发布前后只读 preflight 均确认四服务 active、Core live/ready 200、数据库 head 为
  `0043_browser_device_checks`、非终态比赛为 0；未执行 migration downgrade，也未修改比赛状态。
- 同步了本规格的五个 Core 源文件，并从同一次 `node scripts/build-web.mjs` 构建原子切换完整 Web
  standalone。Core 文件和 Web `server.js`/`BUILD_ID` 的 SHA-256 均与本地构建一致。
- 公网 `/`、`/lobby`、`/debate`、两个历史问卷页面返回 200；`/experiments -> /lobby`、
  `/admin/experiments -> /admin/matches`，未认证 `/api/auth/me` 返回 401。
- 连续约五分钟 10/10 次 live、ready、`/debate` 与四服务状态检查通过，重启计数未增加，启动后
  warning-or-higher 日志为 0。回滚副本为
  `/root/_archived_20261001-jx-release/jixia-backup-before-spec261-20261001T103416Z`，
  Web 即时回滚目录为 `/opt/jixia-web-before-spec261-20261001T103416Z`。
- 发布后脱敏核对确认启用模型为 `qwen3.7-flash`，Core/Jobs 运行时为
  `qwen-audio-3.1-asr-flash-streaming` 与 `qwen-audio-3.1-tts-flash`，北京百炼工作空间已配置；
  备用音色保持 `DISABLED/NOT_CALIBRATED`，未将密钥写入仓库或日志。

## 2026-10-02 Spec 262 浏览器全面 QA

- 用户级安装并验证 `chrome-cdp` npm 0.2.0、`@playwright/cli` 0.1.22；安装
  `~/.codex/skills/chrome-cdp` 和工作区 `.claude/skills/playwright-cli`，并使用两者及
  agent-browser 完成线上只读巡检。
- 线上 16 个公开/认证边界入口、资源/网络、语言切换、无效登录、注册空表单、排行榜筛选、
  503 恢复、390×844 窄视口和键盘焦点通过；Chrome CDP 滚动后图片无 broken image，线上
  preflight 仍为四服务 active、head `0043_browser_device_checks`、非终态比赛 0。
- Web Vitest 52 files / 210 tests、Core 非集成 392 passed、contracts 和 diff check 通过。
  本地 Playwright 中文 reference 59 项为 47 passed / 12 failed；失败上下文留在
  `apps/web/test-results/`，部分是历史实验断言或真实 runtime/设备边界，未将其报告为线上通过。
- 发现并记录 QA-001：排行榜 503 状态的重试按钮显示内部 key `Leaderboard.reload`；完整报告与
  截图/CLI 输出在 `artifacts/qa-20261002/report.md`。生产认证比赛、设备、LiveKit、ASR/TTS
  和压力验收因没有授权会话仍待补证，不创建生产测试数据。

## 2026-10-02 Spec 262 QA-001 修复发布

- 补齐排行榜错误状态的中英文 `Leaderboard.reload` 文案；本地定向 Playwright 排行榜 4/4
  通过，生产以 `/api/leaderboards` 503 Mock 复验显示“重新加载”，不再暴露内部 i18n key。
- Web-only standalone 已原子发布；远端即时回滚目录为
  `/opt/jixia-web-before-qa-fix-20261002T031917Z`，构建标识为 `7bFFFfrx7J_sLydRF18LN`，
  本地/远端 `server.js` SHA-256 一致。未修改 Core、Jobs、LiveKit、数据库或 migration。
- 发布后 preflight 通过：四服务 active、Core live/ready 200、head
  `0043_browser_device_checks`、非终态比赛 0；连续 5/5 次 live、ready、Web 200，最近一分钟
  Web warning-or-higher 日志为 0。
- 认证生产比赛、设备、LiveKit、ASR/TTS、后台和压力验收仍因缺少授权测试会话待补证；本轮不创建
  生产测试数据。

## 2026-10-02 Spec 262 认证只读线上巡检

- 发现并复用现有 Chrome `admin` 登录态完成只读导航：用户端大厅、建房、个人/资料/改密、问卷，
  `/experiments` 与 `/admin/experiments` 历史回退，现有 4v4 房间和历史比赛详情均可加载。建房空
  提交只显示名称/辩题校验，未创建房间、未选择席位、未启动设备检测或比赛。
- 管理后台用户、模型、音色、辩题、规则、比赛、裁判结果、问卷、日志、事故、后台任务、审计和
  系统设置入口完成只读巡检；模型 API key 页面保持遮罩，未读取或记录密钥。
- 生产健康观察：事故页仍有 2026-10-01 的历史 OPEN 记录，包含 ASGI 异常、LiveKit FFI、ping
  timeout 与 advisory lock connection lost；系统设置显示磁盘使用率 79.0%，接近 80% 告警阈值。
  两项仅记录为待运维核查，没有在本轮修改。
- 认证只读证据保存在 `artifacts/qa-20261002/results/online-auth-readonly-20261002.md`；真人麦克风、
  LiveKit/ASR/TTS、生产比赛控制、问卷写入和压力测试仍未验收。

## 2026-10-02 Spec 262 本地完整门禁复核

- Jobs 非集成 25/25 通过；Storybook 94 项中 86 通过、8 失败，失败集中在过时的 Guide/rooms
  文案和 debate layout 场景定位，日志为 `artifacts/qa-20261002/results/storybook-final.log`。
- 启动隔离 PostgreSQL、执行 head migration 并运行测试 Core 后，标准 Playwright 236 项完整执行：
  92 passed、144 failed；失败主要是默认 `en-US` 与旧中文硬编码断言不一致，以及当前已重定向的历史
  实验页面断言，日志为 `artifacts/qa-20261002/results/browser-standard-core-final.log`。
- 中文 locale 关键页定向复验为 15/16，房间/赛后/比赛关键路径 25/31；剩余项集中在旧 hero、toast
  aria 名称/错误文案和网络状态按钮断言，日志分别为 `browser-zh-key-pages.log` 与
  `browser-zh-runtime-key-pages.log`。
- 修正排行榜 i18n 源码断言后，`pnpm test:tooling` 已达 50/50。`pnpm lint`、`pnpm typecheck`、
  `pnpm format:check` 分别受既有 Ruff/Pyright/Prettier 问题影响，原始日志保存在该目录。

## 2026-10-02 Spec 262 本地门禁收尾

- 将仓库 Playwright 与认证配置固定为 `zh-CN`，并同步当前发布行为的历史实验入口重定向、首页 hero、
  头像资源、网络状态按钮和房间错误 Toast 断言；隔离 PostgreSQL + Core + standalone Web 下四个
  桌面视口标准 Playwright 232 passed、12 skipped，日志为 `artifacts/qa-20261002/results/browser-standard-final-green.log`。
  跳过的 12 项是已停用历史实验创建/编辑流程（3 场景 × 4 视口），原测试保留且原因明确。
- Storybook 11 文件 94/94、Web Vitest 52/210、tooling 50/50、OpenAPI contracts 和 `git diff --check`
  通过。设备复用请求已显式 Mock，Storybook 最终无相关 MSW 警告。
- QA-002：Axe 检出指南未选中角色按钮对比度 4.34:1，低于 4.5:1；已将其文字颜色调整为
  `text-slate-600`，Storybook 94/94 复验通过，并随 QA-003 Web-only 构建发布。
- `pnpm lint`、`pnpm typecheck`、`pnpm format:check` 仍受工作区既有 Ruff/Pyright/Prettier 问题影响；
  本轮未为刷绿而修改无关业务代码。生产写入、真人麦克风、LiveKit/ASR/TTS 和压力测试仍未验收。

## 2026-10-02 Spec 262 生产 QA 授权尝试

- 用户已明确允许使用独立 QA 数据进行线上端到端测试。用现有 Chrome `admin` 会话填写唯一标题的短规则 4v4 训练赛建房并提交后，页面仍停留在 `/rooms/create`；后台按该标题搜索为 0 场比赛，公开大厅也未出现该标题，未形成可运行比赛或媒体数据。
- 临时 QA 注册表单已填入合成数据，但未勾选/提交平台条款；平台条款确认仍是注册和真人参赛测试的行动前置条件，不能代为假设同意。
- 已读公开条款：平台条款版本 `platform-terms-v1`；人类辩手说明版本 `human-participation-v1`，涉及麦克风采集、实时 ASR、向 Agent/AI 裁判提供文字上下文和音频保存范围。
- 因此真人席位、设备检测、LiveKit、ASR/TTS、后台比赛控制、问卷写入和大屏模式仍未取得生产通过证据；报告与规格已记录该边界。
- 现有 admin 会话在大厅仍显示已有房间入口；建房服务明确拒绝已有房间或活跃参与者再次建房，本轮没有离开或修改既有比赛来绕过该约束。

## 2026-10-02 Spec 262 认证浏览器补充

- 使用独立临时 PostgreSQL、head migration、真实 Core 和 standalone Web，直接运行认证 Playwright（注册、平台条款确认、资料编辑、退出确认、重新登录、`/debate` 保护边界）1/1 通过。
- 当前 `/profile` 按产品设计重定向为 `/me?edit=profile` 并打开“编辑资料与头像”对话框；修正了旧测试的标题断言，未修改产品代码。
- 临时认证数据库容器已清理；未读取或写入生产数据。

## 2026-10-02 Spec 262 Core / Jobs 集成切片

- 独立 PostgreSQL + head migration 下，当前 v2.0 Core 集成 11/11、Jobs PostgreSQL 集成 2/2 通过；正式 4v4 房间测试改为复用 migration 预置的唯一 admin。
- 历史 auth migration 集成混跑为 28 passed、5 failed，失败集中在迁移回退顺序污染和旧全 Agent/admin 创建假设，已单独记录，不冒充当前 v2.0 通过。

## 2026-10-03 Spec 262 QA-003 发布

- 修复 Web `LocaleProvider` 的首帧 hydration mismatch：首个客户端 render 固定使用服务端中文默认值，
  挂载后同步持久化/浏览器 locale，并在 Next head reconciliation 后保持标题与元数据同步。新增英文持久化
  reload 回归；LocaleProvider 3/3、smoke 7/7 通过。
- Web-only standalone 已原子发布到线上，BUILD_ID `zYyxSOk-xY_gDAzgebcSF`；远端 `server.js` SHA-256
  与本地构建一致。发布前回滚副本为 `/opt/jixia-web-before-qa003-20261003T0910`，旧 BUILD_ID
  `7bFFFfrxJ7_sLydRF18LN` 保留。未修改 Core、Jobs、LiveKit、数据库或 migration。
- 发布后四服务均 active、Core live/ready 200，公网 `/` 200；全新 Playwright 会话切换英文并刷新
  `/register` 后 `lang=en`、英文标题正确，控制台无 React #418。匿名 `/api/auth/me` 401 与
  autocomplete verbose 属预期边界。
- 生产注册、真实 4v4 比赛、设备/LiveKit、ASR/TTS、问卷写入和压力验收仍待用户明确完成平台条款
  确认并提供可用 QA 会话；不得把本次匿名发布后复测写成这些功能已通过。

## 2026-10-03 Spec 262 认证只读补充

- 使用现有 `admin` 会话再次只读巡检 `/me`、一个准备中的正式 4v4 房间、已结束比赛记录、`/debate`
  保护入口及全部管理员入口；页面均可加载，关键页控制台未见 error。未选择席位、未启动设备检测、
  未开始/结束比赛，也未修改赛后记录。
- `/admin/models` 显示启用的 `Qwen 3.7 Flash / qwen3.7-flash` 和北京百炼兼容地址，密钥仅末四位遮罩。
  `/admin/voices` 发现 `博闻 Agent / yunhuanhuan_v3.1` 为“启用 / 失败”，其余音色含“就绪”和“待校准”；
  不能把真实 TTS 全量验收写成通过。
- `/admin/settings` 磁盘使用率为 79.3%；`/admin/incidents` 返回 200 个查询结果，并有多个 LiveKit FFI
  `OPEN` 记录。两项仅记录为运维风险，未在本轮关闭、清理或修改。
- 进一步展开事故 `TTS provider failed, falling back to edge-tts`：状态 `OPEN`，52 次，影响 52 场比赛，
  时间为 2026-10-01 03:05:01。该历史证据显示 TTS 供应商失败后的静默回退，违反工程契约；真实 TTS、
  主持音频和媒体验收继续保持未通过，未修改事故处理状态。当前代码检索未发现 `edge-tts` 回退实现；
  Core Agent voice 单测 26/26、Jobs TTS 单测 1/1 通过，但不能据此关闭线上历史事故。

## 2026-10-03 Spec 262 生产 QA 续测

- 用户明确确认平台条款并授权合成 QA 数据；两个临时账号完成注册/自动登录，凭据未进入仓库、文档或日志。
- 真实 Chrome 创建隔离正式 4v4 房间 `aeed7572-506c-441f-b7dd-c1f9ef975ba7`（房间号 `474569`，标题 `QA Chrome 全链路 20261003 1159`），选择正方 1 辩后设备检测通过；页面显示网络稍弱警告，确认后状态为“设备正常，已自动完成准备 / 已准备”。
- 启动时产品正确提示正式 4v4 每方需要 2 名真人 + 2 名 Agent；单真人房间未启动，未关闭该 QA 房间或修改真实比赛。
- 已有房间的账号再次建房返回 `409 room_owner_conflict`，说明生产 owner 冲突边界可见且未误报成功；短赛第二房间未形成。
- 未登录访问 `/join/474569` 正确重定向到 `/login?return_to=%2Fjoin%2F474569`，未产生访客成员或席位副作用。
- 真人设备检测通过不代表正式媒体链路通过。多真人席位、真人发言、LiveKit、ASR/TTS、主持音频、赛后归档和压力测试仍未验收；QA-005 的历史 TTS fallback 事故保持未关闭。

## 2026-10-03 Spec 262 生产媒体切片

- 用户确认平台条款后，新增三名隔离合成账号补齐正式 QA 房间 `474569` 的正方 2、反方 1、反方 2 真人席位；四名真人设备检测均显示“已准备”。
- 房间 match id `73331c7d-924c-48e0-86b6-3ab637d5fa53` 成功通过正式 4v4 门禁并启动。真实 Chrome 端进入麦克风实时发言，服务端计时推进并出现 ASR 同步文本；虚拟媒体账号完成后续真人回合。
- 页面依次显示主持音频播放、Agent 发言和文字同步，比赛进入自由辩论。故意超时未开始发言后，安全暂停、三秒申请恢复和重新开始发言路径均通过。
- 三个 Playwright 媒体会话无 console error/warning；自动化临时快照、日志已清理，未保留凭据、Cookie、API key 或 LiveKit token。完整无人值守比赛、赛后归档/问卷、后台写入、大屏和压力仍未验收。

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
- 五轮正式 4v4 模拟完成 50 次发言，4 真人 + 4 Agent，暂停/恢复通过，拒绝 15 个过期回调；这是确定性本地证据，不等同于生产并发或 50 路 LLM 压测。
- 本地 LLM 容量门禁探针 50/50 合成 lease 成功、最大并发 50，第 51 个以 `llm_capacity_full` 拒绝；未产生供应商流量，不能替代真实压测。
- 赛后/问卷/实验定向测试 29/29 通过；赛制/规则/Prompt/论文实验规则测试 48/48 通过。
- Core formal room/persistence integration was rerun against an independent PostgreSQL database: 6/6 passed. Jobs PostgreSQL host-TTS integration was 2/2 passed. The local PostgreSQL container was stopped after verification and its data volume preserved.
- 完整 `test_experiment_integration.py` 循环在独立 PostgreSQL 通过 1/1，覆盖实验批次、阵容/排期、标注/问卷持久化与赛后任务创建；未写入生产。线上只读 preflight 确认 QA 后非终态比赛为 0。

## 2026-10-03 隔离生产比赛完成

- 重连三个合成参赛端后，真实 Chrome 房主申请恢复；三秒倒计时后自由辩论继续，隔离 match `73331c7d-924c-48e0-86b6-3ab637d5fa53` 自然进入 `已结束 / 本场辩论已完成`，未手动终止。
- 赛后记录显示 29 次发言、上下文版本 25；文字记录、下载、Opus 回放和 READY 人类原始音轨（89.4 秒）均可见。只读生产数据库确认比赛 FINISHED、29 个媒体文件和回放 READY，POSTMATCH_AUDIO 成功，24 小时 TRANSCRIPT_AUTO_ARCHIVE 待处理。
- 房主确认已有真人文字后，按钮变为禁用的“我的文字已提交”并提示“你的文字已经提交”；刷新等待后 AI 裁判区域仍显示评分任务尚未创建。
- 三个合成账号的 `/me/postmatch-surveys` 均显示没有单场问卷；房间快照为普通 `formal-4v4-standard`，问卷开关关闭、无启用 JUDGE 模块且 judge.enabled=false，因此没有 AI 裁判和单场问卷是预期行为。AI 体验问卷显示五个评分题和保存/提交控件，未提交个人问卷。
- 仍待：启用 `paper-experiment-4v4` 的实验批次（线上当前无排期）及其问卷/AI 裁判链路、线上 TTS fallback 事故、后台写入、五场并发和 50 路 LLM 压力。

## 2026-10-03 provider-chain production load revalidation

- Fixed `scripts/ops/provider_chain_probe.py` ASR readiness race: feed PCM concurrently before awaiting ready; local probe tests 2/2 passed. A temporary remote copy (deleted after use) produced TTS 5/5, ASR 5/5, LLM 50/50, LiveKit 5/5 and exit 0. Aggregate p50/p95/max ms: TTS 10693/10834/10834; ASR 15121/15178/15178; LLM 1839/2479/2722; LiveKit 293/557/557.
- Concurrent LiveKit Room destruction reproduced a native Tokio non-unwinding panic after successful connections (single room clean). Probe LiveKit cleanup is now sequential; application concurrency was not changed. Production `jx-core`, `jx-jobs`, `jx-web`, `jx-livekit` stayed active. ExternalCall evidence is aggregated OPS_PROBE counts only and contains no secrets.
- This closes the missing real provider-call evidence, but not the historical TTS fallback incident or the enabled/failed voice observation; those remain production risks.

## 最终复核（2026-10-03）

Provider、赛后/问卷/实验、规则和模拟器相关定向回归 81/81 通过；敏感信息扫描无结果，`git diff --check` 通过。生产临时探针与输出已清理，四个 systemd 服务 active、NRestarts=0。

## 2026-10-03 standalone Playwright revalidation

`node scripts/build-web.mjs` passed. Directly starting the generated standalone Web and invoking the same four Playwright desktop projects yielded 236 passed, 12 skipped, 0 failed out of 248; skipped cases are the explicitly retired historical experiment UI scenarios. The standard `pnpm test:browser` wrapper remains blocked before browser launch by pnpm 11 `ERR_PNPM_IGNORED_BUILDS`, so this evidence is labeled standalone browser regression.

## 2026-10-03 production experiment preflight

Read-only production query found zero P01-P18/E01-E03 accounts, zero experiment batches, and zero ScheduledMatch rows. The existing preparation script would create 21 accounts and 21 scheduled matches, so it was not run; the complete batch/roster/schedule/annotation/questionnaire/postmatch loop remains covered by the separate PostgreSQL integration test 1/1.

## 2026-10-03 experiment gate confirmation

Production `.env` reports `PAPER_EXPERIMENT_ENABLED=false`; no P01-P18/E01-E03 accounts, experiment batch, or schedule rows exist. The online experiment manual makes this a pre-recruitment gate requiring identity mapping and live human/audio evidence, so the QA run did not enable it or create research data.

## 2026-10-03 experiment-disabled browser boundary

Playwright CLI anonymous checks: `/experiments` returned 307 to `/lobby`, `/api/experiments/capabilities` returned `creation_enabled=false`, `history_readable=true`, `target_version=2.1.0`, and `/api/admin/experiments/batches` returned 401 `not_authenticated`. No production write was performed.

## 2026-10-03 experiment browser-scope audit

The 12 skipped Playwright cases are three `legacy UI retired` experiment setup/participant scenarios across four viewports, not runtime failures. Current disabled production behavior (redirect, capability false, admin 401) was separately verified with Playwright CLI.

## 2026-10-04 production anonymous smoke

Playwright CLI rechecked nine public routes. Ordinary routes loaded as expected; disabled `/experiments` redirected to `/lobby`, and anonymous invite entered the login boundary. The only console error was expected `/api/auth/me` 401; no writes or credentials were retained.

## 2026-10-04 production Core OOM finding

Core localhost live/ready remained 200 and the hero image hash still matched the rollback snapshot. Journal review found Linux OOM killer terminated jx-core on 2026-10-03 19:50; anon RSS was about 1.48 GiB, systemd restarted it at 19:50:23, and current `NRestarts=1`. A 50-second read-only watch held MemoryCurrent near 116.8 MB with available memory 956–997 MB. Track this as an unresolved production stability risk.

- OOM window read-only DB aggregation (18:30–19:51) found no new matches or ExternalCall rows, so the spike cannot currently be attributed to provider load or the QA match; root cause needs separate memory profiling.
- The OOM window also contained two `internal_exception` entries adjacent to media Range 206 requests at 19:06:11; logs are redacted, so root cause is unknown and recorded as a playback anomaly.

## 2026-10-08 voice and rule Agent management specification

- 用户要求所有赛制自动同步启用音色，并可独立编辑规则 Agent 启停。Spec 263 已获“实施，然后测试，然后上线”明确批准，并完成实现、测试与生产发布。
- 发现音色编辑、单项启停及批量启停直接覆盖规则 Agent.status；重新启用已有音色不补齐缺失的规则 Agent，无法保证独立停用偏好。
- 生产只读盘点：16 个启用、2 个停用 AGENT 音色；6 套配置默认模型的可编辑 4v4 规则各缺少 8 个对应 Agent，另有一套无默认模型测试规则无 Agent。拟保留稳定 ID 和现有配置，只补缺失关系，分开规则开关与全局音色可用性。
- 根目录 PRD 工作副本处于既有删除状态，本次从 Git HEAD 阅读相关要求，未覆盖该改动。

## 2026-10-08 production admin credential recovery

- 按用户明确授权，已将既有 admin 账号密码重置为用户指定值；仅持久化 Argon2id 哈希，未将密码写入代码、文档或日志。账号保持 ADMIN / ACTIVE，解除 5 次失败导致的登录锁定，并撤销 98 个旧会话。
- 事务内保留密码恢复审计，沿用既有会话和连接 lease 撤销方式；未更改认证逻辑、权限、migration 或业务历史。
- 公网验证：POST /api/auth/login 200，GET /api/auth/me 200 且返回 admin / ADMIN，GET /api/admin/overview 200；验证会话已通过 logout 200 注销。临时恢复脚本已清理。旧登录会话需要重新登录。

## 2026-10-08 Spec 263 production release

- 已统一音色驱动的赛制 Agent 同步：缺失关系自动补齐，音色编辑/全局启停保留各规则独立选择、模型、生成参数和 Prompt；管理页提供规则开关、有效状态与不可用原因。
- 生产非终态比赛 0 时发布，补齐 6 套赛制的 48 个 Agent；再次同步新增 0，后续预览缺失关系 0。无默认模型的旧测试规则保留并显示配置提示，未擅自设定模型。
- 验证：Core 392、Jobs 25、Web 214、独立 PostgreSQL 12、四视口专项浏览器 28 项全部通过。Lint、类型、契约、仓库生产 build 通过；保留 1 项既有 React Hook 警告。pnpm 运行时以进程设置避免既有未决依赖脚本的重复安装检查，未改变锁文件或依赖。
- BUILD_ID `3J93tV43x3F_GTGokW3Oi`；生产真实浏览器跨赛制开关验证通过，原配置已恢复，验证会话注销，三个桌面尺寸无溢出或页面错误。四服务 active、Core ready 与首页 200。
- root-only 回滚点：`/opt/jixia-spec263-20261008/backup`（DB 和旧 Core 文件）、`/opt/jixia-spec263-20261008/previous-web`（旧 Web）；不含对受保护环境文件的覆盖。历史数据与 migration 未改动。
- 既有 OOM 根因仍未定位；本次发布的正常重启及健康检查不代表该风险已关闭。问卷类型/格式门禁修复仅在本地，无业务语义变更，未覆盖生产问卷代码。
- 发布后连续 6 个健康样本均正常，Core 内存峰值 178.1 MiB，最近 5 分钟错误级日志记录 0；临时上传包、验证脚本和本地独立运行目录已清理，回滚点保留。

## 2026-10-10 Agent 思考中故障诊断

- 当前生产比赛 `d7925a79-9b56-41f5-bf6f-9539a69c4f8f` 处于 `RUNNING / AGENT_PREPARING`；最新 Agent generation、LLM streaming call 和 TTS call 都是 `STARTED`，没有完成时间或错误记录。
- 百炼网络本身可达：服务器到供应商的 TCP/TLS 建连成功，未认证探针在约 40–190 ms 返回预期的 401/404，连接 RTT 约 9 ms；因此证据不支持“API Key 或供应商网络整体不可用”。
- Core 进程复用了取消前遗留的按比赛持久化 TTS WebSocket。线上连接已空闲约 18 分钟、接收队列仍积压约 15 KB，代码在取消/重置路径没有丢弃该连接；这会让下一次恢复继续使用陈旧 socket。
- 代码级第二个阻塞点已本地复现：TTS 失败后，LLM 回调仍无条件向容量 32 的文本队列写入；TTS 消费者退出后队列填满，LLM 回调永久阻塞，因而停止消费供应商响应，generation 和两个 ExternalCall 都长时间保持 `STARTED`，前端显示“Agent 正在思考中”。
- 结论：本次现象是 Agent 编排的连接清理与失败传播缺陷，叠加此前真人端离线导致的取消/恢复；不是单纯 API 可用性问题。建议修复取消时关闭 TTS 连接、TTS 失败快速传播/有界写入、整条流水线总超时，并增加对应回归测试；本次诊断未修改线上比赛状态或部署代码。

## 2026-10-10 Agent provider stall recovery release

- 实现并验证 Spec 266：取消/重置时丢弃比赛级 TTS WebSocket；LLM 文本入队同时观察 TTS 任务失败，避免 TTS 退出后队列填满造成永久阻塞。
- Core 全量测试 420 passed、76 skipped；Agent/MatchActor 定向测试 49 passed；故障切片 22 passed；Ruff 和 Pyright 均无错误。
- 生产发布前 preflight 通过：迁移 `0044_rule_audio_generations`、非终态比赛 0、四服务 active、Core live/ready 200；仅替换 Core runtime 文件并重启一次，未改数据库。
- 发布后连续 6 个本地/公网健康样本均 200，jx-core active，最近 10 分钟 error 日志 0。回滚副本保留在 `/opt/jixia-spec266-20261010/backup/runtime.py`。

## 2026-10-10 主持音与 YXH 四辩诊断

- 用户报告主持开头吞音/衔接不足与 YXH 自由辩论后直接结束。只读生产确认 YXH revision 24 包含反方四辩 120 秒、正方四辩 120 秒和结束主持音；最新已完成 match `df919b6f-970e-4ef3-8cd7-9b6a151b13d2` 仍停在自由辩论 action index 6，后续四辩动作存在但未执行。
- 本地临时 Actor 模拟复现 `_finish_free_turn` 双方耗尽直接 FINISHED，即使后面还有总结动作；现有末段自由辩论测试未覆盖自由辩论后续动作。
- 主持前两段资产可解码且时长正确，第一处声音约 50 ms、尾部留白约 240 ms；Web 播放按已消耗时长无条件 seek，并在 host_audio_remaining_ms 更新时重建播放器，与 Core 在音频获取前已开始计时共同造成截头/重复加载风险。尚未完成真人首字音质试听。
- 已写 Spec 267（待批准），计划阶段推进修复与主持/Agent 服务端顺序媒体调度、800 ms 前留白/600 ms 后留白；记录现有浏览器主持路径与 TechDesign 单服务端输出的实现偏差。未写业务代码、未更改生产比赛或历史数据。

## 2026-10-10 production TTS match verification

- 通过线上 UI 创建并启动隔离纯 Agent QA 房间 `085102`，match `a4357c3c-4b0d-41e0-b3ed-344c4b590e49`，使用 YXH / 4v4 和当前百炼配置。
- 前 4 个 Agent 发言完整完成；4 个 LLM 与 4 个 TTS 调用成功，TTS 首结果均正常，4 个 `AGENT_RAW` 文件 READY，总计约 1.55 MB。页面连续显示 Agent 实时发言和同步文字记录，证明当前 TTS 语音链路可正常发言。
- 第 5 个 Agent 的 LLM 首 token 连续两次供应商失败（`llm_provider_failed`），系统在约 11 秒和 10 秒后分别收敛为失败/恢复保护，没有出现无限思考；该场随后由管理员控制台终止，未保留活动任务。

- 终态核对：match `TERMINATED`、活动比赛 0、活动后台任务 0；失败调用属于 LLM 首 token 阶段，不能归因于 TTS 不可用。QA 房间和比赛记录保留用于诊断，未删除历史数据。

# 2026-10-11 Spec 271 音色兼容问题（实施中）

- 线上比赛 `2dce08b6-b4f9-40c5-b99a-3e01eef0ba28` 的恢复倒计时和 Agent 重启均成功，
  但两次 Agent 发言均在 TTS `task-failed` 后失败。LLM 调用有供应商 request id，TTS
  返回事件为 `InvalidParameter`；因此不是恢复状态机、网络或 LLM 首 token 根因。
- 冻结配置中的 `longrongtaolian_v3.1` 不在百炼 `qwen-audio-3.1-tts-flash` 官方系统音色
  列表；直连复现约 10.2 秒后失败，`longyingtao_v3.1` 直连约 0.56 秒成功。官方说明
  音色必须属于当前模型：[Qwen-Audio-TTS 音色列表](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list)。
- 已添加 TTS 参数错误分类、LLM 对 TTS 回调异常的保真传播、四个已实测历史错误音色的
  显式 3.1 映射、生产资源创建/更新拦截和回归测试；未修改比赛冻结快照或数据库。
- 已部署 Core 修复，备份为 `/opt/spec271-20261011184127`；四服务 active，Core live/ready
  和公网首页均 200。目标比赛恢复后 sequence 10→24，状态 `RUNNING`，通识 Agent 的实际
  TTS 音色为 `longyingtao_v3.1`，首音约 224ms，随后 `longwan_v3.1`、`xiaxiaochen_v3.1`
  等调用均成功，未再次进入 ERROR。
- Web 错误文案源文件已更新；本机 `pnpm build` 因当前依赖安装门禁
  `ERR_PNPM_IGNORED_BUILDS` 阻断，线上修复不依赖该 bundle。当前仍需在依赖门禁恢复后
  发布 Web 文案 bundle，并在现场确认实际扬声器听感。

## 2026-10-12 多场景重复模拟与风险审计（Spec 272）

- 100 场正式 4v4 内存状态机模拟完成 1000 次发言，暂停/恢复和 300 个过期回调全部收敛；
  100 场五真人模拟完成 500 次发言，非法 UUID、timer 故障和 400 个过期回调全部收敛。
  两类各并发 5 份再次运行，分别完成 500 轮/5000 次和 500 轮/2500 次合成发言。
- 31 个 Agent/ASR/暂停/超时/重复命令场景重复 25 轮，775/775 通过。50 路本地限流突发
  50/50 完成，最大模型并发 4，释放后可继续取得 lease。以上均为内存/合成测试，不是
  真人音频或供应商压力证据。
- Core 非集成 437、Jobs 25、Web 单元 219 通过。浏览器全量 260 项为 228 通过、12
  跳过、20 失败；失败依赖本地 `127.0.0.1:8000` Core 或真实音频元素，隔离 PostgreSQL
  未启动，不能作为产品回归通过。`pnpm test:tooling` 另失败于发布预检 migration head
  仍为 0044 而仓库 head 为 0045，以及 Web React Compiler lint 的 2 个错误。
- `contracts:check`、Ruff、Pyright 和仓库生产 `pnpm build` 通过。Storybook 94 项中 86
  通过、8 失败，失败集中在房间准备按钮和真实音频元素故事，需在 Core/fixture 前置恢复
  后重跑。
- 故障注入确认三个适配器风险：LLM 首正文前持续 SSE 心跳会按行重置首 token 预算并在
  外部保护超时前不返回；ASR 持续 heartbeat 且 sender 不结束时 30 秒接收窗口会不断重置；
  LLM 正文后无 `[DONE]` 直接 EOF 会返回成功。三项均需独立规格补总截止时间、心跳预算和
  截断流失败语义；本轮未改生产代码或注入线上。

## 2026-10-12 Spec 273 provider 流收敛与发布门禁完成

- LLM SSE 改为绝对首 token 总预算，空白/注释/heartbeat 不再续期；正文后必须收到 `[DONE]`，
  截断 EOF 归一为 `llm_stream_interrupted`，取消与 TTS 回调错误传播保持不变。
- ASR 增加整段任务截止时间，sender 与 receiver 并行等待并使用有界发送；持续 heartbeat、
  sender 阻塞或异常均能清理 socket 并收敛到稳定超时/协议错误。修复 PCM 帧发送位置错误，
  保证所有帧按序发出。
- 管理 CLI 和发布 preflight 动态读取 Alembic 当前 head；规则音频审核状态、阶段编辑 flush、
  归档音色保留规则内启停偏好；正式 4v4 数据库夹具不依赖种子顺序。Web 按钮禁止文字换行，
  紧凑视口下语音控制栏高度稳定；pnpm 生命周期脚本逐项声明，工具链门禁恢复。
- 最终验证：Core 非集成 448/448、隔离 PostgreSQL 集成 76/76、Jobs 25/25、Web 219/219、
  Storybook 94/94、Playwright 248 通过/12 跳过/0 失败、Node tooling 50/50、QA 13/13、
  release-state 9/9；contracts、lint、typecheck、Ruff、Pyright、生产 build 全通过。
- 本轮只使用合成故障与隔离数据库，未部署或修改生产环境；未覆盖真实供应商压力、真人音频
  盲听和生产发布，这些仍需独立变更窗口验收。浏览器代理日志中的 Core `ECONNREFUSED`
  为 mock 场景预期噪声，断言全部通过。

## 2026-10-13 Spec 274 上下文内存与 TTS 流预算完成

- Agent 运行时把完成 token 数限制在 32768 以内，并阻止 generation 参数中的 `max_tokens`
  覆盖按发言时长计算的硬预算；历史上下文只保留最近完整记录至 24,000 字符预算，截断
  会插入系统诊断标记，不回写历史。Agent 和 Judge 的数据库历史查询均限制为最近 128 条，
  再应用字符预算，避免长赛程把全量 Speech 记录装入单次请求。
- Core/Jobs TTS 增加 180 秒绝对任务截止、16 MiB 音频上限和 128 条事件采样；握手阶段的
  heartbeat、持续音频、静默接收、发送/接收异常均会结束任务并清理连接/临时文件。
  新增 `tts_task_timeout`、`tts_audio_too_large`、`llm_output_too_large` 和
  `llm_stream_interrupted` 稳定错误及 Web 文案。
- 比赛运行时终态 Actor 在 60 秒保留期后淘汰；管理器关闭时清空 Actor、订阅、回调和延迟
  任务引用，Presence 关闭比赛级连接；异常 WebSocket 握手回滚订阅/租约，CLI WebSocket
  帧大小限制为 256 KiB。赛后 Judge、transcript/postmatch 响应同样使用有界历史/文字预算；
  导出音频限制为 16 MiB/文件、64 MiB/场，Voice Preview 并发限制为 2，LiveKit webhook
  验签前限制请求体。ASR segment/capture 回调在事务内锁定并复核 speech attempt、epoch、
  context、机会代次和 task/call 关联，队列满后的失败回调也有 10 秒 watchdog 并主动中止。
- 本地剖析证据 `profile_output/spec274-memory-profile.json` 覆盖 1/10/25/50/100 轮合成
  单 Actor 比赛；另有 1000 个终态 Actor 的合成 registry 清理验证。它们证明清理路径有效，
  不等同于真实供应商压力或生产跨场次长期运行。
- 验证：Core 非集成 468/468、隔离 PostgreSQL 集成 76/76、Jobs 32 通过/2 跳过、Web 222/222、
  Storybook 94/94、Playwright 248 通过/12 跳过/0 失败、Node tooling 50/50、QA 13/13；
  contracts、lint、typecheck、Ruff、Pyright、生产 build 全通过。隔离 PostgreSQL 集成测试
  已在串行运行下通过，数据库随后停止。
- 只读线上复核（2026-10-12 UTC）：四个 systemd 服务 active，Core live/ready 均 200，Core
  RSS 约 80 MiB、fd 35、cgroup `memory.events` 的 `oom_kill=0`；历史内核 OOM 记录仍显示
  2026-10-03 Core 的 `python3` 达到 total-vm 约 6.2 GiB、anon-rss 约 1.41 GiB，其他同刻
  进程仅为 MiB 级，根因尚未通过线上长时剖析确认。10 月 11 日服务日志还出现多次
  `tts_connection_cleanup_timeout`；本地已把 TTS socket close/discard 改为有界清理并加入
  hanging-close 回归测试（Agent voice 54 项通过），但尚未部署线上。
- 生产 Core OOM 根因、真实供应商长时压力、真人音频盲听和变更窗口发布仍未完成，不能把本地
  故障注入证据表述为生产稳定性关闭。

## 2026-10-13 Spec 274 资源清理与大文件哈希加固

- Core/Jobs 的供应商 WebSocket close/discard、Agent/Host decoder、AudioSource 和 LiveKit
  room 关闭均有独立的有界清理；超时会摘除资源引用、取消并消费清理任务结果，记录稳定的
  `agent_resource_cleanup_timeout`/`agent_resource_cleanup_failed` 诊断，不阻塞比赛 Actor、
  重试或 Jobs worker。新增 hanging provider/native-resource 回归测试。
- Jobs 导出包、Host TTS 和实验导出不再用 `read_bytes()` 做 SHA-256，统一使用 1 MiB 分块
  哈希，避免大文件哈希阶段创建第二份整文件内存副本。
- 本轮增量验证：Core 非集成 468/468，Jobs 32 通过/2 跳过；`pnpm lint`、`pnpm typecheck`、
  `pnpm contracts:check`、Ruff、Pyright 和 `git diff --check` 通过。此前 Web 222、Storybook
  94、浏览器 248/12 skip、Node tooling 50、QA 13 及生产构建证据仍有效。
- 线上只读复核仍显示 Core 当前约 88 MiB RSS、约 97 MiB swap，cgroup `oom_kill=0`；
  2026-10-03 的历史 OOM 仍是 Core python3（anon-rss 约 1.41 GiB），尚未通过线上长时
  剖析确认唯一根因。本轮代码未部署、未重启生产服务。

## 2026-10-13 Spec 275 终态房间清理与生产内存护栏

- 生产只读审计把 OOM 关联链路进一步收窄：对应比赛在 14:01（Asia/Shanghai）已结束，
  同一 LiveKit room 到 19:51 才结束，终态后存活约 5 小时 49 分；期间共有 36 次参与者
  会话（26 Agent、9 真人、1 ASR）。同一批终态房间普遍 `rooms.ended_at IS NULL`，而
  `matches.ended_at` 已有值；线上 Core 只读日志还记录了多次 `agent_cleanup_timeout`、
  `tts_connection_cleanup_timeout` 和 `offline_timer_stale`。这支持“终态清理链路不闭合”
  是高优先级原因，但尚未有对象级 heap snapshot 证明唯一泄漏对象。
- `_commit` 现在在写入 Match 终态的同一事务中写入关联 Room 的 `ended_at`；Core 启动增加
  幂等校正，只处理终态且缺失时间的 Room，优先使用关联 Match 的结束时间，不删除
  `RoomConnection` 的 epoch 高水位记录。新增 `reconcile_terminal_room_end_times` 回归测试。
- AgentRuntime 增加 4096 条有界终态比赛 tombstone；`start_agent`、主持音和自由辩论决策
  在 `close_match` 后拒绝启动，避免排队的终态启动任务重新建立 LiveKit 会话。ASR/Agent/Host
  资源关闭继续使用有界取消并消费迟到异常。新增终态 fence 测试。
- 本轮代码验证：Core 非集成 `488 passed, 76 deselected`，隔离 PostgreSQL 集成
  `76 passed, 488 deselected`，Jobs `37 passed, 2 skipped`，QA `18 passed`；Web 单元
  `222 passed`、Storybook `94 passed`、浏览器 `248 passed, 12 skipped`；`pnpm lint`、
  `pnpm typecheck`、`pnpm contracts:check`、生产构建、Ruff、Pyright、`git diff --check`
  均通过。
- 当前生产仍只读观察：Core RSS 约 90 MiB、swap 约 97 MiB、cgroup `oom_kill=0`，服务未
  重启。尚未执行生产发布、数据库校正或 systemd 配置。发布前需校准 `MemoryHigh/Max`、
  将 Core 软/硬 `nofile` 一起设为目标值，并按分钟观测 RSS/swap、Actor/任务/队列、LiveKit
  房间和 WebSocket 数量。实验批量导出风险已由 Spec 276 的流式写入与容量护栏覆盖，仍需
  在受保护窗口用代表性大批次校准阈值。

## 2026-10-13 Spec 276 实验批量导出与排行榜内存护栏

- `EXPERIMENT_BATCH_EXPORT` 生产路径现在使用 SQLAlchemy async stream 逐行写入 ZIP；CSV
  保留 UTF-8 BOM、列顺序与公式转义，JSONL 保留一行一条记录，manifest 记录行数、未压缩
  字节数和 SHA-256。每行、每文件、整批上限分别为 4 MiB、64 MiB、256 MiB，超限使用稳定
  错误码并清理临时 ZIP；旧内存 helper 仅保留兼容/测试用途且也有边界。
- 每 256 行让出事件循环，并在成功、异常、取消路径关闭数据库游标；排行榜 worker 同样
  改为流式读取，分数使用 sum/count，不再保留全量 scores 列表。
- 新增实验导出 2 项、排行榜 1 项回归测试；本地 `apps/jobs/tests/test_experiment_worker.py`
  6 项与 leaderboard 相关 7 项通过。未进行真实大批次压力或生产发布。
- 生产环境仍未写入、未 reload systemd、未重启服务。受保护发布前需执行只读 preflight，
  再观察 Jobs RSS、临时磁盘、导出时延、LiveKit/连接租约和 Core OOM 计数。

## 2026-10-13 Spec 277 迟到回调与启动清理的有界查询

- Agent/ASR 迟到回调改用数据库端 source speech 精确条件、`LIMIT 1` 和行锁，保留旧 JSON
  数据兼容分支；启动时终态房间时间校正改为集合更新，旧连接租约按 500 条批次清理，避免
  把整张历史表 materialize 到 Core 内存。
- 定向连接/Match 服务测试 28 项通过；完整 Core 非集成 `488 passed, 76 deselected`，
  隔离 PostgreSQL 集成 `76 passed, 488 deselected`。
- 本轮未写生产数据库、未重启服务；发布前仍需在受保护窗口执行只读 preflight。

## 2026-10-13 Spec 278 管理员问卷流式展示与导出

- `/api/admin/surveys/personal` 的 `responses` 保留原有 JSON 形状，但改为 SQLAlchemy
  async stream 逐条编码；个人与赛后问卷 JSON/CSV 导出改为 `StreamingResponse`，不再把
  全量 ORM 行、rows 列表和完整 CSV/JSON 字符串同时保留在 Core RSS。导出审计只使用
  `count(*)`，每个流式结果在成功、异常和取消路径关闭。
- 新增 `apps/core/tests/test_survey_routes.py`：验证个人展示 JSON 形状、JSON/CSV 字段和
  游标关闭，专项路由/服务测试共 16 项通过。`uv run ruff check` 与 `uv run pyright` 对本切片通过。
- 生产未写入、未重启、未发布；未完成真实大批次导出压测。参与者赛后问卷列表仍有全量任务
  与逐场 speech 投影的规模风险，需后续独立分页/游标规格。

## 2026-10-13 Spec 279 单场研究导出行数护栏

- `MATCH_EXPORT` 的 speeches、match events、provider calls 和 export items 查询现在请求
  一个 sentinel 行并执行固定上限（分别 8,192、16,384、16,384、100）；超限以
  `export_match_rows_too_large` 失败，避免异常比赛把多个无界查询同时 materialize 到 Jobs
  内存。正常 ZIP 字段、排序和 provider 内容处理保持不变。
- 参与者 `/api/me/postmatch-surveys` 已完成只读风险审计：当前数组契约会加载用户所有任务，
  并为每个比赛读取 speeches/participants；正式 4v4 的单场记录有业务边界，但历史任务总量
  没有分页上限。分页/游标与批量投影需另立规格，本轮未改变接口语义。
- 新增超限回归测试；Jobs 全套 `37 passed, 2 skipped`，Ruff、Pyright 和 diff-check 通过。本轮
  未写生产数据库、未重启或发布。

## 2026-10-13 Spec 280/281 参与者问卷分页与 LiveKit 预检形态修正

- `/api/me/postmatch-surveys` 现在按 `created_at,id` 提供默认 25、上限 50 的稳定游标分页，
  通过 `X-Next-Cursor` 返回下一页；列表默认摘要投影，详情只读取目标任务。Match、Room、
  MatchParticipant 和 Speech 改为批量读取，Speech 使用列投影、每场 512 sentinel、整页
  16,384 行和 64 MiB 文本预算，超限返回脱敏 `survey_history_too_large`；非法游标返回
  `survey_query_invalid`。Web 使用 `useInfiniteQuery` 和“加载更早的比赛”，旧数组响应兼容。
- 修正 FREE_DEBATE 摘要模式的答案可见性：可见目标 ID 独立于 `items=[]` 计算，不会因摘要省略
  语音项目而错误隐藏已允许的答案。专项 Core survey 测试 `20 passed`，Core 非集成当前
  `492 passed, 76 deselected`，正确驱动的隔离 PostgreSQL 集成 `76 passed, 492 deselected`，
  Web `222 passed`；Ruff、Pyright、contracts、生产构建和 diff-check 通过。Playwright 全量
  `248 passed, 12 skipped`；测试中的 Core `ECONNREFUSED` 是本地 Web mock 代理预期噪声。
- 发布预检新增 Spec 281：Core/Jobs/Web 仍需 systemd active；LiveKit 接受 `jx-livekit` 或
  `livekit.service` active，或严格校验名为 `jx-livekit`、running 且镜像前缀为
  `livekit/livekit-server:` 的 Docker 容器。容器缺失、停止或镜像不匹配均 fail-closed，
  不执行任何启动/重启/拉取。shell 语法和 QA `20 passed`（含伪造 systemd/Docker 命令）通过。
- Spec 282 让发布预检额外输出非终态比赛按 `status` 的整数计数，同时保留总数必须为零的
  门禁；不输出房间、用户或凭据。QA 相关测试 `16 passed`，shell 语法、Ruff 与 diff-check
  通过。当前线上只读结果为 `PAUSED=1`，因此门禁仍明确阻断。
- 清理本轮工作树的格式门禁：Prettier 现在对应用、契约、脚本和根 README 全部通过；原始
  长行辩论转录 `a.md` 加入 `.prettierignore`，保留源文本不重排。随后 Web lint/typecheck、
  生产 build、contracts、Node tooling `50 passed`、QA `20 passed` 和 diff-check 均通过。
- 格式化后再次运行 Storybook `94 passed` 和 Playwright `248 passed, 12 skipped`；浏览器代理
  到未启动本地 Core 的 `ECONNREFUSED` 仍是 mock 场景预期噪声，不代表线上 Core 验证。
- 线上仍只读且未发布：当前生产 LiveKit 是 Docker 容器；Core/Jobs 进程软 nofile 仍为 1024，
  MemoryHigh/MemoryMax 未配置，历史终态房间的 `ended_at`/租约仍需受保护窗口修复。未连接或
  修改生产 Docker、systemd、数据库、服务或外部 provider。

## 2026-10-13 Spec 285 标注任务分页与批量查询

- 参与者 `/api/experiments/annotation-tasks` 和专家 `/api/experiments/expert-tasks` 现在按
  `created_at,id` 使用默认 25、上限 50 的稳定游标分页，并以 `X-Next-Cursor` 返回下一页；
  非法游标返回 `experiment_query_invalid`，旧数组响应字段保持不变。
- 单页任务的比赛尝试、标注项目、机会、语音、答案、问卷及专家答案改为批量 SQL 查询，
  消除逐任务 N+1；Web `fetchTaskPages` 自动合并多页，详情/保存/提交调用路径不变。兼容的
  服务 helper 仍按页批量加载完整历史，HTTP 路由不会无界 materialize。
- 实验单元测试 9 项、相关 Ruff/Pyright 通过；尚未在生产写入、重启或发布。受保护窗口需用
  独立 PostgreSQL 做真实多页查询计数和响应大小验证，再运行线上预检。

## 2026-10-13 Spec 287 赛后音频回放输入与 ffmpeg 上限（由 Spec 289 修订）

- `POSTMATCH_AUDIO` 现在先检查运行时快照逻辑 UTF-8 字节数（4 MiB），以 sentinel `LIMIT 513` 读取已完成
  语音，并限制动作 512、源 1,024、单输入 16 MiB、总输入 256 MiB 和路径 4,096 字符；超限使用
  稳定错误码，避免异常比赛把 Jobs 内存和 ffmpeg 参数无界放大。
- ffmpeg 继续使用参数数组并加入 64 MiB `-fs` 临时输出上限，发布前拒绝达到上限的截断文件；
  正常回放顺序与 READY/FAILED 闭环保持不变。`test_audio_worker.py` 5 项、Ruff、Pyright 通过。
- 本轮未写生产数据库、未重启或发布；受保护窗口需用代表性大比赛验证长时 ffmpeg、临时磁盘、
  Jobs RSS 和回放成功率，再执行发布预检。

## 2026-10-13 Spec 286 实验赛后标注 payload 有界化

- `EXPERIMENT_POSTMATCH` Jobs 路径对 speeches 和 free-debate opportunities 使用 4,096 行
  sentinel；发言显示文本总量上限 64 MiB，单个冻结上下文 8 MiB，机会上下文总量 64 MiB。
  JSON 预算按 UTF-8 紧凑编码计算，非对象 JSON 和超限均使用稳定
  `experiment_postmatch_payload_too_large` 失败，避免历史复制到机会/专家答案时无界放大。
- Core 兼容物料化路径使用相同边界，超限抛出带 `code` 的
  `PostmatchAnnotationWorkError`；事务中的已插入标注项会随异常回滚，不产生部分成功。
- Core/Jobs 现在在完整读取 speeches 和 opportunities 之前执行聚合预检；发言只统计有
  `opportunity_id` 的行，JSONB 字节按 `::text` 的逻辑 UTF-8 大小计算，避免压缩后的
  `pg_column_size` 绕过护栏；sentinel 查询保留为并发变化的二次防线。Jobs 专项测试 11 项、
  Core 实验测试 12 项、隔离 PostgreSQL 赛后集成 1 项通过，Ruff/Pyright 通过；本地未写生产
  数据库、未重启或发布。受保护窗口需用真实长比赛/大 JSON 校准阈值，并观察 Jobs/Core RSS、
  事务时延和任务创建数量。

## 2026-10-13 Spec 289 赛后音频取消清理与逻辑大小边界

- `_run_ffmpeg` 改用 `stderr=DEVNULL`，超时和 `CancelledError` 都执行有界 kill+wait；取消
  保留给调用方，避免 ffmpeg 子进程和 stderr 管道残留。真实 `sleep` 子进程取消复验无残留，
  Jobs 音频专项测试 10 项通过。
- 运行时快照大小改用 PostgreSQL `octet_length(runtime_snapshot::text)` 的逻辑 UTF-8 字节，
  并在第二次读取时重复条件，压缩 JSONB 不能绕过 4 MiB 护栏。音频源路径按主持/辩手配置根
  解析，拒绝路径穿越、符号链接逃逸和过长路径；根内缺失文件仍跳过。
- 规则允许的估算时长上限为 90 分钟，原始人类 PCM 约 172.8 MB；回放总输入预算调整为
  256 MiB，保留 Agent/主持音频余量，输出仍限制 64 MiB。该输入预算是 Jobs/ffmpeg 护栏，
  不截断业务音频。
- Spec 289 尚未写入生产、重启或发布。验证：Jobs 音频专项 `10 passed`、Jobs 全集 `50 passed, 2 skipped`，Ruff、Pyright、
  `git diff --check` 通过；真实长规则回放与受保护窗口观察仍待执行。

## 2026-10-13 Spec 288 标注响应二级载荷与专家音频授权护栏

- 参与者/专家标注列表默认 25 条、上限 50 条；每个任务最多 512 个项目、2,048 个答案，
  单任务载荷 8 MiB、单页总载荷 64 MiB。Core 先用数据库 `count` 与
  `octet_length(jsonb::text)` 聚合预检，再读取关联 JSON/文字，超限返回脱敏
  `experiment_annotation_payload_too_large`（HTTP 413），不截断历史。
- 专家音频授权读取最多 4,096 个答案、64 MiB 逻辑 UTF-8 JSON；Web 每页请求 25 条，最多
  自动读取 4 页，并拒绝重复游标，避免任务历史或恶意游标造成无界请求。
- Core 赛后标注物料化和 Jobs 任务在完整 payload 查询前增加行数、文字和 JSON 字节聚合预检；
  专项 Core `test_experiments.py` `15 passed`、实验 PostgreSQL 集成 `1 passed`，Web 全套
  `226 passed`，变更文件 Ruff/Pyright/Prettier 通过。
- 本地未写生产数据库、未重启或发布；受保护发布前仍需观察真实实验批次的响应大小、Core RSS、
  Jobs RSS 和超限任务错误闭环。

## 2026-10-13 最终有界化静态复审

- Specs 284–291 的本地单元、Web、构建和数据库集成验证已通过；未发现新的阻断性代码问题。
- 兼容服务 helper、批次/预约 Web 便利客户端和参与者答案写入现在分别有页数/行数、游标、
  JSON 字节与请求键数护栏；超过预算会显式失败，不会静默返回截断历史。HTTP 分页仍可供调用方
  逐页读取，不能把便利层失败误认为数据丢失。
- `resolve_cleanup_path` 现在拒绝根目录本身、路径穿越、符号链接逃逸、路径解析异常和过长路径，
  避免单条坏记录让整个文件清理批次进入重试。

## 2026-10-13 Spec 290 标注答案请求体护栏

- `ParticipantAnnotationAnswerSaveRequest.answers` 现在最多 4 个键，紧凑 UTF-8 JSON 不超过
  64 KiB；超限在请求模型阶段以 422 拒绝，不进入标注事务。Core 标注测试 17 项、契约检查、
  Ruff 和 Pyright 通过。
- 批次/预约兼容 helper 已按有界页逐页恢复完整数组语义；HTTP 路由仍只返回当前页。线上未写入、
  未重启或发布，需在受保护窗口运行迁移/服务预检后再发布。

## 2026-10-13 Spec 291 实验历史全量聚合护栏

- 批次/预约 HTTP 分页仍可按游标完整读取；Core 兼容 helper 和 Web 全量便利层不静默截断，
  但在 200 页、10,000 条或 Web 16 MiB UTF-8 聚合预算耗尽时 fail-closed，避免合法增长或
  唯一游标异常造成无限追页与内存放大。
- Web 额外校验每页响应必须为数组，并拒绝游标不前进/重复；Core 以
  `experiment_history_too_large`（HTTP 413）区分历史量超限，保留 `experiment_query_invalid`
  处理游标回环。
- Core 实验专项 19 项、Web 全套 228 项、Ruff、Pyright、Prettier 与 diff-check 通过；未写生产、
  未重启或发布。历史超过便利层预算时应直接使用分页接口或后续摘要界面，不能将失败误认为数据丢失。

## 2026-10-13 Spec 292 Web 标注任务聚合响应护栏

- 参与者/专家任务的 Web 自动聚合继续每页请求 25 条、最多追踪 4 页，同时校验每页响应为
  数组；跨页累计最多 100 条、64 MiB 紧凑 JSON UTF-8 字节。超出任一预算或响应形状异常时
  fail-closed，不返回部分任务历史；重复游标和页数上限语义保持不变。
- 新增 Web `experiments-api` 回归覆盖非数组响应、跨页条数溢出、跨页 UTF-8 字节溢出；专项
  与 Web 全套 Vitest、lint/typecheck、Prettier、生产构建、contracts 和 diff-check 已复跑通过。
  未写生产、未重启或发布；超出便利层预算时使用 HTTP 游标分页。

## 2026-10-13 Spec 293 标注历史兼容 helper 有界聚合

- `ExperimentService.list_participant_tasks` 与 `list_expert_tasks` 兼容 helper 现在按现有
  降序 `(created_at, id)` 游标逐页恢复完整数组，但最多 200 页/10,000 条；超限抛出
  `experiment_history_too_large`，不返回截断结果。
- 每次续页都会拒绝重复游标和未向更旧记录前进的游标，统一抛出
  `experiment_query_invalid`；HTTP 分页接口和数据库 schema 未改变。
- `apps/core/tests/test_experiments.py` 新增参与者/专家两类 helper 的行数、页数、重复和不前进
  游标回归；该专项共 `27 passed`，变更文件 Ruff、Core service Pyright 与 diff-check 通过。
- 本规格仅完成本地实现；未写生产数据库、未重启或发布，待受保护窗口与 Specs 275–292 一并
  进行发布前预检。

## 2026-10-13 最终本地验证与线上只读快照

- Core 非集成 `517 passed, 76 deselected`，Core 实验专项 `31 passed`；Jobs 非集成
  `50 passed, 2 deselected`，Web Vitest `231 passed`，QA 工具 `27 passed`（其中运行时审计
  `11 passed`），Storybook `94 passed`；生产构建、OpenAPI contracts、
  ESLint、TypeScript、Ruff、Pyright、Prettier 和 `git diff --check` 全部通过。
- `pnpm test` 聚合门禁完整通过（此前完整聚合基线 tooling Node `50 passed`、QA `25 passed`、
  Web `231 passed`、Core `516 passed`、Jobs `50 passed`）；新增 Spec 297 后 Core 增量回归
  `517 passed`，QA 增量回归 `27 passed`，数据库实验集成另有 `1 passed`。
- Playwright 浏览器回归 `248 passed, 12 skipped`；日志中的 `127.0.0.1:8000 ECONNREFUSED`
  是预期的无 Core 测试桩告警，未导致用例失败。
- 形式化 4v4 模拟器 `--rounds 100` 完成 100 轮、1,000 次发言并拒绝 300 个过期回调；
  31 个语音可靠性场景全部通过（31/31），均为纯内存模拟，不代表真实供应商或 LiveKit 压力。
- 线上最新只读复核：Core/Jobs/Web/LiveKit active，版本与健康端点正常，数据库已在
  `0045_postmatch_showcase`；仍有 1 个 `PAUSED` 比赛、3 个非终态房间和 62 个终态房间缺少
  `ended_at`，活动租约当前为 0。Core/Jobs 进程 soft `nofile` 仍为 1024（hard 524288），
  MemoryHigh/Max 未设置。未执行生产写入、重启或发布。

## 2026-10-13 Spec 294 实验历史游标排序与问卷 q6 预算

- `list_batches` 与 `list_appointments` 兼容 helper 现在解码续页游标，并按各自 SQL 排序
  严格验证单调推进；重复、解码失败或排序回退立即返回 `experiment_query_invalid`，按
  解码后的值去重，避免通过 padding 变化绕过回环检测。预约 `scheduled_at ASC NULLS LAST`
  的有时间值到 NULL 桶转换和 NULL 桶内 slot 递增均有明确校验。
- `_assert_participant_child_budget` 在读取问卷 ORM 行前用 PostgreSQL `octet_length`
  统计 `MatchQuestionnaire.q6` UTF-8 字节，并纳入单任务 8 MiB、整页 64 MiB 预算；超限
  仍以 `experiment_annotation_payload_too_large` fail-closed，不 materialize 超大自由文本。
- 新增 Core 回归覆盖批次/预约游标回退、预约 NULL 桶和 q6 超限。Spec 294 本地实现完成，
  未写生产数据库、未重启或发布，待受保护窗口与前序 Specs 一并预检。

## 2026-10-13 Spec 295 运行时 cgroup 峰值只读审计

- `scripts/ops/runtime_guard_audit.py` 在保留 nofile 与可选 MemoryHigh/MemoryMax 门禁的基础上，
  只读采集 Core/Jobs `ControlGroup` 下的 `memory.current/peak`、swap current/peak、
  `memory.events oom_kill` 和 `pids.current/max`；缺失、权限错误或路径越界统一输出
  `unavailable`，未知事件键、Environment 和原始文件内容不进入报告。
- 新增显式 `--require-cgroup-telemetry`（预检可用 `JX_PREFLIGHT_REQUIRE_CGROUP_TELEMETRY=1`）
  对缺失快照或非零 `oom_kill` fail-closed；默认保持关闭，避免把尚未校准的生产环境误报为
  已修复。脚本不写 cgroup/systemd、不 reload、不重启。
- Spec 295 本地实现完成，QA `test_runtime_guard_audit.py` `11 passed`，`scripts/qa/tests`
  总计 `27 passed`；Ruff、Pyright、shell 语法和 `git diff --check` 通过。未写生产、未重启。
- 只读生产证据：主机 `MemTotal=1,932,260 kB`、SwapTotal≈4 GiB；10 月 3 日内核明确记录
  `task_memcg=/system.slice/jx-core.service`，Core Python `anon-rss=1,479,204 kB` 且当时
  Free swap 仅 144 kB。当前服务重启后的 cgroup 峰值 Core≈203 MiB、Jobs≈146 MiB，
  `oom_kill=0` 不能覆盖历史峰值。MemoryHigh/Max 仍未设置，候选限额必须先经 staging 压测。
- 将本地脚本通过 SSH stdin 在生产只读执行（`--require-cgroup-telemetry`）：telemetry 字段均
  可读且两服务 `oom_kill=0`，但 Core/Jobs 进程 soft `nofile=1024` 使审计按预期 fail；未写入
  生产文件、数据库、systemd 或 cgroup，未重启服务。

## 2026-10-13 Spec 296 运行时 cgroup 路径异常闭环

- `_read_cgroup_file` 现在捕获 `Path.resolve()` 的符号链接循环和其他解析异常，统一返回
  `unavailable`，不把路径或原始文件内容泄漏到审计输出。
- 新增循环符号链接回归；运行时专项 `11 passed`，完整 QA 当前 `27 passed`。未写生产或
  改变发布门禁默认值。

## 2026-10-13 Spec 297 Agent 发言暂停恢复决策栅栏

- Agent 发言/收尾暂停后恢复为 `AGENT_PREPARING` 时，清除旧的决策轮次、Agent/真人手队列、
  选择状态和中间文本；恢复事件随后重新发起权威准备流程，迟到旧回调无法复用旧选择。
- 原先“Bug 4”模拟中的 TODO 伪通过已改为真实断言；暂停恢复专项 20 项、语音可靠性测试
  7 项和 Core 全非集成 `517 passed, 76 deselected` 通过。未写生产、未重启或发布。
- 同时复跑服务器现有旧版 preflight：健康与服务均通过，但脚本硬编码 `0044`，因此把当前
  数据库 `0045_postmatch_showcase` 误报为迁移失败；更新后的本地 preflight 动态读取 checkout
  head，发布前必须先替换旧脚本，不能把该旧版失败当作数据库回滚证据。

## 2026-10-13 发布门禁复审遗留项

- Spec 283 运行手册要求发布预检报告并阻断 `nonterminal_rooms` 与 `nonterminal_matches`；
  当前 `scripts/ops/preflight_v2_1.sh` 仅统计非终态比赛，尚未统计非终态房间。线上只读快照仍
  有 3 个非终态房间，因此即使暂停比赛清零，也必须先补齐房间计数门禁后再进入发布窗口。
- 本轮仅做文档、规格状态和测试证据闭环，未写生产数据库、systemd、cgroup 或比赛状态。

## 2026-10-13 Spec 298 非终态房间发布门禁

- `preflight_v2_1.sh` 现在统计 `rooms.status NOT IN ('FINISHED', 'TERMINATED')` 及按状态计数，
  非终态房间不为零时与非终态比赛一样 fail-closed；这覆盖独立的 `WAITING/PAUSED` 房间，
  不会只因比赛表为空而放行重启。
- QA 覆盖预检源码中的房间计数与只读门禁；本地尚未写生产、重启或执行修复。线上当前
  3 个非终态房间仍是发布阻断项。
- Spec 298 后重新执行 `pnpm test`：tooling Node `50 passed`、QA `27 passed`、Web
  `231 passed`、Core `517 passed, 76 deselected`、Jobs `50 passed, 2 deselected`；浏览器
  回归 `248 passed, 12 skipped`，PostgreSQL 实验集成 `1 passed`。

## 2026-10-13 线上只读复核（继续审计）

- 四个 systemd 服务仍 active 且重启次数为 0；Core live/ready 与实验 capabilities 均返回 200，
  capabilities 仍为 `target_version=2.1.0`、`creation_enabled=false`、`history_readable=true`；
  数据库 head 与 checkout 均为 `0045_postmatch_showcase`。
- 数据状态没有改善或恶化：`matches FINISHED22/PAUSED1/TERMINATED63`，`rooms
FINISHED10/PAUSED1/TERMINATED89/WAITING2`；非终态比赛 1、非终态房间 3、终态房间缺少
  `ended_at` 62，活动/终态/孤儿租约均为 0。
- Spec 295 资源审计仍只因 Core/Jobs 进程 soft `nofile=1024` 低于 524288 而失败；systemd
  hard/limit 已是 524288。当前 cgroup `oom_kill=0`、MemoryHigh/Max 未设置，不能据此覆盖
  历史 OOM 风险；本轮仍未写生产、重启服务或修改 systemd/cgroup。
- 在隔离 PostgreSQL `jx_debate_qa_experiment` 上运行 `repair_terminal_state.py` 默认只读模式：
  非终态房间/比赛、缺失 `ended_at`、活动/终态/孤儿租约均为 0，`ready_to_apply=true`，退出码 0；
  未执行 `--apply`，也未改变任何数据库状态。

## 2026-10-13 取消、容量与诊断阻塞复审修复

- Spec 274 补充验收后复现并修复统一 LLM limiter 的两个问题：模型名额等待取消时遗漏
  已取得的全局名额；全局/模型各自重新计时会把 3 秒预算放大到约 6 秒。现在 registry、
  全局和模型等待共用一次绝对截止时间，取消原样传播且同步归还已占名额。六项回归覆盖
  取消、超时、跨层预算、registry 等待与释放/取消竞态。
- Agent 正文构造/获取容量/播放任务均进入清理范围，排队失败或取消关闭 spool 与客户端；
  重复取消和 spool 关闭 OSError 不跳过容量归还及播放任务取消。Judge 同步释放名额后再
  有界尝试关闭客户端，关闭异常不覆盖有效结果或真实 Provider 失败；统一使用冻结
  ModelProfile ID，避免与 Agent 按名称/ID 分成两个模型池。
- 进一步复现自由辩论决策已结束或已取消、但诊断持久化阻塞时仍持有名额和客户端。
  现在 start 诊断在获取容量前有界执行；stream 成功、失败或取消后先归还名额并有界
  关闭客户端，再执行有界完成/失败/取消诊断。诊断上限 0.5 秒，消费迟到异常，保留
  3 秒快速决策、一次重试、取消传播和旧回调栅栏。
- 组合专项 `89 passed`，新增 Core 回归合计 22 项；20 轮合成容量复核取消 1,000 个
  等待者后恢复 1,000 个名额，51 个名额探测仍拒绝超限，0.2 秒预算实际约 0.201 秒。
  没有真实 Provider 请求，不代表 50 路 LLM 压力。
- 最新 `pnpm test` 聚合全部通过：tooling Node `50 passed`、QA `36 passed`、Web
  `231 passed`、Core `539 passed, 76 deselected`、Jobs `50 passed, 2 deselected`；
  独立 PostgreSQL `jx_debate_qa_experiment` 完整 Core 集成 `76 passed, 539 deselected`。
  Lint、TypeScript/Pyright、OpenAPI contracts、新增测试定向 Pyright、Ruff、格式、shell
  语法及 `git diff --check` 通过。本轮前端未变，未重复已有 Storybook/浏览器/构建基线。
- 最新每队两真人两 Agent 的形式化模拟完成 100 轮、1,000 次发言并拒绝 300 个迟到回调；
  31 个语音可靠性场景全部通过。仍为内存/合成模拟，不替代真人麦克风、真实供应商和
  LiveKit 现场验收。
- Spec 295 补充 systemd `LimitNOFILESoft` 只读诊断，runtime QA `14 passed`；硬限制、
  声明软限制和进程实际限制分别展示，新字段不改变既有门禁。生产 root cause 为 unit
  未声明 LimitNOFILE、继承 manager soft=1024；hard=524288 并不表示 soft 已生效。
  当前 Core/Jobs 打开的 FD 为 34/8，没有耗尽证据；未安装 drop-in、未 reload 或重启。
- Spec 298 增加真实 shell `check_database` 的六项行为回归，release QA `16 passed`：
  零房间放行、独立房间阻断、缺失或错误类型拒绝。仅替代初始查询，不接真实数据库。
- 只读定位确认唯一暂停比赛就是 `2dce08b6-b4f9-40c5-b99a-3e01eef0ba28`，关联暂停房间
  `604ae47b-d325-4efb-83cf-9ead134d8e91`；另有两个 WAITING 房间
  `d529f170-591d-44c5-b348-e301be6af341` 与 `95565a6c-98a3-497e-807e-1703c111eaed`，
  活跃租约为 0。已请求用户决定保留恢复或审计终止比赛、保留或正常关闭候场房间；
  未得到决定前保持这些业务状态。Spec 283/298 门禁仍阻止生产修复和发布，本轮没有
  生产写入、重启或部署。
- 剩余边界：底层 close/FFI 若忽略取消，实际回收仍可能延迟；有界诊断失败可能缺少
  诊断记录并留下稳定告警。历史 OOM 风险仍需 staging 校准 MemoryHigh/Max；未把合成
  模拟、当前低 RSS 或当前 oom_kill=0 宣称为真实压力/历史内存风险已完成。

## 2026-10-13 Jobs 主持任务 PostgreSQL 回归修正

- 扩大数据库验证首次出现 Jobs `1 passed, 1 failed`：旧测试只有 rule/asset 两个 payload
  字段，没有 Spec 264 已要求的 generation 身份，worker 正确拒绝为
  `tts_generation_stale`，因而测试的 READY 断言失败。没有为旧 fixture 修改 worker 栅栏。
- 先补 Spec 264 回归验收，再仅更新 `apps/jobs/tests/test_postgres_integration.py`：复用
  `create_generation` 生成正式 4v4、当前 generation、manifest、冻结输入及 active task；
  验证任务/资产/批次完整成功、文件正时长/校验信息和仍待人工审核。
- 增加缺 generation 身份不调用 Provider 的真实数据库回归，并验证第二次轮询由已有
  reconciler 将资产/批次收敛为 FAILED，attempt 不增加、review 仍 PENDING。
- 集成设置现在必须显式提供独立 TEST_DATABASE_URL，缺失或等于应用数据库地址时在
  构造数据库连接前拒绝；禁用 dotenv。清理仅删除本轮 fixture IDs，不再全表 TRUNCATE。
- 新建本地隔离数据库 `jx_debate_qa_jobs`，升级到 `0045_postmatch_showcase`，连续两轮
  集成各 `5 passed, 50 deselected`；Jobs 非集成 `50 passed, 5 deselected`，Ruff、格式、
  定向 Pyright 与 `git diff --check` 通过。收尾任务、资产、批次、调用和夹具用户/规则均为 0。
- 本轮只更新测试与规格证据；没有生产调用、数据库写入、重启或上线。当前最新验证组合
  为 Core 非集成 539/集成 76、Jobs 非集成 50/集成 5、Web 231、Node tooling 50、QA 36；
  全部通过，真实供应商压力与现场音频仍按前述边界待验收。

## 2026-10-13 跨模型容量、发言尾部和真实 socket 清理

- Spec 274 先补验收，再完成四个可验证资源切片。忙模型等待者原先预占全局容量，
  使空闲模型在实际默认 3 秒预算下三轮全部被拒绝；现在 model-first 获取、同一绝对
  预算，全局等待失败或取消只归还已取得的模型名额。当前 limiter 10 项回归通过。
- Agent 正文 LLM 已结束、媒体或诊断仍在处理时原先仍占名额；四场尾部等待时第五场
  20/20 被拒绝。现在 stream 返回即同步归还容量并有界关闭，再继续原诊断/TTS/播放。
  六类真实 `_execute` 五场合成场景重复三轮共 18/18 通过，未结束 stream 的硬上限、
  reset 取消及回调身份仍有效。Agent 清理回归当前 17 项通过。
- 本地真实 WebSocket Close 不响应复审发现：五条 Core TTS/ASR/Jobs 路径的外层 2 秒
  返回不等于 FD 已释放，改前约 22.2 秒才随 keepalive 关闭。共享 provider_cleanup
  helper 只在退役连接关闭失败、超时或取消时同步 transport.abort；正常关闭不 abort，
  新替换连接受原栅栏保护。原样 2 秒常量的五路径并发在 2.0017 秒全部 FD=-1/CLOSED，
  不是 fixture finally 清理后的结果。新增 21 项 loopback/取消/替换回归通过。
- 后台模型测试此前绕过共享 limiter，容量已满仍调用 Provider。应用现在统一创建并
  注入 Agent/Judge/后台测试同池，使用稳定 ModelProfile ID；构造、请求、容量拒绝、
  取消均先归还名额、再 0.5 秒有界关闭、再诊断，保留原权限、模型与失败语义。
- 独立复审补齐后台失败诊断屏障：普通诊断抛错/挂起不能覆盖原 Provider 或容量 503；
  Provider/构造失败后诊断等待中的新外部取消仍传播。诊断同原 session/route task
  顺序执行，不创建 detached DB task。当前后台回归 27 项通过；诊断超时可能留下
  STARTED 和脱敏稳定告警，容量已经释放，不宣称失败记录必然完整。
- 最新聚合和最终切片回归组合：Core 非集成 598、Core 隔离 PostgreSQL 76、Jobs
  非集成 50/隔离 PostgreSQL 5、Web 231、Node tooling 50、QA 36，共 1,046 项通过。
  聚合 pnpm test 时 Core 为 592，末次四项诊断错误及两项取消回归之后完整 Core
  复跑为 `598 passed, 76 deselected`。最终 Lint、类型、contracts、定向新测试 Pyright、
  Ruff/格式、diff 检查通过；本轮前端未改，未重复此前浏览器/Storybook/构建基线。
- 上述 Provider 请求均为合成或本地 loopback，无生产写入、重启或上线；HTTP/native
  close 不响应取消时仍可能延迟物理回收。历史 OOM 的 staging MemoryHigh/Max 校准、
  真实 50 路、五场媒体、180 秒真人 ASR 和 11 音色盲听仍未验收。
- TechDesign §8.2 还要求比赛轮转与 ACTIVE > JUDGE > CONFIG_TEST；当前 limiter
  没有比赛/角色关联，共享上限与 model-first 不等于完成公平调度。已写 Spec 299
  设计草案（有界队列、非抢占、同时授予两层 lease、3 秒期限、轮转/取消回归及回滚），
  并按 AGENTS.md 请求批准；未写该规格业务代码，未以等待时间代替批准。
- 已请求用户决定真实暂停比赛和两个候场房间的保留/处理方式，尚无回复。生产仍按
  Spec 283/298 非终态与受保护窗口门禁执行，不为发布擅自终止比赛或改写数据库。

## 2026-10-13 Provider 诊断事务、迟到结果与恢复闭环

- Spec 273/274/217 先补对应验收，再完成本轮故障切片。LLM 非 2xx 响应的静默或慢速
  错误体原先绕过首正文截止；现在诊断 body 读取使用同一绝对剩余预算，保持原错误
  和重试次数。本地真实 HTTP 原样 10 秒常量下约 10.006/10.008 秒返回，返回时 FD=-1、
  pool 为空；新增 10 项通过，两个短预算 socket 场景重复 20 轮共 40 次通过。全量曾
  暴露夹具将 TCP RST 误当泄漏，已兼容 FIN/RST，仍检查真实 socket 在最终清理前关闭。
- Agent start/finish 纯诊断最多等待 0.5 秒；LLM start 在获得名额前执行，TTS 诊断不能
  无限阻挡 decoder EOF。调用前后复核现有身份，取消中的诊断迟到完成不能重启旧 Provider。
  `_create_generation`、完整业务 blob 和 generation 成败提交仍为权威事务，不可丢弃。
- 真实 PostgreSQL 证明失败 capture 异常曾回滚原失败标记并跳过 Actor 错误回调；成功
  capture 的 savepoint 回滚则可能产生 ORM MissingGreenlet，重复请求已成功 LLM。
  现在 Agent 先提交权威成败及完整业务响应，再独立 session 有界保存纯 capture，不锁
  generation 行。共享 helper 在诊断屏障外 flush 权威修改，savepoint 回滚后 refresh
  调用行；权威 flush、refresh、blob 和提交失败仍传播，未伪装成诊断成功。
- 取消清理保留 FAILED generation 的首次错误及完成时间；已 FINALIZED/CANCELLED 不改写。
  三类实际恢复链发现并修复旧失败被 `agent_cancelled` 覆盖的问题。503、首正文超时、
  截断 EOF 各经原两次尝试进入 ERROR，再经自然 3 秒恢复倒计时重新发言，完整文本、
  Speech/Generation/音频资产落库，Actor/Match/Room 均 FINISHED。8 席每队两 Human 两
  Agent，但每场仅验证一个 Agent 动作；HTTP 与媒体边界合成，不是完整真人媒体比赛。
- 迟到 LLM/TTS/播放结果曾复活旧 generation、触发完成回调或额外重试。正文在所有
  Provider/诊断/媒体返回边界复核不可变 run/generation，LLM-ready 在原行锁内拒绝
  退役结果。实际 PAUSED 的 Actor/数据库广播窗口中，fence 仅失效输出，注册 run/spool
  保留至原 reset/finalizer 接管，防止正文提前退出后清理找不到旧资源；正式截断 finalizer
  保持 spool 所有权。新增迟到结果 34 项非集成、11 项 PostgreSQL 回归通过。
- attempt 清理先脱离旧引用，独立启动 TTS/decoder/source/room 有界清理，消费迟到异常。
  同步 finish_input/clear_queue 异常也隔离；真实 Actor 暂停提交后仍广播并调度原清理，
  Host reset 不再跳过任务取消和其他关闭。新增 attempt 20 项、生命周期 10 项通过。
  原样预算下 cancellation-resistant reset 约 3.002 秒返回，但 4 个底层任务仍可未完成；
  没有将有界返回写成 native/FFI 已物理回收。
- 实际比赛事件 INSERT 失败时 Actor/数据库 sequence、Speech、事件和广播均不推进；
  同一命令随后可正常提交，重复命令仅返回 duplicate，不再次广播。新增 runtime 故障
  PostgreSQL 14 项、成功 capture 13 项、共享 helper ORM 9 项、恢复链 3 项均通过。
- CI 显式创建独立 `jx_debate_qa_agent_faults` 并传入不同于应用库的 TEST_DATABASE_URL；
  新故障夹具仅接受该本地专属库、禁用 dotenv。旧 voice 白盒夹具改为正确 SecretStr、
  禁用 dotenv并补齐实际类型，定向 Pyright 从 61 个错误到 0，56 项通过；未放宽生产栅栏。
  本地已运行实际 pnpm test:db，不宣称 GitHub CI 已远程执行。
- 冻结业务代码后的完整 `pnpm test` 为 Core 非集成 684、Jobs 非集成 50、Web 231、
  Node tooling 50、QA 36；标准 `pnpm test:db` 在专属本地库串行 Core 126、Jobs 5。
  最新组合共 1,182 项通过，比前期 1,046 项增加 136 项，不重复累计专项或 40 次复测。
  Lint、Web/Contracts/Python 类型、契约、格式、新增测试定向类型和差异检查通过。
  最后旧 voice 夹具类型修正另经 56 项/定向类型复验；前端未改，未重复此前
  Storybook 94、浏览器 248/12 skipped 与生产构建基线。
- 最新生产只读快照：四服务 active，live/ready 200，真实 DB head 为
  0045_postmatch_showcase。远端旧 preflight 固定 0044 的报错是旧脚本漂移，不是实际
  数据库落后；当前仍有原暂停比赛及原两个 WAITING 房间，连接租约 0。Core/Jobs 实际
  nofile soft=1024、hard=524288，MemoryHigh/Max 仍 infinity，当前 oom_kill=0 不消除
  历史 OOM。未修改这些业务状态、安装 drop-in、重启、写生产数据库或部署。
- 剩余范围：TechDesign §19 的完整 Provider capture 有界异步 writer 尚未实施，管理成功、
  音色预览、裁判、ASR、Jobs 仍存在直接等待纯 capture 的路径。已写待批准 Spec 300：
  独立 capture 表与兼容投影、条数及字节上限、固定 worker、一次重试、迟到隔离与关闭
  期限；未写其业务代码或 migration。Spec 299 的比赛轮转/角色优先级也仍待批准。
  真实 50 路、五场媒体、180 秒真人 ASR、11 音色盲听、staging 内存校准和生产发布
  均未验收；继续保留生产非终态门禁及此前等待用户的房间/比赛处理决定。

## 2026-10-13 剩余同步研究采集的重复故障探针

- 本轮只新增 `scripts/qa/probes/` 显式风险探针及文档，不修改业务源码、migration、
  供应商配置、生产数据、服务或部署。Spec 272 先补 test-only 范围；Settings 禁用
  dotenv，合成凭据／内容，专属 loopback `jx_debate_qa_agent_faults` 串行运行。
- 管理员模型测试 6 项最终 2 控制通过／4 风险红检；实际 SSE 已成功，但 request/
  response capture 挂起 0.75s 后 call STARTED、token/完成时间/成功审计未提交。
  另一 session 200ms 行锁 55P03；取消释放锁却回滚已观察成功结果。SQL 存储错误
  对照保留成功和审计。原容量已释放不代表 route 请求成功。
- 裁判 15 项最终 7 控制通过／8 风险红检；capture 边界异常替换成功／503，挂起
  0.8s 后 Judge RUNNING／call STARTED，取消丢失结果，独立 UPDATE 真实 55P03。
  normal/Python/SQL 存储错误 × 成功／503 控制保留完整业务响应或原两次错误；权威
  SQL flush 失败传播／回滚控制通过。现 Postmatch 不写审计，未把 audit=0 伪装成缺陷。
- Jobs 主持 6 项最终 4 控制通过／2 风险红检；实际 manifest/claim/worker，合成
  有效 100ms Ogg。capture 挂起约 0.9s 时 Rule/Generation/Asset/Task/ExternalCall
  五张表行锁均阻挡另一 session。外部取消使锁释放，但 call STARTED、任务/资产/
  generation RUNNING、lease 未清空，文件未关联资产，原 tts_provider_failed 未提交。
  普通存储错误对照保留成功或原错误和 attempts=1/max=2 的已有重试语义。
- ASR 3 项最终 2 控制通过／1 风险红检；实际当前信封 capture callback 持有 Match/
  Speech 行锁，独立锁查询约 160ms 返回 55P03；真实 Actor 暂停观察 0.35s 仍等待，
  Actor/DB 均 RUNNING/sequence=0、广播0，未暴露未提交状态。已提交 final segment
  保留；SQL 捕获失败／取消控制释放锁后实际暂停提交并广播 PAUSED。未接麦克风、
  ASR WebSocket 或真人完整发言，不把该 callback 探针当成媒体验收。
- Core 音色预览 6 项最终 3 控制通过／3 风险红检；实际路由、QwenTtsConnection、
  gain/文件替换/ORM/audit，synthetic WS 输入 100ms Ogg。挂起时有效预览已发布
  （解码约106.5ms），call STARTED／Voice NOT_CALIBRATED／audit缺失；取消后文件
  仍在，连接关闭边界和两个名额释放，成功记录未提交。ExternalCall UPDATE 真实
  55P03，而 VoiceProfile 行锁可取，未误称持音色锁。normal/Python/SQL错误均成功。
- 各独立最终运行分别 7.19s／5.94s／4.15s／3.80s／4.57s。四文件首组30项
  15通过／15风险失败，13.54s；加入预览后五文件36项连续两轮均18通过／18风险失败，
  13.24s／13.81s，失败场景一致。重复执行不重复计独立测试，不计入既有1,182绿检，
  不用 xfail、放宽产品断言或外部取消把未完成写成通过。36场景不是36场真实比赛。
- 夹具问题均已修并与产品失败分开：synthetic HTTP client 所有权、合法短用户名、
  PyAV PTS/time_base、ASR正式状态／合法call kind／完整身份／实际Human user ID；
  预览复用专属库唯一ADMIN，不改其认证或删除借用账号。早期 setup/type 错不计产品
  风险。最终五探针 Ruff、format与Pyright通过；仓库diff检查通过。
- 已批准／上线 Spec214 的实际脱敏 JSON、allowlist headers、关键事件／聚合响应
  与长期研究保留曾被 TechDesign blanket 禁止遗漏。本轮对齐 TechDesign §12.2/19，
  保留凭据／全量token/interim／二进制禁存、单侧2MiB、安全拒存／详情审计／导出边界；
  HEAD PRD旧禁止仍记录为事实来源漂移，未恢复缺失文件。该研究采集不是普通30日日志。
- Spec300更新为待批准方案，继承214采集／安全／保留／权限与导出；补清历史失败尝试
  仍可落库、构造预约／字节预算、独立连接锁释放、ASYNC_V1列表缺口、导出投影冻结
  和必备兼容回滚。新增provider表／导出metadata快照表、writer及拥塞拒收策略尚未批准
  或实施。普通存储异常隔离已绿，永久挂起／取消仍红，不能把这个剩余范围写成已修复。
- 未改动生产；Spec299也仍待批准。真实50路、五场媒体、180秒真人ASR、11音色盲听、
  staging内存校准、线上发布及真实暂停比赛／候场房间处理仍未完成，不因健康快照或
  这些合成探针通过部分控制而取消受保护发布门禁。
- 导出冻结追加4项最终2控制通过／2风险失败，4.09s；前一有效轮也为2／2，4.64s。
  实际create_export、capture helper/immutable blobs和Jobs `_match_package`：旧call
  的capture在cutoff后提交会把晚到正文加入旧研究包；更新两侧Blob指针后同一export
  的request/response正文和agent-calls.jsonl哈希都变。只hash研究内容，排除manifest/
  ZIP时间字段。precutoff完整capture与postcutoff新call两控制通过。214“任务开始”
  时点仍有文字歧义，沿用现Core/Jobs真实export.created_at，不规定无正文索引行为。
  最早时钟断言失败仅为Colima与宿主60–70ms偏差，合成capture改同PG时钟后复测；
  不算产品风险。所有owned-row teardown正常，专属QA库、无Provider或生产访问，
  最终Ruff/format/定向Pyright/diff通过；该探针也不计入原1,182标准绿检。
- 最终六文件合并串行复验40项=20控制通过／20产品风险失败，14.86s，无清理错误；
  six-probe目录Ruff、format、Pyright全部通过，Spec272/300/README及probe README
  显式绕过默认ignore的Prettier检查通过，仓库git diff --check通过。TechDesign和
  MEMORY按既有ignore保留格式，不虚称默认formatter实际检查它们。40项包括重复
  结果／取消／行锁和两个导出冻结场景，不是40场真人比赛或20个不同根因；不重新
  累计同一用例的重复运行，之前1,182标准绿检为本轮未改业务代码的既有基线。

## 2026-10-13 Spec 300 批准实施与最终本地回归

- 用户明确回复“批准”授权Spec300新表、writer、冻结投影与拥塞策略。Spec299公平调度
  仍未批准，真实暂停比赛/两个WAITING房间的处理也未授权。未切/建/合并/删除分支、
  未提交，保留原脏工作树和缺失历史PRD，不读取api.md/.env/密钥进文档代码或日志。
- 追加0046_provider_capture_snapshots：capture独立于ExternalCall，ASYNC_V1新调用无行
  明确gap；统一legacy/new投影不把完整业务blob当raw。Core/Jobs各一固定writer，
  原事务验证身份、flush/prepare/commit后非阻塞submit；权威blob、审计、状态/权限/
  计时/模型和原重试不变，过期和回滚不投递，历史已结束claim仍可存接受证明。
- 64项、8MiB retained+8MiB workspace、单侧2MiB、metadata64KiB、batch8项/4MiB+64KiB，
  编码workspace7MiB/排序引用1MiB、固定1线程1连接；statement750ms/lock100ms/
  idle-txn1s、batch1s、普通诊断错误只重试一次/50ms、shutdown3s。超时驱动finish
  强制本连接socket/事务回收并隔离writer，20lateoffers不新建替身/线程/连接。
- 实际内存审查额外修：sorted(items)→bounded keyrefs、固定tail ring、stream bytearray
  freeze、UTF8增量decode，完整安全扫描先于排序拒收；同250000-key event额外peak
  38,250,710→5,719B。拒收不抛进Provider流；dotted key裸值也拒存，用的均为合成值。
  原完成batch/Future/exception traceback保留raw而counter归零已修，release确定清
  raw bytes；线程未结束时原预算仍保留。实际adapter metadata/events边界复核有界。
- 实际自由决策authority-finish异常红检曾提前返回未提交解析值且只有1attempt；现仅
  commit成功后接受候选，保留2attempt/唯一success或最终failed回调。预览初始commit/
  constructor/audit/rollback未移交lease确定释放；ASR缺callback/envelope和callback
  异常finally关闭；Core/Jobs启动/退出异常按已启动所有权清writer、保原异常。
- 新导出独立RR transaction首SQL前设置隔离级别，冻结Provider call/raw metadata+
  正文指针/hash；marker1零行不补读最新，旧NULL任务保留明确legacy边界。无Match
  FORUPDATE；100items/4096calls。此冻结不代表整ZIP/manifest/所有转录被冻结。
  显式match删除按65,536唯一候选、五类全库引用GC，超限413在删除前回滚；共享其他
  match/非match/capture/snapshot仍保留。7天export下载到期目前无删除后台任务，
  collect_export_blob_ids只是可复用helper，不冒称到期自动GC或改变研究保留期。
- PROVIDER_CAPTURE_ENABLED=true默认；同版本false不构造writer且新raw明确缺口，
  legacy/new读取、冻结导出与五引用GC仍在。此构件为兼容降级，非未经验证旧binary
  回滚；不downgrade新表、不删历史、不退同步热路径/切模型。example/deployment已说明。
- 原六文件40项20绿20产品风险红，现保原权威/原错误/审计/hash/锁断言并增4项过期/
  rollback，实际共享writer+严格QA PG连续两轮44/44通过（14.93s/16.21s）。不把88次
  执行算88独立场景，不把synthetic HTTP/Ogg/ASR callback算真人或生产压力。
- 冻结源后完整pnpm test：tooling50、QA36、Web231、Core非集成789、Jobs非集成61；
  pnpm test:db在独立loopback jx_debate_qa_agent_faults串行Core176/Jobs5，标准总1,348。
  含writer20PG/projection9PG/adminreader15PG/export6PG/三类实际Actor自然恢复链；
  复跑专项不再加计。旧同步夹具挂起/legacy断言/catalog新表和migration import路径
  已修；早期setup或fixture失败不作为新产品风险，也没削弱authority失败传播断言。
- Lint/typecheck/contracts/格式/diff通过；browser248/12retired skip，Storybook94，
  CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build完整standalone37静态routes通过。
  浏览器合成路由有既有unmocked Core代理ECONNREFUSED日志，没冒称实际后台/生产E2E。
- system-profile技能本地独立runner最终132权威commit/112capture：sustained32/32，
  flood80/100与20容量gap；encode p99 sustained5.273ms、offer p99 0.015ms；水位
  14,204,962B=13.547MiB、items64、结束queues/retained/workspace0、drain462.984ms。
  CPU0.855s/墙钟2.707s含计划等待；整个进程RSS peak72.547MiB/baseline64.109MiB，
  16MiB仅capture记账上限。profile汇总SQL歧义列/Decimal JSON两次脚本错误已修且
  own-row finally清理，正式只记最终成功。profile_output已ignore、临时脚本/结果/
  report未提交，report列出instrumentation，可清理临时测量而保永久内存回归。
- 最新生产只读：四服务active/live ready200、实际DBhead0045。远端旧preflight仍固定
  旧head造成migration FAIL，不表示实际仍0044；候选0046未部署。PAUSED原match
  2dce08b6-b4f9-40c5-b99a-3e01eef0ba28和room604ae47b-d325-4efb-83cf-9ead134d8e91，
  WAITING rooms95565a6c-98a3-497e-807e-1703c111eaed/d529f170-591d-44c5-b348-e301be6af341，
  leases0。没有生产写入/迁移/重启/部署或故障注入，继续Spec298非终态门禁。
- 未验收：真实staging RSS/CPU/连接锁/发言尾延迟和MemoryHigh/Max校准、实际混合
  投影浏览器/受保护上线后QA，50路流式LLM、五场完整媒体、180秒真人ASR、11音色
  盲听和长期稳定性；历史50/50真实短probe不验收本版完整并发。Jobs普通runtime-log
  engine dispose→stop次序仍为静态候选，未实测且不扩Spec300同步改普通logwriter。

## 2026-10-13 Spec 300 实际隔离 Core／Jobs／Web 补验

- 前一轮有可验证进展：已批准业务实施、标准1,348回归、44探针两轮与本地profile通过。
  本轮继续补验，不以剩余生产门禁将目标误标complete或block；业务源码保持冻结。
- 使用agent-browser＋system-profile技能，独立 fresh QA PG升级0046，合成ADMIN/USER、
  provider/media配置明确None/_env_file=None；实际Core8100＋已完成pnpm build standalone
  Web4100。生产、原脏树、用户删除文档和既有数据均未改变。没有读项目api.md/.env。
- 浏览器真实cookie登录、54条列表两页50/4、source筛选自动回第一页、legacy/ASYNC
  complete原始JSON及gap两侧UNAVAILABLE/null/capture_not_persisted通过；完整authority
  business blob真实PG精确保留。三类敏感查看审计、provider_request_id filter、USER403
  在独立HTTP/PG核验；USER浏览器亦显示需管理员权限、列表/详情/export下载403。
- Jobs专用合成FINISHED Match3calls（不算一场完整比赛）加入后列表57条。真实UI
  export preflight→确认→创建200，seq3/context2/marker1冻结3输入。创建后真实writer
  晚补gap、live async scalar改CANCELLED/error，旧冻结仍SUCCEEDED/null/gap且JSONL
  hash e0c3009f56053801c2786a3d582bbfbb27fc441bc9a0095479791de096197766不变。
- 实际Jobs claim→ZIP→item/task complete，仅1attempt，SUCCEEDED1/1。真实UI点击下载
  route200；同Browser cookie fetch保存bytes/独立验证2631B、ZIP SHA256
  f1df30df037fb6591bf344b64648bb89ad1a41fd810541e1eae5908c5c190e9c，3rows JSONL一致。
  不声明OS保存对话框、全ZIP/manifest/transcript字段冻结或真实媒体验收。
- 仅QA Core正常关闭再同构件flagoff启动，lifespan assertwriterNone，Browser三详情+
  export GET/download同hash、独立HTTP/PG权限/审计均通过；不是旧binary回滚。
  两个ownBrowser和QA Core/Web均关闭，8100/4100无监听；独立合成DB/ZIP证据保留供复现。
- 证据 ignored profile_output/spec300_browser/report.md＋12截图、API/hash JSON；
  spec300/browser_backend_{check,flagoff_check}.json、flagoff_lifespan_check.json、
  export_e2e_state.json。临时harness Ruff/format/Pyright（export）/编译均绿；不额外
  累加标准测试数。ADMIN唯一索引fixture冲突、错方法405和错预期文案wait timeout
  不计产品风险。实际页面无JS异常；另低影响中文顶栏matchData翻译key已截图列待修，
  没有扩300修改无关UI源码。
- 本轮只读productionSSH Core/Jobs/Web active，live/ready200；Core cgroup当前
  158679040B/peak213200896B、Jobs58683392B/peak152641536B；MemoryHigh/Max仍infinity，
  nofile soft1024/hard524288。没有安装limits/dropin、生产migration/重启/状态变更/发布。
  原PAUSED比赛/两个WAITING房间仍按上一轮门禁保留；处理选择问题尚无用户回复，
  不把“批准Spec300”解释成授权终止真实比赛或关闭候场。
- 真实目标staging资源/发言尾延迟/cgroup校准、受保护生产发布及上线QA、50路真实
  LLM/五场完整媒体/180秒真人ASR/11音色盲听仍未验收；本地实际应用链补验只关闭
  读投影/冻结artifact/同构件flagoff兼容这一缺口，不能取消其他发布边界。

### 同轮资源补验红检：预约精度与 overflow Unicode（补修中）

- 旧32路profile未覆盖50路实际文本预算及不同正文。独立新进程 actual adapter＋
  synthetic SSE50×6，按24k history/fixed306char/free102char预算，业务300正确；
  600side只有276 request COMPLETE/24request gap/300response gap，完整pairs0。
  50完整pair理论raw+metadata=6,986,975B<8MiB；分组trace证明event前workspace0，
  retained6,612,674B，但每小event临时加2MiB越8MiB后latch whole-response gap。
  此为容量内保守预预约缺陷，不能把accepted300计成300完整raw成功。
- 独立native压缩实际thread-held红检：当前tiny complete+UNAVAILABLE两side冻结
  需要固定7MiB，而真实compressor占>1MiB导致writer.prepare=None；强断言新test
  test_small_complete_and_gap_snapshots_prepare_during_actual_native_compression已红，
  无PG/socket/Provider。该风险与paper retained拒收主因分开，不互相混称。
- 原正文在trace前构造emoji+>2MiB ASCII+emoji，overflow两896KiB片段decode宽化；
  新增peak11,107,534B>7,340,032B预约，ledgerpeak9,502,720B，输出TRUNCATED约1.8MiB。
  原有RawProviderEvent增量解码没有覆盖此fragment路径。新补修保UTF8首尾内容/schema/
  hash及原按字符缩片段，采用内部bytes/增量escape，不提升16MiB/7MiB/各侧2MiB。
- Spec300已在实施前追加精度切片：有限扫描后按可信上界预约retained/workspace，
  保完整敏感检测/排序子预算/rollback及缺口政策；现正在修并补永久red→green验收，
  该追加切片未完成时不把整Spec300写成最终再回归已通过。

### 预约补修专项冻结（同轮，完整再验收待执行）

- provider.py/provider_budget.py/new reservation_precision test源码冻结，新增19+原16编码
  专项35/35通过64.22s；Ruff/format/Pyright/编译通过。完整pnpm/PG/多轮pressure尚待
  同源码再跑，前面的1,348仍为补修前基线，不冒称最终绿检。
- 512KiB无sorted/无deepcopy有限计数后再预约retained实际UTF8上界；small workspace
  1.5MiB+3×encodedceil≤7MiB，深/高key/overflow原7MiB。item64/retained8MiB+
  workspace8MiB/单侧2MiB/排序ref1MiB/固定线程连接及失败语义均未扩。
- 内部UTF8 bytes片段增量decode/escape，不将privatewrapper漏入公开JSON，原字符
  halve/边界replacement/hash保持。emoji overflow peak11,107,534→5,594,668B，
  widekey构造peak33,558,615→5,594,666B，均≤7,340,032B；tiny15,256≤1,576,401B，
  深127层counter19,904≤524,288B（不把source已有对象计成新增capture分配）。
- header/binary_summary自身oversize的截断循环先红（alarm5s），增加最低wrapper尺寸
  预拒和字符长度无进展guard后两绿；巨宽key敏感normalize改trim索引+finitekey compare，
  全原key/value regex仍保留，长空白包PASSWORD控制仍REJECTED。非全原文随机采样。
- 新QA-only ignored profile_output/spec300/qa_no_dotenv/sitecustomize.py通过PYTHONPATH
  禁用历史test/contracts调用的默认dotenv读取（显式合成路径仍可用）；verify.py在
  source读文件前强断言env_file=None已绿，不读/移动/修改工作区.env/api.md，不改业务
  Settings或生产启动。清单README.md列临时instrumentation，不算永久业务修复。

### 2026-10-14 满载计数开销补修冻结（最终完整门禁待执行）

- 第一版quiet actualadapter纸面50×6及50×30分别300/1500pair、600/3000side全COMPLETE；
  两大侧1×30亦30pair/60side全完整，硬台账/队列/线程结束归零。忽略报告
  profile_output/spec300_pressure/post_reservation/report.md保留完整pre/post哈希版本；
  SQL为空sink，捕获/prepare/实际zlib处理不叫PGpersist，不算实际50路供应商验收。
- 该版本极端body50×6 active loop lag908.620ms vs旧199.972ms，SSE533.931vs394.943ms；
  whole RSS仍近1GiB、off也高。quiet paper50×30 GC后RSS仍on+16.5MB/off+8.9MB；
  约815496B harness固定留存仅解释一小部分，bytes/str未被GC tracked覆盖、allocator/
  HTTPX/source存活未完全隔离，不能台账0就宣传进程无泄漏/内存完全归还或生产流畅。
- 新10项public入口强断言先6红/4控制绿：request/response/event容量仅余16KiB时仍
  scan2MiB source，组合128×1KiB仍scan131072B。补修仅两个newretained入口以余额
  和str len+2字节下界拒无需存储raw，原保守ceiling/容量码/所有硬上限不变。
  writer已有reservation仍只workspace count；资格内原key/value敏感全文检查继续。
- 最终29new reservation+16旧encoding=45/45绿60.82s；大str2MiB→0扫描，组合131072→
  14336B，敏感尾部三控制、已持4MiB但全池8MiB writer control绿、释放归零强断言绿。
  Ruff/format/Pyright/编译全绿，source重新冻结：provider0e71e44.../budgeta0eda2a...。
  root实际native/future release3/3此前版本绿1.23s，最终标准门禁仍将再含这3项。
- 最终source的新quiet pressure post_available重跑正在执行，root暂不并行CPU/PG门禁；
  新版本不覆盖priorpost_reservation/pre-fix。临时Linux cgroup envelope仅准备白名单计划，
  cached image缺Linux native依赖，尚未build/install/start；不等于目标staging资源验收。

### 2026-10-14 最终构造预算标准门禁通过（实际链补验继续）

- 最终冻结源码 pnpm test 1,197非DB全部通过：tooling50/QA36/Web231/Core819/Jobs61；
  严格独立PG Core176（101.55s）/Jobs5（1.81s）亦通过，标准总1,378。
  Core有12条既有Alembic path_separator弃用提示；Lint/typecheck/contracts全部exit0。
  新增/重复专项与pressure不重复累加。QA-only PYTHONPATH禁默认dotenv guard继续生效。
- post_available静机十个新进程及一个独立FD控制完成；paper50×30 1,500pairs/3,000sides
  COMPLETE、111,305,250B/3,000uniqueSHA、capture ledger/queue/thread结束0；最大单路30轮完整。
  Body满载active908.620→235.190ms，SSE533.931→358.665ms；没有提高硬预算。
- 九原case FD7；一个paper-off原snapshot9是sampler ps两管道竞态，原JSON保留。
  仅ignored observer修stop/join后独立FDcontrol7，不替代原CPU/RSS对照。
  SQL为空sink，不能叫PGpersist；近1GiB extreme RSS/off亦高、RSS增量未全部归因。
- Linux白名单154file复制和pinned cached arm64Python3.12.14镜像构建完成。
  uv0.12.5+uv.lock frozen，独立Linuxvenv，不挂宿主venv/工作区/env。仅import阶段，
  首次Settings缺synthetic DATABASE_URL是fixture错误，留原failedcontainer inspect。
  实际性能/PG/UTF8导出尚待协调静机/独占窗口；不是目标staging或真实媒体通过。

- 最终同源码显式六类故障探针两轮44/44，17.67s/16.81s；重复不计标准总数。
  Web源/契约未改，前述Storybook94/Browser248+12历史skip/build37routes证据复用，
  不声称本构造补修后重跑前端三门禁；新actual Core/Jobs/HTTP UTF8另验。
- actual admin-route+MockTransport/PG压力ON修正测量guard后300业务calls/300SUCCESS审计；
  300 capture rows均requestCOMPLETE/responseUNAVAILABLE（最大侧拥塞，非完整采集）。
  kind/编码长度/chunk_count完整关联各300；原先错误比较原始_content SHA与存储wrapper
  blob SHA导致无效QA断言；初次失败未记exact assertion位置，不能称已现场确定唯一原因。
  初失败证据保留，原业务hash语义未改，后续真实强断言及清理绿；OFF仍在补验。

### 2026-10-14 实际 PG 压力／Unicode 导出补验完成（Linux限额测量中）

- actual admin route函数+MockTransport+PG，ON/OFF各50×6都300业务calls/300SUCCESS审计；
  ON300 rawrow（请求全COMPLETE、响应全UNAVAILABLE最大侧拥塞），OFF0 rawrow。
  native_*计数只NoSQLsink仪器，本PG0不解读零压缩/零落库。idle-tx采样各5，未测驻留
  时长；采样lockwait/ungranted0非所有瞬间无锁。ownedbackend峰7/6，cleanup自有
  model/call/audit0、borrowADMIN保留、authority/writerbackends0、allpooldispose后FD7/thread0。
  RSS599/654MB、active453/334ms与routep99 1087/533ms如实保留，非流畅媒体验收。
  报告pg_final/report.md+instrumentation表/54文件manifest；首次fixturehashguard错误
  原记录保留且exact原assertlocation未知，业务内容hash/存储wrapperhash语义均未改。
- 新actual Core Auth HTTP→preflight/POST freeze→late realwriter→actual Jobs→GET/download
  UTF8链全绿，legacy+async大于2MiB混合中文/补充字符/emoji/escape原始SHA及片段hash
  一致，公开schema1/TRUNCATED/headtail普通str。新export0104f7b4…、ZIP12464B/SHA
  277ead60ca5778388f572df5760f422b64e4ce31e0a05e773c8b574930340ea7，冻结/late/ZIP/HTTP
  JSONL SHA75667d1f7f80dad2dd2acba5f44beec3c677595a88d759ff0ecceea2808c8307全一致。
  独立export_utf8_state.json未覆盖旧state/ZIP；原ZIP严格原bytes，2631B/原hash不变。
- 仅ignored工具4处typing/可变state path命名修好，Ruff/Pyright绿；新HTTP工具凭据cookie
  仅内存/trust_envFalse，无businesschange；Core25240确认command后正常SIGINT exit0，
  ps消失、8100/4100无监听。此为本地QA不是目标staging，不是完整比赛/真实媒体。
- Linux quiet单独测量已GO，四normal压力+45 memory tests待最终结果；其它tests/PG/Core
  均停，不并行CPU。目标staging资源/发言尾延迟及受保护production边界仍保留。

### 2026-10-14 Spec300 本地实施／验收结项（生产／真实验收仍未完成）

- 最终标准1,378全绿、44显式探针两轮全绿、Lint/typecheck/contracts绿；business三hash
  0e71e44/a0eda2a/4497353贯穿静机PG/UTF8/Linux补验保持。git diff --check绿。
  规格300状态及索引已更新，不将新增/重复45专项或多轮pressure再计标准总数。
- 临时arm64Linux2CPU/768MiB/无网络/只读非root四normal全Exit0/OOMfalse/oom_kill0：
  paper50×6 ON300pairs/600side完整，maxsingle1×30 ON30pairs/60side完整，均0errors、
  logicalwater8,696,551/12,625,822B<16MiB，budget/queue/thread0、FD7，sampler已停。
  对照OFF各300/30业务控制通过；不以off零captured_interval当实际响应0ms。
- Linuxpaper ON/OFF process RSSpeak160.117/142.164MiB，cgrouppeak117.789/104.012MiB，
  ONadapterp95 140.586ms vsOFF15.755ms，active65.301ms；maximum ON/OFF RSSpeak
  166.711/140.473MiB、cgroup124.508/102.285MiB、ONcapturep95 253.648ms/active243.836ms。
  RSS和cgroup计量不同，仍有真实同步大payloadCPU开销，不声称任何输入无卡顿或全部RSS归还。
- 同frozenbundle 45pure memory/encoding tests全部绿84.22s，JUnit45/0error/0failure/0skip，
  DockerExit0/OOMKilledfalse。这例无退出前RSS/cgroup memory.events，未伪造oom_kill0。
  来源锁/镜像/完整性/资源/Instrumentation/局限详见spec300_linux/REPORT.md和inventory。
- 所有actualCore/Jobs/HTTP服务已终止、8100/4100无监听；sevenownedLinuxcontainers按
  label/terminal inspect逐个收尾。生成证据/白名单/image/base/独立合成DB默认保留；
  总临时工具清单 spec300/INSTRUMENTATION.md，永久tests/migrations/business代码非清理范围。
- 未进行生产migration/restart/deploy/真实状态处理，0046尚未部署生产；Spec300批准
  不扩展至Spec299公平调度或真实非终态房间终止。发布仍需非终态门禁／受保护窗口和
  目标staging资源/实际speech尾延迟；50真实LLM/五场媒体/180秒人ASR/11voice盲听未验收。
  低影响中文admin header matchData key仍记录待修，不混入300无关UI修改。

- Linux收尾已完成：7owned容器（首失败import/v2/四normal/memory）三labels/stopped
  逐个核验并存终态inspect，仅rm对应name，runlabel剩余0；image36c55167…与basef77ac9e…
  保留，无prune/force/DB触碰，cleanup-result.json存完整自有IDs/labels/保留项。
  最终全体instrumentation及证据manifest在ignored spec300/下，阅后可按需协助清理；
  不能由清理工具文件推定有权删除独立DB、历史数据或永久回归代码。

- 2026-10-14 最终证据清单核对完成：Linux 报告／生成摘要的心跳描述订正为源码实际的 20ms，原始测量数据未变。跨目录清单验证 136 个文件及 9 份最终检查日志，SHA256 `485f23ed0104f20273519a2a0484afb0709096f19d590ce49d6663b333b352cc`；三份冻结业务源码 hash 保持不变，最终 `git diff --check` 通过。

### 2026-10-14 全面目标继续：主持恢复红检与尺寸计量 CPU 归因

- 上一轮最终清单核对是进展，目标未完成。当前工作树与规格再次核对：PRD 路径仍缺失，不恢复用户删除文件；Spec299 仍未批准；目标 staging 没有已核实的独立主机/数据库/服务归属，不以临时 Linux 容器替代。
- 新纯同步 capture 微测四种合成正文，每例五次无 profiler 与一次 cProfile；近限 ASCII 墙钟中位 77.324ms、escaped overflow 113.958ms，包含公开 capture、解码/hash验证及释放。前者完整 regex search profiled 35.827ms；后者 _json_size 九次共85.623ms（累计时间不能相加预测收益）。业务 Provider hash未变，无DB/网络/Settings/真实模型/事件循环测量。忽略证据在 spec300_cpu_followup/；保持完整安全扫描的单次尺寸固定点优化仅为设计，尚未实施或宣称收益。
- Spec267 主持暂停恢复补修落实既有批准切片B：取消抵抗旧任务删除替换登记、同action旧完成/失败误推进或ERROR、queued旧callback/guard跨暂停恢复、完整重播及二次重试保护过短。最终八项纯Runtime/实际Manager/Actor红检均落产品强断言，2.44s，无fixture/cleanup错误。已先补267范围/验收/回滚，再授权单一主持切片实施；不改正式speech计时/媒体/模型/公开DTO/migration，不触碰生产。
- docs/deployment.md 单文件事实纠正通过Prettier/diff：provider_chain_probe会新增OPS_PROBE ExternalCall/raw记录，不是只读；--load只是32token短请求/分组调用/串行LiveKit且不传PCM。auth browser入口会migration与TRUNCATE五表CASCADE，只能新建可销毁QA库，不能使用保留fixture/admin的旧库。本轮未运行这些入口。

- 正常小尺寸PG持续补验完成：现有actual admin route+MockTransport/PG on/off各50×30、各1500成功调用/审计；ON实际1500 COMPLETE/COMPLETE rows、3000完整侧，双方blob kind/size/chunks各1500有效、错误码空；OFF0capture rows。ON accepted1500/water50items/5,343,557B、ledger/queue0、未隔离。两例自有records/authority-writer backends0，borrowADMIN保留，FD7/thread0。ON/OFF RSS174292992/166281216B、CPU15.346/6.012s、wall34.547/10.612s、active51.253/14.885ms；非Actor/ASGI/真实model/media/staging。补验不覆盖此前极端响应UNAVAILABLE证据，metadata/chunk校验不称全blob重哈希。报告与两新JSON/独立manifest在spec300_pressure/pg_normal_*20261014；未重复计标准测试。当前所有PG压力进程已终态，窗口交还host slice。

### 2026-10-14 主持恢复补修及测试隔离最终本地验收

- 本目标继续取得实际进展，未完成整体目标，不标 complete/blocked。落实已批准267而非299：旧run登记/callback/guard及fallback、await后和行锁/flush后身份、完整重播两次保护、挂起host PCM不阻控制。四业务hash f914530a/c95e7e90/7a7381f4/9e74fffa 最终冻结，capture三hash0e71e44/a0eda2a/4497353不变。真实before三文件在原spec300_linux/...71c02ba8/context/bundle逐个hash核实，719行host-only diff单独留证。
- 主持34纯/相关310/实际PG8均绿；独立只读审查发现fallback与锁入口两项后已补修。原8+rowlock3+fallback1干净产品红保留，原native6直接红/2fixture覆盖/4warnings如实保留，不重建旧锁冒充原构件；修后8native场景全绿。
- 首次全PG Core157绿/27容量失败：共享故障fixture不清活动Room，新host8在auth之后导致cap满。root误用保留合成证据QA运行含auth TRUNCATE总入口，可避免且已向用户说明，不能声称原QA数据完整保留；原日志/tmp/jx-host267-final-pg-20261014.log保留。生产未触碰。docs/deployment补全pnpm test:db和auth browser破坏性范围。
- TEST ONLY共享fixture按本次精确Room/Match UUID终态化、ended_at/snapshot一致，freshsession owned活动Room/非终态Match0，保留seed与captures；任一close异常仍收尾/报错，5pure检查绿。新独立可销毁PG同dbname新32768端口，正序host8→member28及反序各36绿，无产品容量放宽/未知行清理。
- 最终标准1,425绿：50tool+36QA+231Web+858Corepure+61Jobspure+184CorePG+5JobsPG。pnpmtest初版853Corepure后新增5另完整复跑858，不重复加专项/旧853。全Lint/typecheck/contracts、Ruff followup、新测试Pyright、diff绿；无本轮Web改动，旧Storybook94/browser248+12skip/build37routes保留而非重新运行。
- 新owned PG8b284f04...正常stop并rm -v精确标签/ID容器，退出0/OOMKilledfalse、remaining owned0；移除仅自己新卷，不prune/image/旧库。测试后head0046/其它client0/8faultRoomTERMINATED；还有3formalPAUSED/1WAITING为该可销毁全套生成，不谎称所有fixture逐行清空。已整体移除新实例，所有root/agent测试session终态。
- 新host报告及PartB、normalPG/CPU报告与各独立manifest留存；旧136+9最终manifest不覆盖。Spec267/索引状态更新本地通过尚未发布，Spec300历史1,378保留、尺寸优化仍仅设计。临时测量文件可在核验后协助清理，永久回归/修复/真实before回滚bundle不得混删。
- 遗留：native无限吞取消/重复物理退役尚无新硬准入、sharedpublisher中途失败局部native回收未实证；正常尺寸capture完整仍有同步CPU开销，单次尺寸固定点优化未实施。299公平仍待单独批准、staging未核实、PRD缺失不恢复用户删除；真实50LLM/五场完整媒体/180s真人ASR/11音色盲听及受保护发布未验收。没有新增production migration/restart/deploy/真实非终态处置。
- 本轮证据独立收录于 `profile_output/spec300/host267_followup_manifest.json`，逐文件记录大小、行数与 SHA256；清单自身不纳入，旧测量清单及原始记录保留。

### 2026-10-14 全面目标继续：尺寸计量补修与Publisher启动强红

- 上一goal turn完成host证据清单是progress；本轮继续实际改动，不标complete/blocked。
  先读AGENTS/300/TechDesign12.2+19/Spike/agent_docs并补300精确范围，沿已批准构造
  性能切片实施，只改provider内部尺寸计量和新增pure tests，不改媒体/模型/事务。
- exact原0e71源码保存在spec300_cpu_accounting/provider_before.py；first公开64KiB正文
  read红393216字符6遍，hash/65,684原B/65,863存B等先通过，修后327680字符5遍。
  首次实际遍历同时识别stable exacttypes和metadata occurrence=1，局部digits保持原
  固定点初值，custom/alias/noninteger保留原读序；freeze实际计量/guard/安全扫描不变。
- 独立审查实证首实施tuple type-membership可被ABCMeta.__eq__伪装（51B/1read vs旧
  451B/4read），修为is链并追加回归；原临时源码hash未保留如实记录，不重建冒红。
  最终provider135abc6628e4d870...，新增32测试7a908b8e...冻结；budget/writer和host4hash
  逐个保持。仅本切片162行diff在accounting_only.patch，整个Git dirty tree不替代基线。
- 七种quiet交替fivepair均证明原始/冻结hash长度status同、ledger0；近限ASCII墙钟中位
  76.444→70.570ms、Unicode35.741→32.399ms、escaped115.896→76.739ms、dense
  1980.180→1689.628ms，后者仍1.69秒同步风险。含decode/hash/release，不是production
  capture尾延迟或event-loop测量；单独cProfile正常sizecall3→2/overflow9→4，重叠时间
  不相加。原CPU/PG/Linux/host数据与历史manifests保留，不重写成新构件通过。
- 最终当前标准1,457绿：50tool+36QA+231Web+890Corepure+61Jobspure+184CorePG+5JobsPG。
  pnpmtest/lint/typecheck/contracts全exit0、新teststatic0/0；Corepure80.79s/PG102.93s，
  13PGwarnings只existingAlembic path_separator。专题83pass/3DBskip如实，32新已含890。
  没有Web业务变化，不冒称本轮新浏览器/Storybook/build。所有工具sessions已终态。
- 本轮总DB命令严格新建exclusive可销毁c981ca0c...同QAdbname／loopback32769，auth
  TRUNCATE只影响新库。后查head0046/client0/8TERMINATED；仍3formalPAUSED+match
  SYSTEM_RECOVERY/1WAITING+matchFINISHED，不称所有fixture逐行清空。核精确ID/labels
  正常stop/rm -v自有stopped容器及匿名卷，exit0/noOOM/ownedremaining0，无force/prune。
  没访问旧保留QA/生产，不触image、不执行production migration/restart/deploy。
- 独立native启动探针Spec272真实SDK1.1.14Python+FakeFFI/真实businesshelper+fake资源
  11项4控制绿/7强风险红：cancel后永久subscribe1，五次1/2/3/4/5且5个maxsize0孤儿队列
  各收16合成事件；business5初始化错误Room/source零清理请求，正常移交控制绿。
  finally自有订阅/task0，native初始化/network/Settings/DB/Provider均0，不能称native
  FD泄漏或实际媒体通过。首harness超时原日志保留不计产品红绿；二次1.675s收敛。
- Spec272记录7红仍未修复；Spec274原owned资源清理可准备下一startup rollback切片，
  固定pending/retired全局quota及满额新拒绝行为须新scope批准，不能私改installedSDK。
  Spec299公平仍待批准，独立staging未核实，PRD缺失不恢复；真实50LLM/五媒体/物理
  ASR/11音色及受保护发布均未完成。整体goalactive，后续优先资源启动闭环。
- 本轮报告PartA/B、回滚源码/聚焦patch、完整logs、新PG创建/收尾metadata与独立manifest
  位于profile_output/spec300_cpu_accounting；publisher_audit独立六文件manifest保留。
  核验后可协助清理临时测量文件，永久修复/回归及必要回滚基线不混删。

### 2026-10-14 全面目标继续：Publisher 启动所有权与原生取消闭环

- 目标继续取得实际修复，不标 complete/blocked。先补 Spec274 精确启动清理／SDK
  独立 join 切片，再改共享 Host/Agent runtime；全局 pending/retired 准入、满额拒绝和
  shutdown 新政策未混入。没有读 .env/api.md 或实际密钥，没有生产读写／迁移／重启／发布。
- 唯一业务改动 runtime 当前 bd9b2eb3b47af8ae9e5ff1eb876b7909f2842a0ae87c99615ad1816e1ee6c75c；
  helper 局部 owner 成功后才移交，SDK join 由独立任务 shield，取消后迟到 owner 只清
  精确旧 Room。所有已返回 Source/Room 关闭先独立调度、复用既有每资源0.5s预算，
  重复取消不撤掉已调度清理，保留原错误并消费迟到异常，不创建迟到媒体或业务回调。
  before f914精确源码及89行startup-only patch在新证据目录；267其它三源码及capture
  provider/budget/writer三hash全部不变，整个Git dirty diff不作为本切片范围。
- 两永久新测试14+8全绿，精确旧helper分别5产品红/1正常绿与5产品红/3正常绿；
  setup/teardown无失败，原强断言未放宽。SDK迟到成功/失败各五次重复terminal归零。
  相关Host/Agent生命周期/回调145绿；独立只读review22绿，无本切片阻断缺陷。
- 实际原生SDK1.1.14／FFI／测试LiveKit1.13.5以全新internal网络、无外网默认路由、
  loopback DNS／signal、无host发布端口运行。350ms只延迟真实ConnectCallback Python
  投递。裸SDK与精确旧helper均ready0并在FFI callback实际self-SIGTERM，child exit-15，
  wall16.689/15.891s。候选两个独立child exit0，各观察>=20s，join未取消、SDK自有
  ready→connect完成→publicdisconnect各一次，订阅/队列与join/listener Python tasks0。
  Source/Track/pub/Settings/DB/Provider全0，没有PCM，不称完整比赛或可听语音已验收。
- 原生首client在浅mount路径IndexError退出、未启动SDK child；原console/preflight/
  prepared manifest和入口源码副本保留。v2总入口exit1保留两旧强红，不改成全绿；
  repeat独立exit0。候选两轮final FD21（导入前8/native init18），native threads与即时
  RSS null，峰RSS142467072/150929408B为ru_maxrss，不能宣称physical归零。新clients、
  server7abe...及空internal network84ff...精确ID/labels normalstop/rm，shared images保留。
- 最终当前可运行标准**1479绿**：50tool+36QA+231Web+912Corepure+61Jobspure+
  184CorePG+5JobsPG，专项/重复不另加。pnpm test/lint/typecheck/contracts/test:db均exit0，
  新测试Ruff/Pyright绿。现有Node exact24.12与本机24.19warning未改工具链。本轮Web
  未改，未重复历史Storybook/browser/build，全部工具会话已终态。
- PG总入口只用本轮新独占可销毁d6860594...／loopback随机端口，strictguard要求
  agent_faults QA库与experiment哨兵同属新实例，auth TRUNCATE不碰旧QA/生产。head0046、
  clients/idle-in-tx0；还有3PAUSED/1WAITING rooms及3SYSTEM_RECOVERY/1FINISHED matches
  为本套件fixture，不称逐行终态清空。随后labels/ID核验normalstop exit0/noOOM、rm-v
  精确自有container/volume，absent；不force/prune/删除镜像，不再误用保留QA总入口。
- 后续只读枚举发现ASR独立RTC入口仍wait_for取消SDK connect、局部Room成功后才赋值，
  是当前静态风险，未在该ASR路径原生复现或修复。不能由共享publisher两轮绿推断所有
  Core建连安全。永久pending/global事件queue字节、shutdown、SDK内部异常、取消抵抗
  physical回收、dense capture同步CPU、Spec299公平、目标staging和受保护发布仍未关闭。
  根PRD既有删除状态保留；真实50LLM/五完整媒体/180秒真人ASR/11音色盲听未验收。
- 本轮完整报告PartA/B、强红/绿日志、源码/聚焦patch、native检查点、资源终态和独立
  manifest在profile_output/spec274_publisher_startup/，旧CPU/Host manifests保持历史
  快照；核验后可协助清理临时测量，永久修复/回归/必要rollback基线不混删。

### 2026-10-14 全面目标继续：ASR 原构件强红、原生退出与 Spec301 设计

- 上一turn实际publisher修复/1479回归/native候选两轮为progress；本turn继续新增真实
  ASR receiver/runtime／actual Manager／actual SDK 和原生故障证据，非重复状态汇报。
  当前ASR业务SHA acb26d74ce5a179a239b3da5fa6e069236c3e444d95bd8448db1db6eea25a255
  未变，before同hash已保存；publisher仍bd9，未初始化／切换／提交Git分支。
- 永久test_asr_startup_cleanup.py 844f06a...11项原构件8产品红/3控制绿，2.60s；
  test_asr_sdk_connect_ownership.py 9d4934d2...12项7产品红/5控制绿，2.57s，所有setup/
  teardown/guards正确。合计23项15失败/8正常控制，不重复加首轮/追加/单独controls；
  新增强红尚未修复，上一1479标准绿是当时冻结套件，不冒称当前含23新测试全绿。
- SDK三入口（外部cancel／receiver自有10s缩短／实际Manager外层10s缩短）×迟到成功/
  失败六例投递前后采样，均遗留订阅1，成功没有ready/disconnect。外部/Manager取消
  还留开放receiver registry；内层timeout原清registry有效但join仍取消。原异常对象
  及控制先通过再失败产品断言，fixture最终退订不计应用回收。
- application三普通startup错误缺自有Room清理；Provider构造直接receiver会留部分
  _room接管，不能泛化到原runtime Exception分支（它有close）。两外cancel强红止于
  registry断言，下游oldtrack/replacement未执行不冒称实测。单startup event+inventory
  重复消费与handoff后同track/pub无重发重复投递分别留证；正常close重复cancel跳过
  Room清理强红。hung close late-error消费原控制绿，不把已有效预算误报为新故障。
- 必须保留两个ensure共用receiver时的正常控制：仅等待_lifecycle_lock的B被cancel
  不得盲pop/close A正常startup；actualSDK A join/ready/交付与后续EOS归零全部正常。
  设计需逐调用ticket/等价代次比较实际启动owner，不能只比receiver或复用Task。
- 新独占internal网络、本地LiveKit1.13.5和缓存Linuxarm64 SDK1.1.14两个fresh child
  实测精确原ASR receiver.start／ensure_match外部取消：FFI callback实际self-SIGTERM、
  exit-15，wall16.847/15.573s；ready/disconnect0，退出前订阅/queue1/1，Receiver未closed。
  350ms只延ConnectCallback Python投递，nativeRequest/Callback/kill真执行，父30s
  deadline未kill。无Source/trackconsumer/PCM/Provider/Settings/DB，不是生产历史事故归因。
- Native初始化FD18/退出前23，不用它宣称永久物理泄漏；native threads/即时RSS null。
  两case原红exit1如实保留，未放宽断言。客户端12d991...／server3e6a...／空internal
  networkf842...精确IDs/labels正常stop/rm、noOOM、ownedremaining0，共享镜像保留，
  不force/prune，不访问旧QA或生产。本轮未运行PG/标准总入口，全部工具会话终态。
- 官方LiveKit docs＋Server v1.13.5 primary源码证明同identity非resume join替换旧参与者，
  ASR固定jx-asr-matchid在迟到旧请求时会碰撞恢复连接；具体overlap仍为推断，尚未
  原生复现。仓库唯一jx-asr消费者为token生成，human/webhook/parser/grants均不依赖
  精确ASR格式。仅shield旧SDKjoin无法关闭该风险，固定identity屏障又使永久pending
  持续占原10s恢复窗口，不为避审批选择恢复效率更差方案。
- 已形成Spec301／规格入口：独立SDKowner、弱receiver+gate、局部一次交付、轨道去重、
  原10s/retry、精确invocation/registry回收及独立2s清理；明确每join唯一私有ASR
  identity增量。AGENTS未逐字禁suffix；身份delta超出原publisher明确切片，按其18/51
  的未批准前业务/媒体契约要求单独请求。async审批问题已发仍pending，无授权不实施
  该依赖工作；已有诊断/强红/native/方案和rollback已完成可review，不重复要publisher权限。
- 整体goal仍active，未complete/blocked：301身份和实施待批准；后续候选native overlap、
  pending字节/全局retired/shutdown、SDK内部异常、dense CPU、299公平、目标staging及
  受保护发布、真实五媒体/50LLM/180s真人ASR/11voice尚未验收。根PRD既有删除保留。
- 报告PartA/B、原source、全部阶段强红/控制、native检查点/收尾及新独立manifest位于
  profile_output/spec274_asr_startup/。此前publisher102/CPU/Host manifests保留历史，
  不覆盖成当前文件集。核验后可清临时测量，永久tests/回滚源单独保留。

### 2026-10-14 Spec301 明确批准：ASR 启动修复与两轮原生闭环

- 用户明确“批准”后实施301，上一goal turn有实际业务修复/标准回归/native证据，属于
  progress。继续完整目标，不标complete/blocked；本turn核到旧native handle已missing，
  六case results及cleanup均terminal，不重新启动实验。无Git分支/提交、密钥读取或生产操作。
- 先按已授权274完成独立normal close：acb14项5红9控制绿、late socket两项1红1绿。
  中间401e录音unlink失败后残留旧路径，actual old_run_speech可删除替换录音；保留强红，
  close异常分支只清四ref修为cdf，独立17绿。普通close先独立安排全部resourceowners，
  late producers终态后同旧Provider最多补close一次回收socket/capture，不代表阻止API请求。
  close58清单/PartB及cdf回滚副本独立保存，301直接基线为cdf，不倒退此已验证修复。
- 最终唯一业务 ASR runtime e7857d3094e81ca7df1f79b31978e540dab6bc8e3655261d010e348dabd5f8f9。
  独立shield SDK join保留10s，局部Room/Provider一次handoff及独立rollback；每join唯一
  私有ASR identity，weak listener/精确Room gate、inventory补轨/live publication去重。
  每调用failureticket在lifecycle锁内封闭，等锁cancel不退役primary；独立精确registry
  cleanup保护replacement。终态close先request_close阻止join后迟到Provider/consumer，
  _closed/detach仍原锁内时点。八相邻源码SHA/十五非启动方法AST不变，protocol仍119337。
- 原23startup/actualSDK强断言全绿；新增identity六项cdf六干净产品红→候选六绿。
  root首候选非connect Timeout误分类两红、reviewdbe三种terminal close竞态三红均修，
  最终reviewedges六绿；原assert/log/source不覆盖。相关113/独立46+12绿，不重复加总。
  两fixture工具误观测、分类测试额外旧资源断言、猜错路径与类型入口失败保留并单列。
- 301冻结完整标准1531绿：Node50/QA36/Web231/Corepure964/Jobspure61/CorePG184/JobsPG5；
  pnpm test/lint/typecheck/contracts:check/test:db全exit0/source前后同e785，全部工具终态。
  后来新增尚未修复风险测试不能沿用1531称新套件全绿。没有当前Web变更，不重跑历史
  browser/Storybook/build，无依赖升级，Node工具版本warning未改工具链。
- PG仅fresh独占75ea7d5f…/loopback随机32771，two distinct QA DB同新instance，dotenv
  guard/syntheticsecret脱敏，auth TRUNCATE不触旧QA。head0046/clients/idle-in-tx0，fixture
  PAUSED3/WAITING1/TERMINATED8 rooms等真实分布留证，不称逐行清空；normalstop0/noOOM/
  rm-v0/absent，仅自有实例/卷移除，images/旧库/生产未触碰。
- 实际native SDK1.1.14/LiveKit1.13.5/冻结ASR e785，receiver.start cancel、ensure_match
  cancel、同Runtime recoveryoverlap各两freshchildren全部exit0，wall22.369/22.239、
  22.380/22.236、25.124/25.027秒；旧ready→SDKconnect返回→disconnect，SDKcancel0。
  两overlap真实current→迟到旧 native dispatch，uniqueidentity不同且current各3.053秒
  保持可用；旧无Provider/consumer/callback，当前真实unopenedProvider构造close各1。
  每例20秒观察，无selfSIGTERM/parentdeadlinekill；最终FFIsubscriber/queue/join/listener
  Python tasks0。主观测task1/诊断持Room，FD8→18→21；native线程/即时RSSnull，峰RSS
  136372224–138330112B不当即时值或physical释放量。未送PCM，不称真实ASR发言验收。
- Native三新owned IDs client995e…/server9fbc…/internalnet30f3…按精确labels正常收尾，
  cleanup COMPLETE/noOOM/remainingowned0。7dc/dbe bundles只准备未native跑；cachedimages
  保留，无force/prune/production。独立rawderived核验、PartA/B和新manifest补齐，不覆盖
  原acb两selfSIGTERM/机制identity43/publisher102/ASR65/close58冻结历史证据。
- 301规格/索引/272已更新本地完成尚未发布；报告在spec301_asr_implementation与
  spec301_asr_candidate_native。继续迟到Provider请求退役诊断/Spec302草案，未批准不写
  protocol业务。永久pending/globalquota/shutdown/SDK内部fault/nativeEOS/denseCPU/
  299公平/staging及受保护发布、真人ASR/五媒体/50LLM/11音色尚未验收。根PRD删除保留。
  核验后可清临时测量文件，永久回归与必要回滚基线单独保留。

## 2026-10-15 — 完整流程候选1,564门禁通过；生产等待候场处理决定

用户批准Spec302后，关键协议／Session owner／回调返回撤销／pending连接恢复／旧pause intent与事件sequence切片完成，正常模型、重试、权限、RTC和权威计时保留。当前1,564标准＋浏览器248／Storybook94，类型／Lint／契约全绿，561 source前后冻结；12个同场场景完成240合成发言，真实独占PG全服务20speech到两四辩、FINISHED、评委／投票／AWARDS。模型／媒体为合成边界。

完整Core185／Jobs5门禁曾因新夹具遗留10个HOST_TTS红2项；实际claim UUID证实跨suite污染，测试finally仅清自有白名单后before2红／after2绿／fresh全190通过。未改Jobs业务或原断言，所有失败保留。自有PG均按ID/labels正常stop/rm、无OOM、client/idle0、sentinel保留；3PAUSED fixture仅在已销毁instance，不称逐行归零。Colima正常恢复前daemon stopped；未exec/SQL/清理旧dev，但其状态不声称完全未变。

发布资格及PartA/B见profile_output/workflow_candidate/release/RELEASE_REPORT.md。Backend209文件与Web完整同build均有SHA manifest；Web在服务器独立Node24.18/x86_64进程13关键路由＋16静态资源200，启动0.886秒、VmHWM122288KiB、正常TERM终态143、stage删除；未改运行服务或生产DB。Linux交叉构建libuv134、首错误showcase根URL404、B904／CLI／测试名／SQL收据错误和原PG红均保留。

生产只读health200、Core/Jobs/Web active及LiveKit v1.13.5 running、head0045。已知两WAITING房间仍在，非终态比赛0、房间2、对应租约0；旧终态时间缺63／lease1、nofile soft1024。按Spec283需用户明确取消两候场后走管理员接口，再维护备份、终态元数据修复、追加0046、已批准nofile drop-in、部署及真实供应商／全媒体QA／五分钟观察。没有重启、迁移或终止真实对象绕过门禁。Goal仍active；非关键Spec299未实施，真人设备／180秒／5场媒体／50路长LLM／11音色盲听未验收。临时runner/context／源码副本可验收后清理，永久回归、原始失败、hash和回滚证据保留；本机只有聚合运维与自有合成测试数据。

精确资格元数据：

{
  "backend_artifact_sha256": "163427a7fde857405e90095964a548a905b4ecb62c283e0f9531de4e82e158a0",
  "web_artifact_sha256": "c5e1c1ee7da27caf5747d47666f72b8e2631b202725bbbc1a13ec927fe186fdb",
  "web_build_id": "rXEZ_cELP2w10TtkgdzGb",
  "source_freeze_sha256": "b6575045cfb425f48887c9f4534f168b509bf5bcac5b6de6325526391198f072",
  "business_sha256": {
    "apps/core/src/jx_core/asr/protocol.py": "8687d144c054a04e2a5393f005d45c98402ed3ab12f8bb63b460e968c637948f",
    "apps/core/src/jx_core/asr/runtime.py": "a7b3b4ce7effb49d3c5d784a4fa9642129c5e0bf3d1804ad20a132c72bc1d523",
    "apps/core/src/jx_core/matches/domain.py": "7f29dc1303214d3fb414fcc348275e1ddb0aff69b2177b4b3b475b48ab54debf"
  },
  "migration_head": "0046_provider_capture_snapshots",
  "production_published": false,
  "goal_complete": false
}

## 2026-10-15 — 用户批准关闭旧候场后完成生产发布

用户明确选择关闭两个旧候场并继续发布。正常管理员登录、POST rooms/{id}/terminate
分别200／TERMINATED，登出200，历史保留；未保存密码／session或SQL修改状态。
发布ID20261015-asr302-6004cca8，维护前和停服务后非终态比赛／房间／租约0；正常
停Web→Jobs→Core，服务器私有custom DB备份48,094,551B且pg_restore --list通过，
原源码／旧Web／兼容回滚保留在服务器。追加0046及repair_terminal_state正常完成，
63个终态结束时间补齐，Core/Jobs nofile drop-in生效，正常启动Core→Jobs→Web。
发布runner实际exit0／健康和完整发布预检通过。备份和生产媒体、数据库、日志未复制本机。

独立核验209安装文件SHA全匹配，Web rXEZ_cELP2w10TtkgdzGb，模块从部署apps/core/src
加载，SDK1.1.14／LiveKit running；13公开路由＋16静态200，3未登录受保护API均401。
根目录0755、Core/Jobs实际nofile软硬524288。专属agent-browser会话验证首页图像已加载、
登录表单等待水合后正常，无Application error，会话已关闭；首未等待DOM和instrumentation
误判systemd安装路径保留，修正工具后复验，不把工具错误算产品红或抹掉原结果。

连续300.188秒／11次采样全健康、同PID／重启0、cgroup OOM0、进程swap0、观察期
warning-or-higher日志0；MemAvailable最低552740KiB。这是发布后五分钟观察，非五场压力。
实际TTS→PCM→RTC→真实Receiver→ASR以及当前LLM/Judge短链已通过，自有RTC／音频回收；
完整4v4真Runtime＋实际供应商／媒体正在新独占服务器PG中执行，每队2模拟真人＋2Agent。
没有生产创建比赛／用户，不伪造Provider callbacks，不把短链当全场。Goal继续active；
详情profile_output/workflow_candidate/release/RELEASE_REPORT.md及production_qualification.json。

真实完整媒体前两轮均自然到FINISHED和双方四辩，实际Provider／RTC链及资源正常回收，
211.677／228.895秒、QA Python峰RSS261376／262192KiB；但各JUDGE两次invalid导致
工具exit1，保持Goal active，不称完整QA通过。首旧global JudgeProfile配置差异后，
第二已用实际enabled RuleJudgeConfig仍失败；同配置8席短判据与旧global短控制均通过，
尚无确定失效类别。快速决策invalid4／2条按原重试／跳过规则仍完赛，理由缩到2字的
测试输入未消除，不能无证据归因为token截断。继续独占测试库内只导出shape／范围／
finish_reason诊断；不改生产模型／prompt／校验、不抹失败，证据归档于
profile_output/workflow_candidate/real_media_qa。真实归档音频仅检查QUEUED任务，未运行。

## 2026-10-15 — 生产已发布；完整真实媒体门禁失败，继续定位

六次真实混合4v4整体未通过：前两场FINISHED但AI Judge两次格式失败；full03–06末主持
ERROR，其中full04–06权威code为agent_pipeline_failed。full06实际Database5/10及内存
callback observer仍在shielded SDK connect native FFI completion阶段挂起；不是通过短链
即可关闭的问题。同一6921B失败synthetic Ogg解码通过并仅保留服务器私有目录。
固定4Agent＋5Host lifecycle02实测73.758s通过，15次原disconnect12–92ms正常，warning0，
5次原host finished在Actor.submit取消但自然FINISHED，不能把callback取消单独认定根因。
lifecycle01漏改旧count断言exit1原证据保留。22轮源／SHA／actual terminal及限制已归档
profile_output/workflow_candidate/real_media_qa/RESULTS.md与INDEX.json。

Root单次只读资源样本UTC18:14:02：LiveKit Dockerv1.13.5正常、FD9/nofile524287:524288、
OOMKilled=false；三个服务同发布PID、NRestarts0，MemAvailable949828KiB。Jobs/Web此时
有少量进程swap，不推广此前五分钟swap0。首工具读/proc/0错误保留，修复只读工具skip
未运行caddy systemd，未改服务。qualification已纠正遗留的候场blocker和服务变更标记，
publishedtrue而complete_flowfalse。下一自有最小切片free接末主持，业务源码和生产配置
未变化；实际Judge/AWARDS/归档音频、物理麦克风仍未闭合，Goal active。

Full07精确SDK ledger完整复验266.845s仍末Host ERROR／agent_pipeline_failed／exit1；
stage7正常COMPLETED四辩后Room16 disconnect等原生FFI683超过501ms，stage8Room18/19
同样；未到SDK687，不认定取消传播listener。stage8 Host retry4.688s成功、Agentjoin11.382s，
末Host两次13.7s挂起。最终owner/pending0、ownRTC/PG/audio清理通过，Judge未触发。
Free-closing01自然FINISHED但4pending令overallfailed，旧Task未采身份，不事后猜因。
当前生产/业务/锁文件仍1.1.14及原冻结SHA；授权测试范围272已补一次正式SDK1.1.20
独立target完整对照，生产依赖升级另写规格。官方tag对照FFI73→80、Rust0.8.1→0.9.2，
候选有相关native生命周期修复，尚无实际候选结果、不以发布说明认定当前根因。

## 2026-10-15 — 隔离SDK自然完赛；新Spec303审批待答

full08同一7230c792…probe改用独立官方RTC1.1.20 / FFI80（生产仍1.1.14），238.562s自然
FINISHED，19publisher joins返回，末Host3439ms成功，正常Human/Agent暂停恢复通过。
仍3次native disconnect预算取消，不能单轮宣称根因关闭。最终owner/pending0，ownRTC/
PG/audio及SDK target删除有收据。整体exit1为1423严格Speech状态断言，实际被拒状态未
采，不能回填或放宽；源码候选普通Agent pause未终态旧Speech，需PG复现。DDL没有
INTERRUPTED。Judge实际首次JSON有效1081tokens、8席、expression14/16越max10，非截断；
最终retry/AWARDS值未在断言前保存，不能认定通过。白名单prompt数字检查上限全unknown，
未证配置冲突。57个候选SDK/startup本机强回归通过，其中12用复制fixture只改SDK版本
pin，原生产fixture不动；不是候选全量1,564门禁。临时overlay已精确删除，源码/失败保留。

已写specs/303-complete-flow-rtc-and-judge-contract.md及规格入口，SDK固定、旧AgentSpeech
精确退役、裁判既有评分约束三个顺序切片，3完整串行和实际归档音频门禁；按AGENTS
未批准前不写业务代码，已通过async请求用户批准，等待答案，不假定同意。现业务561
freeze不变／生产302 release仍运行；Goal active。可继续独立测试工具诊断保存顺序、
源及证据清单更新，不能先改依赖或部署候选SDK。

## 2026-10-15 Spec 303 批准与切片实施

用户明确批准Spec303实施、完整复验并上线。此前待批准状态已解除；生产仍为20261015-asr302-6004cca8，新候选尚未发布。A只更新livekit 1.1.14→1.1.20及uv.lock和准确版本断言，无其他锁包升级。B的真实独占PG已复现普通暂停旧STARTED、finalizer持有旧FINALIZING、合法历史编辑context漂移三组遗留，新发言均FINALIZED；修复只精确退役旧Speech为RESET，保留Generation/finalizer/asset和迟到回调栅栏。C当前模型两次合成完整历史API对照均通过，严格JSON Schema/明确json_object分别756/751 tokens，8个原ID且全部量表合规；采用严格schema，保留1600预算及正常一次重试。切片回滚源码及新专项证据在profile_output/workflow_candidate/spec303/。三场实际RTC/ASR/TTS完整比赛、真实Jobs音频归档和受保护发布仍待执行，不能将旧1564门禁直接写为新候选通过。

## 2026-10-15 Spec303已发布，发布后QA进行中

最终567文件源冻结，1614标准检查通过（Node50、QA49、Web231、Core1030、Jobs61、独占PG Core188/Jobs5），Lint/type/contracts通过；C模型/预算/重试不变。full10/11/12同最终业务与SDK三场独立完整媒体全部passed，双方Human/Agent自由四组合、双方四辩、末主持、Judge首次SUCCEEDED、8席投票/重复拒绝与AWARDS完整；正规Jobs归档24/26/26输入、121044/121764/123204ms，输入/回放解码样本差0，全owner/pending0、RTC/PG清理。

实际发布20261015-spec303-29073b57，SDK1.1.20及四白名单文件安装，0046保持、86比赛/102房间/1203发言数量不变，服务器本地DB与70SDK文件SHA备份。首发布690ea321因jx-web Requires=jx-core导致Core stop连带Web退出而脚本漏start Web，preflight失败；旧4文件/SDK已精确恢复，手工正常start Web核验200，未恢复DB；第二发布补齐服务start/recovery Web后成功。旧失败和恢复收据保留，不称首轮成功。独立209安装SHA、SDK20、同Web BUILD_ID/13公开路由/16static/3保护路由初验通过，own agent-browser首页图像已加载/登录2inputs正常/session关闭。实际部署源码full13与五分钟观察仍在运行，尚不标最终全部完成。合成mic与物理mic、五场并发/50LLM/11盲听保持分开。

## 当前最终状态：Spec303完整流程版本已上线

20261015-spec303-29073b57已发布并完成全部本规格门禁。最终源码567文件冻结；1614标准＋Lint/类型/契约通过；发布前full10/11/12和实际部署SDK/业务full13共四场独立短时完整混合4v4、真实Provider/TTS/RTC/ASR、双方Human/Agent自由四组合、两四辩、末主持、有效Judge、8人排序/重复拒绝及AWARDS全部passed，四份正规Jobs回放READY且解码样本差0。full13 224665ms，16正式发言，普通Human/Agent暂停恢复，回放123944ms/472671B，owner/pending0。发布后300.073秒/11采样健康全200、三服务PID一致/重启0、无新增服务warning、guard0，209安装SHA匹配，SDK20与旧Web包一致。服务器自有测试PG/RTC/音频、342文件候选目录及原失败synthetic Ogg已精确删除，原历史/媒体/回滚备份保留。正式报告profile_output/workflow_candidate/spec303/RELEASE_REPORT.md；PartB清单最终生成。保留短时合成mic和物理mic区别；正式最长时长、五场压力、50LLM、11音色盲听未执行。有限连接关闭预算警告不宣称长期绝对无风险。

## 2026-10-15 用户追加每队2Agent/2人反复完赛测试（进行中）

用户本次要求反复模拟，新增仅测试wrapper：同一Python PID/同一asyncio事件循环/同SDK1.1.20，5场顺序完整短时4v4，每队真人席位2+Agent2，1/3/5实际Human/Agent暂停恢复，2/4正常。原full强断言/真实Jobs归档保持，新增全局任务基线及清理失败停后轮，实际暂停证据/自然恢复>=2900ms强门禁；本地48专项/Ruff/格式通过。已启动sole batch，同一livekit FFI进程，生产代码/模型/配置/服务不变，独占PG/RTC/媒体逐场回收；每场Manager等对象仍重建，不声称同Manager长驻或物理mic/并发。结果待真实batch terminal，当前运行中不能沿用前次4场通过计本批成绩。证据profile_output/workflow_candidate/repeated_4v4_20261015/。

## 2026-10-15 同进程每队2Agent/2模拟真人连续5场完成

本批single PID3812905/single event loop/SDK1.1.20/nativelib同SHA，5场18分25秒5/5完整passed；每场8固定/双方四辩/自由Human-Agent四组合/单FINISHED连续sequence/有效Judge首次/8名排序票重复拒绝/AWARDS/实际Jobs回放采样差0，79条正式发言。第1/3/5场两类实际暂停自然3秒恢复。跨轮全局async基线pending0，每场owner0/RTC/PG/音频清理，服务器11文件工具目录最终删除，代码/锁SHA不变，Core3627171/Jobs3627173/Web3627175及重启0/健康200保持，历史102/86/1203不变。新增batch48专项、tooling Node50+QA97、Ruff/格式/diff通过。

保留风险：清理后当前RSS 223.7→277.4MiB（229020/247792/264924/278624/284016KiB），FD全20、线程13/13/13/14/15、后段增长变小；未证明泄漏或找到测试观察包装跨轮root，需未来Python堆/FFI订阅/原生句柄分层。5条快速自由decision格式失败按原skip处理；6TTS/3媒体cleanup预算警告但所有owner/pending0，不称无异常。短时合成mic，单场Runtime对象仍重建，不覆盖物理/最长时长/同Manager长驻/并发。当前报告profile_output/workflow_candidate/repeated_4v4_20261015/REPORT.md，测试完成，不需为本次测试重新上线；生产版本仍20261015-spec303-29073b57。

## 2026-10-15 Spec304明确批准，切片候选复验中

用户已明确批准Spec304实施、完整测试并上线，并指定主持显示“龙风月瑶”且固定实际音色longanwen_v3.1。目标540a67d7比赛的只读证据确认跨真人speech_id复用、98字逐字重复和固定阶段缺少历史；未证明服务器持方互换或该场主持资产中途换声。新候选包含ASR旧身份退役/权威截止、重置一致性、结构化单份历史与身份、管理员阶段重置及LLM占位、主持保留音色和冻结输入、大屏各状态及全屏布局。新增0047仅追加Speech.origin；历史比赛不改写。

当前纯Core1114/Jobs61、Web260/Storybook96/浏览器268（另12既有跳过）、工具Node50+QA147通过；完整独占PG第二轮Core210/Jobs5/应急34通过，第一轮陈旧fixture三个失败原证据保留并精确修正。尚发现3秒交接后的旧ASR合法failure未终态原Speech，正补真实PG回归及最小修复，不能把现有全绿推广到该边界。Linux独立Web首次锁安装下载timeout及一次缓存镜像未就绪失败均保留，正在同源重试；生产包尚未构建通过。三场正常/自然到时/恢复完整Provider语音、实际prompt配对比较、资源切换与发布均未执行，当前生产仍Spec303，Goal active。证据位于profile_output/workflow_candidate/spec304和profile_output/spec304；新主持QA输入已修正为无Agent头像，并以实际VoiceProfileCreate校验，非仅mock通过。


## 2026-10-15 — 赛后投票与展示本机／线上只读核对

用户要求查看本机和服务器开发状态，本轮未实施、发布、重启、切换展示或投票。
线上只读采样2026-10-15T06:05:57Z：Core/Jobs/Web active，Core loopback live/ready
均200；四个showcase页面HTTP200，未登录showcase API401。首次把Core health路径
请求到公网Web入口得到404，修正为既有8100健康入口后200，不认定产品健康失败。
Web BUILD_ID rXEZ_cELP2w10TtkgdzGb，构建manifest有judges/vote/awards/admin四路由。
赛后后端f4de524f…及0045 migration 3824e856…与本机SHA相同；两项Web源码原始SHA
不同，但同一Prettier规范化后完全一致，差异为格式。生产库只读事务核验head0046、
三表存在，showcase1条（JUDGE_REVIEW、未发布）、评委条目0、观众票0，账号/比赛
唯一约束存在；没有调用会创建默认展示记录的已登录GET，也未导出生产明细或凭据。

本机重跑test_postmatch_showcase.py与test_postmatch.py共31通过（3.64s）。既有独占
测试库5场报告有完整八席排序、单票提交/重复拒绝及AWARDS证据；本次没有重新进行
登录浏览器或手机真人验收。实现已有点评/胜方/最佳辩手、排名交换选择器、排名汇总、
前三和完整名单、后台阶段发布；每账号每场一票，访问还要求既有房间成员或管理权限。
缺口：无二维码/扫码入场路径；观众奖与评委奖共用awards页，未分独立页面；后台阶段
改变仅影响数据与投票开关，没有展示端自动切页；后台按钮显示英文枚举，UI没有阶段
撤回按钮。Showcase TS类型仍手写，虽已有生成契约，后续实现应按AGENTS复用生成类型。
当前判断为基础功能已上线，现场扫码/分屏颁奖/展示联动未完整交付；未因本次只读核验
改规格状态或把历史测试推广为现场验收。原工作树变更保留，本轮仅追加本记忆。

## 2026-10-15 Spec304完整复验补修，尚未发布

当前生产仍20261015-spec303-29073b57，本规格未写生产资源/比赛/用户、未重启服务。只读目标比赛、24次串行同模型配对及主持前缀/谢幕实际调用已完成；候选字面重合下降但不宣称语义永不重复。Web260/Storybook96/浏览器268（12既有跳过）均绿；Linux同Node24.12/锁定pnpm正式pnpm build及UID1000冒烟通过，新BUILD_ID eTp9xpoGM_OdBPrWeF7oD，tar d35afcb9…；构建下载/3GiB虚拟机OOM失败原证据保留，临时Colima6GiB已恢复原3GiB及旧卷/镜像/停止容器。

pg-full-06的Core/Jobs1422与当时标准1985均绿，但新增实际应急测试不允许沿用为最后上线资格。normal01/timeout01真实短混合4v4自然完赛、有效Judge/AWARDS/正规Jobs回放采样差0，仅诊断旧源；recovery01在隔离库唯一admin fixture失败已修，recovery02在全比赛TTS记录增量断言失败，全部owner/pending0且RTC/PG清理，不计通过。该增量尚未证明本场实际新增合成原因；独立实际AST强控制另确认pending Agent/Host启动可在暂停清理后迟登记，作为发布阻断修复，而非把计数简单豁免。

管理员稳定operation发言重置现统一暂停并自然3秒恢复（旧无operation自动重试不改），已发请求的取消capture现在由精确owner持有至持久化，避免提前清空。最新启动退休runtime d2f572a…用有界单match pending token和服务端执行时权威检查覆盖锁等待/旧清理/Host排队；新37控制、276 Agent单测和25真实PG边界已绿。最终全套pg-full-07运行，QA工具正加有界实际synthesize/raw入站/ACK后PCM观察、逐次reset历史窗口、pending媒体owner0；重置阶段将用同AF3新尝试，跳过下一NG3，双方四辩均须实际发声。必须最后新冻结源重跑normal/timeout/recovery三场，再受保护发布；所有01/02失败及旧源证据保留，不能混入新cohort。

发布工具44强控制、打包器30安全控制已绿，保护活动比赛、全部业务源码CAS/历史计数、完整旧Web和DB备份、jixia读/cache权限、追加0047、精确主持generation/manifest资产CAS与实际解码审核。打包器当前拒绝旧cohort，尚无true资格/发布包。发布时仅23源文件（22业务py+0047），全122审计；主持正常资源接口切换longanwen_v3.1并禁用reserved正式Agent，保留历史。实际发布、服务后完整媒体与五分钟观察仍待完成，Goal active。证据profile_output/workflow_candidate/spec304/及profile_output/spec304/web-final/。

## 2026-10-15 Spec304最终真实恢复03发布阻断

候选0eddbb9517/182文件的recovery03在136893ms真实失败，全部源码前后SHA一致。reset_speech/stage实际提交并自然恢复；NG3 skip的精确占位TTS0、全局TTS增量0，但被退休的Agent协程在PAUSED+media_fenced=true仍进入_execute。独立本地实际方法强控制确认此前已建spool/decoder/RTC连接及LLM诊断（实际外部模型/TTS/PCM均0），不是可豁免noop。C按已批准304继续最小首IO与各启动await后精确guard补修；保留03原检查器和失败收据，不改成通过。所有owner/pending0、RTC/PG/媒体自有资源清理已通过；生产业务/规则音色/服务仍未变更。真实麦克风材料TTS可正常留生产OPS_PROBE ExternalCall/capture审计，不能宣称整个生产数据库只读。标准tooling03 Node50+QA275、lint/typecheck04/契约03已通过，但必须等新runtime冻结后的完整Core/Jobs和三场04才形成发布资格。


## 2026-10-15 — 观众投票需求澄清与网址提供

用户明确澄清：观众投票无需登录、无需二维码；先前将二维码列为缺口的判断作废。
本轮请求提供页面网址，未实施权限修改。当前showcase API仍要求登录和房间成员身份，
与澄清需求不符，不能宣称匿名投票已可用。服务器只读事务查到现有展示比赛
a4357c3c-4b0d-41e0-b3ed-344c4b590e49，阶段JUDGE_REVIEW、未发布；向用户提供该场
showcase/vote、judges、awards、admin完整网址，并明确当前投票未开放及登录限制。
后续匿名投票若实施，须先按AGENTS写规格，明确匿名防重复与公开数据边界；本轮没有
改变服务、发布状态或投票数据。

## 2026-10-15 Spec304 2073标准通过，实际RTC终局仍阻发布

最终runtime703819ef…已补generation-return与_execute首IO/decoder/publisher await后精确guard；ACK前后迟到已提交UUID均FAILED且旧资源0，新任务自然恢复完赛，29实际PG控制通过。5旧capacity fixture固定注册generation后保持原强断言，全Agent288绿。完整pg-full-11 Core1194+228/Jobs61+5=1488唯一passed、0fail/error/skip，309源成员/SHA不变、0047/owned cleanup、Ruff/Pyright0；加Web260/Node50/QA275为2073，SB96/Browser268+12既有skip与Linux构件保持冻结。旧partial09/10与旧cohort不算资格。

候选d3db7fb08e/182文件recovery04真实298250ms失败于结束主持action9，两次SDK Room.connect各17.7秒超时；双方四辩已FINALIZED，reset两次/skip和3秒自然恢复、media观察与占位TTS0均通过，所有owner/pending/owned资源终态清理。连接前17次<1s，18/19升10.46/18.35秒，20/21未及时完成；不能把agent_pipeline_failed名称认作文本API故障。SDK1.1.20合成原方法控制显示迟到join/listener在native完成/EOS后可自动清理，未证明永久FFI泄漏。A准备同WSS24次ABBA短连接对照并补安全异常类/函数行号；F只读LK/OpenResty/sar，未见持续资源打满，仍需日志分层定位。所有03/04失败留存，禁止资格豁免、加timeout/retry或静默换SDK。生产业务/规则音色/服务仍未发布/重启；正式OPS_PROBE审计保留，不声称全DB只读。

打包器新事实锁2073/1488/275，SHA dd0e0d7d…，30控制绿，仅核验未写包；3个ops保持原审核SHA。下一步真实RTC根因→同源三场新cohort通过→独立review→受保护正式发布→安装后实际完整场及300秒观察；当前Goal active且无发布资格。

## 2026-10-15 Spec304轨道取消订阅真实P0追加补修

cohort05同7038业务/新ecf439工具三场recovery/normal/timeout均真实PASSED且182文件source前后不变，owned资源清理；但不作为最后发布资格。原SDK1.1.20真实native本地控制另复现：track_unsubscribed后原QA/ASR旧consumer仍持Track与AudioStream；同user/epoch新轨道存在时，迟到旧PCM3200bytes实际进入新current speech。精准cancel旧task后旧FFI释放，新consumer/新PCM不受影响，最后FFI0。该缺陷直接属于304旧ASR输入/任务清理P0，Spec304末尾已补实施细化，C获授权独占Core ASRruntime/tests，A独占QA观众精确退役与同网络双观众对照，绝不只修测试遮产品；无新模型/协议/权限/计时/预算。

05 recovery259454ms全强门禁绿，Jobs25sources/replay差0/两reset窗口真实typed请求/占位TTS0/Judge8排名/AWARDS；末Host join仍7198ms，未称RTC根因已修。客户端FFI峰37/队列730/FD499、终态close后0，提示旧track持有与完整负载有关。短原route24 ABBA全绿8.2s（无audience/ASR/TTS负载）；04仍原失败。F420秒只读collector从07:19:35UTC晚于05启动，涵盖后段/可能其他QA，统计不等match资格；无原始IP/secret导出，精确诊断进程已清。

full11旧1488+Web260/Node50/QA293=2091标准绿，SB96/Browser268+12 skip/Linuxartifact保持；Core补修后必须新同源完整backend与cohort06三场，再独立review及发布/安装后完整QA/300秒观察。打包器4495e6c9…硬锁2091，30控制绿，仍无true资格/发布包；F准备只本地审阅的detached启动模板，不执行生产。当前生产仍Spec303，业务/规则音色/服务未改变，Goal active。

### 轨道强控证据校正

C复核原native控制：unsub后旧consumer live/stream未dispose/FFI2为确定证据；先前3200bytes只计总数，未区分新旧native source，不能据此单独断言这3200来自旧PCM。已向用户说明并保留原件，补current_task+sample marker来源控制；已知来源的cancel-resistant unit仍验证旧输入应被拒绝/新轨继续。上一段“3200bytes实际进入新speech”仅这项来源归因被本条撤回，生命周期缺陷不撤、精确退役修复继续，禁止后续报告沿用未经标记native字节归因。

### 来源标记native复核已完成

同一SDK1.1.20/真实native源标记红绿控制已终态。独立旧bf111方法overlay：旧task迟到6400bytes，其中1600样本明确old marker1000，新task另1600样本marker2000；unsub后旧stream/FFI仍活。候选ASR73767：旧task/stream已关、迟到旧bytes/oldmarker均0，新轨继续1600个marker2000样本且迟到旧回调不关新轨，最终FFI0。证据real-full/asr-stream-retirement-native-red-attributed.json与-native-green.json。此条替代原未归因3200bytes论据，确认同类P0但不宣称其为04网络超时唯一因。新Core13专项/完整ASR130/Ruff/Pyright绿且源已最终freeze，A全backend12与双观众24真实对照并行，不改Agent7038/模型/协议/预算。

## 2026-10-15 结束轨道修复后发现Agent失败身份排序P0

ASR最终477266aa…/新25专项/完整ASR142/原native4终止顺序标记0旧流+新流继续已冻结，QA full9d99…/313及双观众24真PCM旧consumer1→24新0通过。full12完整1501仅7376过渡源诊断；full13新ASR完整pure1219+61/targetPG38绿，但CoreDB227+1fail（Jobs完整未跑）不具发布资格。first-token真实failed-run task退出后Actor仍RUNNING；A原case5次重跑绿不能解释，独占PG仅反转gen2.created_at为gen1前1ms，即原handler静默丢gen2failure、两GenFAILED、ActorRUNNING/seq0强红，明确算法不能依赖墙钟。未证明线上实际NTP跳变。

C还核text-generated callback按maxattempt未限context，同context恢复又重从attempt1，SQL排序单修不足。root已授权同304六字段/旧代次P0最小公开只读Agent matcher（current注册run+envelope/action/profile/未fence），C独占Agent runtime/helper tests，A独占service+PG回归。ASR4772与QA9d99不动，后续Agent7038/service95c将被新source替代；必须新full14与最终cohort07三场、独立5critical review，不混06。Spec304末尾已补当前回调判定细化。

06 recovery已PASSED258858ms，AudioFFI峰18/队列68（05峰37/730）、末Host join179ms；一处中段join5284ms，未宣称全链路恒定低延迟/04唯一根因。06 normal/timeout chain9748继续旧源immutable666440aaee/manifest7a745…，只诊断。F collector06 420秒terminal、启动晚如实标覆盖，全部诊断进程清理，无生产代码/资源/服务更改。launcher c262…/14控制已备，包仍无资格。Goal active，生产仍Spec303。

### 回调身份补修进展（尚未冻结/发布）

06 normal/timeout链已全部退出0、前后182源SHA一致，三场只诊断，不沿用为新回调源资格。failure路径新增真实PG强控制与原组共26/26通过：墙钟逆/平/顺、9字段旧结果、同context新run从attempt1、尚无generation配置失败context7、同code两次恢复均ERROR、旧logicalCallID拒绝，以及GenAttempt2/SpeechAttempt1已pop的合法finalizer失败。配置失败内部logicalCallID仅私有MatchCommand，不进入公开事件；跨run错误receipt ID区分，避免重复同code静默卡住。

C Agent runtime daf992f2…已加入仅文本专用的只读matcher：只容许同注册run早到speechNone向首PCM真实speech绑定，其余信封不变；failure仍严格全等。55专项/Agentpure343与Ruff/Pyright0通过；service文本事务边界仍由A补实际红绿，完整full14和最终07三场尚未开始。ASR4772/QA9d99/Web构件不变，生产仍Spec303，Goal active。root已准备安装后QA驱动，只有最终包真实发布后才能运行installed08，不预写通过。

### 最终07应急场通过，完整15仍在验证

最终Agentdaf992/ASR477266/domain40c31/service23ec47与QA9d99、wrapper2e8整体freeze，stage3a83374e32/182文件manifest7386a576…前后一致。recovery07实际PASSED252361ms，独立partial0error：两reset与三自然resume3067/3059/3072ms、占位TTS0、无paused新媒体违规、两四辩5390/7490ms真实音频、9 typed请求/两窗口1与8成功请求、Judge/AWARDS8排名/Jobs25sources6447084samples差0，owners/pending0。22RTCjoin最大525ms、末180ms，4条tts_connection_cleanup_timeout保留，不称无warning/04唯一因已修。正常07/timeout07链17641串行运行。root先前链等待SCP目的文件时碰空JSON，仅本地退出未新跑实际场；确认完整after attest后重启后两场，见orchestration-07.json。

full14 pure1274+61绿但target49有旧capture fixture1红：gid仍None且原SQL仅ISNULL/STARTED命中别场调用，非本run；C只修fixture非空gid+exactmatch+LLM_SPEECH+STARTED与samegid断言，业务182SHA不变，原强capture/HTTP0等不减。完整full15全四套重新起，full14红保留且不算资格。root lint09/typecheck09/契约04/Node50+QA313/tooling06已终态0，5critical打包器32控制绿，仍未计数更新/写true包。生产RO08:34UTC active matches/rooms/leases0，三旧PID0重启、schema0046/旧Web、Spec304备份0，尚未发布。

### 最终候选形成发布资格（生产尚未切换）

full15完整1579unique(Core1274+239/Jobs61+5)、0fail/error/skip，313源前后SHA一致/ownedPG清理；49定向与52应急已包含不重复计。root Web260+Node50+QA313共2202标准，SB96/Browser268+12既有skip、lint09/type09/contract04/tooling06/diff0通过。cohort07三场PASSED252361/229290/226727ms，182文件六份前后SHA一致；三actualJobs回放6447084/6478296/6740844samples均差0、Judge/AWARDS/8排名/dupvote/owners0。C正式QUALIFICATION_REVIEW SHA03961f61…通过，保留TTS关闭有界cleanup诊断4/1/0；明确abort/FD专项与pending0不能独自证明所有FD。

F打包器054371/五critical/32控制据实际锁2202，已实际check-only→write-package，release/QUALIFIED_FULL15_COHORT07严格30成员/23install/audit122/unchanged103；真实stdout PREPARED_FULL15_COHORT07.json SHAeb6389f1…，manifestf0a90d…/qualificationd3b392…/auditb255f3…，root原launcher本地check0。prelaunch08:57无active比赛/房间但1终止房租约，最后08:49:30UTC；08:58:20idle529.8秒。保持原10分钟门禁，等08:59:31UTC后再RO确认才实际launch，不强踢。当前未部署/重启/改主持资源，Goal active；上线后还需installed08完整比赛和300秒独立审计。

## 2026-10-15 Spec304正式交付：已上线并完成安装后混合比赛

**当前生产权威版本：20261015-spec304-8cd36502**，17:02:41北京时间发布完成。已审原publisher/资源工具/审计3ops未变，严格30文件合格包由root单次detached launcher启动，worker/publisher均0，23源/0047/newWeb eTp9xpoGM_OdBPrWeF7oD、SDK1.1.20一致。发布前无活动比赛/房间，仅精确终止房旧租约超过10分钟后正常服务释放并审计。50,930,299字节真实PG备份、兼容源码、完整旧Web保留于 /opt/jixia-release-backups/20261015-spec304-8cd36502；历史比赛/房间/发言总数不变，未恢复/下载生产数据库。

最终业务Agentdaf992/ASR477266/domain40c31/service23ec47；full15后端1579unique、2202标准、SB96/Browser268+12既有skip/正式Linux pnpm build均通过。发布前三场07(252361/229290/226727ms)与安装后一场08(248974ms)实际Provider/RTC/ASR/TTS混合4v4通过，每队2模拟真人+2Agent；双方四辩/谢幕/唯一FINISHED/有效Judge/八人排名/dupvote拒绝/AWARDS/实际Jobs解码采样差0及owners/pending0。安装场6339564samples/25sources，2reset/1AI占位TTS0/3自然恢复/9typedctx均通过；候选182+安装122 beforeafter与三元manifest/reportSHA绑定。独立安装review98dce324…冻结，prequal03961…不改。TTS有界close诊断4/1/0/4保留，底层abort/实际FD专项与pending0证明边界明确。

独立资源RO审计6rule/46assets全通过，唯一启用主持龙风月瑶longanwen_v3.1 rate1/model3.1，精确gen/manifest/审批/SHA一致、reserved Agent可用0，原2enabled/4disabled生命周期不变。整段欢迎含旧cue实际6.47–22.61s，5–8s是另测新增前缀，谢幕5.21–5.69s，不混淆；未实体盲听、未全历史Room snapshot逐字段比对。真实首页/登录浏览器无JS页面错误，独立session关闭。

**观察结论必须分开：** 原INSTALLED_AUDIT_01为exit1/stable=false，原因19条TERMINATED历史比赛ended_at NULL。11样本/300.082秒运行指标均正常：122文件一致，Core/Jobs/Web PID4110699/4110701/4110703全程不变、0重启、health200、guards0、服务journal warning及以上0。F只读server-memory解析真实SHA验证发布前PGdump，当前/备份完全同19个ID与id/room/status/created/ended五字段，规范化SHA两侧9125ed…；非此次发布新增，不靠创建时间猜测，未回填历史记录。原auditfalse不改，单独OPERATIONAL_OBSERVATION_REVIEW f702cd9d…记运行true/metadatafalse，LEGACY_TERMINAL_METADATA_AUDIT d541030c…记基线确证，C独立复核均通过。遗留19历史元数据缺口明确保留，不把原整体审计写成通过。

root清理13个经来源/manifest/工具SHA核验且无活动进程的本轮/tmp目录，释放115294208B约110MiB；所有8候选源归档、失败收据、本地发布包/审阅证据保留，生产backup/history/runtime无清理。Spec304与索引状态已更新，完整最终报告 profile_output/workflow_candidate/spec304/release/RELEASE_REPORT.md。未声称实体mic/正式全时长/五场并发/50路LLM/11音色盲听通过；RTC04单次慢连接仍无唯一因果结论。

## 2026-10-15 用户指定大屏顶栏收敛（待Web发布）

本轮明确要求全屏隐藏房间号/号码/赛制人数和文字记录/退出大屏按钮，只用Esc退出。先在已批准Spec304追加用户具体范围后，最小改DebatePageLayout条件显示，保留品牌/辩题/比赛状态，普通模式保持既有内容，未改LiveMatchPage的原生与fallback Esc处理。既有浏览器用例改验隐藏→Esc恢复并用每project新截图目录，20项/四project/七size全部绿；Web260unit/Lint/types绿。首轮4red系宽屏隐藏按钮getByRole不匹配已用真实DOM筛选修，首轮types与Playwright build清.next并发引起generated文件缺失，按顺序后绿，原日志留存，无放宽产品。

新Linuxamd64/Node24.12标准pnpm build181.255秒成功，273源输入与workspace全相同；Web BUILD_ID HX2A8FaQQj1tYWmwcfx9d，tarSHA3a2a1ca0caf83cc9930d034981817d334a259348e78ada61f0037ae79f26c5b2。真实tar2246项逐SHA、UID1000/静态HTTP/3认证边界smoke以及该实际bundle的5浏览器用例全部通过(同native/fallback/七size)，owned容器清理。临时Colima6GiB正在恢复3GiB，旧容器/卷/镜像保留。F正在准备仅Web切换+旧完整Web回滚与最小真目录控制，C独立只读复核；禁止运行304全栈publisher，Core/Jobs/DB/主持资源不在这次修改范围，当前尚未发新Web。证据profile_output/spec304/clean-header/。

### 大屏顶栏补充已上线并完成正式网址展示验证

新的Web构件HX2A8FaQQj1tYWmwcfx9d已实际发布，PUBLISHED_WEB_REPORT.json记published=true/installed；旧完整Web与缓存备份保留于/opt/jx-web-clean-header-20261015-058dd5fe/web-before。最终Web专用工具5573d4e…通过7项真实目录成功/回滚控制、Ruff/严格Pyright及C独立复核；仅切换jx-web，前后三次活动房间/比赛均0，122后端安装源SHA一致，Core4110699/Jobs4110701均active且PID不变/0重启，新Web4168963active/0重启，健康/静态资源检查通过，数据库写入0、后端服务操作0。原304后端20261015-spec304-8cd36502及19条历史元数据缺口不变。

正式HTTPS原native/Esc检查通过，首轮fallback在合成终态事件处失败：正式站只允许localhost测试socket注入，未改生产限制。C仅在ignored生产UI夹具内拦截精确合成UUID的WebSocket，API静态响应、未知API/外部HTTP/媒体/signaling阻断；原test bodies逐字不变，再跑2项5.9秒全绿（含七视口、终态、Esc恢复普通顶栏）。网络收据保留，不称真实登录/比赛/语音复验；首红及sourceSHA原件保留。Colima已恢复原3GiB/2CPU、旧容器/卷/镜像保留，owned构件冒烟容器清理。最终证据profile_output/spec304/clean-header/及production-ui-owned-01/。

最终生产复查三服务PID不变、active/0重启、live/ready200，新Web BUILD一致。核验发布进程已退出、精确5件上传文件及工具SHA后，仅移除本轮/tmp/jx-web-clean-header-058dd5fecfd949f39f43ab9a481cf700；/opt完整回滚备份和本地全部证据保留，见FINAL_PRODUCTION_CHECK.json。GitHub同步只包含本轮Web/测试/规格/记忆5个文件，私有凭据、生成缓存和本地.claude文件均不加入。
