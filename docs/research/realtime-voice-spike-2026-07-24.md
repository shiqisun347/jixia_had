# 实时语音链路 Spike 记录

- 日期：2026-07-24
- 地域：阿里云百炼华北 2（北京）专属工作空间
- 目标：验证 `qwen3.7-plus -> qwen-audio-3.0-tts-flash -> fun-asr-realtime` 的双流式链路
- 凭据：从本地 `api.md` 临时读取，仅用于本次验证；本文不记录密钥

## 结论

1. `qwen-audio-3.0-tts-flash` 和 `fun-asr-realtime` 均可在当前工作空间实际调用。OpenAI `/models` 未列出它们，但该列表不能作为语音模型授权的唯一依据。
2. 短语音链路的延迟满足 MVP 目标：ASR 首个结果约 230ms，TTS 从首批 LLM 正文到首段音频约 451ms。
3. `qwen3.7-plus` 必须关闭思考模式。默认思考模式首次正文约 7.28s；关闭后约 0.57s，长稿测试约 1.04s。
4. TTS 必须在收到 LLM 首个有效正文后才启动 task。空 TTS task 等待文本超过约 23s 会被服务端终止。
5. TTS 文本入口需要有界队列和轻量节流，但不需要等待完整句。服务端仍负责自动分句和连续合成。
6. TTS 输出应使用 Ogg Opus，而不是传输或写入完整 PCM。综合带宽和二次编码质量，建议百炼输出 32kbps Opus；实测三分钟预计约 0.75MB。
7. 测试音色 `longanlingxi` 未返回字级时间戳。该音色应标记为“不支持时间戳”，播放字幕采用句级事件和语速估算降级方案。
8. 单个 Fun-ASR task 持续处理约 145s、且语音几乎无长停顿时，会随机漏掉连续的大段内容。调整采样率、VAD 静音阈值、多阈值模式和语义断句均未彻底解决。
9. 同一音频拆成 5 个约 30s 的 Fun-ASR task 后，全部成功，合成音频字符错误率约 0.47%。MVP 不应让单个 ASR task 覆盖完整的最长发言。

## 实测结果

### 短链路

| 项目 | 结果 |
|---|---:|
| TTS WebSocket 建连至 `task-started` | 154.5ms |
| TTS `task-started` 至首段音频 | 302.7ms |
| 短音频时长 | 6.8s |
| ASR `task-started` | 125.4ms |
| ASR 首个增量结果 | 229.8ms |
| ASR 结束至最终完成 | 448.2ms |
| 短音频识别 | 逐字正确 |
| ASR 字级时间戳 | 18 个，正常返回 |

### LLM 与长 TTS

| 项目 | 结果 |
|---|---:|
| LLM 默认思考模式首次正文 | 7278.4ms |
| LLM 关闭思考模式首次正文（短请求） | 566.0ms |
| LLM 关闭思考模式首次正文（长稿） | 1039.3ms |
| 长稿长度 | 709 字符 |
| TTS 音频时长 | 144.8s |
| LLM 首次正文至 TTS 首段音频 | 451.2ms |
| TTS 完成全部合成的墙钟时间 | 22.88s |
| TTS 字级时间戳 | 0，当前音色不支持 |

长 TTS 生成速度快于实际播放速度，因此不能把完整 PCM 长期积压在内存中。后续补充测试表明，使用 Ogg Opus spool 可以显著降低带宽、磁盘和内存压力。

### TTS Opus、语速与控制

同一段 73 字中文文本使用 `longanlingxi`、24kHz、目标 24kbps、固定 seed 进行测试：

| `rate` | 云端首个 Opus 包 | 解码后有效音频时长 | 文件大小 |
|---:|---:|---:|---:|
| 0.9 | 292.9ms | 19.85s | 66,436B |
| 1.0 | 442.7ms | 18.59s | 62,089B |
| 1.1 | 332.9ms | 16.67s | 56,141B |

- 输出为标准 Ogg Opus，可由 FFmpeg 增量解码；本机从启动解码器到首批 PCM 约 130ms。
- Opus 解码后再次送入 Fun-ASR，73/73 个规范化字符正确，CER 为 0%。
- `rate` 能稳定改变有效语速，但变化不是严格线性关系，必须按“音色 + rate”实测校准。
- 服务端提供句子 begin/end 事件及原始句子文本，但该音色的 `words` 为空。字幕可使用句级边界，再在句内按实际音频时长估算。
- 当前 task 可在输出过程中取消；收到 `task-finished` 后，同一 WebSocket 可使用新 `task_id` 完成下一次合成。
- 固定 seed 的重复输出 Ogg 字节不完全相同，但解码 PCM 重叠部分 PSNR 约 173dB，声学上基本一致；尾部长度约有 0.12s 差异。不能使用文件哈希作为音色一致性验收方法。

