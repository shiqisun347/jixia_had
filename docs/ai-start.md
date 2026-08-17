# 新会话快速启动

GitHub：<https://github.com/shiqisun347/jixia_had>

给新的 AI coding 工具使用本仓库时，按以下顺序读取：

1. `AGENTS.md`：工程边界、修改流程和门禁。
2. `README.md`：v1.0 项目范围、目录和本地命令。
3. `稷下人机自动辩论实验平台需求文档.md`：产品事实来源。
4. `TechDesign-Jixia-Debate-MVP.md`：架构、状态机和失败语义。
5. `docs/research/realtime-voice-spike-2026-07-24.md`：语音性能证据。
6. `agent_docs/`：简化后的产品、技术、代码和测试约定。
7. `specs/README.md`：新增 v1.0 切片的唯一入口。
8. `MEMORY.md`：只查看当前状态和已验证证据。
9. `api.example.md`：只了解远程凭据文件结构，不读取真实 `api.md`。

## 开始任务

先运行：

```bash
git status --short
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
```

然后说明：当前任务范围、会修改的文件、验证命令和未验证风险。超过单文件文档修正时，先创建 `specs/<id>-<slug>.md`，获得批准后再实现。

## 凭据和数据

- 不读取或输出 `api.md`、`.env`、数据库、用户数据、比赛数据、音频和 token。
- 本地只保存代码、迁移、部署配置模板和文档；真实 PostgreSQL/音频/日志在服务器。
- 远程部署资料只允许使用 `docs/deployment.md` 中的脱敏模板；真实凭据通过服务器密钥管理或本地未跟踪文件注入。

## 完成任务

运行与改动匹配的测试，更新 `MEMORY.md`，并在最终说明中区分“本地通过”“正式站通过”和“仍待真人验收”。
