# Spec 270 — 百炼接入与模型稳定性选型

- 版本：v2.0
- 状态：已实施、测试并发布（2026-10-11）
- 依据：用户允许尝试其他接入方式及 3.0 模型，目标为稳定和效率。

## 范围

1. 在生产服务器使用合成文本/音频、脱敏指标比较业务空间 OpenAI SSE、
   DashScope 原生 SSE、`qwen3.7-flash` 与其日期快照，以及 Audio ASR/TTS 3.0/3.1。
2. 复用现有有界适配器和探针；修正旧线上探针在送入 PCM 前等待 ready 的死锁。
3. 保持北京业务空间专属域名。仅当成功率、首结果/完成延迟、输出校验和音色
   兼容性均支持选择时才变更正式配置；短样本不宣称证明长期可用性。
4. 优先固定可用的日期快照以减少别名升级漂移；保留一次重试后暂停和禁止运行中
   自动切换。若其他方案无收益，保留已经通过验证的接入/语音模型。
5. 为已有 SSE 适配器补齐真正有界的响应头、首正文、空白/心跳和错误边界，
   不改变 10 秒预算；诊断不含 key、原始供应商消息或比赛内容。

## 验收

- 每个可用 LLM 候选至少 20 次，最大并发 4，混合短正文、结构化 JSON 和长上下文。
- Audio 候选至少 3 次；同一合成 PCM 用于 ASR 对比，TTS 使用各自官方音色。
- 回归覆盖响应头停滞、首正文前心跳、正文后心跳、空 SSE、取消和重试；
  Core 测试、Pyright/Ruff 和 probe 测试通过。
- 配置变更不得改写已创建比赛冻结快照；保留旧配置备份和源文件回滚路径。
- 上线后服务 active、健康检查 200、正式配置 provider chain 通过。

## 回滚边界

备份并恢复本次修改的源文件/模型配置；不降级数据库，不删除历史比赛、音频和
调用诊断。现有失败/暂停比赛保留。无新增微服务、数据库 schema 或媒体协议。

## 官方依据

- [接入域名](https://help.aliyun.com/zh/model-studio/beijing-access-information)
- [DashScope 请求格式](https://help.aliyun.com/zh/model-studio/qwen-api-via-dashscope)
- [Qwen3.7 Flash 与快照](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)
- [TTS 音色兼容性](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list)
- [ASR Streaming 参数](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-java-sdk)

PRD 引用路径在当前工作区缺失；本切片不修改产品行为，以 TechDesign/Spike 和
已发布 Specs 260、266、269 的外部调用边界为准。
