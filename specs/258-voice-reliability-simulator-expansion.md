# Spec 258: 语音可靠性模拟器扩展

## 目标

扩展 Spec 251 的 Five-Human Simulator，全面覆盖语音流程中的故障场景：
- ASR 识别失败与重试
- Agent 发言管道故障（LLM/TTS/Playback/Finalization）
- 自由辩论竞态条件
- 暂停/恢复流程
- 超时边界条件

## 验收标准

1. 模拟器不连接数据库、LiveKit 或外部 Provider
2. 覆盖所有语音故障分类（ASR TRANSIENT/AUDIO/CONFIG，Agent 各阶段失败）
3. 验证故障收敛行为（重试 → 暂停/ERROR → 恢复 → 继续）
4. 检测竞态条件（人类举手与 Agent 决策时序）
5. 验证幂等性边界（重复回调、超时重试）

## 故障场景清单

### ASR 故障（test_asr_failure_convergence）

| 错误码 | 分类 | 行为 | 测试验证 |
|--------|------|------|---------|
| asr_empty_audio | AUDIO | 立即重试，2次后暂停 | 验证 Speech 状态 FAILED → 新 Speech 创建 |
| asr_pcm_queue_full | AUDIO | 同上 | 同上 |
| asr_task_failed | TRANSIENT | 同上 | 验证重试预算跨机会隔离 |
| asr_not_configured | CONFIG | 立即暂停，不重试 | 验证直接进入 ERROR/RECOVERY_REQUIRED |

### Agent 故障（test_agent_failure_convergence）

| 阶段 | 故障类型 | 行为 | 测试验证 |
|------|---------|------|---------|
| LLM 生成 | 超时/失败 | 取消 TTS，暂停比赛 | 验证 generation 状态 FAILED |
| TTS 连接 | 失败 | 取消，暂停 | 验证 AgentRuntime 清理 |
| Playback | 失败 | 15秒超时后暂停 | 验证 agent_finalization_timeout |
| Finalization | 事务失败 | 幂等重试，超时暂停 | 验证 50ms  bounded retry |

### 自由辩论竞态（test_free_debate_race_conditions）

| 场景 | 时序 | 预期结果 | 测试验证 |
|------|------|---------|---------|
| 人类先举手，Agent 后决策 | 人类在窗口期内举手 | 人类优先获权 | 验证 selection_locked 为人类 |
| Agent 先决策，人类后举手 | Agent 决策时人类举手 | 人类优先获权 | 同上 |
| 双方同时举手 | 同一 tick 内 | 按服务端顺序 + match_seed | 验证确定性选择 |
| 选择窗口关闭时举手 | hand.window_closed 后 | 拒绝，返回 hand_window_closed | 验证错误码 |
| 跨机会残留举手 | 新机会创建后旧举手仍在 | 过滤旧举手，只保留当前 holder 方 | 验证 queue 过滤 |

### 暂停/恢复流程（test_pause_resume_recovery）

| 场景 | 暂停前状态 | 恢复后状态 | 测试验证 |
|------|-----------|-----------|---------|
| 人类发言中暂停 | HUMAN_SPEAKING | HUMAN_READY_TO_START，保留剩余时间 | 验证 speech_remaining_ms |
| Agent 发言中暂停 | AGENT_SPEAKING | AGENT_PREPARING，重置 speech | 验证 current_speech_id=None |
| 自由辩论选择中暂停 | FREE_SELECTING | FREE_SELECTING，保留队列 | 验证 hand_queue/agent_decisions |
| 主持播报中暂停 | HOST_ANNOUNCING | HOST_ANNOUNCING，保留剩余时间 | 验证 host_audio_remaining_ms |
| 系统恢复后恢复 | RECOVERY_REQUIRED | 按 paused_from_action_state 恢复 | 验证恢复路径正确 |

### 超时边界（test_timeout_boundaries）

| 超时类型 | 触发条件 | 行为 | 测试验证 |
|---------|---------|------|---------|
| human.start_timeout | 60秒未点击开始 | PAUSED/RECOVERY_REQUIRED，HUMAN_START_TIMEOUT | 验证计时器触发 |
| human.wait_timeout | 60秒人类等待无举手 | PAUSED/RECOVERY_REQUIRED，HUMAN_WAIT_TIMEOUT | 验证自由辩论边界 |
| speech.deadline | 发言时间耗尽 | 自动结束发言，推进 | 验证 TIME_LIMIT 结束 |
| selection deadline | 3秒选择窗口关闭 | 按队列/Agent 决策选择 | 验证 window_closed 触发 |
| resume.elapsed | 3秒恢复倒计时结束 | RUNNING，恢复之前状态 | 验证倒计时推进 |

## 实现设计

### 文件结构

```
scripts/qa/
├── five_human_match_simulator.py          # 现有基础
├── voice_reliability_simulator.py         # 新增：语音可靠性模拟器
└── tests/
    └── test_voice_reliability_simulator.py # 新增：测试入口
```

### 核心类设计

```python
@dataclass(frozen=True, slots=True)
class VoiceFaultScenario:
    """单个故障场景定义。"""
    name: str
    fault_type: Literal["ASR_AUDIO", "ASR_TRANSIENT", "ASR_CONFIG",
                         "AGENT_LLM", "AGENT_TTS", "AGENT_PLAYBACK", "AGENT_FINALIZATION",
                         "TIMEOUT_HUMAN_START", "TIMEOUT_HUMAN_WAIT", "TIMEOUT_SPEECH"]
    fault_timing: Literal["BEFORE_START", "DURING_SPEECH", "AT_FINALIZATION", "AFTER_FINALIZATION"]
    recovery_action: Literal["RETRY", "PAUSE", "TERMINATE"] | None

@dataclass(frozen=True, slots=True)
class FreeDebateRaceScenario:
    """自由辩论竞态场景。"""
    name: str
    human_hand_timing: float  # 相对于窗口打开的毫秒偏移
    agent_decision_timing: float  # Agent 决策完成时间
    agent_should_speak: bool
    agent_willingness: float | None
    expected_speaker: Literal["HUMAN", "AGENT"]
    expected_mode: Literal["VOLUNTEER", "FALLBACK", "ALL_AGENT_SKIP_RANDOM"] | None
```

### 测试执行流程

```python
async def run_voice_reliability_suite() -> VoiceReliabilitySummary:
    """执行完整语音可靠性测试套件。"""
    results = []

    # Phase 1: ASR 故障收敛
    results.append(await test_asr_failure_convergence())

    # Phase 2: Agent 故障收敛
    results.append(await test_agent_failure_convergence())

    # Phase 3: 自由辩论竞态
    results.append(await test_free_debate_race_conditions())

    # Phase 4: 暂停/恢复流程
    results.append(await test_pause_resume_recovery())

    # Phase 5: 超时边界
    results.append(await test_timeout_boundaries())

    # Phase 6: 幂等性验证
    results.append(await test_idempotency_boundaries())

    return VoiceReliabilitySummary(results)
```

## 回滚边界

- 仅新增测试代码，不修改生产代码
- 测试失败不阻塞现有 CI
- 发现的新 bug 需单独规格批准修复

## 遗留风险

- 模拟器无法覆盖真实 Provider 网络延迟
- 真实麦克风/扬声器设备问题不在范围内
- 浏览器 WebSocket 重连行为需单独 E2E 测试
