# 249 真人 ASR 终结竞态修复

> 版本：v2.1
> 状态：已发布，真人现场复验待完成
> 批准依据：用户要求快速修复真人结束发言后被要求重复发言的问题（2026-09-02）。

## 范围

- 修复竞赛制自由辩论中 `speech.finish` 已被 MatchActor 接受后，ASR 关闭错误与缓冲 final
  回调竞态导致 `speech.reset`、旧发言人重新获权的问题。
- 修复 Core 重启时从 `FREE_SELECTING` 快照恢复，却因残留 `current_speaker_*` 字段回退为
  `HUMAN_READY_TO_START` 的问题。
- 保留 ASR 失败 Speech 及低基数错误码用于诊断；不记录原始供应商响应或密钥。

## 非范围

- 不切换 ASR 供应商、模型或请求协议。
- 不改变固定发言阶段现有的一次自动重试、第二次失败暂停语义。
- 不修改数据库 migration、比赛计时起点、发言权规则或 LiveKit 媒体协议。
- 不直接改写当前比赛数据库状态。

## 验收

- 自由辩论真人结束发言后，即使 ASR 关闭错误迟到，也不重置为同一人再次发言。
- 失败 Speech 保留 `FAILED` 状态和错误码，下一发言人选择窗口可继续推进。
- `FREE_SELECTING` 状态经历 Core 系统恢复和管理员恢复后仍为选择窗口，旧 speech、用户、
  方位和席位字段均不复活。
- Core 定向测试与 Ruff 通过，生产部署后验证服务健康和一次真人结束发言链路。

## 回滚边界

- 仅回滚本规格涉及的 MatchActor、ASR 回调服务和回归测试；不删除或改写已产生的比赛、
  Speech、事件、音频或审计记录。

## 发布证据（2026-09-02）

- Core MatchActor/ASR 定向测试 `108 passed`，相关 Ruff 检查通过。
- 仅同步 `matches/domain.py` 与 `matches/service.py` 后重启 `jx-core`；服务为
  `active/running`、`NRestarts=0`，live/ready 均通过，启动后无 ERROR/CRITICAL/Traceback。
- 未直接修改生产数据库或删除比赛数据。真人结束发言、ASR final 和下一位获权仍需当前比赛
  现场复验后才能宣称端到端通过。