### TTS 传输与解码方案比较

按 144.8s 长 TTS 在约 22.88s 内返回完毕估算，云端可能以约 6 倍实时速度突发输出：

| 百炼输出 | 单路突发入站估算 | 五路突发入站估算 | 三分钟临时数据 | 本地处理 |
|---|---:|---:|---:|---|
| 24kHz PCM | 约 2.43Mbps | 约 12.2Mbps | 8.64MB | 无 Opus 解码，但仍需向 LiveKit 提交 PCM |
| 16kHz PCM | 约 1.62Mbps | 约 8.1Mbps | 5.76MB | 质量和音频带宽下降 |
| 24kbps Opus | 约 0.17Mbps | 约 0.85Mbps | 实测约 0.59MB | 需要一次本地 Opus 解码 |
| 32kbps Opus | 约 0.22Mbps | 约 1.1Mbps | 实测约 0.75MB | 需要一次本地 Opus 解码 |

LiveKit Python SDK 的公开 `AudioSource.capture_frame` 只接收 PCM `AudioFrame`，没有公开的编码 Opus/RTP 注入接口。自行注入 RTP 会绕开发言时钟、队列、暂停和 LiveKit SDK 的兼容保证，不采用。

成熟实现参考：LiveKit Agents 的 `AudioStreamDecoder` 使用 PyAV/libav、后台单线程和低延迟参数，将 Ogg/Opus 增量解码为 `rtc.AudioFrame`；其房间输出将 `AudioSource` 队列设置为 200ms。MVP 借鉴该实现方式，但不引入完整 LiveKit Agents 编排框架。

本机（Apple Silicon 8 核）PyAV 实测：

- 单线程解码约 987 倍实时速度。
- 五线程并发总吞吐约 1549 倍实时速度。
- 导入 PyAV/Numpy 后进程约 40MB；五路高强度解码峰值约 56MB，增量约 16MB。
- 收到首批压缩字节至首个 PCM frame 约 7.4ms。

生产服务器仍须复测，但即使性能低一个数量级也有充足余量。相较外部 FFmpeg 子进程约 130ms 的启动开销，PyAV 更适合实时主链路；FFmpeg 只用于赛后合并、标准化和诊断。

音质方面，Opus 会造成额外一次有损编码：百炼 Opus 解码为 PCM 后，LiveKit 会再次编码为 WebRTC Opus。相同 PCM 的本地模拟中，一次 24kbps 编码相关系数约 0.952，双重编码约 0.885；双重编码后的音频仍被 Fun-ASR 73/73 字正确识别。该指标只能证明可懂度，不能替代真人盲听。32kbps 作为默认值，在五路场景下仅比 24kbps 多约 0.25Mbps 入站，优先保留更多音色细节。

百炼返回的多句 Ogg 流可完整解码，但容器 duration 可能不准确。实时计时使用解码 PCM 样本数；赛后使用 FFmpeg `asetpts=N/SR/TB` 解码并重新编码为标准单流 Opus，使下载文件的时长和定位正常。

### 长 ASR

测试音频相同，长度 144.8s。CER 仅表示合成音频链路的相对表现，不等同于真人、噪声和不同麦克风环境的最终质量。

| 采样率与模式 | 最终句数 | 尾包延迟 | CER |
|---|---:|---:|---:|
| 24kHz，VAD 1300ms | 4 | 2625.3ms | 40.13% |
| 24kHz，VAD 1300ms，多阈值 | 5 | 25.9ms | 13.48% |
| 24kHz，VAD 800ms，多阈值 | 4 | 2712.1ms | 34.80% |
| 16kHz，VAD 1300ms，多阈值 | 4 | 21.9ms | 32.29% |
| 16kHz，语义断句 | 5 | 417.7ms | 12.23% |
| 16kHz，拆为 5 个约 30s task 并发验证 | - | - | 0.47% |

