# Spec 264：赛制规则管理与主持音频生命周期重设计

版本：v2.0\
状态：已批准实施并完成生产发布（2026-10-09）\
需求来源：2026-10-09 用户要求重新分析、设计赛制规则模块，解决音频一直生成和 CRUD 不完整\
依赖：200、213、260、263

### 2026-10-13 Jobs PostgreSQL 回归补充

本地扩展数据库验收发现旧 Jobs 主持任务 fixture 仍只写 `rule_id/asset_id`，未创建本规格
要求的当前 generation、冻结输入、manifest/config revision 与 active task 关联。本切片
只更新测试和证据，不修改已发布 worker、状态机或 migration。

- Jobs 数据库集成必须显式提供独立 `TEST_DATABASE_URL`，缺失或与 `DATABASE_URL` 相同
  时在连接前拒绝，不能回退到应用数据库。
- 正向 fixture 使用当前 generation 契约，真实执行领取、合成、文件校验及原子汇总，并
  验证资产 READY、任务 SUCCEEDED、批次 SUCCEEDED/待审核和正时长音频文件。
- 旧 generation 身份缺失的任务应明确标记 `tts_generation_stale`，不调用 Provider 或
  修改资产为 READY；下一轮现有 reconciler 将关联资产和批次收敛为 FAILED，不能一直
  停留生成中。不得为让旧 fixture 通过而削弱 worker 栅栏。
- 测试在独立、已迁移的本地 Jobs QA 数据库顺序执行，避免 Core 测试残留队列干扰领取。
  清理只删除本轮 fixture 对象；不接生产数据库，不输出凭据，不将合成音频当作现场
  TTS 验收。
- 回滚仅恢复这一测试补充及证据，不涉及业务数据、worker 代码或迁移。

本地补充验收已完成：新建 `jx_debate_qa_jobs` 并升级至 `0045_postmatch_showcase`，
两轮顺序 PostgreSQL 集成均 `5 passed, 50 deselected`；Jobs 非集成
`50 passed, 5 deselected`。正向任务完整成功且仍待人工审核；缺 generation 身份时
Provider 调用为 0，第二次 worker 轮询由现有 reconciler 将资产/批次收敛为 FAILED，
attempt 保持 1。Ruff、格式、定向 Pyright 与 diff 检查通过，收尾任务/资产/批次/调用及
fixture 用户/规则均为 0；未修改生产 worker、数据库或迁移。

## 1. 事实来源和范围

遵守 PRD 第 3 节、TechDesign 第 10/12/17 节和实时语音 Spike。根目录 PRD 工作副本当前被删除，
本次从 Git HEAD 读取已提交正文，不恢复或覆盖已有工作区改动。Spec 213 的“当前配置原地保存、修改只影响
新房间”继续有效；本规格不引入草稿/发布版本审批系统，也不重新启用旧 FormatVersion 写入口。

本次包含：赛制目录 CRUD、阶段增删排序、规则级校验、主持音频生成/试听/批准/重试、Core/Jobs TTS
配置一致性和旧数据修复设计。Agent 池继续执行 Spec 263 的全局音色自动同步、各赛制独立启停。

新房间仍只支持正式 4v4；历史非 4v4 保留只读。房间、比赛和已发布实验批次继续读取自身冻结快照。
不改变权限、MatchActor、计时起点、自由辩论算法或实时媒体协议，不引入新的服务、消息队列或对象存储。

## 2. 诊断：已证实的问题和潜在风险

### 2.1 2026-10-09 15:48 CST 线上只读证据

| 对象 | 证据 | 结论 |
| --- | --- | --- |
| 规则总览 | 15 套：4 归档、11 非归档；非归档中 2 套显示生成中 | 目录需要区分历史/运营状态与音频状态 |
| 4v4 正式辩论赛 · 新版本 | 10/10 资产 READY，路径/时长已登记；无审核时间、无关联待执行任务，规则仍 GENERATING_AUDIO | “生成成功待审核”被显示成“仍在生成” |
| YXH | 10 条资产：5 READY、5 PENDING；5 条 HOST_TTS 均 PENDING、attempts=0，创建于 10 月 8 日 17:35–17:36 | 主持任务约 22 小时没有被领取 |
| Jobs 配置 | 运行进程有 TTS_API_KEY/TTS_MODEL，但没有 DASHSCOPE_API_KEY；Jobs Settings/CLI 只读取旧字段 | HOST_TTS processor 被静默跳过，其他后台任务仍能执行 |
| Jobs 模型 | CLI 没有把配置的 TTS_MODEL 传入 client，client 默认旧 3.0 模型 | 即使补旧凭据字段，也可能调用错误模型 |
| 123 | READY、0 音频、审核时间为空 | 无主持词的规则也缺少明确的免审核语义 |
| E2E Test Rule (Short) | ENABLED、没有审核时间；此前确认缺默认模型 | 单个 ENABLED 字符串不能证明规则可用 |

