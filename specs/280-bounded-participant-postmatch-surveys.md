# Spec 280：参与者赛后问卷分页与批量投影

- 版本：v2.0
- 状态：本地实现完成，待受保护发布
- 日期：2026-10-13
- 依据：Spec 278/279 对 `/api/me/postmatch-surveys` 的遗留审计。

## 问题

参与者列表接口原先读取当前用户的全部任务，并为每场比赛逐条查询比赛、房间、
席位和完整语音记录。历史比赛增加或异常重放时，单个请求的数据库结果、Speech
对象和最终 JSON 会一起增长；详情接口还会先构造全部历史任务再寻找目标任务。

## 范围与验收

1. `/api/me/postmatch-surveys` 继续返回原有数组形状，默认只读取最近 25 条，
   `limit` 上限为 50；可用 `cursor`（`created_at` 与任务 UUID 的稳定排序键）
   读取后续页面，下一页游标通过 `X-Next-Cursor` 响应头返回。旧客户端无需改动，
   默认请求仍能解析为数组。
2. 列表默认 `include_items=false`，只返回任务摘要、目标总数和已确认数；需要
   语音项目的客户端可明确请求 `include_items=true`。详情路由只投影目标任务并返回
   完整项目，不能扫描当前用户的全部历史任务。
3. 一页的 Match、Room、MatchParticipant 和 Speech 使用批量查询，消除每场比赛的
   N+1 查询。Speech 只加载列表投影需要的列，不读取 ASR 原文、LLM 草稿和音频路径。
4. Speech 查询按比赛设置 512 条 sentinel、整页 16,384 条上限和 64 MiB 可见文本
   预算；超过上限返回脱敏 `survey_history_too_large`，不静默截断答案或比赛记录。
   非法游标返回 `survey_query_invalid`。
5. 新增游标、边界和流式结果关闭回归测试；专项 Core 测试、Ruff、Pyright 和契约
   检查通过。不宣称真实大规模生产压力测试、供应商链路或发布通过。

## 非目标

- 不改变问卷答案校验、提交状态、权限、字段命名或数据库结构。
- 不把分页元数据塞进 JSON 数组，避免破坏既有 Web 客户端；新客户端可读取响应头。
- 不直接写生产数据库、重启 Core/Jobs/Web 或修改已发布 migration。

## 回滚边界

仅回滚参与者问卷列表的分页参数、批量投影和相关测试/文档；保留 Spec 278 的管理员
流式导出、Spec 279 的研究导出上限及其他资源护栏。回滚不删除问卷数据。

## 本地完成证据（2026-10-13）

- `uv run pytest apps/core/tests/test_surveys.py apps/core/tests/test_survey_routes.py -q`：20 passed。
- `uv run ruff check apps/core/src/jx_core/survey_service.py apps/core/src/jx_core/survey_routes.py apps/core/src/jx_core/auth/errors.py`：通过。
- `uv run pyright apps/core/src/jx_core/survey_service.py apps/core/src/jx_core/survey_routes.py apps/core/src/jx_core/auth/errors.py`：0 errors/warnings。
- `pnpm --filter @jx/web lint`、`pnpm --filter @jx/web typecheck`、Web 222 tests：通过。
- 未进行生产写入、重启、真实大规模历史导出或外部 provider 调用。

## 发布前遗留项

- 在受保护 staging 用包含多场历史、异常大 speech 文本和客户端取消的样本验证
  `X-Next-Cursor`、内存峰值、数据库游标回收和错误提示。
- 按真实使用量评估 `postmatch_survey_tasks(user_id, created_at, id)` 复合索引；
  本切片不追加 migration，避免未经 staging 的索引变更。
