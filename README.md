# 稷下人机辩论平台 v2.0

面向现场赛事和研究实验的 4v4 实时辩论系统，支持全真人、全 Agent 和混合队伍。Core 负责比赛状态、权限和计时，Web 负责交互和大屏；独立 Showcase 提供现场投票、点评及颁奖展示。

源码仓库：<https://github.com/shiqisun347/jixia_had>。

## 稳定基线

产品版本为 **v2.0**。Core/Jobs 的内部包版本 `2.1.0` 保留用于兼容既有构件，不表示扩大产品范围。

- Core：已发布 Spec308/309 及此前已发布的可靠性修复；没有带入未发布的 310/312 Core 候选。
- Web：Spec312 已验收展示切片，线上构建 `J6YFijOoE7TSV9ijePYSO`。
- 数据库：追加迁移到 `0049_agent_human_avatars`；旧迁移不可改写。
- 配置：Spec327 已发布固定发言模板，YXH 修订 275、新国辩修订 146，新房冻结生效，旧房和历史不回填。
- Spec327 同批 12 条全文 LLM/TTS 中 10 条联合合格；8 条 120 秒四辩为 90.47025～111.05025 秒。两个时长失败及内容风险保留，不保证后续所有发言都命中窗口。

新房仅支持正式 4v4；历史非 4v4 数据保留读取兼容。部署采用单实例 Core、Jobs、Next.js、PostgreSQL、自托管 LiveKit 与原生 systemd/OpenResty，不引入新的编排服务。

## 文档入口

- [复现与首次初始化](docs/v2-reproduction.md)：工具链、空库、管理员、公开资源及独立 Showcase。
- [本次稳定版验证](docs/releases/v2.0.0/README.md)：测试、备份恢复及已知限制。
- [部署](docs/deployment.md)和[备份恢复](docs/v2-backup-and-recovery.md)：生产数据边界与恢复验收。
- [工程契约](AGENTS.md)、[需求基线与覆盖说明](稷下人机自动辩论实验平台需求文档.md)、[技术设计](TechDesign-Jixia-Debate-MVP.md)、[语音 Spike](docs/research/realtime-voice-spike-2026-07-24.md)。
- [规格入口](specs/README.md)、[当前系统参考](docs/reference/current-system-reference.md)、[线上操作手册](docs/线上实验操作手册.md)。

## 工程结构

| 目录                          | 用途                                             |
| ----------------------------- | ------------------------------------------------ |
| `apps/core`                   | FastAPI、MatchActor、房间/比赛、ASR/LLM/TTS 编排 |
| `apps/jobs`                   | 主持素材、归档、导出、排行榜与有界后台任务       |
| `apps/web`                    | 用户端、管理端和比赛大屏                         |
| `apps/showcase`               | 独立现场展示、手机投票和控制台；独立数据与凭据   |
| `packages/contracts`          | Core OpenAPI 生成的 TypeScript 契约              |
| `migrations`                  | 只追加的数据库迁移                               |
| `scripts/ops/v2_resources.py` | 公开资源包验证及明确新空库的导入                 |
| `resources/v2`                | 脱敏资源包、必要素材与非密钥运行基线             |

## 开发与检查

工具链：Node 24.12.0、pnpm 11.18.0、Python 3.12、uv 0.11.2。先按[初始化文档](docs/v2-reproduction.md)准备私有环境和独立 PostgreSQL，迁移后用 `scripts/ops/bootstrap_v2_admin.py` 初始化新管理员，再导入公开资源；仅执行迁移不会生成当前生产配置。

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm dev
```

标准门禁：

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm contracts:check
pnpm test:storybook
pnpm test:browser
CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build
```

数据库和认证浏览器测试包含清表夹具，只能使用新建、独立、可销毁的测试库，不能指向生产或备份恢复副本。生产 Web 必须使用仓库 `pnpm build`，不直接运行裸 `next build`。

## 数据与验证边界

公开资源只包含当前两条正式赛制及其 198 条冻结模板、必要资源和主持素材；生产完整 734 模板、账号、比赛、录音和日志属于私有备份。真实连接串、模型密钥、加密主密钥、Session、口令和证书私钥均不进入 Git。

源码安装、健康检查或新房冻结通过，不能代替完整混合比赛、真人麦克风、RTC 接收、现场扬声器、五场压力、50 路真实 LLM 或音色盲听。Spec328 的干净环境门禁、备份恢复及 Git 同步按本次独立证据记录，不借用旧发布结果冒称完成。