本轮没有执行生产 TTS 调用、批准音频、修改任务/规则、重启服务或清理数据。

### 2.2 本地代码证据

- `rules/routes.py` 已有创建、基本编辑、删除、启停 API；规则目录仅提供新建和打开工作台，删除/归档
  没有完整 UI。阶段仅有 PATCH，没有新增、删除、重排接口。因此是 CRUD 交付不完整，不是所有后端能力都不存在。
- `host_tts_worker.py:133–145` 成功后只更新单条资产、完成任务，不汇总“全部已生成”；旧模型把生成中与
  等待人工确认合在一个状态，无法表达真实完成进度。
- `rules/service.py:1268` 审核只检查现存资产是否 READY，不比对阶段主持词的预期清单、文件存在性、输入
  指纹或批准对应的生成批次；0 条资产会直接通过。
- Web `rules/[ruleId]/page.tsx:754` 在 GENERATING_AUDIO 时展示审核，在 READY/FAILED 时缺少明确审核/重试
  路径；没有音频列表或试听入口，也没有轮询。校验页用 Agent 总行数而非真正可用数，与后端启用校验不同。
- 全局主持音色原地修改 `provider_voice/rate` 时，当前 `update_voice` 仅把校准标记为 STALE，没有重建已绑定
  规则音频；worker 运行时才读取音色表，输入没有冻结。
- 通用异常分支只 fail task，没有同步资产终态；最后一次领取后进程崩溃会留下 attempts 已达上限的 RUNNING
  任务，现有 claim 条件无法再领取。两者都可能导致“任务已失败、页面仍等待”。这是代码风险，非本轮线上故障证据。
- 任务完成没有 attempt/租约栅栏；资产更新、任务完成分属事务，存在部分提交、旧结果或重复生成风险。
- 删除接口使用 `agent_audio_storage_dir` 清理主持资产，与实际 `host_audio_storage_dir` 不一致；资产按
  `stage-position` 标识，阶段重排后可能错配。旧 draft 导出还过滤 END，不能作为可靠复制来源。

## 3. 产品决策：四种状态分别表达

### 3.1 规则生命周期

新增 `rules.lifecycle_status`，唯一权威值为 `DISABLED / ENABLED / ARCHIVED`。

| 生命周期 | 含义 | 管理行为 |
| --- | --- | --- |
| DISABLED | 新建、复制后默认停用，或管理员明确停用 | 编辑、复制、试听/批准、通过校验后启用、删除/归档 |
| ENABLED | 管理员保留新房间使用意图 | 编辑、复制、停用、归档；当前不满足校验时实际不可用 |
| ARCHIVED | 从运营目录移出并保留历史 | 查看配置和引用，允许复制为新规则；不修改原规则 |

继续原地保存当前配置，包括被历史房间引用的规则；保存不改写已有快照。只有删除受历史引用保护，不能因为
“被引用”就禁止编辑。历史非 4v4 配置只读，不能复制成可用新规则；新建与复制只接受 4v4。

`rule_key/version` 保留历史兼容。目录以规则 ID 隔离，显示配置修订号，不重新组织成赛制版本发布树。
“复制”创建新 rule_key/version=1，可与源规则独立启用；旧同 key 多版本继续遵循既有“一次只启用一个”的约束。

### 3.2 主持音频生成和人工审核

生成批次（下文简称 generation）记录真实工作，不承担规则启停。

