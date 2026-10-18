# v2.0 复现与首次初始化

本文是操作步骤，不是测试通过记录。产品范围是 v2.0；内部 Core/Jobs 包版本 2.1.0 保持兼容。稳定源、Web 构建及 Spec327 配置的范围见[主 README](../README.md)。当前资源只初始化两条正式赛制，不恢复生产账号、房间、比赛或录音。

## 工具链和依赖

使用 Node 24.12.0、pnpm 11.18.0、Python 3.12、uv 0.11.2 与 PostgreSQL 16。根目录 `.nvmrc`、`.python-version`、`packageManager`、`pnpm-lock.yaml` 和 `uv.lock` 是安装入口；构建/恢复记录还应包含实际 Python patch 和构建工具版本。仓库脚本会使用 `corepack pnpm`，须确认 Corepack 可用。

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
```

生产是原生 systemd 部署；本地 PostgreSQL helper 使用固定镜像和归属标签，不需要增加应用 Docker Compose：

```bash
pnpm db:start
pnpm db:status
```

helper 创建的是开发应用库，不会自动创建资源导入所需的新空库或可销毁的集成测试库。不要以 `db:stop` 或测试重置来清理不属于本次任务的数据库。

## 新空库与管理员

1. 由 PostgreSQL 管理员新建明确归属本次初始化的空库，例如 `jx_v2_restore_20261018`。导入器只允许回环地址与 `jx_v2_...` / `jx_spec328_...` 的明确新库名。
2. 在私有运行环境中配置 `DATABASE_URL` 和 `V2_RESOURCE_DATABASE_URL` 指向同一个新库；不把真实连接串、密码或密钥写进命令参数、Git 或日志。配置示例见根目录 `.env.example`，非密钥语音/路径基线见 `resources/v2/runtime.env.example`，后者不包含完整认证配置。
3. 在同一运行环境执行迁移，必须得到唯一 head `0049_agent_human_avatars`：

```bash
pnpm db:migrate
pnpm db:revision:check
```

迁移会插入一个固定管理员占位行，不能直接再运行 `jx-admin create-admin`。先选择非敏感库名变量，再预检并初始化：

```bash
V2_DB_NAME=jx_v2_restore_20261018
uv run --package jx-core python scripts/ops/bootstrap_v2_admin.py \
  --expected-db-name "$V2_DB_NAME"
uv run --package jx-core python scripts/ops/bootstrap_v2_admin.py \
  --expected-db-name "$V2_DB_NAME" --apply
```

工具只接受尚未使用的迁移占位管理员与完整空库；默认预检不写入。`--apply` 交互读取新用户名、真实姓名，并用 `getpass` 两次读取新密码，不接受密码参数或环境变量。在同一事务内精确替换占位行、调用现有管理员创建服务并写正常审计；失败整体回滚。这不是已有实例的密码重置工具，用户、审计或资源已有使用痕迹会拒绝。

数据库只能有一个活动管理员；不要复制生产用户或重用生产口令。导入器会将资源中的 `@admin` 映射为这个新管理员，不保留生产创建者身份。

## 公开资源

资源入口为 `resources/v2/RESOURCE_BUNDLE.json`，素材根目录为 `resources/v2/assets/`。包包含两条正式规则、198 条完整默认/覆盖模板、必要模型非密钥定义、18 个关联音色、34 个 Agent、阶段/动作/裁判配置和14段已就绪主持素材；默认头像素材随包校验。

资源校验不会调用供应商，也不会生成主持语音：

```bash
uv run --package jx-core python scripts/ops/v2_resources.py validate \
  --input resources/v2/RESOURCE_BUNDLE.json \
  --asset-root resources/v2/assets
```

沿用上面的新库名变量。默认 `import` 只预检；`--expected-db-name` 必须与环境中目标库完全一致：

```bash
uv run --package jx-core python scripts/ops/v2_resources.py import \
  --input resources/v2/RESOURCE_BUNDLE.json \
  --asset-root resources/v2/assets \
  --expected-db-name "$V2_DB_NAME"
