# Spec 302 — ASR 协议调用退役代次与迟到 socket 隔离

- 版本：v2.0
- 状态：用户明确批准后实施，标准1,564项通过；2026-10-15已受保护生产发布，实际短语音链通过，完整混合媒体QA失败且继续定位
- 日期：2026-10-14
- 依据：PRD §5.2、§7.1、§10.2–10.4、TechDesign §7.1–7.3、§15.2、Spec 217／274／301
- 事实来源限制：根目录 PRD 为继承的工作树删除；只读 HEAD 中 v2.0 确认稿，不恢复
  或重建文件。当前 TechDesign、已批准规格及实际协议源码共同约束本切片。

## 问题和预期

Spec274 的 receiver close 会取消精确旧 Task、独立关闭旧 Provider，并在迟到生产者
终态后再次幂等关闭。它回收晚建 socket／capture，却不能阻止吞掉 CancelledError
的 socket factory 在 close 后使实际 `FunAsrConnection.recognize_segment` 继续发送
`run-task`、PCM 或 `finish-task`。协议直接 close 的入口也存在同一问题。

关闭前已经进入或等锁的识别调用必须退役；关闭后新进入的识别调用仍可按原设计
建连，正常分段继续复用连接。取消属于生命周期撤销，不额外触发 ASR 业务失败、
重试或暂停。不得依靠永久 `_closed` 标志禁止后续合法识别。

## 单一切片

1. 仅修改 `apps/core/src/jx_core/asr/protocol.py`，在 connection 上维护私有单调
   retirement epoch。每次 public close 在任何 await 前递增；recognize 调用在
   获取连接锁前保存 owner epoch，锁内校验，阻止关闭前已排队的旧调用复活。
2. socket factory 返回后先校验 owner，再交给 `_socket`。旧 factory 的返回值
   只做精确本地、有界 close，不赋值给共享连接、不发送新 run／PCM／finish。
   原 factory 错误仍保留原 `asr_connection_failed` 分类；不把未获得 socket
   写成已回收网络资源。
3. 校验覆盖所有可能跨 close 的挂起边界：socket factory 实际进入前、真实
   sender／recv Task 实际执行前、task-started 返回后、
   chunks 产出后、recv 完成后、用户 callback 返回后和成功 capture 存储前。
   仅在外层 coroutine 检查一次不足以覆盖真实 `create_task` 后的调度间隙。
   当前 Python 3.12 正超时 `wait_for` 直接 inline await，不假造 factory／send／callback
   子 Task；这些入口在同 Task 内 await 前／返回后校验，未来若引入 child 则须入口保护。
   已经进入 transport 的 send 不可撤回，保证 close 之后没有新调用 send／callback。
4. stale owner 用内部取消语义终止旧 recognize，旧 sender／recv 按现有规则清理。
   当前 epoch 的 factory／transport／chunks／callback 普通错误保持原分类与原错误；
   close 后迟到的普通错误先按 owner 退役，不得再触发业务失败／重试。
   异常清理 await 期间还可能发生 close／再次取消；结束后必须重新校验 owner，
   不能因为异常入口时仍当前，就在 close 后重新 store_capture。
   旧调用不得再入 `_captures`，其确切本地 capture 释放；当前 epoch 失败的正常
   capture 仍按现有 `take_capture`／session 持久化路径交付，不改变诊断契约。
5. 不在旧异常／迟到清理路径调用全连接 close 来递增新代次或关闭当前对象；
   `_invalidate_socket` 仍按精确 socket 身份操作，避免旧任务污染替换 socket。
   callback envelope、receiver 生命周期、RTC identity／grants 与 SDK startup 保持不变。

## 不扩展

- 不改模型、供应商、网络协议字段、ASR 分段参数、重试次数、权限、Actor／计时或 migration。
- 不实现全局 retired 数量／字节准入、进程 shutdown 策略、永久不返回的 factory／send／close
  物理回收或无界任务强制终止；不把局部 epoch 作为这些验收的替代。
- 不通过真实 provider／生产故障注入验证，不读 `.env`／`api.md` 或凭据。

## 验收

1. 保存完整当前 Protocol／runtime 的精确源码及 SHA，先运行实际模块 strong red。
   新独立测试不改旧 Spec274 强断言；setup／teardown 和产品 call 分类分开记录。
2. 迟到 factory（直接 close／receiver close）、关闭前等锁调用、挂起 chunks、
   task-started／recv 迟到及 callback 返回后的旧流程，均不再发送新协议请求，
   不新增旧 on_started／on_sentence 入口、不成功返回、不存储旧 capture。
   close 前已进入 callback 的副作用不能撤回，只阻止它返回后的新增操作／成功收尾。
3. 正常同连接分段复用、close 后新识别按需 reconnect、正常 factory 错误和已有
   取消行为保持；新 epoch 的正常 capture／budget 不被旧精确清理污染。
4. 原实际 Module、真实 capture budget 和自有 FakeSocket 跑至少两轮，fixture 回收
   在产品观察／断言之后，所有 Settings／native／network／database guard 为 0，
   无 pending 自有 Task。对应 ASR 回归、Ruff／格式／类型检查及标准门禁真实记录。