| 生成状态 | 显示 | 可执行操作 |
| --- | --- | --- |
| NOT_REQUIRED | 未配置主持词，无需生成/审核 | 查看；启用仍需其他校验 |
| QUEUED | 已排队，已完成 x/y | 刷新；解释 worker/配置阻塞 |
| RUNNING | 正在合成，已完成 x/y | 刷新；查看逐项状态 |
| SUCCEEDED | 全部生成成功 | 试听、批准 |
| FAILED | 本轮处理已结束，部分片段失败/缺失 | 试听成功项、重试失败项 |
| SUPERSEDED | 已被更新配置替代 | 历史诊断；不得审核或回写当前规则 |

人工审核单独为 `NOT_REQUIRED / PENDING / APPROVED`。音频生成成功自动进入待试听，不自动批准。
批准绑定当前 generation 和主持输入指纹，记录管理员和时间；请求体必须带 generation_id 与 manifest_hash，
审核期间发生主持词/音色变化时拒绝旧批准。审核不是启动生成的按钮。

未配置主持词时可免审核，但 Core 必须确认预期清单确实为空；有主持词而资产表为空属于缺失，不能免审核。

### 3.3 校验与可用性

Core 返回两个不同结论：

- `readiness=READY/BLOCKED`：当前配置、音频批准和依赖资源是否合格；用于判断能否启用。
- `availability=AVAILABLE/BLOCKED/ARCHIVED`：生命周期为 ENABLED 且 readiness=READY 才 AVAILABLE。

检查至少包含正式 4v4、合法线性阶段/时长、所有必需 Prompt、默认模型、可用 Agent 数不少于 8、绑定音色与
模型状态、启用裁判的模型/Prompt、预期主持资产完整及批准。返回结构化 blocker（code、说明、定位路径、
可采取动作），目录、工作台、普通房间创建和实验准备/发布共用同一 Core 校验器。

主持词/音色变化后，保留 ENABLED 意图但 readiness 被阻止；页面显示“已启用 · 音频待生成/待试听 · 暂不能
用于新房间”。批准对该规则恢复可用性时，按钮明确写“批准并恢复新房间使用”，同时事务内重新校验。
普通非音频编辑不触发音频再生；依赖不合格也必须显示并阻止新房间使用。

## 4. CRUD 和阶段编辑

| 操作 | 服务端语义 | UI |
| --- | --- | --- |
| 查 | 所有状态均可查询，默认隐藏归档；可搜索/筛选/分页 | 正式/历史/归档筛选，名称、阶段数、时长、可用 Agent、音频进度、启停、可用性 |
| 新建 | 4v4、选择当前模板或空的有限结构；默认 DISABLED，自动同步 Agent 和默认 Prompt | 抽屉创建后进入工作台，不冒充已可开赛 |
| 改 | 当前配置事务内保存；expected_config_revision 不一致返回冲突，不覆盖他人编辑 | 基本信息、阶段、Prompt、Agent、裁判分区保存；失败保留输入 |
| 复制 | 复制完整阶段（含 END 主持词）、动作、Prompt、裁判、规则 Agent 偏好/模型/参数及 Prompt 覆盖 | 要求新名称；默认停用；明确音频需完成当前输入校验 |
| 启停 | ENABLED/DISABLED 意图改变；启用须 readiness=READY；锁定同 key 版本与房间创建 | 可用原因明确；未就绪时定位阻塞项 |
| 归档 | 显式变为 ARCHIVED，取消当前未完成 generation；保留配置、历史文件和审计 | 显示引用计数与影响，新房间不可选 |
| 删除 | 只有无房间/比赛/批次/旧版本引用且未启用才物理删除；否则 409 RULE_REFERENCED | 预检后展示“删除”或“归档”；不把 DELETE 成功偷偷解释成归档 |

复制时按同音色映射保留源规则 Agent 启停、模型、参数和 Prompt 覆盖；新启用的全局音色按 Spec 263 补齐默认
Agent。生成新的阶段/Agent ID，覆盖通过映射关联新阶段；不复制任务、审核证明、用户、房间或实验数据。

阶段支持新增、删除、上移/下移、完整重排和动作增删排序，复用既有四种有限原语。阶段 ID 稳定，排序只改
position；类型更换使用删除/新增，并确认受影响的 Prompt 覆盖。结束阶段唯一且固定最后，可编辑主持词，
不能删除；固定动作席位只允许 1–4，单次发言不超过 180 秒，估算最长不超过 90 分钟。

