# Spec 279：研究导出与赛后列表的内存边界

状态：本地实现完成，待受保护发布。

## 背景

`MATCH_EXPORT` 原先把单场比赛的 speeches、match_events、external_calls
以及导出项全部收集为 Python 列表。正常 4v4 比赛行数很小，但重连循环、回调
重放或损坏数据会让这些查询无上限增长，后台任务可能先耗尽内存，再影响 Core
和 Web。参与者 `/api/me/postmatch-surveys` 同样会返回该用户的全部问卷并为每
场读取完整比赛记录；目前每个比赛的 4v4 记录有业务上限，但接口没有分页契约。

## 范围与验收

1. `MATCH_EXPORT` 的单场 speeches、events、provider calls 和 export items 查询
   各请求一个 sentinel 行，超过上限立即以脱敏错误码
   `export_match_rows_too_large` 失败；正常导出的字段、排序和 ZIP 结构不变。
2. 上限固定为 speeches 8,192、events 16,384、provider calls 16,384、单批
   export items 100，与正式 4v4 和创建导出的请求约束相容；不会把不受控行数
   继续 materialize 到内存。
3. 超限路径有单元回归测试，既有导出测试和 jobs/Core 门禁保持通过。
4. 赛后问卷列表本轮只完成风险审计，不改变现有数组契约；后续需另立规格增加
   cursor/limit，并将 speeches、participants 改为批量查询后再实施。

## 回滚边界

回滚本规格仅恢复导出查询的旧 SQL；不改数据库结构、不删除导出数据，也不改变
比赛状态机、权限或外部 provider 协议。上线前必须通过受保护 staging 验证；本轮
不执行生产写入、重启或发布。
