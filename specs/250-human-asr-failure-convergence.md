# 250 真人 ASR 失败收敛与比赛防卡死

> 版本：v2.1
> 状态：已实现并发布
> 批准依据：用户批准《真人 ASR 与比赛卡死彻底修复方案》（2026-09-02）。

## 范围

- 修复自由辩论选择事件把缺失 `decision_round_id` 序列化为字符串 `"None"`，导致内部定时
  命令持久化抛出 UUID `ValueError` 并停止推进的问题。
- 内部定时命令发生领域、持久化或未知异常时进入可恢复错误，不再产生无人处理的 Task 异常。
- ASR 失败按瞬态、音频、内容/时长、配置/协议分类；失败 Speech 保留，禁止在真人发言中途
  静默自动 reset。
- 为 `SPEECH_FINALIZING` 增加有界服务端收尾看门狗；成功 final 取消影响，超时进入可恢复错误。
- Web 显示“正在整理文字记录”、ASR 错误分类和可操作恢复说明；管理员诊断继续复用现有工作台。

## 不变量

- Core/MatchActor 是唯一状态权威；状态变更先提交数据库再广播。
- 不静默切换 ASR provider 或模型，不直接改生产比赛状态，不修改既有 migration。
- 回调继续校验 Speech、attempt、connection epoch、context version、opportunity 及 generation。
- 日志只记录低基数错误码、命令类型、sequence、异常类型、SQLSTATE/约束；不记录密钥、Cookie、
  Authorization、供应商原始响应或用户语音/文字内容。

## 验收

- 缺失、`null`、`"None"` 或非法 UUID 不再逃逸为 `ValueError`；内部定时失败会进入
  `ERROR / RECOVERY_REQUIRED` 并留下脱敏诊断。
- `asr_pcm_queue_full`、`asr_empty_audio`、`asr_task_failed` 不会在 `HUMAN_SPEAKING` 中静默
  重置；失败 Speech 保留，比赛明确进入可恢复状态。
- `SPEECH_FINALIZING` 在 final 成功时正常推进，在无 final 时有界进入恢复状态；迟到回调不改变
  新 Speech 或新机会。
- Agent 决策失败按既有 SKIP/真人等待/fallback 语义收敛，不触发真人 Speech reset。
- Core/Web 定向测试、Ruff、ESLint、TypeScript 通过；发布前活动比赛为零，发布后 live/ready、
  服务重启数和错误日志通过核验。

## 回滚边界

- 回滚本规格涉及的 Core/Web 源码和测试，不删除或改写已产生的比赛、Speech、事件、调用、音频
  或审计记录。
