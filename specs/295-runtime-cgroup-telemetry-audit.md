# Spec 295 — 运行时 cgroup 内存与进程数只读审计

- 版本：v2.0
- 状态：本地实现完成，待受保护发布
- 日期：2026-10-13
- 依据：Spec 275 生产 OOM 证据与当前运行时护栏审计。

## 问题

`runtime_guard_audit` 目前只能看到 systemd 声明的 `MemoryHigh/MemoryMax` 和进程
当前的 `nofile`，无法证明服务重启前的 cgroup 内存峰值，也无法发现 cgroup 已经发生的
`oom_kill`。历史 Core OOM 恰好发生在重启前，单看当前 RSS 或重启后峰值会低估风险。

## 范围

- 只读读取 Core/Jobs 的 systemd `ControlGroup`，并在 cgroup v2 根目录下读取：
  `memory.current`、`memory.peak`、`memory.swap.current`、`memory.swap.peak`、
  `memory.events` 的 `oom_kill`，以及 `pids.current`/`pids.max`。
- 审计输出仅保留上述白名单字段、服务名、PID 和资源限制；不存在或无法读取的字段以
  `unavailable` 表示，不输出 unit 环境变量、命令行、路径中的密钥或原始文件内容。
- 保持现有 nofile 与可选内存护栏门禁语义；本规格不自动把峰值转换为限额，不写 cgroup、
  systemd 或数据库。
- 发布预检继续只读调用审计脚本，允许运维人员用当前值、峰值和 `oom_kill` 作为受保护
  窗口的证据。
- systemd nofile 诊断分别读取 `LimitNOFILE`（hard）和 `LimitNOFILESoft`，同时保留进程的
  实际 soft/hard；缺失 soft 属性显示 `unavailable`。新 soft 字段只用于诊断，既有 systemd
  hard 及进程 soft/hard 门禁保持不变，避免把 hard=524288 误解为 soft 已生效。

## 验收

1. fake cgroup 文件能被解析为数值/`max`，缺失文件显示 `unavailable`；只有显式启用
   telemetry 门禁时才因此拒绝放行，未知的 `memory.events` 键不会进入输出。
2. `inspect_service` 通过 `ControlGroup` 读取全部白名单字段，并在无 cgroup 或权限不足时
   返回脱敏的 `None`，不抛出路径或文件内容。
3. CLI 输出包含当前/峰值内存、swap、`oom_kill`、pids 和 systemd nofile hard/soft 字段；脚本仍不执行
   `daemon-reload`、重启、写文件或写数据库。
4. 既有 nofile/MemoryHigh/MemoryMax 门禁和服务/LiveKit 预检行为保持不变；QA 覆盖正常、
   缺失、`max` 与非零 `oom_kill` 情况。
5. 不把重启后的低峰值宣称为历史安全证明；文档明确要求按分钟采集与 staging 压测后再
   选择 `MemoryHigh/MemoryMax`。

## 本地完成证据（2026-10-13）

- `runtime_guard_audit.py` 读取受白名单约束的 cgroup 文件，并输出当前/峰值内存、swap、
  `oom_kill` 和 pids；缺失或路径异常返回 `unavailable`，未知事件键与 Environment 字段
  不进入报告。
- `--require-cgroup-telemetry` 可在受保护发布预检中对缺失快照或非零 `oom_kill` fail-closed；
  默认仍只读采集，不改变现有 nofile/可选 MemoryHigh/Max 门禁。`preflight_v2_1.sh` 通过
  `JX_PREFLIGHT_REQUIRE_CGROUP_TELEMETRY=1` 显式启用该门禁。
- QA `test_runtime_guard_audit.py` `14 passed`；新增 hard 高而 soft 低、缺失 soft 属性和
  进程实际限制与声明不一致的回归；Ruff、Pyright、shell 语法和 `git diff --check` 通过。
- 线上只读复核确认 Core/Jobs 的 systemd hard 为 524288、soft 为 1024，进程也为
  1024:524288。现有 unit 未声明 `LimitNOFILE`，继承 manager 的 soft=1024；当前打开的
  文件描述符为 Core 34、Jobs 8，没有耗尽证据。仓库 drop-in 需在受保护窗口安装并重启
  后验证，不能把 hard 字段当作 soft 已生效，也不能据此断言当前卡顿由 FD 耗尽导致。
- 未写入生产数据库、systemd 或 cgroup，未重启任何服务；当前生产值与候选 MemoryHigh/Max
  仍需在 staging 压测后由受保护窗口决定。

## 回滚边界

仅移除新增 cgroup/nofile soft 诊断字段、解析、显式 telemetry 门禁入口和 QA；保留旧 nofile
与可选内存门禁，不改变 systemd 配置、服务状态、比赛状态或数据库。
本规格不执行生产写入、重启或限额安装。
