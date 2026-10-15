# Spec 286：实验赛后标注 payload 有界化

状态：本地实现完成，待受保护发布

## 背景

实验赛后任务在结束事务中读取比赛全部已完成发言和自由辩论机会，再将发言历史复制到
每个机会的冻结上下文并复制到专家答案。历史脏数据、异常长文本或超大 JSON 会造成
数据库结果、Python 对象和事务写入按机会/专家倍增，阻塞 Jobs 或延长 Core 提交事务。

## 范围与验收

- Jobs `EXPERIMENT_POSTMATCH` 的 speeches 和 opportunities 查询使用 sentinel `LIMIT`
  （上限 4,096 行）；超过上限以稳定 `experiment_postmatch_payload_too_large` 失败，
  不继续创建标注项或专家答案。
- 发言显示文本总量最多 64 MiB；单个冻结上下文最多 8 MiB，所有机会生成的上下文最多
  64 MiB。JSON 预算按写入 PostgreSQL 的 UTF-8、紧凑 JSON 字节计算，非对象 JSON
  fail-closed 使用同一稳定错误码。
- Core 兼容的赛后标注创建路径使用相同的行、文本和 JSON 上限；超限抛出携带
  `code` 的 `PostmatchAnnotationWorkError`，事务回滚，不修改 schema 或迁移。
- Core 与 Jobs 在读取完整 `speeches`、`free_debate_opportunities` 行之前先执行聚合预检：
  发言只统计带 `opportunity_id` 的行，JSONB 大小按 `::text` 的逻辑 UTF-8 字节统计，
  不使用压缩后的 `pg_column_size`；通过预检后仍保留 sentinel 查询作为并发变化的兜底。
- 正常正式 4v4 的发言、问卷标注项、机会冻结历史和专家答案字段保持不变；不截断历史、
  不静默丢弃数据，也不切换供应商。任务失败由既有 Jobs 错误闭环记录并可重试/人工处理。

## 回滚边界

仅回滚 Core/Jobs 应用代码和对应测试、文档即可恢复旧物料化行为，不涉及数据库迁移、
生产数据删除或外部服务重启。发布前后应观察 `EXPERIMENT_POSTMATCH` 成功/失败错误码、
Jobs/Core RSS、事务时延和标注任务创建数量；若代表性长比赛触及阈值，应在新规格中校准
限制，不在生产临时放宽。

## 验证证据

- Jobs `test_experiment_worker.py` 覆盖行数、文本、超大/非对象 JSON 的稳定失败；专项 9 项通过。
- Core `test_experiments.py` 覆盖行数、超大/非对象 JSON 的稳定失败；专项 12 项通过。
- Ruff 检查通过；真实 PostgreSQL 大比赛和长文本压力应在受保护测试/发布窗口执行。
