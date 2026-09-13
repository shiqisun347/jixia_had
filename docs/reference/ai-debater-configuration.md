# AI 辩手配置（v2.0）


## 2. 正式 4v4 策略

| 阶段 | 发言权 | 单次时长 |
| --- | --- | --- |
| 正/反一辩立论 | 指定一辩 | 90 秒 |
| 自由辩论 | 正方先；双方交替；真人举手优先；无真人时本方 Agent 独立决策 | 每方总 360 秒；单次最多 30 秒 |


发言规则：
1. 模拟真人辩手和 AI 辩手 都需要决策是否 要进行发言；
    1.1 如果
自由辩论 Agent 仅决定是否争取本方下一次机会；不感知真人是否举手，真人获权始终优先。决策失败标记失败/跳过，不应重置当前真人发言。

## 3. Prompt 模板

### 固定席位发言

```text
角色：中文辩手；与三名队友共同代表 {{POSITION}}；身份为 {{STANCE}}{{POSITION}} {{SEAT}}
输入：辩题、正反方立场、当前阶段、最长发言秒数
任务：坚持本方立场，完成阶段目标，推动本方论证；口语化、直白、适合朗读；不虚构事实或来源。
输出：≤ {{TARGET_CHAR_COUNT}} 字；只输出朗读正文；禁止解释、Markdown、标题、舞台提示。
```

### 自由辩论发言

```text
角色/立场：同固定席位发言
输入：辩题、正反方立场、最长发言秒数、完整辩论记录 JSON（stage/message）
任务：根据辩论状态、自身个性化与团队协作给出合适发言；口语化、直白、适合朗读。
输出：≤ {{TARGET_CHAR_COUNT}} 字；只输出朗读正文；禁止解释、Markdown、标题、舞台提示。
```

### 自由辩论决策

```text
角色/立场：同固定席位发言
输入：辩题、正反方立场、本方/对方剩余毫秒、完整辩论记录 JSON（stage/message）
任务：从团队整体需要判断是否争取下一次发言。
输出：{"should_speak": true|false, "decision_reason": "20字以内理由"}
```

正式 4v4/实验模式额外强制：仅 JSON；`decision_reason` 必填、去除首尾空白后长度 `1–20`；字段必须恰为 `should_speak` 与 `decision_reason`。

## 4. 可用模板变量

| 场景 | 必填变量 |
| --- | --- |
| 固定发言 | `TOPIC`、`POSITION`、`STANCE`、`AFFIRMATIVE_STANCE`、`NEGATIVE_STANCE`、`SEAT`、`STAGE_NAME`、`MAX_SPEECH_SECONDS`、`TARGET_CHAR_COUNT` |
| 自由决策 | `TOPIC`、`POSITION`、`STANCE`、`AFFIRMATIVE_STANCE`、`NEGATIVE_STANCE`、`SEAT`、`SIDE_REMAINING_MS`、`OPPONENT_REMAINING_MS`、`DEBATE_HISTORY` |
| 自由发言 | `TOPIC`、`POSITION`、`STANCE`、`AFFIRMATIVE_STANCE`、`NEGATIVE_STANCE`、`SEAT`、`MAX_SPEECH_SECONDS`、`TARGET_CHAR_COUNT`、`DEBATE_HISTORY` |

允许变量全集：以上变量；未知变量或缺少必填变量时规则保存失败。

## 5. 生成与限流参数

| 项目 | 设置 |
| --- | --- |
| 普通发言 `max_tokens` | `max(32, floor(target_chars × token_per_char) + 16)` |
| `target_chars` | `max(20, floor(发言秒数 × chars_per_second × 0.85))` |
| 决策 `max_tokens` | 正式 4v4/实验 `32`；旧非正式路径 `64` |
| 正式 4v4/实验决策 | 强制 `temperature=0.75`、`top_p=0.9`、`enable_thinking=false` |
| 普通发言思考模式 | 未配置时 `enable_thinking=false`；资源显式值可覆盖 |
| 参数覆盖 | 模型参数先合并，Agent 参数后合并；正式决策再覆盖上述三项 |
| 模型参数能力范围 | `temperature: 0–2`；`top_p: 0–1`；`max_tokens: 1–32768` |
| `token_per_char` | 资源可配，`(0, 10]`，默认 `1.0` |
| `chars_per_second` | 音色可配，`(0, 20]` |
| 全局并发 | 50 路 LLM |
| 单模型并发 | 采用模型资源的 `model_limit` |
| 容量排队 | 3 秒；超时：`llm_capacity_full` |

## 6. 调用时限与失败边界

| 调用 | 连接/首 token/流空闲超时 |
| --- | --- |
| 正常发言 | 各 10 秒 |
| 自由辩论快速决策 | 各 3 秒 |
| Agent 任务取消清理 | 3 秒 |
| Agent 收尾 | 15 秒 |

模型响应必须是流式 OpenAI 兼容 Chat Completions；响应无首个文本 token、非 2xx、JSON/SSE 无法解析、流中断或超时均视为失败。系统不得静默切换模型或供应商。

## 7. 上下文装配

- 发言历史：同场 `FINALIZED`；正式/实验模式额外允许 `STARTED` 且有文本的记录。
- Prompt 历史：完整 JSON；每项保留阶段与消息（实验历史按阶段分组）。
- 当前 Agent 可见自身最近最多 4 次 Agent 发言；最近历史窗口为 8 条。
- 运行时传入：当前阵营、当前/下一阶段、正反立场、双方自由辩论剩余时间、动态目标字数。

