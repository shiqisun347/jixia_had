# 稷下人机交互平台 v2.1

源码仓库：<https://github.com/shiqisun347/jixia_had>

稷下是一个桌面优先的 4v4 人机实时辩论实验平台：公开注册用户进入房间，选择真人席位或观战；Agent 通过 OpenAI 兼容 LLM、Fun-ASR 和 Qwen TTS 参与正式辩论。单实例部署最多同时运行 5 场比赛。v2.0 新建房间只支持 4v4，历史非 4v4 房间和比赛仍可读取。

## 事实来源

- [产品需求](./稷下人机自动辩论实验平台需求文档.md)：用户流程、比赛规则、失败语义和验收目标。
- [技术设计](./TechDesign-Jixia-Debate-MVP.md)：模块边界、数据模型、实时媒体和部署约束。
- [实时语音 Spike](./docs/research/realtime-voice-spike-2026-07-24.md)：ASR/TTS 实测证据与限制。
- [v2.1 开发规范](./AGENTS.md)：实现、测试和发布契约。
- [v2.x 规格入口](./specs/README.md)：新切片只记录仍然有效的变更。
- [当前系统参考](./docs/reference/current-system-reference.md)：代码地图、状态不变量和发布门禁。
- [线上实验操作手册](./docs/线上实验操作手册.md)：管理员、参与者、专家和服务器发布操作。
- [v2.1 完成审计](./docs/v2.1-completion-audit.md)：自动化、数据库、浏览器与线上证据。

规格 161–170 和 `docs/v1.0-audit.md` 作为 v1.0 历史回归证据保留，不扩大 v2.x 产品范围。当前论文实验适配见 Specs 201–204。

## 工程结构

| 目录                 | 责任                                                  |
| -------------------- | ----------------------------------------------------- |
| `apps/core`          | FastAPI 单体、MatchActor、房间/比赛、ASR/LLM/TTS 编排 |
| `apps/jobs`          | 主持音频、赛后归档、导出和排行榜任务                  |
| `apps/web`           | Next.js 用户端和管理员后台                            |
| `packages/contracts` | OpenAPI 生成的 TypeScript 契约                        |
| `migrations`         | PostgreSQL 版本迁移，只允许追加                       |

## 本地开发

环境要求：Node 24、pnpm 11、Python 3.12、uv。先准备独立 PostgreSQL，并将本地配置写入 `.env`（不要提交真实凭据）。

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm db:migrate
pnpm dev
```

常用门禁：

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm contracts:check
pnpm test:storybook
pnpm test:browser
pnpm build
```

数据库集成、真实认证浏览器和正式站验收分别需要独立的 `TEST_DATABASE_URL`、浏览器环境和服务器授权；证据不足时不得把真人语音、五场压力或 50 路 LLM 验收写成已通过。

## 运行边界

生产使用 PostgreSQL、单实例 `jx-core`、自托管 LiveKit、本地音频文件、systemd 和当前
服务器的 1Panel OpenResty 入口（Caddy 为历史设计基线）；不引入 Redis、Kafka、Kubernetes、
OSS 或默认 Docker Compose。比赛状态、权限、计时和失败语义均由 Core 权威维护，Web 只负责展示与交互。