每次结构修改完整验证整个规则，事务锁定 Rule、原阶段/动作/Prompt；排序用安全的临时 position/延迟约束
避免唯一冲突。保存结构、修订号、受影响的 generation 和审计同一事务提交。Web 不重编运行时配置。

## 5. 音频数据、任务和文件

### 5.1 输入清单和批次

新增 `rule_audio_generations`：rule_id、generation_id、创建时 config_revision、manifest_hash、manifest
（有界、类型化的预期片段）、status、review_status、申请人/时间、开始/完成时间、审批人/时间、错误摘要。
Rule 的 `current_audio_generation_id` 指向当前 generation；同规则只有一个 current，旧批次保留只读。

manifest 包含：稳定 stage_id、开始/结束边界、当前 position、文本/哈希、主持 voice ID/provider_voice/rate、
TTS provider/model/workspace 和固定编码参数；只保存非敏感配置，禁止保存凭据。指纹同时包含模型和音色参数，
不能只按文本判断复用。配置修订号用于并发编辑；音频指纹独立，改 Agent 或 Prompt 不应让音频任务过期。

扩展 `host_audio_assets`：generation_id、片段输入快照/input_hash、active_task_id、byte_count/checksum。
保留已有 error_code，状态扩展为 PENDING/RUNNING/READY/FAILED；唯一约束从 `(rule_id, segment_key)`
调整为 `(generation_id, segment_key)`。进度、尝试次数来自资产与其当前任务，使用聚合/批量查询，避免另一份
可漂移的独立计数权威。stage_id/边界标识素材；position 只用于编译快照中的旧 segment_key，不当作素材身份。

创建/主持词修改/全局 HOST 音色有效参数变化/管理员显式重建时，在同一事务写 manifest、资产与任务并替换
current 指针。仅生成发生变化或缺失的片段，其他片段按完整 input_hash 复用已有验证文件。新 generation
仍须明确批准，不能继承整批审核证明；无音频变更不创建新批次。TTS_MODEL 的部署变更通过显式干跑/重建动作
刷新相关规则，不在 GET 中写入数据库。

### 5.2 任务身份、有限重试和收敛

HOST_TTS payload 包含 rule_id、generation_id、asset_id、manifest_hash 和 generation 的 config_revision。
领取时冻结 task_id/attempt_no，worker 只使用资产输入快照，不在运行中重新读取变化后的 voice 参数。

提交结果必须校验：当前 generation、manifest_hash、资产所属关系、active_task_id、task 的 attempt_no/租约
仍有效。批次 config_revision 与 payload 必须一致；不能要求 Rule 的总修订号始终相等，否则无关 Prompt/Agent
编辑也会错误废弃主持任务。这里没有 match/speech/connection/context 身份，不伪造实时比赛回调字段。

每项首次失败自动重试一次；二次失败为明确 FAILED，不换模型或供应商。手动“重试失败项”有管理员幂等键，
只为失败/缺失项创建新的有界任务，保留旧任务与尝试诊断，active_task_id 更新使旧结果失效；成功项不重做。

写文件到独立 task/attempt 临时路径，校验字节数、可解码性和正时长后原子发布不可变路径；数据库事务再一起
更新资产、任务和 generation 聚合状态。文件发布后数据库提交失败可重试，不允许先对外宣告 READY。时长需
核对实际解码样本数，不能只信 Ogg 容器 duration；复用现有 PyAV/有界 Jobs 路径，不改实时播放计时。

任务有全程超时和可续租 lease；attempt 耗尽且 lease 过期必须转终态，不能留下不可领取的 RUNNING。
所有异常（含内部异常）统一走资产/任务收敛。其他片段仍在处理时批次保持 RUNNING 并显示失败数；所有可执行
任务结束后，全成功为 SUCCEEDED，否则 FAILED。旧批次结果只记脱敏诊断，不能把新批次标失败或成功。

有界 reconciler 处理过期 lease、丢失任务、过时任务、资产与批次不一致；GET 只读，repair/retry 显式写入。
通用后台任务重试入口对 HOST_TTS 委托同一音频服务，不能只重置 task 而不恢复资产/generation。

### 5.3 文件和快照保留

