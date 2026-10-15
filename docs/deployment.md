# 服务器在线部署教程

本文只记录部署流程，不包含服务器地址、密码、API Key 或真实数据。生产环境的敏感值由服务器受保护的 EnvironmentFile 提供；本地 `api.md` 永不提交。

## 组件

- PostgreSQL：业务数据、Session、任务队列。
- `jx-core`：FastAPI 单实例，监听内网端口，持有 PostgreSQL advisory lock。
- `jx-jobs`：主持音频、归档、导出、清理和排行榜任务。
- `jx-web`：Next standalone 用户端/管理端。
- LiveKit：自托管音频转发。
- 1Panel OpenResty：HTTPS 和公网反向代理（当前服务器实际入口；Caddy 仅为历史设计基线）。

本文早期发布记录曾以 Core/Jobs 2.1.0、数据库 `0041_ai_debate_questionnaires` 为基线，并要求在
追加 `0042_agent_decision_reason` 前清零活动比赛；该历史基线不代表当前 head。当前仓库已追加到
`0046_provider_capture_snapshots`，发布前必须以 `scripts/ops/preflight_v2_1.sh` 动态读取 checkout 的
Alembic head，并在受保护窗口核对生产 head，不能复用旧版硬编码 migration 预检脚本。
Web 只展示 Core 权威状态。
生产部署必须启用 HTTPS，LiveKit 只传输音频，比赛命令继续使用 Core WebSocket。

Spec 300 在 Core/Jobs 各使用一个有界研究采集 writer。`PROVIDER_CAPTURE_ENABLED`
默认 `true`；故障窗口可在同一兼容版本中设为 `false`，停止新增 raw capture 写入，
保留管理员历史投影、冻结导出和包含新引用的内容清理。新调用仍有权威结果，raw 缺口
显示 `capture_not_persisted`。不得退回同步写热路径、移除新表或使用不识别新 capture/
快照引用的旧读取和清理代码。此开关需按下述受保护发布窗口应用，不能绕过非终态门禁。

## 发布顺序

生产保留受控创建的管理员、公开注册用户和历史比赛数据。任何资源恢复只允许按对应规格的
白名单执行，不得恢复旧 Session、凭据或未批准的房间、比赛、实验、日志和排行榜数据；也不得
在命令行传递密码。发布前必须先处理非终态比赛/房间和终态清理门禁，不能用迁移或重启绕过。

面向研究人员的页面和比赛操作见 [线上实验操作手册](./线上实验操作手册.md)。

1. 在独立测试库执行全部追加 migration 和发布门禁；确认 Alembic head 与当前发布版本一致。
2. 构建 Web：在与生产 Web 相同的非敏感配置中显式设置
   `CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build`（或使用生产反向代理前的 Core 地址）。
   构建产物不得使用开发默认端口 `8000`；门禁会校验 rewrite 目标。
3. 查询非终态比赛和房间；任一数量不为零时等待，不暂停、终止或删除真实比赛或房间。
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
存在非终态比赛或房间时，脚本返回非零；发布人员必须停止，不得用脚本自动修复服务器状态。
受保护窗口可额外设置 `JX_PREFLIGHT_REQUIRE_CGROUP_TELEMETRY=1`，让缺少 cgroup 峰值快照或
非零 `oom_kill` 直接阻断；该开关只验证证据，不会替代 staging 压测，也不会自动设置
`MemoryHigh/MemoryMax`。

资源审计中的 `systemd_nofile` 对应 systemd 的 hard 限制，`systemd_nofile_soft` 对应
声明的 soft 限制；`soft_nofile`/`hard_nofile` 来自实际进程。hard=524288 不能证明 soft
已生效。需要安装既有 `LimitNOFILE=524288:524288` drop-in 时，仍按 Spec 283 的受保护
窗口执行；先核对 `LimitNOFILESoft`，再受控重启并核对实际进程限制，不用临时 `prlimit`
绕过发布门禁。

应用发布后、正式实验启用前，使用服务器受保护配置运行一次真实供应商配置短链路探针。
该探针不创建实验批次或保存音频文件，但会新增 `source_kind=OPS_PROBE` 的
`ExternalCall`，并通过现有 capture helper 保存脱敏原始 request/response；因此会写入
数据库，不能当作只读预检。标准输出只包含脱敏延迟与计数。它还会产生一次短 TTS、
ASR、LLM、裁判调用和一个随连接关闭的临时 LiveKit 房间：

```bash
set -a
source /opt/jixia-debate/.env
set +a
PYTHONPATH=/opt/jixia-debate/apps/core/src:/opt/jixia-debate/apps/jobs/src:/opt/jixia-runtime-site \
  /usr/bin/python3 scripts/ops/provider_chain_probe.py
```

探针全部通过只证明服务端供应商配置短链路可用，不替代真人麦克风、设备兼容、并发压力
或音色盲听验收。`--load` 的 LLM 探针仍是 `max_tokens=32` 的健康 JSON 请求；各类探针
分组执行，LiveKit 连接按次串行清理且不传音频，不能据此验收 50 路长期真实流或五场完整
媒体比赛，也不验证 Spec 300 的应用 writer 热路径。失败时保持
`PAPER_EXPERIMENT_ENABLED=false`，不得静默更换模型、音色或供应商。

数据库集成门禁使用独立数据库，不能指向生产 `DATABASE_URL`：

```bash
RUN_DATABASE_INTEGRATION=1 TEST_DATABASE_URL='postgresql+psycopg://.../jx_test' \
  uv run --package jx-core pytest -q apps/core/tests/test_experiment_integration.py
```

全量 `pnpm test:db` 包含真实 Agent 故障与 ORM 回滚夹具，也包含具有清空行为的
`apps/core/tests/test_auth_migration_integration.py::auth_database_url`：每个认证测试
前后会对房间、比赛、用户、模型、审计等表执行 `TRUNCATE ... CASCADE`。
因此只能使用专门新建、独立且可销毁的 PostgreSQL 实例／数据库；不能复用应用库、
生产库或保留 ADMIN、导出与验收证据的既有 QA 库。

Agent 夹具还要求数据库名为 `jx_debate_qa_agent_faults`、主机为 `127.0.0.1` 或
`localhost`。保留同名既有 QA 库时，可在新建的可销毁实例中创建同名库并使用新端口，
显式传入不同的 `DATABASE_URL` 和 `TEST_DATABASE_URL`；地址不同不等于有权清空。
CI 自动创建可销毁库并传入两个独立地址；本地 `db:start` 只启动应用 PostgreSQL，
不会代建此 QA 库。夹具迁移加载禁用 dotenv，不使用生产配置。

`pnpm test:auth:browser` 是另一种具有清空行为的认证测试入口：
`scripts/run-auth-browser.mjs` 会先对 `TEST_DATABASE_URL` 执行 migration，并在测试前后
调用 `scripts/reset-auth-test-db.py`，对 `users`、`sessions`、`audit_logs`、
`room_connections` 和 `user_consents` 执行 `TRUNCATE ... CASCADE`。其现有地址校验仅要求
测试 URL 存在且不等于 `DATABASE_URL`，不会证明数据库可安全清空。只允许使用专门新建、
独立且可销毁的 QA 数据库；不能指向生产、应用库或保留管理员与验收 fixture 的既有 QA 库，
也不能复用上述 `jx_debate_qa_agent_faults`。运行前须由调用者核实地址与数据库归属。

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
