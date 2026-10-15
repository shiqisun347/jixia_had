# Spec 283 — Terminal 状态修复运行手册

状态：implemented locally, production execution pending protected window

## 问题

历史 Core 重启前，部分已结束房间缺少 `rooms.ended_at`，且连接租约可能残留。发布闸门必须在发布前拒绝这些状态，但人工直接 SQL 修复容易误触正在进行的房间。

## 范围

- 新增只读默认的 `scripts/ops/repair_terminal_state.py`。
- 报告非终态房间/比赛、终态房间缺少结束时间、连接租约及孤儿租约数量。
- `--apply` 仅在不存在任何非终态房间和比赛时执行：补齐终态房间 `ended_at`、清除连接租约，并把对应仍在房间中的成员标记离线。
- 修复使用现有 Core 原子服务和有界批处理，不删除比赛、房间或用户历史。

## 验收

1. 默认运行不写数据库，并输出可机器读取的 JSON 报告。
2. 发现非终态房间或比赛时，`--apply` fail-closed，退出码为 2。
3. 应用成功后重新检查，`terminal_rooms_missing_end_at == 0` 且 `active_leases == 0`。
4. 清理连接租约按批次执行，单次不物化全表。
5. 不读取或输出 `api.md`、`.env` 或任何凭据。

## 回滚边界

该操作只补写终态房间时间戳、删除连接租约并更新在线标记。租约删除本身不可恢复，但不会删除业务历史；执行前必须保存 JSON 报告并在受保护窗口运行。

## 受保护窗口执行顺序

下面的顺序是运维操作手册，不在本地测试或普通发布命令中自动执行。每一步都应保存
脱敏输出；不要把 EnvironmentFile、连接串或访问令牌复制到终端记录。

1. 先运行只读发布预检和修复脚本的默认模式。只要 `active_matches`、
   `nonterminal_rooms` 或 `nonterminal_matches` 不为零，就停止，不得使用 `--apply`。
   当前存在暂停比赛时，必须先通过现有管理员流程恢复并完成，或记录审计后终止；不能用
   SQL 直接改状态，也不能靠删除租约绕过比赛状态机。
2. 预检确认没有非终态房间/比赛后，进入维护窗口并停止新连接入口。先停止 Web 和 Jobs，
   再停止 Core，使数据库在修复期间没有业务写入；LiveKit 容器保持只读观察，只有确认没有
   参与者和房间后才允许按既有 LiveKit 运维流程停止。停止后再次运行默认只读报告，报告
   必须仍然满足 `ready_to_apply=true`。
3. 保存这份报告后才执行 `python scripts/ops/repair_terminal_state.py --apply`。脚本退出码
   为 2 表示安全门禁重新发现了活动状态；任何非零结果都应恢复服务到原状态并停止排查，
   不得重试到成功为止。成功后检查 `after.terminal_rooms_missing_end_at == 0`、
   `after.active_leases == 0`，并保留 JSON 作为回滚边界证据。
4. 只有在修复成功后才安装仓库中的 systemd drop-in，执行 `systemctl daemon-reload`，
   然后按 Core → Jobs → Web 顺序重启。重启完成立即运行
   `scripts/ops/runtime_guard_audit.sh --min-nofile 524288`；Core/Jobs 的进程软、硬
   `nofile` 都必须达到 524288。`MemoryHigh/MemoryMax` 未经 staging 校准前不要填入猜测值，
   也不要把未设置内存上限报告成已修复。
5. 最后再次运行完整只读预检，检查健康端点、迁移 head、终态时间戳、租约和 LiveKit
   容器镜像；观察至少一个完整清理周期的 Core RSS、swap、OOM 计数、连接租约和 LiveKit
   participant 数，再解除维护窗口。任一门禁失败时保留服务停止状态并按发布回滚手册恢复，
   不删除数据库历史。

该顺序通过“服务停止后再应用”的维护窗口消除了修复脚本与实时写入之间的竞态；脚本本身
仍保持默认只读，并且 `--apply` 的非终态门禁永远保留。systemd drop-in 的安装、重载和
重启不由仓库脚本代办，避免误触生产。

## 非终态对象的管理员处理路径

以下仅用于用户明确决定放弃继续比赛或取消候场房间后；执行前重新核对具体 ID 和状态。
若选择保留恢复比赛，则继续恢复排查，不能进入要求全部非终态归零的修复/重启窗口。

- 暂停比赛：`POST /api/admin/matches/{match_id}/terminate`，无请求体，由 ADMIN 会话
  通过 MatchActor 串行终止，关联房间同步进入终态。保留已有历史，但不能再正常恢复该场
  比赛；不要先用房间终止接口绕过比赛 Actor。
- 独立候场房间：确认仍为 WAITING 且没有非终态比赛后，分别调用
  `POST /api/rooms/{room_id}/terminate`，无请求体，管理员正常关闭并保留房间记录。
- 上述接口均要求有效、已完成初始改密的会话和允许的 Origin/Referer；现有 `jx-admin`
  只有创建管理员功能，没有离线终止命令。不能以清理数据或直接 SQL 更新状态替代。
- 比赛终止写入 `admin.match.terminated` 审计，但没有自定义原因字段；房间关闭接口没有
  内置原因审计。将用户决定、理由、具体 ID、操作者、时间和前后状态保存到运维记录中，
  不声称接口已存入其不支持的原因字段，也不记录会话凭据。

2026-10-13 只读定位：暂停比赛为 `2dce08b6-b4f9-40c5-b99a-3e01eef0ba28`，关联房间
`604ae47b-d325-4efb-83cf-9ead134d8e91`；两个独立 WAITING 房间为
`d529f170-591d-44c5-b348-e301be6af341` 和 `95565a6c-98a3-497e-807e-1703c111eaed`。
已请求用户选择，尚未执行终止、关闭或发布。
