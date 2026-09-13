# Spec 213 生产验证记录

日期：2026-08-23  
范围：赛制规则工作台、规则 Agent 池、阶段 Prompt、AI 裁判和白名单资源恢复。

## 发布与数据

- 发布前确认非终态比赛为 0，并创建 root-only 数据库、源码和 Web 回滚备份。
- 数据库只向前从 `0028_connection_leases` 迁移至 `0037_rule_owned_agent_pools`。
- 白名单恢复导入模型 1、音色 12、辩题 10、规则 10、裁判 1；未导入旧 Agent、用户、
  Session、房间、比赛、实验、日志或排行榜。
- 恢复后保留现有 2 个用户；房间、比赛、批次、排行榜均为 0。规则私有 Agent 共 54。
- 论文实验规则最终为 1 个启用版本、2 个归档旧版本、4 份确认 Prompt 和 9 个启用 Agent。

## 服务与供应商

- Core、Jobs、Web、LiveKit 均 active，重启计数为 0；Core live/ready 通过。
- 只读 preflight 确认包版本 2.1.0、当前源码导入路径、migration 0037 和活动比赛 0。
- TTS、ASR、LLM、AI 裁判和 LiveKit 短链路探针均通过并正常退出。
- 发布后最近十分钟 Core、Jobs、Web 无 warning-or-higher 日志。

## 浏览器

使用独立 `agent-browser` 会话和管理员权限验证：

- 规则目录展示当前论文实验规则及保留的历史规则，论文实验规则为启用状态；
- 规则工作台按阶段展示，阶段设置和 Prompt 位于右侧抽屉；
- 正方立论无历史，反方立论包含历史，两个自由辩论 Prompt 与确认稿一致；
- Agent 页面先选规则，只显示该规则的 9 个 Agent，编辑器保持最小字段范围；
- AI 裁判启用，使用全局模型和完整 Prompt，结果进入排行榜；
- 创建房间页只提供已启用论文规则，明确人类创建后选座、其他席位由不同 Agent 自动填满。

1280×720、1440×900、1920×1080 均无页面级横向溢出或控制台错误。Axe WCAG A/AA 为
0 violations；颜色对比度因渐变背景有 1 项无法自动判定，未报告确定违规。线上未提交测试房间，
以保持生产房间和比赛数据为 0；实际创建流程由本地 Playwright 覆盖。

## 0038 完成性发布

- 数据库从 0037 只向前迁移至 `0038_experiment_rule_snapshot`；发布前后非终态比赛均为 0。
- `experiment_batches.rule_config_snapshot` 已存在，旧 `ck_scheduled_seats_agent_not_first` 约束
  已移除，`alembic check` 无待生成操作。
- 旧 FormatVersion OpenAPI 只保留列表和详情 GET；旧 `/admin/formats` 页面重定向到规则目录。
- 登录态浏览器确认规则目录及论文规则 9 个 Agent；独立匿名浏览器确认登录回跳，无页面错误。
- TTS、ASR、LLM、AI 裁判和 LiveKit 真实短链路探针全部通过。
- 发布时源码 rsync 一度将运行目录权限继承为 0700，Core/Jobs 因 CHDIR 自动重试；Web 尚未切换。
  恢复 0755 后服务正常，再完成 Web 原子切换。随后 5 分钟 10/10 样本四服务 active、Core ready、
  重启计数不再增长、0 warning。
- 发布前数据库、源码和 Web 的 root-only 备份位于
  `/opt/jixia-backup-before-0038-20260823T013152Z`，另保留
  `/opt/jixia-web-before-0038-20260823T013152Z` 即时 Web 回滚副本。
