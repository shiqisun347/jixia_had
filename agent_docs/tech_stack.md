# v1.0 技术栈

| 层 | 方案 |
| --- | --- |
| Web | Next.js、TypeScript、Tailwind、TanStack Query、Zustand、Storybook、Playwright |
| Core | FastAPI、Pydantic、SQLAlchemy async、psycopg、Alembic、MatchActor |
| Jobs | Python async worker、PostgreSQL 有界任务队列 |
| 数据 | PostgreSQL；本地文件保存音频和导出 |
| 实时媒体 | 自托管 LiveKit；Fun-ASR realtime；Qwen TTS Opus 双流；PyAV 解码 |
| LLM | OpenAI 兼容流式适配层 |
| 部署 | Ubuntu、systemd、Caddy；`jx-web`、`jx-core`、`jx-jobs`、LiveKit |

## 运行边界

- `jx-core` 是单实例权威，使用 PostgreSQL advisory lock。
- Web 不决定比赛状态或计时；LiveKit 只传音频，命令走 Core WebSocket。
- 实时队列有界，FFmpeg 不进入实时 TTS 播放路径。
- 不引入 Redis、Kafka、Kubernetes、OSS、LiveKit Egress 或第二个编排服务。

实时参数和故障语义以 TechDesign/Spike 为准，不在此重复维护。
