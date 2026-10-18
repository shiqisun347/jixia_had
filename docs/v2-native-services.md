# v2.0 新主机原生服务模板

公开入口是 `ops/systemd/templates/*.service.example`，包含完整 Core、Jobs、Web 和 LiveKit unit。它们是新实例模板，不是生产 unit 的逐字备份。现有主机完整 unit、密钥与证书只进入私有灾备。本文不执行安装、启动或替换在线服务。

## 路径与配置

| 路径                                                                 | 内容与权限                                                                                     |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `/opt/jixia/current`                                                 | 指向选定稳定发布目录；包含锁文件安装产生的 `.venv` 与完整 Web 构建，部署用户管理，服务用户只读 |
| `/opt/node-v24.12.0/bin/node`                                        | Linux Node 24.12.0 可执行文件；安装位置不同则同步修改 Web unit                                 |
| `/var/lib/jixia/runtime`                                             | Core/Jobs 工作目录；保持无 `.env` 文件，避免源码目录的 dotenv 意外参与配置                     |
| `/var/lib/jixia/{resources,avatars,agent-audio,match-audio,exports}` | 私有持久数据；资源根下保留 `host-audio/`，复制公开资产后不覆盖已生成文件                       |
| `/etc/jixia/core.env`、`jobs.env`                                    | 仓库外受保护配置；root 所有、0600，由 systemd 读取后注入进程                                   |
| `/etc/jixia/web.env`                                                 | `NODE_ENV=production`、`HOSTNAME=127.0.0.1`、`PORT=3000`；不能放供应商密钥                     |
| `/etc/jixia/livekit.yaml`、`livekit-keys.yaml`                       | 非密钥媒体配置与私有 API-key 映射分别存放；root 所有、0600                                     |

创建专用系统用户/组 `jixia`；它是示例服务身份，与任何生产登录账号无关。由运维创建上述目录，使服务用户可以进入发布目录并读源码/构件、写持久目录。不要向服务用户授予 Docker 组权限；LiveKit unit 通过 root 管理 Docker，Core/Jobs/Web 仍使用非特权用户。

`uv sync --all-packages --frozen` 在最终发布目录创建 Python 3.12 环境，unit 使用这套 `.venv/bin/python` 和明确源码路径，不依赖登录 shell 或系统 Python。Core 正式 CLI 固定 `workers=1` 并保留权威进程锁，不能改用多 worker 或额外裸 Uvicorn。模板中的本机 PostgreSQL 依赖名为 `postgresql.service`；发行版或明确独立数据库部署不同，应先核实并调整该依赖，不凭模板创建第二个数据库服务。

从 `resources/v2/runtime.env.example` 提取非密钥值，结合根 `.env.example` 在私有配置中填写。Core 必须明确 `CORE_HOST=127.0.0.1`、`CORE_PORT=8100` 与真实 Web HTTPS origin 的 `CORS_ORIGINS`。`DATABASE_URL`、加密主密钥、供应商认证及 RTC 认证只在私有文件中注入；不要把 root `.env` 复制进发布目录。Jobs 与 Core 的数据库、TTS 接入、主持/音频/导出目录必须一致；Jobs 的 `HUMAN_AUDIO_STORAGE_DIR` 应指向同一 `/var/lib/jixia/agent-audio`，该内部变量用于归档音频，不代表换了真人收音协议。

## Web 构建闭合

执行 `CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build`。`CORE_API_ORIGIN` 是构建时 `/api` rewrite 的目标，不是向浏览器发布密钥的环境变量；仅改变启动环境不会改写旧构建。Core CORS origin 则是用户访问 Web 的 HTTPS origin，两者用途不同。

仓库构建脚本将素材复制进 standalone，部署时保留整个 `apps/web/.next/standalone/`，不能只复制 `server.js`：

```text
apps/web/.next/standalone/
├── node_modules/...
└── apps/web/
    ├── server.js
    ├── public/...
    └── .next/
        ├── BUILD_ID
        ├── server/...
        └── static/...
```

同时保留这次构建的原始 `apps/web/.next/{BUILD_ID,static,routes-manifest.json}` 供校验与回滚。Web unit 绑定回环 3000；HTTPS 反向代理转发 Web、`/api` 和 Core WebSocket 的规则参考 `ops/openresty/`，替换示例/历史域名和证书路径。WebSocket 不能被错误地当成普通短 HTTP 请求。

