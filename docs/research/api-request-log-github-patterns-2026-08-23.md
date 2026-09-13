# API 请求日志 GitHub 代码调研

> 日期：2026-08-23  
> 范围：第三方 API 请求/响应采集、尝试分组、详情加载和大 JSON 展示  
> 结论：复用项目现有 `external_calls` 与内容 Blob，不引入新的可观测性服务

## 1. 调研问题

当前 `/admin/logs` 展示 Core/Jobs 的系统事件，而用户需要查看第三方 API 实际请求与响应。
项目已经具备外部调用索引、压缩分块内容 Blob、单场比赛调用列表和按需详情 API，缺口不是
“再造日志系统”，而是把采集边界推进到供应商适配层，并建立全局调用查询页面。

本次调研重点验证四种成熟模式：

1. 列表是否只加载元数据，正文是否按需加载；
2. 一次业务动作的多个尝试如何关联；
3. 流式调用如何表达请求、关键事件、最终结果和错误；
4. 多 MiB JSON 如何避免阻塞浏览器。

## 2. 参考实现

### 2.1 Langfuse

参考提交：`62751446149b702b419a9292ddfc2280cdf74b8c`

- [`ObservationDetailView.tsx`](https://github.com/langfuse/langfuse/blob/62751446149b702b419a9292ddfc2280cdf74b8c/web/src/features/traces/components/ObservationDetailView/ObservationDetailView.tsx)
  先加载 observation 元数据，选中后再异步取得 input/output；详情和列表不共用大正文响应。
- [`jsonViewSizeGate.ts`](https://github.com/langfuse/langfuse/blob/62751446149b702b419a9292ddfc2280cdf74b8c/web/src/features/traces/components/IOPreview/fns/jsonViewSizeGate.ts)
  对非虚拟化 JSON 设置明确尺寸门槛，避免多 MiB 内容冻结主线程。
- [`LargeJsonFieldFallback.tsx`](https://github.com/langfuse/langfuse/blob/62751446149b702b419a9292ddfc2280cdf74b8c/web/src/features/traces/components/IOPreview/components/LargeJsonFieldFallback.tsx)
  超大字段使用有界预览，并明确说明内容过大，不让页面静默卡死。

采用：元数据列表、选择后按需加载 JSON、稳定尺寸的详情区、明确的截断/读取失败状态。

不采用：Langfuse 的 ClickHouse、Worker、评分和完整 Trace 产品；本项目只有最多五场活动比赛，
现有 PostgreSQL 和 Core 管理 API 足够。

### 2.2 Jaeger UI

参考提交：`c321fb32bbca0bdce9e8c2d40afb35abbf54c12b`

- [`SpanDetail/index.tsx`](https://github.com/jaegertracing/jaeger-ui/blob/c321fb32bbca0bdce9e8c2d40afb35abbf54c12b/packages/jaeger-ui/src/components/TracePage/TraceTimelineViewer/SpanDetail/index.tsx)
  将一次执行尝试看作独立 span，详情分开展示属性、事件、错误和关联；同一 trace 负责表达调用链。

采用：每次重试是独立记录，使用稳定的业务动作 ID 分组；列表展示 provider、operation、状态、
耗时和 attempt，详情再展示事件与正文。

不采用：通用 Trace 时间轴、服务拓扑和 OpenTelemetry Collector。比赛已有自己的权威事件时间线，
再引入一套 Trace 后端会重复事实来源。

### 2.3 OpenTelemetry GenAI 语义约定

参考提交：`56d6b11a02129319bf371083fa134b7ce989c976`

- [`gen-ai-spans.md`](https://github.com/open-telemetry/semantic-conventions-genai/blob/56d6b11a02129319bf371083fa134b7ce989c976/docs/gen-ai/gen-ai-spans.md)
  区分 provider、operation、请求模型、响应模型、流式标记、usage、error 和 server address。
  输入消息、输出消息与 system instructions 是显式 opt-in 的敏感内容，并要求保持发送顺序。

采用：字段命名和低基数元数据边界；输入/输出必须由管理员权限、审计和正文安全策略保护。

不采用：把完整 Prompt 或响应塞进普通日志属性。正文继续进入项目现有压缩内容 Blob，列表只查询
低基数元数据，避免日志索引膨胀。

## 3. 对现有代码的映射

| 成熟模式 | 项目现有基础 | Spec 214 缺口 |
| --- | --- | --- |
| observation/span 元数据列表 | `ExternalCall` 已保存 kind、provider、operation、attempt、状态、延迟、Token | 缺少全局分页查询、来源、业务动作分组和新采集版本过滤 |
| 详情按需加载 I/O | `CallContentBlob`、`CallContentBlobChunk`、`load_content_blob` 和 `/api/admin/external-calls/{id}` | 当前部分内容是业务摘要，不是适配层实际供应商 JSON |
| 重试为独立 span | `ExternalCall.attempt_no` | 缺少跨不同调用类型通用的 `logical_call_id` |
| 流式关键事件 | 已保存首结果与完成耗时 | 缺少供应商关键原始 JSON 事件和聚合最终响应 |
| 大 JSON 有界展示 | 当前 `<pre>` 有滚动高度 | 缺少 2 MiB 落库边界、截断元数据和渲染尺寸门槛 |
| 安全 opt-in 内容 | 管理员详情 API、审计基础、日志脱敏器 | 缺少适配层请求头白名单和正文疑似密钥拒存 |

## 4. 选型结论

- 保留 `external_calls` 作为唯一第三方调用索引，追加字段和 migration，不新建第二套日志事实表。
- 保留压缩、分块、按哈希去重的内容 Blob；新增统一的供应商报文采集信封和 2 MiB 单侧边界。
- `/admin/logs` 改为“API 请求日志”，复用现有 URL 筛选、自动刷新互斥、新记录缓冲、分页和详情组件。
- 系统事件继续用于事故聚合和内部诊断，但不再占用独立后台列表。
- 不引入 Langfuse、Jaeger、OpenTelemetry Collector、ClickHouse、Redis 或新的微服务。
- 不复制上游代码；只采用其已经验证的信息架构和容量控制方法。

## 5. 实现时必须验证

- 列表 API 永远不返回请求/响应正文，展开详情时才读取两个 Blob。
- 同一业务动作重试两次时形成两条记录，第一次失败内容不会被第二次成功覆盖。
- LLM、裁判、ASR、TTS、主持音频、配置测试和运维探针都能标记来源；探针不进入实验统计。
- 2 MiB 边界按 UTF-8 序列化字节计算，不按 JavaScript 字符数或数据库压缩后大小计算。
- 大 JSON 不改变页面宽度，不阻塞主线程；详情读取失败、拒存和截断均有明确状态。
