# Spec 281：LiveKit 容器部署形态的只读发布门禁

- 版本：v2.0
- 状态：本地实现完成，待受保护发布
- 日期：2026-10-13
- 依据：Spec 275 运维审计与 2026-10-13 线上只读检查。

## 问题

`preflight_v2_1.sh` 将 Core、Jobs、Web 和 LiveKit 都按 systemd 单元检查。当前线上
LiveKit 由名为 `jx-livekit` 的受控 Docker 容器运行，`livekit.service` 可能保持 inactive；
预检因此会在服务实际运行时错误阻断发布，降低门禁可信度。

## 范围与验收

1. Core、Jobs、Web 继续要求对应 systemd 单元 active。
2. LiveKit 先接受 `jx-livekit` 或 `livekit.service` 任一 active 的 systemd 单元；若没有，
   只读检查名为 `JX_PREFLIGHT_LIVEKIT_CONTAINER`（默认 `jx-livekit`）的 Docker 容器，
   要求状态为 running 且镜像以 `JX_PREFLIGHT_LIVEKIT_IMAGE_PREFIX`（默认
   `livekit/livekit-server:`）开头。
3. Docker 不存在、容器不存在、状态非 running、名称不匹配或镜像前缀不匹配均 fail-closed；
   不执行 pull、start、stop、restart、inspect 以外的写操作。
4. 新增脚本文本与伪造命令回归测试；不改变数据库、服务状态、认证和媒体协议。

## 非目标与回滚

不自动启动或修复 LiveKit，不改变真实部署编排。回滚仅删除容器识别分支和对应测试，恢复
全量 systemd 检查。

## 本地完成证据

- shell 语法检查通过。
- QA 覆盖 systemd active、Docker running 且镜像匹配、容器停止或镜像不匹配的 fail-closed
  情形。
- 未连接或修改生产 Docker、systemd、数据库或服务。
