# 稷下人机交互平台 v1.0

稷下是一个桌面优先的人机实时辩论实验平台：公开注册用户进入房间，选择真人席位或观战；Agent 通过 OpenAI 兼容 LLM、Fun-ASR 和 Qwen TTS 参与线性赛制辩论。单实例部署最多同时运行 5 场比赛。

## 事实来源

- [产品需求](./稷下人机自动辩论实验平台需求文档.md)：用户流程、比赛规则、失败语义和验收目标。
- [技术设计](./TechDesign-Jixia-Debate-MVP.md)：模块边界、数据模型、实时媒体和部署约束。
- [实时语音 Spike](./docs/research/realtime-voice-spike-2026-07-24.md)：ASR/TTS 实测证据与限制。
- [v1.0 开发规范](./AGENTS.md)：实现、测试和发布契约。
- [v1.0 规格入口](./specs/README.md)：新切片只记录仍然有效的变更。

历史切片规格和旧工作记忆已从 v1.0 仓库移除，不作为当前需求来源；发布证据只保留在 `docs/research/`。

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

生产使用 PostgreSQL、单实例 `jx-core`、自托管 LiveKit、本地音频文件和 systemd/Caddy；不引入 Redis、Kafka、Kubernetes、OSS 或默认 Docker Compose。比赛状态、权限、计时和失败语义均由 Core 权威维护，Web 只负责展示与交互。
