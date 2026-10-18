# Spec 309 — 退役发言的正式收尾任务所有权

- 版本：v2.0
- 状态：已批准、实施并于2026-10-17随Spec308受保护发布；完整normal/recovery混合赛通过，安装后完整浏览器QA及Git交付余项保留。
- 日期：2026-10-16
- 基线：批准本规格前，Spec308的91个核心文件逐SHA等于已发布Spec307；批准后仅允许下述单文件例外。

## 问题与证据

Spec308全量PG复验发现既有普通暂停测试偶发失败：旧Speech已经RESET，正式finalizer仍持有同一generation和音频文件；最终generation.status为FINALIZED，却残留error_code=agent_cancelled。

第二轮全PG为285通过、1失败；独立重复同一原测试5次为4通过、1失败，未改测试或运行源码。证据为 profile_output/spec308/CORE_INTEGRATION_V2.xml 和 PAUSE_FINALIZER_ISOLATED_*.txt。该失败不是新头像、Prompt或展示造成的。现有证据只证明generation元数据/任务所有权竞态，不能推断已导致线上阶段推进故障。

因果路径：reset_agent取走旧run后，_cancel_run仍可能先把正在正式收尾的generation标成FAILED/agent_cancelled；稍后原_persist_final写FINALIZED而不清除error_code。相反提交顺序则通过。Spec303已要求退役清理不得抢占正式finalizer的generation和资产。不能删除None断言、改等待顺序或只选通过运行消除失败。

## 唯一允许切片

批准后，仅在 apps/core/src/jx_core/agent/runtime.py 的退役清理与正式收尾任务所有权交接处，准确识别已有正式收尾任务持有的同一run/generation，避免退役清理将其错误标成取消失败。保留立刻媒体fence、旧Speech RESET、迟到业务回调拒绝、所有有界清理及真正未交接/失败任务的取消落库。不能普遍忽略agent_cancelled、清空历史错误，不能让未知任务保持在途。

不改变MatchActor/比赛状态机、赛制和阶段顺序、发言资格、计时起止、暂停/恢复条件、重试次数、模型/供应商/音色协议、ASR、LiveKit、PCM收尾长度、任何历史正文或冻结配置。不追加数据库迁移或扫描修复历史generation。

Spec308原91项冻结清单保留作为原始证据；批准后唯一新增例外为上述runtime.py的已审核最小差异，其余90项及runtime其他逻辑仍须保护。不将例外混同为91项全部未改。

## 已实施的精确交接

- owner由同一run内的不可变generation UUID及仍运行的finalizer Task组成。原600ms尾保护成功、正式文件已提升至storage后，在即将调用原 `_persist_final` 的同一无await窗口登记；不是按 `finalizing`、`natural_complete` 或文件存在普遍跳过取消。
- reset仍立刻fence并清旧producer/TTS/source/decoder/room；只在精确generation对应的持久化owner仍活跃时不抢写 `agent_cancelled`。已结束或generation不匹配的owner不豁免取消。TIME_LIMIT沿用已fence但仍登记的权威收尾，不新增尾静音或等待producer ACK。
- persist异常、超时、取消及尾保护失败/中断，在原错误回调前有界终结同一generation；保留现有行锁、首次FAILED诊断及FINALIZED保护。事务已提交后取消不改成功终态；持久化成功后的业务回调失败不把generation回写FAILED。不清任何历史error。
- reset只删除临时spool，已rename的正式storage仍归旧finalizer；迟到close使用捕获的旧source与旧run，不读取或移除替换run。文件提升异常纳入同一原失败回调/有界清理。数据库不可用时记录既有诊断，不伪称终态写入成功。

原runtime SHA为 `2b70044fd0556b1caf4d93d40e766900955469e67e416ff231eff2ec7f670914`；当前候选为 `f174e56773bec848cf177a30338c84ca20e12d50fb193b8fd469cba1a2419abb`。90项保护实核相等；原源码、候选源码、完整diff及SHA收据在 `profile_output/spec308/core-finalizer-audit/`，以最终安装候选重新核对。

## 验收

- 原held-finalizer测试不删断言，在真实独立PG中强制“取消先提交”和“正式收尾先提交”两种交错均通过。
- 同时核旧Speech仍RESET、新Speech可正常完成，旧callback不能推进或覆盖新发言，generation/asset/file确由原finalizer收尾；非正式收尾的取消和真正失败保持原状态及脱敏诊断。
- 原暂停/重置/迟到结果/尾音/清理失败专项及全量Core/Jobs PG、Web标准门禁全部复验；不能用重复运行中的通过覆盖已知失败。
- 完成Spec308要求的原时长两场每队2真人+2Agent真实TTS/ASR比赛、逐Agent末句接收及归档清理，和安装后复验。

