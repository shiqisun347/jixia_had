# Spec 293：标注历史兼容 helper 有界聚合

状态：本地实现完成，待受保护发布

## 问题

Spec 285/288 已将参与者与专家标注 HTTP 列表接口改为有界游标分页，但
`ExperimentService.list_participant_tasks` 与 `list_expert_tasks` 仍为历史兼容
helper。它们会自动不断追页并把所有任务响应保留在一个数组中；上游游标回环、游标
不前进或持续增长的历史会让 Core 无限查询并放大内存。

## 范围与验收

- 两个兼容 helper 继续返回完整数组语义，但最多追踪
  `EXPERIMENT_HISTORY_MAX_PAGES`（当前 200）页、`EXPERIMENT_HISTORY_MAX_ROWS`
  （当前 10,000）行；达到任一上限时抛出稳定的
  `experiment_history_too_large`，绝不返回被截断的数组。
- helper 使用与数据库排序一致的降序 `(created_at, id)` 游标。重复游标或不向更旧
  记录前进的游标立即以 `experiment_query_invalid` 失败，避免依赖页数上限才收敛。
- HTTP 分页接口、每页字段、权限、游标编码和数据库 schema 保持不变；需要更大历史的
  调用方继续直接按游标分页读取。
- 增加针对两个 helper 的总量、重复游标和不前进游标回归测试。

## 非目标与回滚边界

不改变标注生命周期、答案保存、音频授权或数据库数据，不修改既有 migration，也不执行
生产写入、重启或发布。回滚只移除 helper 的聚合护栏及其测试/文档；HTTP 分页和历史
数据不受影响。