5. 未执行的真实 Provider、真人 PCM、180 秒 ASR、五场媒体压力保持待验收。

原 `test_asr_receiver_close_late_socket.py` 的 late 场景除了资源回收强断言，还记录
旧行为为 recognize 成功、`run-task`／`finish-task` 两次发送。本规格新期望与这两项
观察相反；获批实施时允许仅更新 late 场景的原因观测为取消／无发送，继续保留
正常完成控制、精确 socket／capture／reservation／budget／Room 回收强断言。
不得改写已冻结的原测试副本、日志或 manifest，也不得把行为改变归为旧证据原本全绿。

## 回滚与审批

业务回滚仅恢复 `protocol_before.py` 的 SHA
`119337061cd5be91bf9e6598462aa0c2697eb151123f19c1eb275219532ee023`，不回滚
Spec301 runtime `e7857d3094e81ca7df1f79b31978e540dab6bc8e3655261d010e348dabd5f8f9`、
Spec274 receiver close 或 capture writer 已批准修复。副本与测试证据放在
`profile_output/spec302_asr_protocol_retirement/`，无数据库数据变更。

局部退役目的与 Spec274 的已批准清理方向一致，但本草案新增 Protocol 调用撤销／
capture 退役语义；它不自动继承 Spec301 RTC 启动审批。未获明确批准前不写业务代码。

## 实施前强证据

独立新测试 `test_asr_protocol_retirement_epoch.py`、`..._errors.py`、`..._scheduling.py`
对完整精确 Protocol 原模块连续两轮均为 **14 产品红／2 正常控制绿**，1.30／1.40 秒。
每轮16项 setup／teardown 全通过；每例 call 内 finally 独立输出 owned Task 全终态、
pending 空、capture reservation／bytes／workspace 0，不能只凭 phase green 推断 fixture。
两轮源码 SHA 前后均一致；没有业务改动、外网、native、Settings、数据库或真实 Provider。
迟到 factory（direct／receiver）、锁前排队、PCM iterator、迟到 task-started 与
on_sentence 返回后收尾，以及旧 ordinary error／cleanup 跨 close／真实 sender／recv
调度均有直接产品观察；正常复用／close 后 reconnect 和正常 factory 错误／capture
控制通过。首两轮旧 finally 的混合 hygiene 失败与首调度 wait_for 假设错误单列并保留，
不计入最终干净14项产品红。详细边界与 PartA／PartB 位于
`profile_output/spec302_asr_protocol_retirement/BASELINE_REPORT.md`；不能称已修复。

## 2026-10-15 完整流程候选最终验收

已本地完成；最终Receiver a7b3、Protocol8687、domain7f29，标准1,564通过（Node50+QA36+Web231+Core996+Jobs61+CorePG185+JobsPG5），Lint／类型／契约exit0，561冻结文件前后一致。同场真实Room/Manager/独占PG链20发言完成双方四辩、single FINISHED、评委发布、8席投票／重复拒绝及AWARDS；模型／PCM／RTC为合成边界。六场景两轮12场／240合成发言及浏览器248／Storybook94通过，12项既有legacy浏览器跳过保留。

Spec217/274补齐精确Session＋不可变Envelope、callback await返回／错误后撤销、旧录音保护、pending abort后同配置新Provider、旧pause intent同步清理。完整流程fixture10个自有HOST_TTS曾污染后续Jobs，已用真实wrong-UUID→own-UUID对照确认；仅finally按自有UUID＋kind＋own rule白名单清理，不改Jobs业务、原测试或强断言。最终新fixture6eb1，全量fresh190通过及正常精确收尾。

发布包／资格、PartA/B、源码回滚及原始失败见 profile_output/workflow_candidate/release/RELEASE_REPORT.md 与相关证据目录。Web同次构建在服务器独立原生Node进程13关键页面/API＋16静态资源全200，临时进程／目录终态。未关闭真实房间、迁移、重启或切换生产。两个已知WAITING房间仍在；Spec283要求明确取消决定后才能维护发布。Goal保持进行中，真实完整混合媒体／现场及生产QA待下一窗口；不把本候选当作五场／50路LLM／11音色或真人ASR验收。

## 2026-10-15 生产发布终态补充

此前候选／未发布段落保留为当时快照。用户明确批准正常关闭两个旧候场后，
`20261015-asr302-6004cca8`已受保护发布。正常管理员接口保留历史，维护前／停服务后
非终态房间和比赛0；服务器私有备份后升级0046、修复63个终态结束时间并安装已批准
Core/Jobs nofile设置。部署runner及完整预检exit0；独立209文件SHA、Web构建、13路由
和16静态资源核验通过，未登录的3个受保护API均401。300.188秒／11次观察健康、同PID、
无重启、cgroup OOM0及新增warning-or-higher日志0。真实短供应商／RTC／ASR链通过，
完整4v4混合媒体QA仍进行中；未把合成麦克风、短链或五分钟观察当作现场／五场压力。
详见`profile_output/workflow_candidate/release/RELEASE_REPORT.md`和独立生产收据。