当前本地证据：新增首批8项在原源码为5失败/3通过，保留 `UNIT_BEFORE.xml`；扩展至12项后，连同原尾音、迟到、清理和voice专项共157通过（`UNIT_AFTER.xml`）。Core非PG单次全量为1574通过、19个localhost bind EPERM；只重跑这19项所属的两个原文件，在工具批准监听本机临时端口后31项全部通过（`CORE_UNIT_AFTER.*`、`LOCAL_SOCKET_AFTER.*`），不能称单次1593全绿。Jobs非PG61通过；Ruff与显式虚拟环境解释器的严格Pyright零错误。真实PG两种强制交错及四种owner退出故障、完整比赛和安装后验收仍以各自后续收据为准，不用这些局部测试替代。

## 发布及回滚

2026-10-16 后续根进程真实验证：同一候选runtime SHA `f174e56773bec848cf177a30338c84ca20e12d50fb193b8fd469cba1a2419abb`，取消先提交及正式收尾先提交两种强制交错均通过；包含四种owner退出的独立PG专项19通过，全量Core PG291通过。标准单次Core非PG1593、Jobs非PG61、Web327、脚本QA390通过；全Storybook96、全浏览器288通过（12项显式跳过未计通过）。证据为 `profile_output/spec308/SPEC309_PG_RESULTS.json`、`CORE_INTEGRATION_V3.xml`、`standard-tests-final.txt`、`FULL_BROWSER_FINAL.txt`。以上不撤销早期失败记录；完整混合赛、安装和安装后验收仍待执行。

2026-10-17 同一runtime SHA的新完整后端门禁Core1594/PG329、Jobs61/PG5、QA426通过且无skip，254候选源及193测试源前后相同（qualification/backend-03）。原时长正常混合赛已自然FINISHED、双DOM一致，但额外三条接收波形差异导致旧QA关闭抛错、恢复场未执行；不将其宣称Spec309完整流程验收通过。接收器验收方法仅按Spec308 §8分列规范逐Agent覆盖与额外全轨诊断，原matcher和产品不变，具体新两场与发布仍需实际证据。

与Spec308同一受保护发布窗口上线前，必须取得本规格的明确批准和实际绿证据。没有批准时保持生产当前版本，继续完成Spec308独立工作，但不修改runtime、不以失败门禁宣称已发布。

## 2026-10-17 实际发布记录

用户已明确批准本规格；原异常与红绿证据保留。最终同源Core unit1594/PG331、Jobs61/PG5、QA446共2437项单次通过且无fail/error/skip。normal/recovery原时长每队2真人＋2Agent均FINISHED，恢复场实际执行暂停/恢复、重置/恢复及占位继续，六项owner/lease均0，无pending或SDK unraisable。

已安装runtime SHA `f174e56773bec848cf177a30338c84ca20e12d50fb193b8fd469cba1a2419abb`，原保护列表其余90项相同；来源173项核验，Web hY-mZF-_0jzWG3V9sQjgt、schema0049、三服务active/0重启及服务/公网五分钟观察通过。接收轨3个局部尾NCC失败、1条短ASR not_evaluable及旧失败按Spec308分列，不由本所有权修复推论全部媒体完整。

首轮安装后浏览器总体failed，精确Session撤销已由服务端独立确认；QA工具关闭等待互锁已通过真实UDS红控定位，同深度v2受控修复不冒称生产完整复验通过。完整浏览器QA及Git交付尚未完成。用户最新展示/Prompt要求已另写Spec310待批准；该规格冻结本runtime及所有Core/Jobs，不新增本修复范围。

回滚仅恢复本切片单文件差异，沿用Spec308的idle/CAS、历史与兼容头像边界；不restore数据库、不修改历史、不强制结束房间。恢复前必须无非终态房间/比赛、活动owner或lease，并按已安装SHA比较后替换runtime；Core/Jobs停服窗口与健康检查复用原发布流程。撤销本修复会重新暴露已确认的原所有权竞态，不能将回滚版本标作已消除该问题。头像0049与资源配置遵循Spec308独立兼容边界，不能以恢复runtime为由降schema或覆盖历史。
