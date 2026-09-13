# 214 第三方 API 请求日志

> 版本：v2.0  
> 状态：已完成生产发布，完整 PostgreSQL pytest 套件待补  
> 需求来源：2026-08-23 `grill-me` 逐项确认；用户随后明确批准 Spec 214  
> 依赖：200、210、213  
> 调研：`docs/research/api-request-log-github-patterns-2026-08-23.md`

## 1. 目标

将后台 `/admin/logs` 从系统事件列表改为“API 请求日志”，让管理员核对平台向第三方供应商
实际发送和收到的脱敏 JSON 结构。覆盖比赛和非比赛业务调用，明确展示每次尝试的输入、输出、
关键流式事件、状态、耗时和关联数据，用于线上诊断与论文实验复核。

本规格不改变 MatchActor、计时、发言权、模型选择、重试次数、ASR/TTS/LiveKit 协议或用户可见错误。
不引入新的服务、消息队列、日志后端或对象存储。

## 2. 已确认的产品决策

### 2.1 页面与权限

- 后台导航和页面标题统一为“API 请求日志”，路径继续使用 `/admin/logs`。
- 页面只允许管理员访问；列表、详情和导出接口均由 Core 再次校验管理员权限。
- 打开某条记录的请求或响应正文属于敏感详情查看，必须写入追加式审计日志。
- 页面不提供单条或批量导出按钮。比赛相关调用只随“比赛与数据”的研究包导出。
- 旧系统事件不再显示独立列表；它们继续持久化并用于事故聚合，关联事件只在事故详情中展示。

### 2.2 覆盖范围

新采集覆盖：

1. Agent 固定/自由辩论正式发言 LLM；
2. 自由辩论发言意愿决策 LLM；
3. AI 裁判；
4. Fun-ASR；
5. Agent 和主持音频 TTS；
6. 模型连接测试、音色试听/校准等配置测试；
7. 生产运维 provider 探针。

每条记录必须标记来源，至少能区分 `MATCH`、`HOST_AUDIO`、`CONFIG_TEST` 和 `OPS_PROBE`。
来源可以细分，但探针和配置测试不得进入论文实验统计或排行榜。

### 2.3 重试与历史

- 一次实际供应商尝试对应一条 `ExternalCall`；自动重试创建新记录，不覆盖失败尝试。
- 同一业务动作的所有尝试共享稳定 `logical_call_id`，列表可分组并可直接筛选该 ID。
- 新页面只展示 Spec 214 上线后带供应商原始采集版本的记录。
- 历史 `ExternalCall` 不删除、不回填、不推测性重建，也不在新页面显示；原单场工作台读取兼容保留。

### 2.4 保留与导出

- 新采集正文与调用索引作为业务/研究数据长期保留，不受普通运行日志 30 天保留期影响。
- 比赛调用随整场比赛的显式删除而删除；非比赛调用保留，后续如需清理必须另立规格。
- 比赛完整研究包中的 `agent-calls.jsonl` 扩展为包含该场新采集记录的请求/响应信封、关键事件、
  截断/拒存元数据和哈希；不包含其他比赛、主持音频、配置测试或运维探针。
- 导出继续冻结任务开始时的数据库截止点，并在 manifest 中记录调用采集 Schema 版本。

## 3. “供应商原始 JSON”的精确定义

### 3.1 采集边界

原始内容必须在供应商适配层、序列化之后或反序列化之前采集，而不是由业务层根据结果重新拼装：

- HTTP/SSE：记录脱敏 URL、允许的请求/响应头、实际请求 JSON、最终聚合响应和关键原始事件；
- WebSocket：记录实际发送的 JSON 控制帧、关键供应商 JSON 事件和聚合最终结果；
- 二进制音频帧不保存，只聚合方向、格式、帧数、总字节数、音频时长和首/末帧时间。

流式调用不逐条保存 LLM token、ASR interim 或 TTS 音频帧。关键事件至少包括连接/任务开始、
首个有效结果、任务完成和供应商失败；事件内容保留供应商实际 JSON 结构。最终响应保留完整聚合文本、
usage、finish reason、任务 ID、音频/转写汇总和供应商错误结构（以该接口实际返回内容为准）。