已 READY 文件不可覆盖。房间与已发布批次继续冻结旧 segment_key/path/hash/duration；重排阶段、更换声音、
重新生成、停用或归档都不能删除被旧快照引用的文件。新增快照可附加 generation/input_hash 元数据，不更改
MatchActor 消费格式。删除/清理由 HOST_AUDIO_STORAGE_DIR 安全解析，不能使用 Agent 存储根。

清理前核对规则当前 generation、房间/比赛、实验批次冻结快照引用；无法确定引用时保留文件并报告。
旧 schema 的快照没有 generation_id，必须按 storage_path 检查，不以“数据库资产行已删除”当作无引用。
文件清理在提交后复用现有 FILE_CLEANUP 有界任务，失败可重试，不同步删除历史资产。

## 6. Core/Jobs 配置和阻塞可见性

Core/Jobs 使用同一明确配置契约：TTS_WS_URL、TTS_API_KEY、TTS_MODEL、TTS_WORKSPACE_ID 和
HOST_AUDIO_STORAGE_DIR。生产必须显式配置本项目已选择的 Qwen 3.1 TTS；Jobs 传入模型，不依赖 client 的
旧默认值。旧 DASHSCOPE 字段的兼容仅允许明确迁移，并记录使用旧键的脱敏提示，不静默改模型/供应商。

新增 PostgreSQL worker heartbeat/capability 行，Jobs 定期更新最近心跳与 host_tts 的 CONFIGURED/BLOCKED
和安全配置指纹（不含 secret）。Core 在音频面板返回“凭据未配置”“Jobs 无心跳”“存储不可写”等可操作阻塞。
后台任务仍能运行时不谎称整个 Jobs 停止；已批准文件不依赖实时 worker 心跳，worker 离线不使旧音频失效。

接口与 UI 只公开管理错误码和人类可读说明；供应商原始响应、堆栈、路径或密钥不进入 Toast。诊断复用现有
受保护日志/ExternalCall；记录实际使用模型、voice、task/attempt，不能继续把主持调用错误命名为 server_commit。

## 7. API 和 Web 交互

### 7.1 契约

规则目录/工作台新增具名 Pydantic 响应，OpenAPI 生成 Web 类型；去掉工作台手写 dict/重复 Stage DTO。
摘要含 lifecycle_status、config_revision、audio summary、readiness、availability、blockers、reference_summary
和 capabilities。capabilities 解释所有操作可否执行及原因；服务端提交时重新校验，不能信前端预检。

```json
{
  "lifecycle_status": "ENABLED",
  "audio": {"generation_id": "uuid", "status": "SUCCEEDED", "review_status": "PENDING", "ready": 10, "total": 10},
  "readiness": "BLOCKED",
  "availability": "BLOCKED",
  "blockers": [{"code": "HOST_AUDIO_REVIEW_REQUIRED", "message": "主持音频已全部生成，请试听并批准", "target": "audio"}]
}
```

新增/补全接口：

| 路径 | 作用 |
| --- | --- |
| GET /api/admin/rules | 白名单 q/lifecycle/audio_status/include_archived/page/page_size/sort/order；一致的计数与分页 |
| GET /api/admin/rules/{id}/workspace | 类型化完整配置、统一校验、引用、音频摘要和能力 |
| POST /api/admin/rules；PATCH /api/admin/rules/{id} | 创建、基本保存；写入带 expected_config_revision |
| POST /api/admin/rules/{id}/duplicate | 新身份，完整配置复制/映射；管理员幂等键 |
| POST/PATCH/DELETE /api/admin/rules/{id}/stages[/{stage_id}] | 阶段 CRUD；受完整结构校验 |
| PUT /api/admin/rules/{id}/stage-order | 全量 ID 顺序和 expected revision，原子重排 |
| POST /api/admin/rules/{id}/enable、disable、archive | 明确生命周期动作 |
| GET /api/admin/rules/{id}/deletion-preview；DELETE /api/admin/rules/{id} | 引用/文件/任务预检，提交再次校验 |
| GET /api/admin/rules/{id}/audio | 当前批次、逐项状态、任务/worker 阻塞；有界历史批次查询 |
| GET /api/admin/rules/{id}/audio/assets/{asset_id}/preview | 管理员授权试听、路径边界与文件验证；不暴露存储路径 |
| POST /api/admin/rules/{id}/audio/generate、retry、approve、reconcile | 显式生成/仅失败重试/绑定批次批准/修复；幂等键和审计 |

