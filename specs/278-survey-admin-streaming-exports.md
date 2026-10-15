# Spec 278 — 管理员问卷展示与导出流式边界

- 版本：v2.0
- 状态：本地实现完成，待受保护发布
- 日期：2026-10-13
- 授权：用户要求继续全面发现并修复稳定性风险；本规格只改变管理员问卷读取和导出的内存行为。
- 依据：Spec 275/276 的内存审计遗留项——管理员个人/赛后问卷接口对不断增长的表使用 `.all()`，并把完整 JSON、CSV 和行投影同时保留在 Core RSS。

## 问题

`/api/admin/surveys/personal`、`/api/admin/surveys/personal/export` 和
`/api/admin/surveys/postmatch/export` 原先先把数据库结果转成完整列表，再构造响应。
个人问卷答案和赛后问卷答案均为 JSONB，数据量或单份答案变大时，数据库结果、Python
对象、JSON/CSV 字符串和最终响应会同时驻留，导致管理员查看或导出拖慢 Core，并可能放大
前文记录的内存压力。

## 范围与验收

1. 管理员个人问卷展示保留原有 JSON 对象形状（`version` 和 `responses` 字段），但
   `responses` 通过 SQLAlchemy async stream 逐条编码；不在 Core 生产路径构造完整结果列表。
2. 个人问卷和赛后问卷 JSON/CSV 导出保留原有字段、排序和 `Content-Disposition` 文件名，
   以 `StreamingResponse` 逐行读取和编码；CSV 只保留当前行的短暂缓冲，JSON 只保留当前
   行和数组分隔符。
3. 每个流式 SQL 结果在成功、异常和取消路径调用 `close()`；审计事件先提交，避免客户端
   中断时回滚或遗留游标。导出审计使用单值 `count(*)`，不构造行列表。
4. 增加回归测试，验证旧 JSON 形状、JSON/CSV 字段和游标关闭；不宣称真实大规模导出或
   生产压力测试通过。

## 非目标

- 不改变问卷答案校验、权限、审计动作名、查询排序、字段命名或数据库 migration。
- 不重构参与者 `/api/me/postmatch-surveys` 的逐场投影；该接口仍存在全量任务和语音记录的
  读取风险，留作后续分页/游标规格。
- 不直接写生产数据库、重启 Core 或修改 systemd 资源限制。

## 回滚边界

仅回滚 `survey_routes.py` 的流式响应、问卷路由回归测试和本规格文档；保留 Spec 275/276
的终态清理、Jobs 导出流式写入和其他资源护栏。

## 本地完成证据（2026-10-13）

- `uv run pytest apps/core/tests/test_survey_routes.py -q`：3 passed。
- `uv run ruff check apps/core/src/jx_core/survey_routes.py apps/core/tests/test_survey_routes.py`：通过。
- `uv run pyright apps/core/src/jx_core/survey_routes.py apps/core/tests/test_survey_routes.py`：0 errors/warnings。
- 本规格未进行真实大批次导出、供应商调用、生产写入、重启或发布。

## 发布前遗留项

- 在受保护窗口用代表性个人/赛后问卷规模执行 JSON 与 CSV 导出，观察 Core RSS、响应时延、
  DB 游标持续时间和客户端取消后的连接回收。
- 为参与者赛后问卷列表设计独立分页/游标协议，并在不破坏已有客户端的情况下限制跨场次
  语音记录投影；当前规格只关闭管理员接口的最直接全量响应风险。