### 3.2 采集信封

请求与响应正文仍使用供应商字段，但由平台信封表达不可由供应商报文提供的采集事实：

```json
{
  "capture_schema_version": 1,
  "transport": "HTTP|SSE|WEBSOCKET",
  "url": {"origin": "https://example.invalid", "path": "/v1/example"},
  "headers": {},
  "body": {},
  "events": [],
  "binary_summary": null,
  "capture": {
    "status": "COMPLETE|TRUNCATED|REJECTED_SENSITIVE|UNAVAILABLE",
    "original_bytes": 0,
    "stored_bytes": 0,
    "sha256": null
  }
}
```

信封不把业务摘要伪装成供应商响应。某部分在适配层不可获得时使用 `UNAVAILABLE` 并说明固定低基数原因，
不得生成看似真实的占位 JSON。

## 4. 安全与容量边界

### 4.1 永不落库的内容

- `Authorization`、`Proxy-Authorization`、Cookie、API Key、Access Token、私钥、签名、连接串；
- URL 查询参数中的凭据或签名；保存 URL 时默认只保留 origin 与 path；
- PCM、Opus、Ogg 或其他二进制音频正文；
- 供应商 SDK 内部堆栈、服务器内部文件路径和环境变量。

请求/响应头使用固定允许列表，不采用“先保存所有头、页面再隐藏”。正文递归执行敏感键名检查和疑似
凭据检测；无法确认安全时，正文不落库，状态为 `REJECTED_SENSITIVE`。页面展示的是已经通过安全边界的
落库 JSON，不依赖展示时二次脱敏来保护数据库和备份。

### 4.2 2 MiB 单侧上限

- 请求与响应分别以安全处理后的 UTF-8 紧凑 JSON 字节数计算，硬上限各 `2 MiB`。
- 未超限保存完整内容，记录原始字节数、存储字节数和 SHA-256。
- 超限时保存有界的首尾片段、未截断内容 SHA-256、原始字节数和 `TRUNCATED`；信封本身也必须在上限内。
- 截断必须发生在写入有界队列/数据库之前；压缩后大小不能作为边界。
- 安全拒存优先于截断：不得通过只检查首尾片段绕过完整正文的敏感内容检测。
- 流式响应使用增量哈希、增量敏感检测、固定头部缓冲和固定尾部环形缓冲；不得为了截断先在
  内存中重新拼接一份无上限正文。已经由供应商 SDK 聚合的对象也只能单次遍历，不再复制完整序列化结果。

采集和安全处理不得把未经约束的正文复制到普通 Python 日志、异常消息、队列或 Web 查询缓存。

## 5. 数据与 API 设计边界

### 5.1 复用与追加 migration

继续复用：

- `external_calls`：每次尝试的索引和性能指标；
- `call_content_blobs` / `call_content_blob_chunks`：压缩、分块、内容寻址正文；
- `GET /api/admin/external-calls/{call_id}`：按需加载详情；
- 现有比赛工作台和研究包导出边界。

通过 migration 0039（实现时若已有更新 migration 则顺延）向 `external_calls` 追加足以表达以下事实的字段：

- 原始采集 Schema 版本和采集时间；
- `source_kind` 与可选来源资源 ID；
- `logical_call_id`；
- 请求/响应各自的采集状态、原始字节数、存储字节数和 SHA-256；
- 可用的供应商 request/task ID。

不得修改 migration 0024、0035 或其他历史 migration。新增字段对历史行允许为空，作为新页面排除历史记录的
可靠条件。具体列组合可在实现时收敛，但不能把高频正文或事件塞入 `external_calls` JSONB 索引列。

### 5.2 查询 API

新增或收敛 `GET /api/admin/external-calls`，只返回分页元数据，不返回 Blob 正文。支持：

- 时间范围；
- 调用类型、来源、状态；
- provider、operation、model、voice；
- 赛制规则、实验批次、比赛 ID；
- `logical_call_id`、供应商 request/task ID；
- 开始时间和耗时排序。