长 task 的错误主要表现为整段缺失，不是零散同音字错误。同一音频按约 30s 拆分后得到 637/638 个规范化字符，说明 TTS 音频完整，问题集中在 Fun-ASR 长 task 的内部连续分段。

### 远程集成复测（2026-08-04）

在目标 Ubuntu 服务器和正式 HTTPS 站点完成了 006 链路复测。浏览器使用受控中文测试音频作为 microphone MediaStream，完整经过 LiveKit 发布、`jx-core` Python RTC 订阅、16kHz 单声道 PCM 转换、Fun-ASR 双流式识别、业务 WebSocket 字幕和 PostgreSQL final 持久化。该测试证明系统集成链路可运行，但不等同于真人麦克风和复杂公网环境验收。

- 19.5 秒业务发言得到实时 interim 和 final，数据库记录首个 interim 703ms、finish 后 final 15ms；页面在 final 前保持整理状态，随后才推进下一动作。
- 同一条 Fun-ASR 连接以真实墙钟速度发送 31 秒 PCM，自动轮换为 2 个 task；首个 interim 231ms，两个 task 均正常 `task-finished`，最终拼接文本非空。
- 文字稿 raw/display 双字段持久化成功；本人修改 display 后接口返回 200，比赛 `context_version` 从 0 增至 1，raw 保持不变。
- 首次 ASR 超时按规则标记失败并退回重新开始；第二次业务发言成功，说明单次重试和过期 attempt 隔离路径已实际触发。

同日完成 007 Agent 语音远程复测：正式 HTTPS 站点创建并启动一场全 Agent 的 1v1 线性比赛，4 个固定 Agent 动作均由 `qwen3.7-plus` 流式生成、Qwen Audio TTS 双流式合成、PyAV 增量解码并经 LiveKit 播放，最终比赛状态为 `FINISHED`，4/4 均首次尝试成功。数据库记录的 LLM 首 Token 为 726–1035ms，首音频包为 218–470ms，TTS 完成延迟为 2466–2879ms；Opus 资产大小为 720–796KB，解码播放时长为 22.9–24.8s。该结果证明单场端到端链路和指标持久化可用，不代表五场并发、P50/P95 或 50 路公平调度已验收。

008 远程复测补充：一场短自由辩论生成 43 个权威事件并完成 5 个 Agent 正式回合，阵营顺序为正→反→正→反→反；最后的同方连续回合由另一方剩余时间耗尽触发。另一场受控浏览器房间在举手窗口内成功显示“取消举手（第 1 位）”，窗口结束后进入“轮到你发言了！”和“开始发言”。期间修复了 Actor 内部计时器取消 Future、重复自由回合幂等键、Agent 超时清理阻塞正式提交以及 ERROR 状态无法终止四个真实竞态。

暂停与掉线复测：Agent 播放中手动暂停立即提交 `PAUSED`，保留 4359ms，随后经过 `match.resume_countdown` 和 3 秒倒计时，从该 Agent 动作起点按剩余时长重新生成。固定人类发言房间关闭业务 WebSocket 后，数据库中 `match.offline` 至 `PLAYER_OFFLINE_TIMEOUT` 自动暂停的间隔为 60.006s；重连后设备/在线检查通过并恢复到手动开始状态。ASR 运行时测试进一步证明同一 `speech_id` 可在暂停前后生成连续 segment `[1, 2]`，暂停时不提前 final，恢复后合并文字与音频时长。

## 建议实现

ASR 分段方案已经产品确认：

- 每次人类发言仍只有一个业务 `speech_id`，但内部包含多个递增的 `asr_segment_no` 和独立 `task_id`。
- Fun-ASR 固定使用 16kHz、16-bit、单声道 PCM，每 100ms 发送 3200 字节。
- 单个 ASR task 最长约 30s。优先在已得到 `sentence_end` 后轮换；达到硬上限时直接结束当前 task。
- 复用同一条 WebSocket 连接依次创建新 task；切换期间使用已有的约 1s 有界 PCM 队列暂存音频。
- 各段最终文本按 `asr_segment_no` 拼接，对用户仍表现为一次连续发言。
- 保留 `heartbeat=true`、`semantic_punctuation_enabled=false`、`multi_threshold_mode_enabled=true`；`max_sentence_silence` 先使用 1300ms。
- 某个分段失败时，按照已确认规则重试整次发言，而不是只静默丢弃该段。

TTS 播放建议：