旧 review-audio 保留兼容一轮，要求当前成功批次并委托同一 approve 服务；旧规则 status 只作兼容投影，
所有写入从 lifecycle/audio/readiness 统一计算，不允许新旧路径各自赋值。

### 7.2 目录与工作台

目录行显示：名称、配置修订、阶段数/时长、有效 Agent 数、音频 ready/total 与待试听/失败、启停、可用性、
更新时间和操作菜单。历史非 4v4 与归档显式过滤，不混进可用赛制列表；当前坏配置保留可见并给修复入口。

工作台保持有限导航：概览、基本信息、阶段流程、阶段 Prompt、AI 裁判、主持音频。概览显示可用 Agent 数和
“管理本赛制 Agent”链接，进入现有规则范围 Agent 页，避免维护第二份可漂移表单。目录可直接进入这个入口。
阶段抽屉提供动作/主持词/Prompt；编辑开始/结束主持词都可明确看到音频失效影响。

主持音频面板展示按阶段排列的文本、音色/模型、状态、真实时长、试听、失败原因/重试。生成状态和审核状态
分开；进度表示已完成片段数，不是伪造百分比。生成中没有审核按钮；全生成后才显示“确认已试听并批准”。
无需主持词时明确“无需音频”，不要求空审核。

队列/运行中每 3 秒通过 TanStack Query 刷新摘要；配置阻塞继续以低频刷新 capability（修复配置后可自动恢复
显示），页面隐藏时暂停高频轮询；终态停止。rule_id/generation_id/revision 栅栏避免迟到数据覆盖新规则。
使用同一 Query cache 失效规则/音频/Agent；不靠 local state 判断就绪，不改实时 WebSocket。

使用共享 AdminShell/DataTable/Drawer/ConfirmDialog/StatusBadge/Toast。危险操作按预检解释影响；保存失败
保留输入与字段错误，离开有未保存编辑时确认。目标 1280×720、1440×900、1920×1080，无页面横向溢出；
遵守 reduced motion 和 transform/opacity，状态变化不撑高核心布局。

## 8. 迁移、旧数据修复和回滚

只追加一个或必要的后续 migration（预计从 0044 起，实际编号以实施时 head 为准）：新增 lifecycle/current
指针、generation/worker heartbeat、资产元数据、任务索引/约束。旧 Rule.status/audio_reviewed_at 保留兼容
投影，旧输入音频/历史文件不改路径；部署期间不允许不兼容版本交叉写。

回填工具默认 dry-run，报告规则、预期片段、资产、任务、引用、文件一致性和异常计数；apply 才分规则事务
回填。来源必须从 RuleStage 主持词计算预期 manifest，再与资产比对，不以资产表自己证明资产齐全。

- 旧 ENABLED/DISABLED/ARCHIVED 映射原意图；DRAFT/READY/GENERATING_AUDIO/FAILED 映射 DISABLED，
  不猜测此前的启用意图。保留旧 status 原值供审计。
- 预期清单为空才 NOT_REQUIRED；有词但缺资产/文件/时长/输入指纹，标失败/待核验。
- 完整 READY、文件/时长验证通过且旧审核时间有效的批次可保留历史批准；旧资产缺模型/voice/rate 历史元数据
  则标记 legacy provenance，不伪造为当前模型生成。已有启用规则的原文件继续兼容读取，后续修改必须生成新批次。
- 完整 READY 但旧审核为空，回填 SUCCEEDED/PENDING，不自动批准。对应“4v4 正式辩论赛 · 新版本”。
- YXH 的 5 条未执行任务：在配置修复后显式补身份/重新排队；已有 5 条成功资产校验后保留。不能只改规则为 READY。
- 任务终态与资产不一致、孤儿任务、耗尽 lease 等列为异常；由 reconcile/retry 收敛，不删除故障证据。
- E2E 等无默认模型/不满足条件的规则保持原配置并显示不可用；不擅自补模型或删除测试规则。

