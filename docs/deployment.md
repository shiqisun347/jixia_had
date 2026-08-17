# v1.0 部署教程

本文只记录部署流程，不包含服务器地址、密码、API Key 或真实数据。生产环境的敏感值由服务器受保护的 EnvironmentFile 提供；本地 `api.md` 永不提交。

## 组件

- PostgreSQL：业务数据、Session、任务队列。
- `jx-core`：FastAPI 单实例，监听内网端口，持有 PostgreSQL advisory lock。
- `jx-jobs`：主持音频、归档、导出、清理和排行榜任务。
- `jx-web`：Next standalone 用户端/管理端。
- LiveKit：自托管音频转发。
- Caddy：HTTPS 和公网反向代理。

## 发布顺序

1. 在独立测试库执行迁移和测试。
2. 构建 Web：`pnpm build`。
3. 备份当前服务目录和 standalone 目录，保留可回滚副本。
4. 追加 migration（不得修改旧 migration）。
5. 同步 Core/Jobs 源码和 Web standalone；排除 `.env`、数据库、音频、日志和缓存。
6. 重启顺序：migration → `jx-core` → `jx-jobs` → `jx-web`。
7. 检查四个 systemd 服务、Core `/health/live`/`/health/ready`、公网首页和关键权限接口。
8. 观察 5 分钟错误日志，无异常后记录发布证据。

## 推荐命令模板

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm lint
pnpm typecheck
pnpm test
pnpm contracts:check
pnpm build
pnpm db:revision:check
```

远程操作应使用已配置的 SSH key 或服务器密钥管理，不把密码拼进 shell 历史。同步时使用明确文件列表或排除规则，禁止对 `/opt` 等宽目录使用 `rsync --delete`。

## 回滚

应用回滚：停止服务，恢复上一份源码/standalone，重启并检查健康状态。数据库只允许按已批准的前向兼容策略处理，不执行破坏性 downgrade。保留发布时间、构建标识、迁移 head、健康检查和日志摘要。

## 本机数据边界

本机工作区不保存生产数据库、比赛记录、用户信息、音频或日志。`.env.example` 只包含变量名和占位说明；真实 EnvironmentFile 只存在服务器。
