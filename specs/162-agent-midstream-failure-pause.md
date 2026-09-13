# 162：Agent 播放中失败立即暂停

## 范围

修复 Agent 已开始播放后 TTS/LLM 媒体链路失败时，失败回调被媒体清理阻塞、比赛仍继续空倒计时的问题。播放中的失败必须先进入 Core 权威错误状态，再由既有中断清理流程停止旧任务和媒体。

## 验收

- Agent 已有 `speech_id` 后发生 provider failure，即使 `_cleanup_attempt` 卡住，也会先调用 `handle_agent_failure`。
- MatchActor 收到当前 generation 的失败后进入 `ERROR/RECOVERY_REQUIRED`，不继续消耗发言倒计时。
- 播放前第一次失败仍遵循完整重试一次的既有语义；过期 generation 仍由服务层丢弃。
- 不修改比赛状态机、媒体协议、数据库 schema 或 migration。

## 回滚边界

仅回滚 AgentRuntime 的失败处理顺序及对应回归测试。

## 状态

已完成并发布。

## 验证证据

- `apps/core/tests/test_agent_voice.py`：18 passed。
- `apps/core/tests/test_match_domain.py`：36 passed。
- Ruff check、Ruff format check：通过。
- 正式站 `jx-core` 重启后 active/running，Core live/ready 通过，启动后无 warning 级日志，公网比赛页返回 200。
- 服务器回滚副本：`runtime.py.rollback-20260819-162`。