不支持 Prompt、转写或响应正文关键词搜索。所有筛选均由服务端执行并设置对应索引；默认按开始时间和 ID
倒序，使用稳定游标/分页，单页最多 100 条。

详情 API 只在管理员展开记录时加载请求/响应 Blob，并在同一事务记录敏感查看审计。列表不得因某个 Blob
损坏而失败；详情分别报告 request/response 的读取、拒存或截断状态。

## 6. 后台交互

- 页面标题“API 请求日志”，说明其展示第三方调用而非 Core/Jobs 普通事件。
- 默认列表为紧凑表格，字段至少包括时间、类型、来源、provider/operation、模型或音色、状态、attempt、
  首结果耗时、总耗时和关联比赛。
- 筛选状态写入 URL；保留当前页面已有的自动刷新互斥、标签页隐藏暂停和“有 N 条新记录”缓冲语义。
- 点击一行打开二级 Drawer/详情区；只显示格式化的原始 JSON，不提供业务易读视图，也不提供正文搜索。
- 请求、响应和关键事件使用稳定的内部滚动区域；JSON 长键和值必须换行，不能撑宽页面。
- `TRUNCATED`、`REJECTED_SENSITIVE`、`UNAVAILABLE` 和 Blob 读取失败必须直接显示原因、字节数与哈希，
  不能展示成空对象或“调用成功所以日志完整”。
- 页面没有下载按钮。比赛工作台可以继续链接到同一详情组件，避免维护第二套 JSON 展示。

系统事件页面移除后，事故详情增加关联事件列表；未关联事故的普通系统事件保留在数据库和受控运维查询中，
不出现在后台导航。

## 7. 运行与故障语义

- 调用采集失败不得改变供应商调用结果、自动重试次数、比赛状态或计时；记录低基数
  `capture_error_code`，不得把原始内容写入补偿日志。
- 创建 `ExternalCall STARTED`、供应商调用和完成采集保持 attempt 一致；迟到回调继续校验现有
  `match_id`、`speech_id`、`attempt_no`、`generation_id`、`connection_epoch` 和 `context_version`。
- 捕获完成必须有界且不能阻塞实时音频队列；实现时需要专项证明五路并发下没有增加播放缓冲或 ASR 丢帧。
- Jobs 产生的主持 TTS 使用同一 Schema 和安全函数，但 Jobs 不获得比赛状态写权限。
- 运维探针显式传入 `OPS_PROBE` 来源，不创建房间、比赛或实验业务数据。

## 8. 验收标准

### 8.1 Core / Jobs

- 五类调用和四类来源均有成功、失败、取消、一次失败后重试成功的覆盖。
- 断言实际适配层发送/收到的 JSON 与落库正文一致；认证头、签名和二进制音频不存在于数据库。
- 敏感正文拒存、2 MiB 边界、UTF-8、多字节中文、首尾片段、SHA-256 和 Blob 损坏均有测试。
- 新页面查询只返回新采集版本，历史行不出现；列表 SQL 不加载 Blob，筛选和稳定游标有 PostgreSQL 测试。
- 打开详情产生审计记录；普通用户、比赛参与者和未认证请求均无法读取。
- 研究包只包含目标比赛截止点前的新调用详情，manifest Schema 与 SHA-256 正确。
- 系统事件保留清理不删除 `ExternalCall` 或内容 Blob。

### 8.2 Web

- 列表覆盖加载、空态、失败、筛选、URL 恢复、分页、自动刷新缓冲和详情按需加载。
- Drawer 只显示 JSON；截断、拒存、不可用和读取失败状态可区分。
- 1280×720、1440×900、1920×1080 无页面级横向溢出；长 JSON 不改变核心布局。
- 详情 JSON 的大内容渲染有门槛，不冻结主线程；键盘、焦点返回和 Axe 检查通过。
- `/admin/logs` 导航标题为“API 请求日志”，页面不存在导出控件；事故详情能查看关联系统事件。

### 8.3 完整门禁

