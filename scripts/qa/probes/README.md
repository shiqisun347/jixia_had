# Provider 研究采集阻塞风险探针

这些探针按已授权的 [Spec 272](../../../specs/272-resilience-simulation-audit.md)
先复现同步采集阻塞与导出漂移，再验证用户已批准实施的
[Spec 300](../../../specs/300-bounded-provider-diagnostic-writes.md)。

探针直接运行实际 Core/Jobs 业务、ORM 和 PostgreSQL 事务，以合成 HTTP、TTS 音频或
ASR capture 边界注入挂起、普通存储错误和取消。成功结果、原供应商错误、审计、
完整业务响应及第二连接的行锁都保留强断言；外部取消只是清理，不代表成功。
ASR 使用实际 manager callback 与 Actor 暂停命令，未接入真人麦克风或 ASR WebSocket。

实施前六文件 40 项为 20 控制通过／20 风险失败。Spec 300 接入后保留权威结果、
原失败、审计、正文/hash 和实际锁断言，故障改注入独立共享 writer；另补过期／回滚
场景，当前 44 项独立显式验收。它们位于默认测试目录之外，不使用 xfail，单独记录
通过数；Core 标准回归另包含 writer／ORM／资源／投影测试。合成边界不能据此宣称
完整真人或生产压力已验收。

## 显式运行

由调用者安全设置 `DATABASE_URL` 与独立的 `TEST_DATABASE_URL`，不要 source 仓库
`.env`。探针只接受 loopback 地址和数据库名 `jx_debate_qa_agent_faults`，两 URL
必须不同；所有 Settings 禁用 dotenv，供应商凭据和内容均为合成。

数据库探针必须串行执行，不开启 pytest-xdist；只有已配置的专属测试库会被迁移到
当前 head。夹具仅清理自己创建的行及临时文件，不连接生产或调用真实供应商。

```bash
PYTHONPATH=. RUN_DATABASE_INTEGRATION=1 uv run --package jx-core pytest \
  -q --tb=short scripts/qa/probes/
uv run ruff check scripts/qa/probes/
uv run ruff format --check scripts/qa/probes/
uv run pyright scripts/qa/probes/
```

| 文件                                        | 实际路径与主要检查                                                                                          |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `test_admin_provider_capture_blocking.py`   | 管理员模型连接测试、实际 SSE adapter、调用结果和审计、request/response 捕获挂起及取消、调用行锁             |
| `test_judge_provider_capture_blocking.py`   | PostmatchService/Judge/实际 SSE、完整业务响应、原失败及重试、任务退出与调用行更新、权威 SQL 失败传播        |
| `test_voice_provider_capture_blocking.py`   | Jobs 主持音频 manifest/claim/worker、有效 100ms Ogg、原错误及重试、Rule/Generation/Asset/Task/Call 五类行锁 |
| `test_asr_capture_command_blocking.py`      | 当前身份 ASR capture callback、已提交 final segment、Match/Speech 行锁、真实 Actor 暂停提交及广播           |
| `test_preview_provider_capture_blocking.py` | Core 音色预览、实际 TTS adapter、校准/审计与预览文件、调用行锁及取消                                        |
| `test_export_capture_freeze.py`             | 实际创建导出／Jobs 研究包读取、晚到 capture、正文 revision 更新、同一截止点内容与哈希稳定性                 |

逐次结果、fixture 修正与未执行边界记录在 Spec 272 和 `MEMORY.md`。

## 同一进程连续5场混合4v4（Spec303追加测试）

`server_repeat_mixed_4v4_probe.py` 在同一Python进程/事件循环/部署SDK中串行调用原完整媒体工具5次，每场仅新建独占loopback QA PG与私有媒体根。每队2个模拟真人席位+2Agent，1/3/5场必须实际普通暂停/自然恢复两类发言，2/4正常；沿用full工具全部权威完赛、8名辩手排名、Jobs归档解码与清理断言。全局任务基线跨轮保持，清理失败停止后轮；单轮产品失败保留，不无限补测至成功。正常配置只在服务器内存使用，OPS_PROBE可写已有诊断；不创建生产用户/房间/比赛，不修改或重启生产服务。

部署源码、SDK、4个依赖helper与ops路径均需匹配当前冻结清单；工具必须在获授权的服务器运行，普通本机单测只验证编排及门禁，不调用实际供应商。每场Manager等Runtime仍新建并关闭，不能把结果说成长驻Manager复用、物理麦克风、完整正式时长或五场并发。运行计划与逐轮实际收据见`profile_output/workflow_candidate/repeated_4v4_20261015/`。
