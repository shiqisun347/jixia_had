# v2.0 技术栈

| 层       | 方案                                                                                                  |
| -------- | ----------------------------------------------------------------------------------------------------- |
| Web      | Next.js、TypeScript、Tailwind、TanStack Query、Zustand、Storybook、Playwright                         |
| Core     | FastAPI、Pydantic、SQLAlchemy async、psycopg、Alembic、MatchActor                                     |
| Jobs     | Python async worker、PostgreSQL 有界任务队列                                                          |
| 数据     | PostgreSQL；本地文件保存音频和导出                                                                    |
| 实时媒体 | 自托管 LiveKit；`qwen-audio-3.1-asr-flash-streaming`；`qwen-audio-3.1-tts-flash` Opus 双流；PyAV 解码 |
| LLM      | OpenAI 兼容流式适配层                                                                                 |
| 部署     | Ubuntu、systemd、OpenResty；`jx-web`、`jx-core`、`jx-jobs`、LiveKit                                   |
| 现场展示 | 独立 `apps/showcase`：Flask、SQLite；不参与比赛编排                                                   |

## 运行边界

- `jx-core` 是单实例权威，使用 PostgreSQL advisory lock。
- Web 不决定比赛状态或计时；LiveKit 只传音频，命令走 Core WebSocket。
- 实时队列有界，FFmpeg 不进入实时 TTS 播放路径。
- 不引入 Redis、Kafka、Kubernetes、OSS、LiveKit Egress 或第二个编排服务。

架构和故障语义以 TechDesign 为准；Spike 保留历史实测证据。两份文档中的旧模型和代理软件名称不作为当前运行配置。当前非密钥配置见 [运行基线](../resources/v2/runtime.env.example)，安装与依赖边界见 [复现说明](../docs/v2-reproduction.md) 和 [部署说明](../docs/deployment.md)，不根据历史名称自动切换模型。
