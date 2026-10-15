# Spec 287：赛后音频回放任务的有界输入与 ffmpeg 护栏

状态：本地实现完成，待受保护发布（快照逻辑大小、取消清理和输入预算由 Spec 289 修订）

> 后续修订以 [Spec 289](289-bounded-postmatch-audio-cleanup.md) 为准：运行时快照使用
> `octet_length(runtime_snapshot::text)` 的逻辑 UTF-8 字节；回放总输入预算为 256 MiB；
> 增加取消时回收 ffmpeg、可信存储根和清理路径护栏。

## 背景

`POSTMATCH_AUDIO` 原先将比赛的全部已完成语音和运行时快照动作一次性读入
Jobs 进程，再拼接为一个 ffmpeg 命令。异常比赛、历史脏数据或超大音频文件会
同时放大数据库响应、Python 内存、ffmpeg 参数长度和临时磁盘占用，可能阻塞
其他后台任务。

## 范围与验收

- 运行时快照先以 `octet_length(runtime_snapshot::text)` 检查逻辑 UTF-8 字节，超过 4 MiB 返回稳定错误码
  `audio_runtime_snapshot_too_large`；动作最多 512 条。
- 已完成语音查询使用 sentinel `LIMIT 513`，最多接受 512 条；超过上限返回
  `audio_speech_rows_too_many`。查询按 `created_at,id` 稳定排序。
- 回放输入最多 1,024 个源；每个文件最多 16 MiB，累计最多 256 MiB。超限分别
  返回 `audio_sources_too_many`、`audio_input_file_too_large` 或
  `audio_input_total_too_large`。音频路径和输出路径最多 4,096 个字符。
- ffmpeg 命令以参数数组执行，不经过 shell，并通过 `-fs 67108864` 限制临时输出；
  发布前再次检查大小，达到上限的截断文件返回 `audio_output_too_large`，不会标记
  为 READY。
- 正常 4v4 回放的文件顺序、编解码参数、READY/FAILED 任务闭环和过期清理保持不变；
  无效或缺失的历史文件仍按原逻辑跳过，未引入供应商切换或数据库迁移。

## 回滚边界

仅回滚 Jobs 应用代码即可恢复旧的回放构建逻辑，不涉及数据库迁移或数据删除。发布
前后需观察 `POSTMATCH_AUDIO` 成功率、失败错误码、Jobs RSS、临时磁盘和回放时延；
本地限制过严时可在后续规格中调整常量并补充代表性大比赛验证。

## 验证证据

- `apps/jobs/tests/test_audio_worker.py` 覆盖空源、源数量、路径长度、`-fs` 输出参数、
  取消回收、逻辑 JSON 字节和清理路径边界；专项测试 10 项通过。
- `ruff` 与 `pyright` 对 audio worker 通过。真实 PostgreSQL 大比赛和 ffmpeg 长时压力
  应在受保护测试/发布窗口执行；本规格未写入生产数据库、未重启服务。
