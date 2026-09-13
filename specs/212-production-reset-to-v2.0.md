# 212 生产全量清库并回退 v2.0

> 版本：v2.0  
> 状态：已完成  
> 批准依据：用户明确选择生产回退，并在“完整清空生产数据库”与“仅清除业务历史”之间选择 1（2026-08-23）  
> 依赖：200、211

## 1. 范围

- 生产 Core、Jobs、Web 回退至服务器已验证留存的 v2.0 制品；LiveKit 和 OpenResty 不变。
- 完整清空生产 PostgreSQL 业务 schema，包括用户、管理员、Session、房间、比赛、实验、配置、日志和排行榜。
- 使用 v2.0 migration 从空 schema 初始化到 `0028_connection_leases`，不对当前数据库执行 Alembic downgrade。
- 不恢复旧数据库 dump，不保留任何旧账号或历史比赛数据。

## 2. 执行前门禁

- Core、Jobs、Web、LiveKit 当前均 active，Core live/ready 为 200。
- 非终态比赛必须为 0。
- 新建包含当前源码、Web 和 PostgreSQL custom dump 的 root-only 备份，并校验文件非空及摘要。
- 回退制品 Core/Jobs 必须报告 `2.0.0`，migration 最高为 0028，Web standalone 必须存在。

## 3. 执行顺序

1. 停止 Web、Jobs、Core，LiveKit 保持运行。
2. 清空并重建 PostgreSQL `public` schema；立即使用 v2.0 migration 初始化至 0028。
3. 原子切换 v2.0 Core/Jobs 源码和 Web standalone；保留生产受保护环境文件，不输出其内容。
4. 按 Core、Jobs、Web 顺序启动，检查版本、migration、健康、权限和公网页面。
5. 确认用户、比赛和非终态比赛计数均为 0，公开注册可用。

## 4. 管理员初始化

- 全量清库后不存在管理员。
- v2.0 的管理员 CLI 错误地要求 migration 0024，与实际 head 0028 不兼容，因此本次不绕过其校验或写入临时密码。
- 用户通过公开注册创建新账号后，再以受控操作将该账号提升为唯一管理员；不恢复旧管理员，不在文档或日志记录密码。

## 5. 回滚边界

- 若 schema 初始化或应用启动失败，保持入口不可用，使用本次执行前备份恢复 v2.1 源码、Web 和数据库。
- 不使用 Alembic downgrade，不混用 v2.0 应用与 0036 schema 作为最终状态。
- 数据清空只能通过执行前备份恢复；备份不得删除或降低权限。

## 6. 验收

- Core/Jobs/FastAPI 版本为 2.0.0；生产 migration 为 `0028_connection_leases`。
- Core、Jobs、Web、LiveKit active，Core live/ready 和公网首页为 200。
- 用户、房间、比赛、实验数据与运行日志为空；未授权管理接口继续拒绝访问。
- 记录真实执行结果、备份位置、失败恢复能力和管理员待初始化状态。

## 7. 执行结果（2026-08-23）

- 执行前新建 root-only 恢复点 `/opt/jixia-resets/20260822T182454Z`，源码、Web standalone、
  PostgreSQL custom dump 均通过 SHA-256 与 `pg_restore --list` 校验。
- 回退制品来自 v2.1 发布前保留的 v2.0 备份；Core/Jobs 均为 2.0.0，migration 链止于
  `0028_connection_leases`，Web standalone 可启动。
- 已停止 Core/Jobs/Web，重建空 `public` schema 并以 v2.0 migration 初始化至 0028；未执行
  Alembic downgrade，未恢复任何旧 dump。migration 0016 自动创建的默认管理员随后按全量清库
  要求删除，最终用户、Session、房间、比赛和运行日志计数均为 0。
- Core、Jobs、Web、LiveKit 均 active，重启计数归零；Core live/ready、本地 Web、公网首页、
  注册页和排行榜为 200。匿名浏览器无页面或控制台错误；管理接口未授权返回 401，v2.1 赛制
  中心接口返回 404。
- 回退过程中先后发现 v2.0 备份不含空 `data` 目录，以及暂存 `.env` 属主不符合服务账号读取
  要求；均在未恢复任何历史文件、未输出配置内容的前提下修正。最终状态不存在混合版本。
- 清库完成时没有管理员；随后已按用户明确授权通过受控交互创建唯一管理员，密码未写入文档、代码或日志。