执行对应单元和 PostgreSQL 集成测试后，运行 `pnpm lint`、`pnpm typecheck`、`pnpm test`、
`pnpm contracts:check`、`pnpm test:storybook`、专项 Playwright、`pnpm build` 和
`git diff --check`。真实 provider 探针只证明短链路采集，不能替代真人 ASR、五场长链路或 50 路持续 LLM。

## 9. 发布与回滚

发布顺序为追加 migration → Core → Jobs → Web。迁移前确认非终态比赛为 0 并完成数据库/源码/Web 备份；
发布后分别验证五类真实 provider 调用、详情审计、比赛导出、四服务健康和至少五分钟重启/告警。

回滚时先回退 Web 页面和 Core/Jobs 采集代码，保留 migration 0039 新列和已经采集的内容，不删除或反向伪造
数据；旧系统事件查询 API 可作为受控运维回退入口。回滚不恢复历史记录到“API 请求日志”页面。

## 10. 非目标

- 不保存 API Key、认证头、签名、Cookie、二进制音频或完整流式 token/interim/chunk；
- 不提供正文检索、日志页导出、参与者查看或实验负责人新角色；
- 不重建历史供应商报文；
- 不实现通用分布式 Trace、服务拓扑、成本平台或告警规则引擎；
- 不引入 Langfuse、Jaeger、OpenTelemetry Collector、ClickHouse、Redis、Kafka 或新微服务。

## 11. 实施证据与遗留风险

2026-08-23 已完成本地实现：migration 0039、五类供应商适配层采集、四类来源、按 attempt
记录与 `logical_call_id` 分组、管理员元数据列表与审计详情、比赛研究包 JSON、事故关联事件，
以及无导出功能的“API 请求日志”页面。采集正文限制为每侧 2 MiB，保留首尾、原始字节数和
SHA-256；敏感字段拒存，认证头、签名、查询参数和二进制音频不入库。采集持久化使用保存点，
失败只记录低基数错误，不改变第三方调用结果或业务事务；ASR 未消费采集队列固定为 16 条。

本地证据：Tooling 47、QA 5、Web 183、Core 非集成 300、Jobs 非集成 23、Storybook 89、
Playwright 228 全部通过；Ruff、Pyright、ESLint、TypeScript、OpenAPI 契约、34 页正式构建和
`git diff --check` 通过。另以隔离浏览器验证 1280x720 与 1440x900 的列表、Drawer、JSON 内部
滚动和页面无横向溢出。

2026-08-23 已完成生产发布。发布前非终态比赛为 0，数据库、源码和 Web 的 root-only 备份位于
`/opt/jixia-backup-before-0039-20260823T073053Z`；生产数据库只向前迁移至
`0039_provider_api_request_logs`。隔离临时 PostgreSQL 空库已完成完整 migration 链、
`alembic check`、新增字段和索引验证并删除；服务器未安装 pytest，因此完整 PostgreSQL integration
marker 仍未执行，不能把该结构门禁写成全部数据库行为测试通过。

生产 TTS、ASR、LLM、AI 裁判和 LiveKit 短链路均通过，四条 `OPS_PROBE` 请求/响应为
`COMPLETE/SUCCEEDED`，Blob 可校验且不含认证头、Cookie、API key、token 或签名字段。线上管理员
在 1280x720、1440x900 验证列表、JSON Drawer、内部滚动和无页面横向溢出；详情查看审计由 0
增至 1。发布前终止比赛的研究包导出成功，manifest 标记 provider schema 1，
`agent-calls.jsonl` 存在且为 0 行，没有重建历史调用。

发布后五分钟 10/10 样本中 Core、Jobs、Web、LiveKit 均 active，Core ready 为 200，重启数为 0；
最终启动窗口内 warning-or-higher 为 0。Web 切换前一次受控停止因 Next 返回 143 被 systemd 标记
failed，旧 Web 随即恢复后使用接受受控 143 的原子切换完成，未影响数据库或 Core/Jobs。真人 ASR、
五场长链路和 50 路持续 LLM 仍未执行，不得据短链路探针宣称通过。
