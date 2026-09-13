# 服务器在线部署教程

本文只记录部署流程，不包含服务器地址、密码、API Key 或真实数据。生产环境的敏感值由服务器受保护的 EnvironmentFile 提供；本地 `api.md` 永不提交。

## 组件

- PostgreSQL：业务数据、Session、任务队列。
- `jx-core`：FastAPI 单实例，监听内网端口，持有 PostgreSQL advisory lock。
- `jx-jobs`：主持音频、归档、导出、清理和排行榜任务。
- `jx-web`：Next standalone 用户端/管理端。
- LiveKit：自托管音频转发。
- 1Panel OpenResty：HTTPS 和公网反向代理（当前服务器实际入口；Caddy 仅为历史设计基线）。

生产环境当前 Core/Jobs 为 2.1.0，数据库 head 为 `0041_ai_debate_questionnaires`；本次 Spec 232
追加 `0042_agent_decision_reason` 前必须完成活动比赛清零和独立库验证。
Web 只展示 Core 权威状态。
生产部署必须启用 HTTPS，LiveKit 只传输音频，比赛命令继续使用 Core WebSocket。

## 发布顺序

当前生产保留 Spec 212 后受控创建的管理员和公开注册用户，但没有历史房间、比赛或实验批次。
Spec 213 只从受保护备份恢复批准的模型、音色、辩题、规则和裁判资源；不得恢复旧用户、
Session、Agent、房间、比赛、实验、日志或排行榜，也不得在命令行传递密码。

面向研究人员的页面和比赛操作见 [线上实验操作手册](./线上实验操作手册.md)。

1. 在独立测试库执行全部追加 migration 和发布门禁；确认 Alembic head 与当前发布版本一致。
2. 构建 Web：在与生产 Web 相同的非敏感配置中显式设置
   `CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build`（或使用生产反向代理前的 Core 地址）。
   构建产物不得使用开发默认端口 `8000`；门禁会校验 rewrite 目标。
3. 查询非终态比赛；数量不为零时等待，不暂停、终止或删除真实比赛。
4. 备份当前服务目录和 standalone 目录，保留可回滚副本。
5. 对生产库做可恢复备份，再执行
   `uv run --package jx-core alembic upgrade head` 追加 migration；不得修改旧 migration。
6. 同步 Core/Jobs 源码和 Web standalone；排除 `.env`、数据库、音频、日志和缓存。Web 必须从同一次
   `pnpm build` 产出的完整 `.next/standalone` 和 `.next/static` 原子切换，禁止只同步单个页面源码或
   复用旧 standalone。构建脚本会拒绝缺少 `/me/ai-experience`、`/me/postmatch-surveys` 路由或个人中心入口的
   不完整产物。
7. 重启顺序：migration → `jx-core` → `jx-jobs` → `jx-web`。
8. 核对实际模块 `__file__`、包版本和源码哈希，再检查四个 systemd
   服务、Core `/health/live`、`/health/ready`、公网首页和关键权限接口。Web 切换后必须逐一请求两个问卷
   页面并检查未登录时为登录边界（不是 404），同时检查构建产物包含个人中心的两个入口；页面源码、静态
   资源和服务进程必须来自同一构建标识。
9. 在隔离数据库恢复 Spec 212 前的受保护 dump，先运行
   `scripts/ops/restore_rule_resources.py` dry-run，再向生产执行白名单导入；核对排除项计数为 0。
10. 运行 `scripts/ops/ensure_paper_experiment_rule.py` 规范化论文实验规则，等待 Jobs 生成主持音频，
    人工复核后启用；不得绕过音频完整性校验。
11. 以管理员会话检查规则目录、规则工作台、阶段抽屉、规则范围 Agent 编辑和房间创建；确认旧论文
    规则已归档且不出现在默认列表。
12. 运行供应商短链路探针，并观察至少 5 分钟 warning-or-higher 日志；无异常后记录发布证据。

