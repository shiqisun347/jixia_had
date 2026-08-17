# 项目工作记忆

产品与架构事实来源：根目录 PRD、TechDesign 和 `docs/research/realtime-voice-spike-2026-07-24.md`。历史工作记忆已移出 v1.0 仓库。

## 当前版本

- 版本固定为 `v1.0`。
- Core：FastAPI 单体 + MatchActor + PostgreSQL；Jobs：有界后台任务；Web：Next.js；媒体：自托管 LiveKit + Fun-ASR + Qwen TTS。
- 不使用 Redis、Kafka、Kubernetes、OSS、LiveKit Egress 或默认 Docker Compose。
- 生产环境最近一次发布包含 160b Agent 中断恢复修复：旧 AgentRun 会在终态清理，暂停/错误/系统恢复会清理媒体任务，恢复前同步清理避免同 action 被误判为重复运行。

## 最近验证

- Core Agent 恢复相关测试：55 passed。
- Pyright、Ruff：通过。
- 正式站 `jx-core`、`jx-jobs`、`jx-web`、`jx-livekit`：active；Core live/ready 和公网首页：通过。

## 仍需单独验收

- 真人浏览器麦克风与 180 秒现场 ASR。
- 11 音色真人盲听。
- 五场并发与 50 路 LLM 压力。
- PostgreSQL Docker helper 的真实 daemon 冒烟。

## 维护规则

每个 v1.0 切片完成后只在本文件记录当前状态、验证证据和遗留风险；不要复制完整需求、实现细节或历史讨论。
