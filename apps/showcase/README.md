# 稷下赛事展示伴随站（v2.0）

这是现有独立 Showcase 的公开可复现源，提供开场展示、评委点评、观众投票和结果展示。
它不导入 Core、连接平台数据库或推进比赛，不是新增比赛服务。
源码来源、排除的私有数据和必要变更见 [SOURCE_PROVENANCE.json](./SOURCE_PROVENANCE.json)。

- `/`：大屏；`F` 或页面按钮切换全屏。
- `/?mode=vote`：手机投票，每轮选择获胜方和一位最佳辩手。
- `/control`：独立管理口令登录，编辑草稿并发布展示。
- `/health`：进程健康检查。

投票沿用同一浏览器签名 Cookie＋每轮唯一约束，并非实名防刷；换设备或清除 Cookie 仍可能再次投票。
投票仅在已发布、允许投票且存在有效大屏连接时开放。后台草稿、评委结果和投票结果继续按原展示阶段隔离。

## 安装与本地运行

从仓库根目录执行，使用 Python 3.12 和现有 uv：

```bash
uv venv --python 3.12 apps/showcase/.venv
uv pip sync --python apps/showcase/.venv/bin/python apps/showcase/requirements.txt
apps/showcase/.venv/bin/python apps/showcase/ops/init_config.py /tmp/jx-showcase-private
```

最后一步交互输入自己的新管理口令，只创建 0600 的 `admin-hash` 和随机 `cookie-key`，不输出口令，已有文件不会覆盖。
正式安装应改用仓库外持久私有目录，确保服务用户可读；不要在仓库、shell 参数或日志中写入口令。

```bash
export SHOWCASE_DATA_DIR=/tmp/jx-showcase-data
export SHOWCASE_PASSWORD_FILE=/tmp/jx-showcase-private/admin-hash
export SHOWCASE_COOKIE_KEY_FILE=/tmp/jx-showcase-private/cookie-key
export SHOWCASE_ORIGIN=http://127.0.0.1:8120
export SHOWCASE_PORT=8120
apps/showcase/.venv/bin/python apps/showcase/app.py --local-http
```

`--local-http` 只用于回环地址开发。生产默认使用 Secure、HttpOnly、SameSite=Strict Cookie；配置独立 HTTPS origin，反向代理到回环端口。
CLI 参数可以覆盖同名环境配置；`env.example` 只有示例路径和域名，不含凭据。
首次运行建立空 SQLite 和八个通用席位，不导入比赛、用户、选票或会话。

## 验证

```bash
uv pip sync --python apps/showcase/.venv/bin/python apps/showcase/requirements-dev.txt
PYTHONPATH=apps/showcase apps/showcase/.venv/bin/python -m unittest discover -s apps/showcase/tests -v
pnpm install --frozen-lockfile
pnpm --filter @jx/web exec playwright install chromium
node apps/showcase/tests/run-browser.mjs
```

单测沿用原投票、版本冲突、Cookie、权限、并发、音频命令及恢复用例。私有照片和录音由临时预设图片及合成正弦波替代。
浏览器测试只使用自己启动的随机回环端口实例，验证原后台唤醒/音频回执与公开页面；不访问线上或调用语音供应商。
合成音频验证不等于真实语音质量、现场播放设备或完整人机比赛验收。

## 私有数据与恢复

公开副本不带现场身份映射、上传头像、问候语音、SQLite、选票、会话、cookie-key 或运行日志。
原预设头像、品牌和奖项插图保留；`static/identities.js` 的现场映射为空，使用席位所选通用头像。
没有 `static/assets/greetings/manifest.json` 和对应 WAV 时，问候播放正常显示不可用，不阻止其他展示或投票。
获授权的现场媒体可另放入被 Git 忽略的 `static/assets/greetings/`，沿用原四 Agent ID 和本地 WAV 元数据协议；不要把私人媒体放入公共提交。

灾备需同时保留 SQLite 一致快照、外部 `admin-hash`、`cookie-key` 和私有媒体。保持签名密钥不变才能保持匿名投票 Cookie 身份。
不要用空实例初始化覆盖现有数据，不要把生产数据复制到公开配置。
部署和恢复见 [DEPLOYMENT.md](./DEPLOYMENT.md)。
