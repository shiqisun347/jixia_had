# Spec 277：迟到实验回调的有界机会查询

状态：本地实现完成，待受保护发布

## 背景

迟到 Agent/ASR 回调在收尾路径中需要找到触发它的自由辩论机会。旧实现按比赛读取
全部 `ACTIVE` 机会，再在 Python 中扫描 `frozen_context.source_speech_id`。历史机会
没有及时收口时，这条回调路径会同时放大 ORM 对象数量、事务锁持有时间和 GC 压力，
使偶发 provider 延迟进一步拖慢比赛恢复。

## 范围与验收

- Agent 迟到 final、ASR 迟到 final 和迟到 ASR 决策三条路径均使用数据库端的比赛、状态、
  source speech 精确条件查询，并限制最多返回一行。
- 保留对旧数据中仅在 `frozen_context` 保存 source speech 的兼容条件；不改变机会状态、
  终态回调 fence、锁语义或比赛状态机。
- 查询不再调用 `.all()` 或在 Python 中扫描全部 `ACTIVE` 机会；重复/异常数据最多选择
  一个稳定的机会，不会扩大回调内存占用。
- 新增 PostgreSQL SQL 编译回归测试，检查 `LIMIT 1`、行锁及旧 JSON 数据兼容条件。
- Core 启动的终态 Room 时间校正使用单条相关子查询 `UPDATE`，不再把全部历史终态房间
  载入 ORM 列表；返回受影响行数供现有日志使用，保持幂等和 Match 时间优先语义。
- Core 启动清理旧 `RoomConnectionLease` 时按 500 行批次读取并删除，仍返回
  `(user_id, room_id)` 对供既有 `RoomMember.online=false` 处理；应用层重复批次直到为空，
  不改变 presence 语义。

## 非范围与回滚

不修改 migration、索引、供应商协议、机会生命周期或生产数据。回滚仅需恢复三条回调
查询到原实现；若发现旧数据条件不足，应先补数据兼容查询，不得静默跳过迟到结果。

## 验证证据

- `uv run pytest apps/core/tests/test_match_service_reset.py apps/core/tests/test_room_connections.py -q`：28 passed。
- 完整 Core 非集成 `488 passed, 76 deselected`；隔离 PostgreSQL 集成 `76 passed, 488 deselected`。
- 生产数据库与服务本轮未写入、未重启；发布前需在受保护窗口执行只读 preflight。
