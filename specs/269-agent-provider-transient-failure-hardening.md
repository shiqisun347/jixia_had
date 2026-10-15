# Spec 269 — Agent 供应商瞬时失败收敛

- 版本：v2.0
- 状态：已实施、测试并发布（2026-10-11）
- 日期：2026-10-11
- 依赖：Specs 162、217、223、266

## 背景

线上比赛 `324550` 在 Agent 首次发言时，两次 LLM 流都在首 token 前结束，
比赛进入 `ERROR`。当前重试紧接着开始，且空流与其他传输异常都归并为
`llm_provider_failed`，网页也没有对应的具体错误文案，导致瞬时供应商抖动
难以恢复和定位。

## 范围与约束

- 保持 TechDesign 的普通 LLM “失败完整重试一次，第二次失败暂停”语义；不增加
  无限重试、不静默切换供应商或模型、不改变比赛计时和状态机。
- 第一次 Agent 失败后，在第二次尝试前使用固定的短暂退避，让供应商连接和本地
  清理完成；退避有界且不阻塞其他比赛。
- 供应商在首 token 前正常结束流时，记录稳定的 `llm_first_token_timeout`，与
  真实 HTTP/传输失败保持可区分；不保存原始响应、密钥或内部路径。
- Web 对 `llm_first_token_timeout`、`llm_stream_stalled` 和
  `llm_provider_failed` 显示可操作的中文/英文说明。

## 验收

1. 空 SSE 流在首 token 超时类别收敛，仍遵守一次重试和最终暂停边界。
2. 第一次失败到第二次开始之间存在不超过 1 秒的固定退避；成功重试仍完成正常
   Agent 播放和收尾。
3. 失败诊断只包含稳定错误码和异常类型；外部请求体、响应体和 API Key 不进入
   日志或页面。
4. Core Agent/LLM 定向测试、Web 错误文案测试、Ruff/TypeScript 和生产构建通过。
5. 发布前不改写非终态比赛；发布后 Core/Web 健康接口连续通过，脱敏 LLM/TTS
   探针成功。

## 回滚

仅回滚 Core runtime、Web standalone 和本规格源文件，恢复本次发布前备份后重启
`jx-core`/`jx-web`；不需要数据库 downgrade，不删除已有比赛或诊断记录。

## 发布证据

- 线上回滚副本：`/opt/spec269-202610111344`。
- Core/Web 文件哈希与本地构建一致；四服务 active，Core/Web `NRestarts=0`，
  Core live/ready 和公网首页返回 200。
- 脱敏生产探针通过：LLM 5/5、TTS、评委 LLM 和 LiveKit；未把真实麦克风或完整
  现场比赛写作通过。ASR 原有探针的 `wait_ready` 节奏仍有超时遗留，实际先送 PCM
  的会话方式已观察到 83ms ready。
