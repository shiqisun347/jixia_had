# Spec 271 — TTS 音色兼容校验与恢复

- 版本：v2.0
- 状态：已实施、测试并发布（2026-10-11）
- 日期：2026-10-11
- 依据：线上比赛 `324550` 恢复后连续收到百炼 TTS `InvalidParameter`。

## 问题

比赛使用了历史冻结音色 `longrongtaolian_v3.1`，而生产模型是
`qwen-audio-3.1-tts-flash`。供应商拒绝该组合；Core 将 TTS 的错误码压成
`llm_provider_failed`，既没有向运维暴露真实边界，也没有给一次可控的恢复机会。

## 范围

1. 将百炼 TTS 的 `InvalidParameter` 映射为稳定的 `tts_parameter_invalid`，不记录供应商
   原始响应。
2. 只对实测无法使用的四个历史错误系统 ID 做显式兼容修正：
   `longrongtaolian_v3.1 → longyingtao_v3.1`、
   `longcanzhuyue_v3.1 → longanlang_v3.1`、
   `longyixuanjun_v3.1 → longanwen_v3.1`、
   `longyingsongliu_v3.1 → longanyuanfei_v3.1`。记录原 ID 和实际 ID；不做基于未知错误的
   自动音色、供应商或模型切换，不改变比赛计时与状态机。冻结快照保留，运行时使用
   明确的兼容映射，实际音色写入原有 TTS 调用/音频记录。
3. 新建/更新资源拒绝这四个错误 ID 并提示替代 ID；保留自定义音色和测试音色兼容。
   现有错误资源用既有 CatalogService 修正，写原有审计，校准状态置 STALE。
4. Web/事件使用可操作错误文案，最终失败仍按既有一次重试后暂停语义处理。

## 验收

- `task-failed/InvalidParameter` 变为 `tts_parameter_invalid`，普通 provider failure 仍保持
  `tts_provider_failed`。
- 冻结了错误音色的 Agent 使用明确映射并能完成文字、TTS 和播放；合法/自定义音色原样使用。
- LLM 文字消费回调中的 TTS 错误保留 TTS 分类，不再压成 LLM 错误；仍只有一次重试。
- 新资源拒绝四个错误系统 ID。
- Core Agent/TTS、MatchActor、规则资源校验、Web 错误文案及类型检查通过；上线后健康检查
  和脱敏 TTS 探针通过，目标比赛恢复到可发言状态。

## 回滚边界

仅回滚 Core/Web 源文件和本规格；不修改已有比赛冻结快照、历史音频、调用诊断或数据库
migration。

## 发布证据

- Core 定向 Agent/TTS 测试 44 项通过；MatchActor 与 Agent 回归 139 项通过；Ruff、Pyright
  通过。Web 文案源文件已更新；本机 `pnpm build` 受仓库当前依赖安装门禁
  `ERR_PNPM_IGNORED_BUILDS` 阻断，因此本次线上修复不依赖 Web bundle。
- 线上备份：`/opt/spec271-20261011184127`。Core 重启后四个生产服务 active，Core
  live/ready 200，公网首页 200。
- 目标比赛恢复后：`RUNNING`，sequence 从 10 推进到 24，多个后续 Agent 的 LLM/TTS
  调用均 `SUCCEEDED`，目标错误音色实际使用 `longyingtao_v3.1`，首音约 224ms；未再次
  进入 `ERROR`。
