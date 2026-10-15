# 259 生产回撤至论文提交快照

> 版本：v2.0 运维规格
> 状态：已完成
> 批准依据：2026-10-01 用户明确要求切换至 `chore: snapshot paper submission version`。

## 范围

唯一目标提交 `b3ef871dbb4c52f8b2de076c6b5971ac5762dc3f`。从 Git archive 独立导出该提交，按锁文件安装 Web 依赖，通过仓库 `pnpm build` 生成完整 standalone。Core/Jobs 使用该提交的完整源码包，首页插画与全部静态文件随同一 Web 制品切换；不编写新业务代码，不切换 Git 分支，不同步当前工作区未提交代码。

## 验收

- 非终态比赛数量为零，数据库 head 与目标 migration head 都为 `0043_browser_device_checks`。
- 目标源码逐文件 SHA-256 与生产 Core/Jobs 一致，运行导入路径指向切换后的目录。
- Web BUILD_ID、server.js、首页 v5 透明插画与构建产物一致；HTML 引用的静态文件可访问。
- 四服务 active，Core live/ready 和公网首页、辩论页为 200，未登录 API 为 401。
- 公网 HTML 与首页插画请求验证通过；当前浏览器自动化连接不可用，因此未将浏览器截图写成验收证据。真实供应商/真人验收仍按既有边界保留。

## 回滚边界

停止 Core/Jobs/Web 后分别将源码包和完整 Web 运行目录原子切换；切换前保留当前源码与 Web 制品，并记录恢复路径。数据库、migration、历史比赛、模型资源、受保护配置、LiveKit 和 OpenResty 保持现状，不执行 downgrade。新进程健康失败时立即恢复切换前源码和 Web。受保护配置不进入本地、归档制品、文档或日志。
