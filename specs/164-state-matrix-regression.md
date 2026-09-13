# 164 状态矩阵回归护栏

状态：本机实现并验证，待生产专项回归

## 目标

为中断、重连、媒体和计时修复建立跨状态回归护栏，避免单点修复改变其他状态的权威语义。Core `MatchActor` 是唯一状态和计时来源；WebSocket、LiveKit、ASR、TTS 回调只能通过已校验事件影响 Actor。

## 状态矩阵

| 状态/场景 | 允许的权威动作 | 必须保持的不变量 |
| --- | --- | --- |
| `HOST_ANNOUNCING` | `host.elapsed`、手动暂停、系统/错误恢复 | 主持音剩余时间单调减少；暂停/恢复和服务恢复不跳过主持音 |
| `PREPARING` | 自动结束、手动暂停/恢复、系统/错误恢复 | 准备倒计时冻结并按剩余时间重排，不会恢复后永久停滞 |
| `HUMAN_READY_TO_START` | `speech.start`、60 秒开始超时、暂停/恢复、重连 | 等待窗口不消耗发言时长；59.9 秒继续，60 秒以 `HUMAN_START_TIMEOUT` 暂停；全局恢复重开完整 60 秒，普通重连不延长 |
| `HUMAN_SPEAKING` / `AGENT_SPEAKING` | 完成、deadline、当前选手离线/重连 | 发言 deadline 与剩余时长一致；旧 epoch 事件不能恢复或暂停错误选手 |
| `SPEECH_FINALIZING` / `AGENT_FINALIZING` | 终态回调、错误 | 过期 speech/generation 结果丢弃；临时字幕不进入下一动作 |
| `FREE_SELECTING` | 举手、取消、窗口关闭、Agent 决策 | 队列顺序、双方剩余时间和决策轮次不被重连改变 |
| `PAUSED` / `SYSTEM_RECOVERY` / `ERROR` | 合法恢复、终止 | 恢复前检查原因；错误字幕清理；计时器冻结且不在后台继续推进 |
| `FINISHED` / `TERMINATED` | 查看终态、赛后记录 | 迟到回调不能重新写入临时字幕或改变状态；最终文字记录不受影响 |
| 所有运行态 | `member.offline` / `member.online` / `offline.expired` | epoch 单调；连续离线只记录一次起点，高 epoch 重复离线只能推进高水位，不能续期；59.9 秒不暂停，满 60 秒才暂停；重复/乱序事件幂等 |

## 自动化护栏

- runtime snapshot 往返保留动作、计时、epoch、离线标记和临时字幕。
- 每个暂停/恢复路径验证 deadline 被清除或按剩余时长重建；启动倒计时恢复后重新进入当前动作，不会停在 `RUNNING/NOT_STARTED`；剩余时间为 `0` 时安排立即到期，不能被当成缺省值重置满时长。
- 更高 epoch 在线事件即使没有状态切换也持久化；提交失败回滚内存高水位。
- 连续离线期间的更高 epoch 离线事件只持久化高水位，不覆盖原始
  `offline_since_ms`、不替换 expiry timer、不重复暂停当前发言者 ASR；提交失败
  同时回滚 snapshot 和内存高水位。
- 临时字幕在 Actor 提交期间到达不会被候选状态覆盖。
- 主持音、Agent、错误恢复、WebSocket 四视口和 LiveKit webhook 输入边界均纳入回归；非法签名事件字段安全忽略，不能污染幂等键。
- 当前真人发言者掉线时，同时冻结 MatchActor deadline 和同一 `speech_id`
  的 ASR session；重连先完成 pause，再恢复同一业务发言。pause 清理中的
  provider close 使用已完成 segment checkpoint 恢复，不重置编号或已确认前缀。

当前证据：Core 非集成测试 191 项、工具 47、QA 5、Web 166、Jobs 19、Storybook 82、contracts、typecheck、lint、正式 Web build 和 Playwright 200/200 通过；presence 离线时间源、连续离线高 epoch、真人开始窗口、ASR 掉线恢复、启动倒计时恢复、零剩余时间和 LiveKit webhook 输入边界均有回归测试。数据库 integration 因本机无 PostgreSQL/Docker daemon 未执行。真人开始窗口已在生产两场专用比赛中分别以 60,009ms 和 60,008ms 的服务端事件间隔验证；真人发言中约 12.5 秒最后标签掉线已在生产验证冻结并恢复。连续离线高 epoch 的确定性边界测试已在生产主机部署模块上通过；自动化浏览器未通过真实设备检测，因此真人双通道冷启动边界仍待现场复测。

## 发布与回滚

本规格只增加测试和状态护栏，不修改数据库 schema。发布前必须通过 Core/Jobs/Web 全量测试、contracts、构建和浏览器专项；生产仍需活动比赛门禁、实际模块路径/hash 校验和真人比赛回归。回滚只恢复本规格对应源码与测试，不回退已完成的 duration 回填。
