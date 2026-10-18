# Showcase 部署与恢复

本目录整理现有伴随站源码，本次整理不安装、重启或替换任何线上服务。以下步骤供新实例或经授权的独立维护使用。

1. 将已审核的 `apps/showcase` 放到独立 release 目录，使用 Python 3.12 创建该服务专用虚拟环境，按 `requirements.txt` 安装锁定运行依赖。
2. 创建独立低权限 `jx-showcase` 用户；准备 `/var/lib/jixia-showcase`（0700）及仓库外的 `/etc/jixia-showcase`。
3. 使用 `ops/init_config.py` 交互创建新实例的管理口令哈希和 Cookie 签名密钥。仅让服务用户读取，私有目录不应能由其他用户进入。已有实例必须保留原凭据，不执行初始化覆盖。
4. 以 `env.example` 为格式创建 0600 的 `/etc/jixia-showcase/environment`，填写实际数据目录、两个私有文件路径、精确 HTTPS origin 和回环端口。
5. 审查 `ops/jx-showcase.service.example`，按实际 release/venv 路径、服务用户及数据目录调整后再使用。模板仍是既有独立服务形态；不会自动创建反向代理、证书或 Core 依赖。
6. 由现有反向代理提供独立 HTTPS 域名并转发到 `127.0.0.1:8120`。保留流式 SSE，避免代理缓冲，设置合理长连接超时；不要将该回环监听直接暴露公网。客户端写请求必须携带与配置 origin 完全相同的 Origin。
7. 检查 `/health`、空库未发布状态、后台登录、草稿不泄漏、发布展示、观众投票和重复提交拒绝。实际现场媒体和声音需另作授权验证。

新构件切换前，保存上一 release、当前只读源/资产清单、环境与凭据的私有备份，以及 SQLite 的一致快照。
SQLite 使用 WAL；运行中不要只复制主 `.sqlite3` 文件。可使用 SQLite backup API 创建独立快照，再在隔离副本做 `PRAGMA integrity_check`。
恢复时停该独立实例后恢复原数据库、原 cookie-key、原口令哈希和所需私有媒体，再以同一 origin 启动并验证历史轮次/选票。
本目录没有自动清库、覆盖生产、证书申请或触发线上更新的脚本；历史一次性发布器留在私有归档。
