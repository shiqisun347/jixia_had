# Spec 294：历史游标单调性与问卷载荷预检

状态：本地实现完成，待受保护发布

## 问题

实验批次/预约兼容 helper 已有分页总量上限，但仅按 opaque 游标字符串去重；损坏的
游标可能在预算耗尽前重复扫描。参与者标注任务的数据库预检也未计入问卷 `q6` 文本，
超大问卷可能先被 ORM 物化，再由响应预算拒绝。

## 范围与验收

- Core 批次和预约兼容 helper 解码每个续页游标，并按各自查询排序验证严格前进；重复、
  回退或无法解码的游标立即返回 `experiment_query_invalid`，仍保留 200 页/10,000 行总量
  护栏，不返回截断数组。
- 预约排序的 `scheduled_at ASC NULLS LAST` 将有时间值进入 NULL 桶视为合法推进；NULL
  桶内仍须按 round/match/id 严格递增，NULL 返回有时间值或槽位不前进立即失败。
- 参与者标注 child-budget 预检在读取关联 payload 前计入 `MatchQuestionnaire.q6` 的
  UTF-8 字节，超出单任务/整页预算时返回 `experiment_annotation_payload_too_large`。
- HTTP 分页、opaque 游标编码、权限、标注生命周期和数据库 schema 保持不变；Web 端只
  检查可识别的重复游标，不猜测供应商/服务端 opaque 游标的排序语义。

## 非目标与回滚边界

不修改数据库、不删除历史、不改变比赛状态机或生产资源配置，也不执行生产写入、重启或
发布。回滚仅移除 Core 兼容 helper 的游标单调性检查、`q6` 预检和对应测试/文档；HTTP
分页及已有总量护栏不受影响。
