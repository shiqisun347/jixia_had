# Spec 275 — 终态房间清理与生产内存护栏

- 版本：v2.0
- 状态：已于2026-10-15受保护发布；终态元数据修复、Core/Jobs有效nofile限制和发布预检通过；未安装未经实测定额的内存限制
- 日期：2026-10-13
- 授权：用户要求继续全面发现并修复比赛稳定性风险，并允许必要改进。
- 依据：Spec 272–274 复核及生产只读 OOM/LiveKit/数据库证据。

## 问题

历史 OOM 对应的比赛已经结束，但 LiveKit 房间继续存活近六小时；同一批终态房间的
`ended_at` 缺失，连接租约和媒体参与者的清理缺少可验证的终态边界。Core 进程还曾在
单进程约 1.4 GiB RSS、约 3.2 GiB swap 的情况下被内核 OOM killer 终止。批量实验导出
仍会将整批数据库行和多个 CSV 副本同时保留在内存中。

## 范围与验收

1. MatchActor 终态提交在同一数据库事务内写入 `matches.ended_at` 和关联
   `rooms.ended_at`，房间状态与比赛状态保持一致；重复终态提交保持幂等。
2. Core 启动时仅校正已有终态且缺失 `rooms.ended_at` 的记录，优先使用关联比赛的
   `ended_at`，没有时使用当前 UTC；不删除历史比赛、连接高水位或音频文件。
3. Agent、ASR、Presence 和 Judge 的关闭保持有界；终态广播后必须触发匹配的媒体/连接
   清理，迟到回调不能重新创建已关闭的 Presence 状态。
4. 新连接替换时，在同一用户摘要行锁内清除旧的 `RoomConnectionLease` 行，再插入当前
   lease；异常断线后的重复重连不会令租约表或 presence 的活动 epoch 无界增长。
5. 新增终态房间时间戳、启动校正、长房间/重复连接和关闭超时回归测试；记录生产只读
   指标和剩余风险。批量实验导出另行设计流式 SQL→Zip 或硬上限，不在本规格中假称已修复。
6. 生产护栏在发布前以观测数据校准：RSS、swap、Actor/后台任务、队列、LiveKit 房间与
   WebSocket 数量按分钟采集并告警；systemd 的 `MemoryHigh/MemoryMax` 和软 `nofile`
   只在受保护窗口配置，不在本地测试中写入生产。

## 非目标

- 不直接修改生产数据库、重启服务或关闭现存历史连接。
- 不修改既有 migration，不改变比赛计时、状态机或 LiveKit 协议。
- 不静默切换模型/供应商，也不以删除诊断记录掩盖清理超时。

## 回滚边界

仅回滚本规格新增的终态房间校正、回归测试和运维说明；保留 Spec 273/274 的供应商
流收敛、资源预算和任务清理修复。

## 本地完成证据（2026-10-13）

- MatchActor 终态提交现在在同一事务中写入关联房间的 `ended_at`；启动校正只处理终态且
  缺失时间的房间，优先采用关联比赛的结束时间，且不删除 `RoomConnection` 高水位记录。
- `RoomConnectionService.acquire()` 现在在用户摘要行锁内删除该用户遗留 lease，再写入
  当前连接；集成回归断言同一用户同时最多保留一条活动 lease。旧连接随后调用 release
  只会返回 false，不会影响新 epoch。
- ASR receiver 的 provider、LiveKit room 和 audio stream 关闭改为有界等待；取消抗性的
  native close 会被取消并脱离，迟到结果由回调消费，不再阻塞终态清理。新增关闭抗性资源
  回归测试；`apps/core/tests/test_asr.py` 当前 27 项通过。
- `AsrRuntime` 增加有界终态 tombstone 和全局关闭闸门；`close_match()` 之后的迟到
  `start_speech()` 不会重新创建 LiveKit/ASR receiver。ASR 回归测试当前 28 项通过。
- Core/Jobs 的 TTS provider socket bounded close 在超时或外层取消后都附加结果消费回调，
  迟到的 native/WebSocket close 异常不会形成未处理 task；Core Agent/Jobs TTS 定向测试
  61 项通过。
- AgentRuntime 为终态比赛增加有界 tombstone，`start_agent`、主持音和自由辩论决策在
  `close_match` 后拒绝启动；运行中的媒体连接仍按既有有界取消路径清理。新增房间时间戳、
  Agent 终态 fence、内容 blob 和待处理文本边界回归测试。
- Core 非集成 `488 passed, 76 deselected`；隔离 PostgreSQL 集成 `76 passed, 488 deselected`；
  Jobs `37 passed, 2 skipped`；QA `18 passed`；Web 单元 `222 passed`、Storybook `94 passed`、
  浏览器 `248 passed, 12 skipped`；Ruff、Pyright、lint、typecheck、contracts、生产构建和
  `git diff --check` 均通过。
- 生产只读审计确认当前进程约 90 MiB RSS、cgroup `oom_kill=0`；历史 OOM 的 Core
  Python 进程 anon-rss 约 1.41 GiB、swap 约 3.2 GiB。对应比赛结束后 LiveKit 房间仍存活约
  5 小时 49 分，且终态房间普遍缺失 `rooms.ended_at`；具体泄漏对象仍需受控长时 profiling。

## 生产内存证据补充（2026-10-13，只读）