发布前：独立数据库演练迁移/回填和旧快照恢复；保留应用与数据库恢复点；核对非终态比赛为 0；确认
Core/Jobs/Web 的兼容范围。先部署兼容读取/配置/任务身份，再迁移回填，再开放管理写入口和新房间规则选择。
生产构建只用 pnpm build。本规格已获用户批准并按下述证据完成发布。

回滚：恢复经演练的 Core/Jobs/Web 兼容制品，保留追加 schema、generation、文件和审计，不 downgrade。
旧代码会重新使用旧状态/忽略 generation，回退时必须冻结规则编辑、音色修改、审核/重试等写入口，停止不安全
的旧 HOST_TTS 领取；已有房间继续用冻结文件。不能删除历史数据或恢复旧数据库覆盖新增比赛来“回滚”。

## 9. 实施切片与验收

1. 统一校验与配置：先修正 Jobs 字段/模型、worker capability 和零音频语义，补齐类型化音频摘要；验证
   TTS_API_KEY 可被正确识别、实际 client 模型正确、凭据输出为 0；不调用替代供应商。
2. generation/收敛：新增 schema、输入冻结、attempt 栅栏、原子提交、有限重试/reconciler。验证最后一条
   成功 → 待审核、二次失败 → 失败、崩溃耗尽 lease → 终态、旧批次成功/失败均不污染新批次。
3. CRUD/阶段：创建、完整复制、基本编辑、阶段增删/重排、启停、归档与安全删除。验证跨规则隔离、保留
   END 主持词和 Agent 覆盖、并发修订冲突、删除与房间创建并发不漏引用、音频免审核不能绕过缺资产。
4. Web：目录与主持音频面板，统一 blockers/Agent 可用数，所有 CRUD 与状态有可操作入口；加载、空态、
   失败、字段冲突、待试听、配置阻塞、历史只读和危险确认均覆盖 Storybook/Playwright。
5. 迁移/发布：独立 TEST_DATABASE_URL 验证旧生成中两类数据、文件引用、幂等 dry-run/apply、前向迁移和
   旧房间/批次快照；真实生产供应商短 TTS、管理员试听/批准和三种桌面视口另留证据。

每片完成对应 Core/Jobs/Web 单测、Lint、类型、契约/数据库或浏览器验证，再进入下一片。最终执行 pnpm lint、
pnpm typecheck、pnpm test、pnpm contracts:check、pnpm test:storybook、专项/必要全量浏览器、pnpm build
和 git diff --check。不把未执行的真人语音、五场压力、50 路 LLM 或音色盲听写为通过。

## 10. 本轮交付状态

已按批准的切片完成实现并发布：Core 增加规则生命周期、结构校验、generation/manifest、音频审核栅栏、
安全阶段 CRUD、readiness/blocker 和房间选择门禁；Jobs 增加 Qwen 3.1 配置兼容、任务/资产收敛、租约修复、
原子文件校验和 worker heartbeat；Web 增加规则归档、阶段操作和独立主持音频面板。数据库仅追加
`0044_rule_audio_generations`，旧资产通过显式 dry-run/apply 工具回填，不修改既有 migration。

本地证据：Core 392 passed/48 skipped，Jobs 25 passed/2 skipped，Web 214 passed；Spec 264 PostgreSQL
集成 5 passed，工具/QA 50+6 passed；Ruff、Pyright、Web TypeScript、ESLint、OpenAPI contracts 和生产
Web standalone build 均通过。

生产证据（2026-10-09）：发布前活动比赛为 0，数据库从 `0043_browser_device_checks` 升至
`0044_rule_audio_generations`；备份为 `/opt/jixia-backup-before-spec264-20261009195240` 与
`/opt/jixia-web-before-spec264-web-20261009200000`。旧数据修复 dry-run 无异常，apply 关联 43 条资产，
当前生成状态为 6 个 `NOT_REQUIRED`、5 个 `SUCCEEDED`；YXH 的 5 个待处理片段经 Jobs 成功完成，保留
历史失败任务诊断。四个 systemd 服务、Core live/ready、公开首页和 `/admin/rules` 均返回正常，发布后
只读 preflight 通过且无进行中比赛。

未执行外部供应商盲听或压力验收；这不作为本规格的通过条件。若回滚，使用上述应用/数据库备份恢复兼容
制品，保留追加 schema、generation、文件和审计，不 downgrade 数据库。
