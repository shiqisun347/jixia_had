# 远程 API/部署凭据模板

`api.md` 是本机未跟踪的敏感文件，不提交 GitHub。新环境请复制本模板为 `api.md`，再由人工填入值；AI coding 工具只能读取本模板，不能读取真实 `api.md`。

## SSH

```text
host: <server-host>
port: 22
user: <deploy-user>
key_path: <local-protected-key-path>
```

## 服务地址

```text
web_url: https://<domain>
core_internal_url: http://127.0.0.1:<core-port>
livekit_url: https://<livekit-domain>
```

## 服务器目录

```text
source_dir: /opt/<project>
runtime_dir: /opt/<runtime-package>
web_dir: /opt/<web-standalone>
data_dir: /opt/<data>
```

## 外部模型

只记录供应商名称、模型名、Base URL 和变量名；API Key、Workspace ID、数据库 URL 和管理员密码只放服务器 EnvironmentFile，不写入此文件。

## 发布记录

每次发布记录：Git commit、迁移 head、构建标识、回滚目录、四个服务状态、Core live/ready、首页状态和错误日志摘要。不要记录 token、用户数据、比赛内容或音频路径。