## LiveKit 单机配置

使用已核版本 `livekit/livekit-server:v1.13.5`；由操作人员提前拉取并记录镜像 digest，不用 `latest`，unit 的 `--pull=never` 不会在启动时隐式下载。unit 在 Linux 通过 host network 运行，挂载两份只读私有文件，未引入 Compose、Redis、Ingress 或 Egress。容器只读、无额外 capability；停止与清理依据 root 私有运行目录中的本次 CID，不按名字删除已有容器。

复制 `ops/livekit/livekit.yaml.example` 到 `/etc/jixia/livekit.yaml`。单独生成随机 API key/至少32字符 secret，将映射写入 `livekit-keys.yaml`（顶层是 `key: secret` 的 YAML map，不包裹 `keys:`）。同一对值通过私有 Core 配置的 `LIVEKIT_API_KEY`、`LIVEKIT_API_SECRET` 提供；示例不包含可用默认凭据。配置内 `/etc/livekit-keys.yaml` 是容器挂载路径，主机文件仍在 `/etc/jixia/`。[v1.13.5 配置与密钥文件校验](https://github.com/livekit/livekit/blob/v1.13.5/pkg/config/config.go)

为自己域名设置可信证书与 HTTPS WebSocket 代理，例如 `wss://rtc.example.com` → 本机7880，并把该 WSS origin 设置为 Core 的 `LIVEKIT_URL`。7880 只允许本机代理访问，需在主机防火墙及云安全组禁止外部直连；示例绑定所有接口以支持媒体网络，不能漏掉这条限制。对外开放7881/TCP和50000–60000/UDP，按云 NAT 情况核实 `use_external_ip` 发现的地址。示例未启 TURN，受限企业网络或现场网络仍须另外核验连通性，不能把 HTTP 健康当成 RTC 通过。[官方端口说明](https://docs.livekit.io/transport/self-hosting/ports-firewall/)、[部署与 TLS](https://docs.livekit.io/transport/self-hosting/deployment/)

## 安装与验收边界

先完成[空库初始化](v2-reproduction.md)、依赖/构建/资产校验及[部署前活动门禁](deployment.md)，再由操作人员将四份 `.service.example` 以对应 `.service` 名安装到 `/etc/systemd/system/`。新模板已经给出 Python 路径和限制，**不要原样叠加现有 `ops/systemd/*.service.d`**：那些文件属于历史 `/opt/jixia-debate` 安装布局，会覆盖 `ExecStart` 和素材路径。

在目标 Linux 上先运行 `systemd-analyze verify` 检查四份实际 unit 与依赖，然后经授权 `daemon-reload` 和启用服务。先就绪数据库/LiveKit，再 Core、Jobs、Web；Jobs 模板与 Core 绑定，Web 也依赖 Core，Core 停止/重启可能连带二者，必须核实全部恢复。该新主机依赖约束与当前线上独立 Jobs 的历史 unit 不必相同，不借整理改动线上服务。

下面仅适用于已完成上述路径、私有配置、镜像与数据库准备的自有新主机；不是本次已执行命令：

```bash
for unit in jx-core jx-jobs jx-web jx-livekit; do
  sudo install -m 0644 "ops/systemd/templates/$unit.service.example" "/etc/systemd/system/$unit.service"
done
sudo systemd-analyze verify /etc/systemd/system/jx-core.service \
  /etc/systemd/system/jx-jobs.service /etc/systemd/system/jx-web.service \
  /etc/systemd/system/jx-livekit.service
sudo systemctl daemon-reload
sudo systemctl enable jx-livekit.service jx-core.service jx-jobs.service jx-web.service
sudo systemctl start jx-livekit.service
sudo systemctl start jx-core.service jx-jobs.service jx-web.service
```

本次本地离线验证只检查 systemd 解析、依赖结构和 LiveKit `ports` 配置解析；验证根中的依赖 unit 与执行文件为不执行的占位。无网络的 `ports` 检查仅在验证副本关闭 `use_external_ip` 并指定回环地址，不能证明正式配置的公网 IP/STUN 发现通过。未启动 systemd 服务/LiveKit、未连 RTC，也未替代真实 Linux 新主机启动验收。实际安装后仍须检查用户/目录权限、模块导入、Core live/ready、Web 静态素材、鉴权、WebSocket、两条赛制正常建房冻结以及私有供应商授权。跨进程、网络、媒体与现场设备结果分别记录。