当前 OpenResty 容器从 1Panel 挂载目录读取证书。Certbot 续期钩子使用
`ops/openresty/certbot-deploy-hook.sh`：只在续期成功后复制证书，先执行
`openresty -t`，再发送 HUP 平滑加载；私钥不进入仓库或命令输出。

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
uv run --package jx-core alembic upgrade head
```

在服务器发布前，先以与 Core 相同的非敏感运行上下文执行只读预检。脚本只读取 systemd
状态、Core HTTP 状态和 PostgreSQL revision/计数，不读取或输出 EnvironmentFile 内容，也
不会迁移、重启、暂停或终止比赛：

```bash
PATH="/path/to/uv/bin:$PATH" \
DATABASE_URL='postgresql+psycopg://由受保护环境注入' \
JX_PREFLIGHT_CORE_URL='http://127.0.0.1:8100' \
JX_PREFLIGHT_PYTHON='/usr/bin/python3' \
bash scripts/ops/preflight_v2_1.sh
```

实际运行时应通过受保护的会话或包装命令注入 `DATABASE_URL`，不要把真实连接串写入 shell
历史。任一服务非 active、版本/导入路径不匹配、健康检查失败、migration 不是当前发布 head，或
存在非终态比赛时，脚本返回非零；发布人员必须停止，不得用脚本自动修复服务器状态。

应用发布后、正式实验启用前，使用服务器受保护配置运行一次真实供应商链路探针。该探针
不写数据库、不创建实验批次，不保存音频、Prompt 或模型正文，只输出脱敏延迟与计数；
但会产生一次短 TTS、ASR、LLM、裁判调用和一个随连接关闭的临时 LiveKit 房间：

```bash
set -a
source /opt/jixia-debate/.env
set +a
PYTHONPATH=/opt/jixia-debate/apps/core/src:/opt/jixia-debate/apps/jobs/src:/opt/jixia-runtime-site \
  /usr/bin/python3 scripts/ops/provider_chain_probe.py
```

探针全部通过只证明服务端供应商链路可用，不替代真人麦克风、设备兼容、并发压力或音色
盲听验收。失败时保持 `PAPER_EXPERIMENT_ENABLED=false`，不得静默更换模型、音色或供应商。

数据库集成门禁使用独立数据库，不能指向生产 `DATABASE_URL`：

```bash
RUN_DATABASE_INTEGRATION=1 TEST_DATABASE_URL='postgresql+psycopg://.../jx_test' \
  uv run --package jx-core pytest -q apps/core/tests/test_experiment_integration.py
```

生产环境还必须提供现有 Core/Jobs/Web/LiveKit/OpenResty 所需配置。当前生产
`PAPER_EXPERIMENT_ENABLED=true`；暂停正式实验能力时改回 `false` 并重启 Core/Jobs。
密钥只放入服务器受保护的 EnvironmentFile；不要在命令行、Git、构建产物或
文档中填写真实值。

## 实验发布验收

- 未登录用户只能看到公开比赛记录；不得取得队内举手或 Agent 决策状态。
- 参与者从“实验”进入自己的预约并自动落入固定席位；非排表用户只能观战。
- 管理员能看到 18 场正式赛和 3 场训练赛，训练赛不进入正式排行榜和研究统计。
- 停用批次前会拒绝活动 attempt；停用后预约入口消失，历史管理和导出仍可读。
- AI 裁判、ASR、LLM、TTS 和 LiveKit 必须用服务器真实凭据各完成一次链路验证；失败时
  按 PRD 暂停或重试，不得静默更换模型或供应商。

远程操作应使用已配置的 SSH key 或服务器密钥管理，不把密码拼进 shell 历史。同步时使用明确文件列表或排除规则，禁止对 `/opt` 等宽目录使用 `rsync --delete`。
同步 staging 内容到 `/opt/jixia-debate/` 时不得让 rsync 覆盖运行目录本身的所有者或权限；同步后必须
确认该目录为服务用户可进入的 0755，再重启 Core/Jobs。

## 回滚

应用回滚：先关闭实验新建能力，停止服务，恢复上一份源码/standalone，重启并检查健康
状态。0029–0040 保持在数据库中，由旧应用忽略可兼容的新增表和字段；不得执行破坏性 downgrade，
不得删除或重新启用已停用批次。保留发布时间、构建标识、迁移 head、健康检查和日志摘要。

## 本机数据边界

本机工作区不保存生产数据库、比赛记录、用户信息、音频或日志。`.env.example` 只包含变量名和占位说明；真实 EnvironmentFile 只存在服务器。
