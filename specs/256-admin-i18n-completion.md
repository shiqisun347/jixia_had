# v2.0 Admin 页面国际化完整覆盖

> 状态：已发布
> 日期：2026-09-08

## 范围

补齐 `/admin` 及其资源、诊断、系统和实验管理工作区的中英文界面。静态 UI 文案、
状态、操作、表单标签、空态、错误提示、抽屉和确认框使用 locale 资源；辩题、姓名、
模型 ID、Prompt、日志请求/响应和其他服务端业务数据保持原文。

## 验收

- 切换为 English 后 admin 导航、页面标题、表格标题、操作菜单、表单、抽屉、确认框和
  空/错/加载状态不再出现中文 UI 文案。
- `zh-CN` 保持现有中文体验；locale 切换不重建比赛或后台请求。
- 通过 Web 定向测试、TypeScript、ESLint、生产构建和 admin 浏览器冒烟。

## 回滚边界

仅回滚 Web 文案、翻译容器和规格文件；不涉及 Core、Jobs、数据库 migration 或比赛状态。

## 发布证据

- 管理后台和实验管理工作区的固定界面文案已接入 locale 边界；标注和两类赛后问卷的
  固定题干、选项、状态与操作也已接入。辩题、姓名、模型 ID、Prompt、文字记录及其他
  服务端业务数据继续保持原文。
- 英文模式下，品牌显示为 `JX-Debate`，副标题显示为
  `Multi-person, multi-agent live voice debate platform`；中文模式保持稷下·争鸣及原副标题。
- 本地验证通过：Web TypeScript、目标 ESLint、`git diff --check`，以及管理、问卷和
  i18n 定向 Vitest `21 files / 45 tests`。完整 standalone 生产构建通过，生成 37 条路由。
- 2026-09-08 生产预检在 migration `0043_browser_device_checks`、`active_matches=0` 下通过。
  一场发现的非终态比赛按用户授权经运行中 Core 的管理员终止 API 和 MatchActor 结束为
  `TERMINATED`，未直接更新比赛数据库记录。
- 完整 standalone 已原子切换并只重启 `jx-web`；回滚目录为
  `/opt/jixia-web-before-256-i18n-20260908020256`。Core live/ready、线上 `/`、`/admin`、
  `/me`、`/me/ai-experience` 和 `/me/postmatch-surveys` 均返回 200，四项服务保持 active。
  独立浏览器会话确认英文首页品牌、导航和受保护 admin/标注/问卷登录边界；启动后五分钟内
  未出现新的 Web warning-or-higher 日志。

## 遗留风险

- 生产浏览器验证未使用管理员或实验参与者凭据，因此已认证的管理表单、具体标注任务和已分配
  问卷内容依赖本地组件测试与构建验证；未对生产数据进行读取或修改。
