# Spec 276 — 实验批量导出流式写入与容量护栏

- 版本：v2.0
- 状态：本地实现完成，待受保护发布
- 日期：2026-10-13
- 授权：用户要求继续全面发现并修复稳定性风险；本规格只改变 Jobs 实验导出资源边界。
- 依据：Spec 275 遗留项——批量导出将多张全量表和 CSV bytes 同时保留在 Jobs 内存。

## 问题

实验批量导出原先对每个查询使用 `execute` 后转换为完整 `list`，再为每个 CSV 和
JSONL 创建独立 `bytes` 副本，最后才写入 ZIP。批次规模或文本字段增长时，数据库结果、
Python 行对象、CSV 字符串、编码后的 bytes 和压缩写入缓冲会同时驻留，造成 Jobs 进程
内存峰值随批次规模线性叠加。每日排行榜也使用 `.all()` 保留所有比赛参与者行，并为
每个参与者保存完整分数列表，存在相同的规模放大路径。

## 范围与验收

1. `EXPERIMENT_BATCH_EXPORT` 的八个 CSV 数据集和一个 JSONL 数据集使用 SQLAlchemy async stream，逐行写入 ZIP；
   不在生产路径保留完整结果集、CSV bytes 或 JSONL bytes。
2. CSV 保留 UTF-8 BOM、列顺序、公式前缀转义、行计数和 schema 版本；JSONL 保留一行
   一条记录；manifest 的文件 `byte_count` 与 SHA-256 对应写入的未压缩字节。
3. 每文件最多 64 MiB、所有数据集未压缩字节最多 256 MiB、单行最多 4 MiB；超过上限
   使用稳定错误码 `experiment_export_row_too_large`、`experiment_export_file_too_large`
   或 `experiment_export_total_too_large`，临时 ZIP 被清理，任务进入失败状态。
4. 每 256 行主动让出事件循环；每个流式结果在成功、异常和取消路径关闭，避免游标泄漏。
5. 每日排行榜使用 SQL async stream，参与者只保留 `score_sum`/`score_count`，不保留完整
   `scores` 列表；排名和平均分语义与原实现一致。
6. 为正常流式写入、结果关闭、manifest 校验、超限失败和排行榜聚合增加单元测试；不宣称真实大批次
   压测或生产发布已完成。

## 非目标

- 不修改实验导出的 SQL 语义、字段顺序、身份映射策略或数据库 migration。
- 不直接写生产数据库、重启 Jobs 或调整生产 systemd 限额。
- 保留仅供兼容/测试使用的 `_build_batch_files`，其也受行、文件和总量上限约束；生产任务
  不再调用该全量内存 helper。

## 回滚边界

仅回滚流式 ZIP writer、容量常量、回归测试和本规格文档；保留 Spec 274/275 的文件哈希、
终态房间清理和其他任务资源护栏。

## 本地完成证据（2026-10-13）

- `uv run pytest apps/jobs/tests/test_experiment_worker.py apps/jobs/tests/test_leaderboard_worker.py -q`：7 passed。
- `uv run ruff check apps/jobs/src/jx_jobs/experiment_worker.py apps/jobs/tests/test_experiment_worker.py`：通过。
- `uv run ruff check apps/jobs/src/jx_jobs/leaderboard_worker.py apps/jobs/tests/test_leaderboard_worker.py`：通过。
- `uv run pyright apps/jobs/src/jx_jobs/experiment_worker.py apps/jobs/src/jx_jobs/leaderboard_worker.py`：0 errors/warnings。
- 本规格未进行生产写入、重启、真实供应商调用或大批次压力测试。

## 发布前遗留项

- 在受保护窗口执行一个代表性大批次导出，观察 Jobs RSS、临时磁盘、归档大小和任务时延；
  依据结果校准 64/256 MiB 上限，而不是以本地合成数据替代现场证据。
- 发布后保留导出失败码、行数、未压缩字节和 ZIP SHA-256 指标，纳入 Spec 275 的 Jobs
  RSS/swap 告警。
