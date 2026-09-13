# v2.1 线上匿名验收记录

日期：2026-08-22  
目标：`https://debate.vsagents.online/`  
范围：未登录公开用户路径；不登录、不创建房间、不修改比赛或实验数据。

## 检查项

| 页面 | 结果 | 证据 |
| --- | --- | --- |
| 首页（桌面/移动） | 通过 | `artifacts/dogfood-v2.1-2026-08-22/screenshots/home-mobile.png` |
| 公开大厅 | 通过；未登录按预期进入登录并保留 `/lobby` 回跳 | `lobby-desktop.png` |
| 排行榜 | 通过；人类/Agent 标签、表格和搜索控件可见 | `leaderboard-desktop.png` |
| 使用指南 | 通过；4v4 流程和公开大厅入口可见 | `guide-desktop.png` |
| `/experiments` | 通过；未登录跳转 `/login?return_to=%2Fexperiments` | 浏览器 URL 与登录表单快照 |

## 在线接口

- `/`：HTTP 200。
- `/experiments`：HTTP 200。
- `/api/experiments/capabilities`：`creation_enabled=false`、`history_readable=true`、
  `target_version=2.1.0`。
- 首页 Axe：0 violations；color-contrast 仅因渐变背景无法自动判断，未形成 violation。
- 首页、排行榜、使用指南和实验跳转未观察到浏览器 page errors 或 console errors。

## 边界

本记录只证明公开 Web 页面与关闭状态的用户体验，不证明登录后的参与者、专家或管理员
权限，也不证明真人麦克风、设备恢复、并发压力、50 路长期 LLM 或音色盲听。
