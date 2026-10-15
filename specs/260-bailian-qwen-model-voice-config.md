# Spec 260：百炼 Qwen 模型与音色配置

状态：已完成（2026-10-01；生产验证完成）

## 范围

- 将启用的 LLM 模型资源切换为北京地域 OpenAI 兼容接口的 `qwen3.7-flash`。
- 保持现有百炼北京地域的 TTS/ASR WebSocket 入口，使用 `qwen-audio-3.1-tts-flash` 与
  `qwen-audio-3.1-asr-flash-streaming`。
- 保留正式使用中的五个 Agent 音色和 `xuyanchu_v3.1` 主持音色。
- 增加官方音色列表中的备用音色，初始为 `DISABLED` 且未校准，不自动改变正式 Agent 的头像或比赛映射。

## 验收

- 生产环境没有进行中的比赛时才执行变更。
- 数据库启用模型的 `model_id` 为 `qwen3.7-flash`，兼容接口地址和 API Key 加密字段保持有效。
- Core、Jobs 重启后健康检查通过，TTS/ASR 配置通过 Settings 校验。
- 运行脱敏的 LLM、TTS、ASR provider probe；不输出 API Key、原始供应商响应或音频内容。
- 备用音色均来自百炼 `qwen-audio-3.1-tts-flash` 官方列表，保持 `DISABLED/NOT_CALIBRATED`，待试听与校准后再启用。

## 回滚边界

- 回滚只恢复配置字段、音色资源和服务进程，不回退数据库 migration，不删除历史比赛或外部调用记录。
- LLM 回滚目标为变更前的模型字段、加密 API Key 字段和生成参数；环境文件保留变更前备份。
- 如果 provider probe 失败，恢复变更前配置并重启服务，不自动切换供应商或模型。

## 依据

- [Qwen-Audio-TTS 音色列表](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list)
- [实时语音合成](https://help.aliyun.com/zh/model-studio/realtime-tts-user-guide)
- [Fun-ASR-Realtime WebSocket API](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api)
