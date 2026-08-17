# AGENTS.md — 稷下 v1.0 开发契约

本文件只规定工程工作方式。产品行为以 PRD 为准，架构和故障语义以 TechDesign 为准，语音性能以 Spike 为准；冲突时停止相关实现并记录冲突。

## 结构与边界

- `apps/core` 是唯一权威：FastAPI、MatchActor、房间/比赛状态、权限、计时和外部模型编排。
- `apps/jobs` 运行有界后台任务：主持音频、赛后归档、导出和排行榜。
- `apps/web` 只负责展示、交互和缓存，不成为比赛状态或计时来源。
- `packages/contracts` 的 OpenAPI 生成类型是 Web/Core 契约来源，不手写重复 DTO。
- PostgreSQL 事务提交后才更新 Actor 和广播；同一比赛的状态修改必须串行进入 MatchActor。
- 实时队列必须有界；不引入 Redis、Kafka、Kubernetes、OSS、LiveKit Egress 或新的微服务。
- 不修改既有 migration；数据库变更只能追加新 migration。`api.md`、`.env` 和任何密钥不得读取进文档、代码或日志。

## 工作流程

1. 先读本文件、PRD、TechDesign、Spike 和相关 `agent_docs/`。
2. 变更超过单文件文档修正时，先在 `specs/` 写 v1.0 规格，说明范围、验收和回滚边界；未批准前不写业务代码。
3. 一次只实现一个可验证切片，优先复用现有模块，不顺手重构无关代码。
4. 每个逻辑变更后运行对应测试、类型检查、Lint 或浏览器验证；失败必须修复或如实报告。
5. 完成后更新 `MEMORY.md`（当前状态、证据、遗留风险）和规格状态。

## 关键实现规则

- 所有外部回调校验 `match_id`、`speech_id`、`attempt_no`、`generation_id`、`connection_epoch`、`context_version`；过期结果直接丢弃。
- Agent、ASR、TTS 的失败必须遵循 PRD 的重试/暂停语义；不能静默切换供应商或模型。
- Agent 中断恢复必须清理旧任务，再由权威恢复事件重新启动；不能靠前端倒计时或本地状态推进比赛。
- LiveKit 只传输音频；比赛命令和状态事件使用 Core WebSocket。
- 用户错误使用可理解、可操作的 Toast 或页面说明；堆栈、供应商原始响应和内部路径只进脱敏诊断。
- 动画只使用 transform/opacity，尊重 `prefers-reduced-motion`，固定核心布局尺寸，避免动态内容撑高页面。

## 命令

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm lint
pnpm typecheck
pnpm test
pnpm contracts:check
pnpm test:storybook
pnpm test:browser
pnpm build
```

数据库集成使用独立 `TEST_DATABASE_URL`；生产构建只能使用仓库脚本 `pnpm build`，不得直接运行裸 `next build`。专项 Playwright 使用 `node scripts/run-playwright.mjs test ...`。

## 禁止事项

- 不初始化、切换、合并、删除 Git 分支，不 force-push。
- 不删除历史数据库数据、修改已发布 migration 或更改认证/权限、比赛状态机、计时起点和实时媒体协议，除非规格明确批准。
- 不把未完成的真人语音、五场压力、50 路 LLM 或 11 音色盲听写成通过。
- 不提交 `api.md`、`.env`、密码、token、临时凭据和生成缓存。

## 当前版本

当前固定版本为 **v1.0**。历史切片规格和旧验收材料已移出仓库；新工作必须以 v1.0 事实来源和 `specs/README.md` 为入口。
