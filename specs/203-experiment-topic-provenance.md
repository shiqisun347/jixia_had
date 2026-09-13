# v2.1 实验辩题来源元数据闭环

状态：已批准需求的缺口修复，已实现待部署  
日期：2026-08-22  
依赖：Spec 201、202，migration 0029；本规格追加 migration 0030

## 1. 问题

PRD 要求实验正式题 C1-C6 和训练题 C7 保存原始来源文本与 CEDAR ID（可取得时）。现有
`topics` 只有题目、正方文本和反方文本；管理 API、Web 和实验发布门禁都不能记录或证明
来源。该缺口在 v2.1 线上资料审计中发现，不能以文档表格代替数据库事实。

## 2. 范围

1. 追加 migration 0030，为 `topics` 增加可空 `source_text` 与 `cedar_id`；不修改 0001-0029。
2. Topic 管理创建、编辑、读取和 OpenAPI 类型支持两个字段。普通房间允许旧 Topic 为空，
   保持历史兼容。
3. 实验批次创建可以引用 Topic，但批次发布前必须验证 21 个场次引用的全部 Topic：
   `source_text` 非空；`cedar_id` 按需求为可取得时填写，不做虚构或强制占位。
4. 实验管理界面清楚展示来源字段并在发布失败时给出可操作错误。
5. 导出/研究数据必须包含 Topic 的 `source_text` 与 `cedar_id`，使赛后数据可追溯。

## 3. 非目标

- 不自动抓取或推断 CEDAR ID。
- 不替用户选择 C7，不写占位来源。
- 不改变普通比赛创建、计时、权限或媒体协议。
- 不修改已存在的 Topic 文本。

## 4. 验收

- 空库升级至 0030，既有 0029 数据前向兼容；`alembic check` 无漂移。
- Topic API/Web 能创建、编辑和读取来源字段。
- 实验批次引用缺少 `source_text` 的 Topic 时不能发布，错误定位到题目来源。
- C1-C6/C7 来源齐全时发布不受阻；`cedar_id=None` 合法。
- 批次导出包含两个来源字段且不泄露密钥或个人映射。
- Core、Web、Jobs、PostgreSQL 集成、contracts、Playwright 与生产构建回归通过。

## 5. 回滚

实现状态：已完成。0030 已在线发布；C1-C6 已经由 CatalogService 写入原始来源文本，管理端
Playwright 4/4 通过。提供的 CEDAR JSON 不含独立 ID，`cedar_id` 保持空值。应用回退时保留
0030 字段；不得在已有实验数据后 downgrade 删除来源信息。正式实验能力继续由
`PAPER_EXPERIMENT_ENABLED=false` 关闭，直到 C7、账号映射和设备验收齐备。
