# v2.0 部署

产品版本为v2.0；既有Core/Jobs内部包2.1.0及`preflight_v2_1.sh`文件名保留兼容。本文记录原生部署步骤，不包含真实连接串、密码或API密钥。首次初始化见[复现文档](v2-reproduction.md)，灾备见[备份恢复](v2-backup-and-recovery.md)。

## 当前已核基线

| 项目 | 已发布事实                                                                         |
| ---- | ---------------------------------------------------------------------------------- |
| Core | Spec308/309及此前已发布可靠性修复；173保护安装源                                   |
| Web  | Spec312展示切片，构建`J6YFijOoE7TSV9ijePYSO`                                       |
| 迁移 | 唯一head `0049_agent_human_avatars`                                                |
| 配置 | Spec327原生138行保存/138正常审计；YXH275、新国辩146，新房生效                      |
| 媒体 | LiveKit Python SDK1.1.20；当前模型/接入由非密钥运行基线明确                        |
| 服务 | 单实例Core、Jobs、Next Web、自托管LiveKit、PostgreSQL、OpenResty及已有独立Showcase |

上述是生产发布证据，不是Spec328本次全门禁、备份恢复或干净克隆已经通过的证明。当前整理不重启、不重新发布在线服务，不改比赛、媒体协议或模型。

## 运行配置

新实例参考`resources/v2/runtime.env.example`保留实际ASR/TTS模型、接入与路径，结合根`.env.example`补齐环境变量。所有真实认证值只通过服务器受保护的EnvironmentFile或进程环境提供。Core、Jobs与媒体端需使用同一组经审核配置，不以源码旧默认值或旧Spike模型名覆盖实际版本。

公开资源导入后，`HOST_AUDIO_STORAGE_DIR`指向`resources/v2/assets`的绝对路径；其余上传头像、Agent/比赛音频和导出目录为独立可写数据路径。生产既有目录可能位于仓库外，源同步不得覆盖或删除。模型密文依赖`LLM_KEY_ENCRYPTION_KEY`，它须单独备份；公开资源包不保存生产密文。

LiveKit只传音频，Core WebSocket承载命令/状态。Core持有PostgreSQL advisory lock，只能有一个权威进程。新主机完整 unit、standalone 布置和 LiveKit 私有配置映射见[原生服务安装](v2-native-services.md)，公开模板在 `ops/systemd/templates/`。生产使用有效HTTPS；OpenResty模板见`ops/openresty/`。Certbot钩子只复制授权证书材料、先检查配置再平滑reload，不把私钥带入源码或输出。

## 新实例或后续应用发布

1. 选定明确稳定提交，校验源码、锁、资源/资产manifest；在独立数据库完成空库迁移或兼容恢复及标准门禁。迁移只追加，不能改旧文件。
2. 生产Web只能从仓库脚本构建，并显式设置非敏感Core地址：

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
CORE_API_ORIGIN=http://127.0.0.1:8100 pnpm build
```

3. 记录并保留同次构建的standalone、static、public、build ID和路由/rewrite检查，不能只同步页面文件或混入旧static。备份旧源码/构建/数据库及持久资产。
4. 发布人员执行fresh只读门禁：服务状态、实际导入路径/版本、迁移head、0非终态房间/比赛/连接及终态清理。出现活动时等待，不为发布自动暂停、终止或删除他人比赛。现有`scripts/ops/preflight_v2_1.sh`只读检查；由受保护环境注入数据库配置，不把连接串拼进命令或shell历史。
5. 新实例从 `ops/systemd/templates/*.service.example` 安装完整 unit，按[原生服务安装](v2-native-services.md)核配置、路径和依赖。现有 `ops/systemd/*.service.d` 属于历史安装布局，不原样叠加到新模板。需受控应用发布时按迁移→Core→Jobs→Web恢复，记录实际依赖；`jx-web.service`依赖Core，停止Core可能连停Web，不能仅start Core后声称全部恢复。LiveKit实际unit为`jx-livekit.service`。
6. 核`module.__file__`、173源/依赖/迁移与构建是否匹配，检查Core `/health/live`、`/health/ready`、Web和公网入口、鉴权与静态资源。核Core唯一实例、服务PID/资源限制、日志和数据目录权限。
7. 以正常API为两条赛制各建一个自有不开赛QA房，核全部198 prompt、变量/contract及修订，正常关闭；新房验证不启动比赛、不调用提供方，旧房/历史不回填。
8. 保存本次发布及保护后验、清理回执和未完成项。只有单独授权并实际执行真实语音/比赛/压力检查后，才能填写相应通过。

Spec327是仅配置原生事务更新，没有源码部署或服务重启；不要为复现它执行历史未发布Core候选。普通源码发布与配置更新是不同操作，必须绑定实际计划和回滚证据。

## 测试数据库与探针

`pnpm test:db`要求单独的`TEST_DATABASE_URL`。完整认证/Agent夹具会TRUNCATE业务表；只能使用新建、明确可销毁的实例/数据库，不能复用生产、应用库、私有恢复副本或保留管理员/验收数据的QA库。Agent故障夹具要求库名`jx_debate_qa_agent_faults`与回环地址，可在新自有实例使用该名称及新端口；地址不同不自动构成删除授权。

`pnpm test:auth:browser`也会迁移并清理认证相关表，不应复用上述Agent测试库。调用者先核目标归属；测试入口变量存在或不等于生产地址，不证明目标可清空。

`scripts/ops/provider_chain_probe.py`及相关真实供应商/语音探针会调用模型、写`ExternalCall`或capture，并可能创建临时媒体房间，不是只读预检。不要把它们放入默认资源bootstrap/离线恢复流程；只有授权真实调用时才运行。短探针通过也不证明50路长期流、五场完整媒体比赛或现场设备通过。

## 独立Showcase与回滚

Showcase源位于`apps/showcase`，使用独立Python环境、SQLite和私有认证文件，监听回环地址后由其独立HTTPS虚拟主机暴露。初始化和本地启动见[复现文档](v2-reproduction.md)；不沿用生产口令或投票数据作为公开默认值。

回滚使用同构建旧应用/静态产物和兼容数据库，不做破坏性downgrade、不覆盖新投票/历史。配置逆向仍需新增正常revision与审计，并检查活动/历史变化；未知提交不盲重试。备份和恢复步骤及验证边界见[备份恢复](v2-backup-and-recovery.md)。

同步仅使用明确文件列表和已审核目标，不对`/opt`等宽目录使用`rsync --delete`。保留服务用户可进入的目录权限，源同步不得夹带.env、数据、录音、日志或临时凭据。