- 云端请求 `format=opus`、`sample_rate=24000`、`bit_rate=32`，使用固定 voice、rate、pitch、volume、instruction 和 seed。
- Opus 到达后追加写入当前发言的临时 spool 文件。一个独立 feeder 根据播放水位从 spool 向 PyAV 解码器推送压缩块，不能把云端突发数据全部直接推入解码器。
- PyAV/libav 在后台单线程增量解码为 48kHz 单声道 PCM；应用 PCM 队列和 LiveKit `AudioSource` 队列各自保持有界，总预缓冲目标约 200–300ms。
- 暂停时记录 `已提交样本数 - LiveKit queued_duration 对应样本数` 作为服务端播放位置，调用 `AudioSource.clear_queue()` 立即停声，并停止 feeder；TTS 可继续完成压缩写盘。
- 恢复时创建新解码器，从 spool 开头快速解码并丢弃到记录的播放样本位置，再恢复 3 秒倒计时和实时提交。这也作为解码器故障后的重建路径。
- 解码器重建失败一次后，按全局规则展示原因并暂停比赛。
- spool 写盘失败时可降级到每路最多 2MB 的压缩内存缓冲并提示录音异常；压缩缓冲也失败时才中断 TTS 主链路。
- 每个音色允许配置有限的 rate 档位。启用或修改 rate 后必须重新校准实际中文字/秒，并据此计算 LLM 目标字数。
- `qwen-audio-3.0-tts-flash` 当前没有可固定的日期快照。固定请求参数不能完全消除上游模型更新漂移，因此音色启用、参数变化或检测到输出特征变化后必须重新校准和试听。

语速、文本长度和字幕建议使用同一份校准数据：

- 校准键为 `(model, voice, rate, pitch, instruction)`，记录有效中文字/秒、首包延迟、句子时长、文件码率和测试时间。
- Agent 配置绑定 voice 和有限范围的 rate；MVP 建议只开放 0.85–1.20、步长 0.05，避免极端语速破坏自然度和音色稳定性。
- `目标字符数 = 阶段可发言秒数 × 校准中文字/秒 × 0.85`。无可靠字时间戳时保留 15% 安全余量。
- 每个 OpenAI 兼容模型记录实际 `completion_tokens / 输出字符数`。`max_tokens` 使用该模型的保守高位比例计算；初始没有样本时按 1 token/中文字符计算。实测 `qwen3.7-plus` 为 173 token / 310 字符，约 0.56 token/字符。
- Prompt 同时给出明确的目标字符数；`max_tokens` 只是硬上限，不能替代字符长度要求。
- 字幕先按 TTS 句子 begin/end 事件划分。解码器按实际 PCM 样本数回填每句时长；句内使用字符和标点权重估算，并保证展示进度单调、不提前显示下一句。

## 尚未完成的验收

- 真人麦克风、普通室内噪声、蓝牙设备和网络抖动测试。
- 至少 10 位中文使用者的安静环境与普通噪声 CER。
- 5 场比赛同时运行时的完整链路压力测试。
- 50 路 LLM 流式调用的目标端点负载测试。
- 11 个候选音色逐一测试音质、稳定性、语速和时间戳能力。

## 官方资料

- [Fun-ASR WebSocket API](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api)
- [Fun-ASR 客户端事件](https://help.aliyun.com/zh/model-studio/fun-asr-client-events)
- [Fun-ASR 服务端事件](https://help.aliyun.com/zh/model-studio/fun-asr-server-events)
- [实时语音合成](https://help.aliyun.com/zh/model-studio/realtime-tts-user-guide)
- [Qwen-Audio-TTS/CosyVoice WebSocket API](https://help.aliyun.com/zh/model-studio/cosyvoice-websocket-api)
- [Qwen-Audio-TTS/CosyVoice 客户端事件](https://help.aliyun.com/zh/model-studio/cosyvoice-client-events)
- [LiveKit Agents AudioStreamDecoder](https://github.com/livekit/agents/blob/main/livekit-agents/livekit/agents/utils/codecs/decoder.py)
- [LiveKit Python AudioSource](https://github.com/livekit/python-sdks/blob/main/livekit-rtc/livekit/rtc/audio_source.py)
- [RFC 6716: Opus Audio Codec](https://datatracker.ietf.org/doc/html/rfc6716)
- [Xiph Opus Recommended Settings](https://wiki.xiph.org/Opus_Recommended_Settings)
