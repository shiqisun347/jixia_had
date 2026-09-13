# Spec 246：Web 静态产物完整性与 `/debate` 加载修复

- Version: v2.1
- Status: Released to production
- Date: 2026-09-01
- Depends on: Spec 231, Spec 244

## Problem

生产 Web 的 `/debate` HTML 曾引用不存在的 Next 静态 chunk，导致浏览器显示
“This page couldn't load”。根因是 standalone HTML 与 `.next/static` 不是同一次
完整构建产物；模型、Core 和 LiveKit 不是该页面加载失败的直接原因。

## Required behavior

- 生产构建在打包前校验构建输出中所有 HTML/manifest 引用的 JS、CSS 静态资源均存在。
- standalone 必须同时携带同一 `.next` 的 server、static 和 public 产物。
- 发布切换使用完整 standalone 归档，不能按页面或目录片段覆盖运行目录。
- `/debate` 页面及其引用的静态资源全部返回成功状态后，发布才算通过。
- 不修改比赛状态机、模型配置、数据库数据或历史比赛快照。

## Verification

- 构建脚本单测覆盖缺失 JS/CSS 引用时失败。
- Web lint、typecheck、测试和正式 build 通过。
- 服务器发布后检查 `/debate` HTML 的所有静态引用返回 200，四个服务保持 active，Core live/ready 通过。

## Rollback

恢复本次发布前的完整 Web standalone 目录并重启 `jx-web`；不回滚数据库，不修改 Core/Jobs。