- 主机总内存约 1.84 GiB（`MemTotal=1,932,260 kB`），Swap 约 4 GiB；当前可用内存约
  0.94 GiB，Swap 仍有约 3.29 GiB。`system.slice` 当前约 1.09 GiB，其中 PostgreSQL、
  Web、监控代理和容器运行时已经占用约 0.95 GiB（不含 Core/Jobs）。
- 2026-10-03 的内核 OOM 记录明确标记
  `task_memcg=/system.slice/jx-core.service`：Core 的 Python 进程达到
  `total-vm=6,520,068 kB`、`anon-rss=1,479,204 kB`，当时全机剩余 Swap 仅 144 kB；这
  是已确认的宿主级 OOM 证据，不是根据当前 RSS 推测。当前 Core/Jobs cgroup 的
  `memory.max`/`memory.high` 仍为 `max`，`oom_score_adj=0`。
- 10 月 11 日重启后的 cgroup 峰值只代表新进程生命周期：Core `memory.peak≈203 MiB`、
  Jobs `≈146 MiB`，不能覆盖历史高峰。当前 Core/Jobs 的 `memory.events.oom_kill` 都为 0，
  但这不证明历史泄漏已经消失。

### 内存上限校准建议（只建议，不是已执行配置）

在隔离 staging 完成五场 4v4、长时 TTS/ASR 和导出压力，并记录 cgroup `memory.peak`、
`memory.events`、延迟和比赛完成率后，才可选择具体值。以当前 2 GiB 级主机作为临时应急
边界，可把 **Core `MemoryHigh=384M`、`MemoryMax=640M`；Jobs `MemoryHigh=192M`、
`MemoryMax=320M`** 作为第一组候选；它们不是生产批准值。`MemoryMax` 超限会在 cgroup 内
触发 OOM，若 staging 的 p99 峰值接近候选上限，应先扩容/拆分服务再提高上限。受保护窗口
安装前必须保留旧 drop-in、执行一次 no-write 预检，安装后复核实际 `MemoryHigh/Max`、
RSS/swap、`oom_kill` 和完整比赛链路；任何异常都按 systemd drop-in 回滚，不改数据库。

## 发布前遗留项

- 不在本地或本轮会话中直接修改生产数据库/重启服务。发布窗口需要先执行只读校验，再观察
  终态房间时间、LiveKit participant 数、清理超时、Core RSS/swap 和 systemd OOM 计数。
- systemd 当前没有校准的 `MemoryHigh/MemoryMax`，Core 软 `nofile` 仍可能为 1024；应以
  staging 压测结果确定内存阈值并把软/硬 `nofile` 一起配置。
- 实验批量导出的全量内存风险已移入 Spec 276：生产路径改为流式 SQL→Zip，并有单行、单文件
  和整批未压缩字节上限；仍需在受保护窗口用代表性大批次校准阈值。

## 运维审计补充（2026-10-13）

- 新增只读 `scripts/ops/runtime_guard_audit.sh` 及 Python 实现：读取 Core/Jobs
  systemd `MainPID`、`LimitNOFILE`、`MemoryHigh/MemoryMax`，并读取对应 `/proc/$pid/limits`
  的软/硬 `Max open files`；默认要求有效软 nofile 至少 524288，
  `JX_PREFLIGHT_REQUIRE_MEMORY_GUARDS=1` 时对无限制的内存护栏 fail-closed。脚本不执行
  `daemon-reload`、重启或任何写操作。
- `scripts/ops/preflight_v2_1.sh` 现在调用上述审计，并在数据库门禁中报告并拒绝：终态房间
  缺失 `ended_at`、终态房间仍有连接租约、任意活动连接租约；这样发布前不会把遗留
  WebSocket/LiveKit 会话误判为可安全重启。
- `ops/systemd/jx-core.service.d/resource-limits.conf` 与 `jx-jobs.service.d/resource-limits.conf`
  提供受保护发布时可安装的 `LimitNOFILE=524288:524288`，本地变更不会自动触及生产。
- 新增 `scripts/qa/tests/test_runtime_guard_audit.py`，覆盖 systemd/proc/cgroup 解析、软限制
  fail-closed、可选 MemoryHigh/MemoryMax/telemetry 门禁和脚本只读性质；本地测试 11 项通过，Ruff、
  Pyright、shell 语法检查通过。生产 systemd 配置仍待受保护窗口执行并复核有效进程限制。
- Spec 295 扩展同一只读审计，补充 cgroup 当前/峰值内存、swap、`oom_kill` 和 pids；
  `JX_PREFLIGHT_REQUIRE_CGROUP_TELEMETRY=1` 时才把缺失快照或非零 `oom_kill` 作为发布门禁，
  默认仅采集并展示，不把重启后的低峰值误当成历史安全证明。

## 2026-10-15 生产发布终态补充

此前候选／未发布段落保留为当时快照。用户明确批准正常关闭两个旧候场后，
`20261015-asr302-6004cca8`已受保护发布。正常管理员接口保留历史，维护前／停服务后
非终态房间和比赛0；服务器私有备份后升级0046、修复63个终态结束时间并安装已批准
Core/Jobs nofile设置。部署runner及完整预检exit0；独立209文件SHA、Web构建、13路由
和16静态资源核验通过，未登录的3个受保护API均401。300.188秒／11次观察健康、同PID、
无重启、cgroup OOM0及新增warning-or-higher日志0。真实短供应商／RTC／ASR链通过，
完整4v4混合媒体QA仍进行中；未把合成麦克风、短链或五分钟观察当作现场／五场压力。
详见`profile_output/workflow_candidate/release/RELEASE_REPORT.md`和独立生产收据。