```

预检通过后，在受保护环境注入包内 `modelEnvKey` 指定的模型密钥（当前为 `JX_LLM_API_KEY`）及新的 `LLM_KEY_ENCRYPTION_KEY`，再次执行同一命令并加 `--apply`。加密主密钥是32字节随机值的 URL-safe Base64 表示，应安全生成、保管，不在终端打印。导入仅在内存中用现有 AES-GCM 逻辑加密写入；不会向外部模型验证密钥。

工具会检查唯一迁移 head、唯一新管理员、资源与业务历史为空、无活动 Core，并在单事务中插入资源后逐字段核对；不创建 `HOST_TTS` 或其他后台任务。失败或超时不盲重试，先只读检查目标状态；重复导入会因目标不再为空而拒绝。不要用生产库试跑，不使用会重建旧默认模板的历史 `restore_rule_resources.py` 初始化当前版本。

运行时 `HOST_AUDIO_STORAGE_DIR` 必须指向上述素材根目录的绝对路径：其下有 `host-audio/`，数据库保存相对路径。Web 自带默认头像；用户上传头像、Agent 发言录音、比赛录音和导出目录须另外配置为空的可写数据目录，不存入源码树。

模型和语音认证、LiveKit 地址/认证及新实例域名还须由运行环境配置。当前非密钥基线明确使用 `ASR_MODEL=qwen-audio-3.1-asr-flash-streaming` 和 `TTS_MODEL=qwen-audio-3.1-tts-flash`，语音 WebSocket 接入见同一基线文件。接入地址依赖对应工作空间授权，新实例须核对自己的授权；不要因为源码历史默认值不同就静默更换模型。离线复现阶段不要开放真实提供方调用或运行可能消费旧队列的恢复副本。

## 启动与构建

本地开发可用 `pnpm dev` 启动 Web/Core/Jobs；真实音频仍需独立 LiveKit 和有效配置。Core 只能有一个实例，不能用第二个进程绕过 advisory lock。

生产构建显式绑定非敏感 Core origin，统一使用仓库入口：

```bash
CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build
```

保留同一次构建的 standalone、static 和 public；不要混入旧 `.next` 或只复制页面源码。新构建的 build ID 可以与线上旧包不同；精确回滚应保留旧包，而不是假设重新构建逐字节相同。

新 Linux 主机安装使用 [Core/Jobs/Web/LiveKit 完整原生服务模板](v2-native-services.md)，包含发布目录、私有 EnvironmentFile、端口、依赖和素材路径；不能只安装历史 drop-in。

## 本次应保存的验收

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm contracts:check
pnpm test:storybook
pnpm test:browser
CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build
```

`pnpm test:db`、`pnpm test:auth:browser` 会调用清表夹具。它们必须使用另建的可销毁测试库，不能复用刚恢复的历史副本、生产或保留验收证据的 QA 库；`TEST_DATABASE_URL` 不同于应用地址并不能证明数据可删除。Agent 集成夹具另有库名/回环地址约束，见[部署文档](deployment.md)。

还需记录空库迁移、资源/素材哈希、新管理员映射和正常 API 建房验收：两条赛制各创建一个自有不开赛房，核完整198条冻结模板、变量和契约，正常关闭并保留历史。健康200和资源导入不证明真人麦克风、RTC、完整混合赛、现场扬声器、压力或盲听通过；这些验收分别记录。

## 独立 Showcase

`apps/showcase` 是现有独立站点，不调用辩论平台接口，使用自己的 SQLite、口令散列和 Cookie 密钥。为它创建独立 Python 3.12 虚拟环境，并从其精确 `requirements.txt` 安装依赖，避免覆盖 Core 环境。

用所选独立解释器执行 `apps/showcase/ops/init_config.py <仓库外私有配置目录>`，交互输入新口令。工具创建权限受限的 `admin-hash`、`cookie-key`，已有文件会拒绝覆盖；不复用生产认证材料。

```bash
python apps/showcase/app.py \
  --data-dir /path/to/private/showcase-data \
  --password-file /path/to/private/showcase-config/admin-hash \
  --cookie-key-file /path/to/private/showcase-config/cookie-key \
  --origin http://127.0.0.1:8120 --port 8120 --local-http
```

这里的 `python` 必须是独立 Showcase 虚拟环境解释器，示例路径须替换为自有目录。`--local-http` 只用于本地；生产使用实际 HTTPS origin 和独立反向代理。首次启动没有发布现场内容，应在 `/control` 录入并发布；`/` 用作大屏，`/?mode=vote` 用作手机投票。

投票为胜方和一名最佳辩手的单选，每浏览器每轮防重，不保证现实中每人只能投一次。大屏首次启音需要现场点击，音量由现场设备控制；离线模型不参与 Showcase 播放。尚未随公开资产提供的声音素材不能写成已就绪，具体文件以仓库实际 manifest 为准。

当前Web的“观众投票”入口沿用已发布的 `https://showcase.vsagents.online/`。复用到其他域名时，需明确调整展示入口并重新构建；本次整理保持原线上地址。公开Showcase不附带现场问候录音，默认会显示对应播放不可用，投票和其他展示仍可使用。
