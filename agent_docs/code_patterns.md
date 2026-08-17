# v1.0 代码模式

## 分层

- Route 只做认证、输入校验、调用 service 和响应映射。
- Service 组织事务和外部依赖；MatchActor 串行执行同一比赛的状态变更。
- SQLAlchemy 访问只在 service/repository 边界；Web 不直接访问数据库。
- OpenAPI/Pydantic 是 HTTP/WS 契约来源，TypeScript 类型由 `packages/contracts` 生成。

## 状态与异步

- 数据库提交后才更新 Actor 并广播。
- 所有回调校验比赛、发言、生成、连接和上下文版本；迟到结果丢弃。
- 后台任务必须可取消、可超时、有界；失败不可静默吞掉。
- React 查询由 TanStack Query 管理；表单临时状态不复制服务端快照。
- 动态列表按 `sequence`/版本应用，旧响应不能覆盖新状态。

## UI

- 复用共享 Button、Toast、ConfirmDialog、Drawer、Avatar 和状态组件。
- 禁用、加载、失败、空态、重试和权限拒绝都要有明确可见状态。
- 核心页面使用稳定布局和内部滚动；动画尊重 `prefers-reduced-motion`。
