# 217 Unified asynchronous callback identity envelope

## 2026-10-14 ASR session ownership acceptance supplement

Status: approved identity invariant, scoped before implementation. Three strong
regressions on exact Runtime `e7857d3094e81ca7df1f79b31978e540dab6bc8e3655261d010e348dabd5f8f9`
and Protocol `8687d144c054a04e2a5393f005d45c98402ed3ab12f8bb63b460e968c637948f`
prove that a cancelled old real `AsrSpeechSession` can publish its segment/final
under the replacement envelope, close or unlink the replacement recording, or
publish an interim as a newer attempt of the same speech. This completes the
already approved immutable callback rule; it adds no product transition.

- At session creation capture the effective immutable envelope and exact session
  owner. Bind interim, segment start/failure/final and provider-capture callbacks
  to that owner. Speech UUID or envelope equality alone is insufficient when the
  same speech is replaced by another session.
- Before and after asynchronous callback boundaries, require the exact current
  session before reading or mutating receiver speech, pause/checkpoint or recording
  fields. Success, error and finally paths of an old task must not publish a new
  speech, finalize it, clear its pause flags, rename or delete its recording.
- Release a stale capture's own reservation using existing cleanup semantics;
  do not publish it as current or close another capture. Current-session callback,
  normal finalization, pause checkpoint and segment numbering remain unchanged.
- No model, retries, RTC transport, public DTO, permissions, timers, migration or
  Actor transition changes. Pending-provider recovery is separately scoped in
  approved Spec 274 below.

Acceptance: retain the three recorded product failures and exact before source;
run those same assertions against the candidate, add normal current/pause controls,
then run related ASR/Manager tests, Ruff and Pyright and the same-match complete
workflow. Evidence belongs in `profile_output/spec217_asr_session_owner/`.
Rollback restores only this exact Runtime slice, retaining Protocol 302 and the
already approved RTC startup fix. No historical data or migration rollback.

- Version: v2.0
- Status: Implemented and published in the protected 2026-10-15 release; real full mixed-media QA remains in progress
- Approved by: user approval in current task
- Date: 2026-08-23
- Depends on: Specs 215 and 216
- Implementation inventory: `docs/design/callback-envelope-inventory.md`

## 1. Problem

Agent, ASR and media callbacks currently validate different subsets of identity.
Some callbacks carry only `match_id` and `speech_id`; others carry a generation or
decision round and rely on Core to recover the remaining context from mutable
state. That makes late callbacks difficult to classify consistently and leaves a
protocol gap in Spec 216 §5.1.

## 2. Goal and non-goals

Every asynchronous callback that can mutate match state or append a provider
result carries one immutable identity envelope. Core validates the envelope
before any Actor command, event publication or business-row mutation. A mismatch
is persisted as a stale diagnostic and has no match side effect.

This does not change stage timing, provider selection, LiveKit transport, public
transcript schemas, or the formal 4v4 selection rules. It does not expose private
identifiers to public HTTP/WebSocket projections.

## 3. Envelope

```json
{
  "match_id": "uuid",
  "speech_id": "uuid|null",
  "attempt_no": 1,
  "generation_id": "uuid|null",
  "connection_epoch": null,
  "context_version": 12,
  "opportunity_id": "uuid|null",
  "opportunity_generation": null
}
```

`speech_id`, `generation_id`, `connection_epoch`, `opportunity_id`, and
`opportunity_generation` are nullable only when that resource does not apply to
the callback class. The envelope itself is always present. `attempt_no` is a
positive integer, `context_version` is a non-negative integer, and present epoch
or generation values are positive integers. The producer captures the values at
task creation; it must never rebuild them from current mutable state when the
callback completes.

| Callback class | speech | generation | connection epoch | opportunity |
|---|---:|---:|---:|---:|
| Agent free-debate decision | source speech when present | null | null | required |
| Agent LLM/TTS/playback | required after speech allocation | required | null | required in free debate |
| Human ASR segment/final | required | null | required | required in free debate |
| Fixed-stage Agent/Human callback | required where speech exists | per media kind | per media kind | null |

Before an Agent speech row exists, generation callbacks may carry a null
`speech_id`; once playback allocation creates the speech, all subsequent
callbacks for that task use the bound speech identity.

## 4. Callback classes

The envelope is required for:

- Agent free-debate decisions, LLM text generation, TTS completion/retry,
  playback start/progress/finish, finalization and failure.
- ASR segment start, interim, final, provider capture, failure and speech
  finalization.
- Human media connection/presence transitions that can pause or resume ASR.

Non-mutating logs may reference the envelope but are not themselves callbacks.

## 5. Validation and stale handling

Core validates in this order:

1. `match_id` exists and is the target Actor.
2. `context_version` and `opportunity_generation` match the authoritative
   opportunity/snapshot when those resources are present.
3. `speech_id`, `generation_id`, and `connection_epoch` match the currently
   authoritative identity for the callback class.
4. `attempt_no` is the current or expected retry attempt.

Any mismatch is recorded with `stale=true`, a stable reason code, callback
class, and the complete redacted envelope. It must not publish subtitles,
advance timers, finalize speech, allocate a speaker, or change an Actor state.
Duplicate callbacks with the same envelope and idempotency key are harmless.

## 6. Compatibility rollout

Migration is additive and does not rewrite historical events.

1. Add a typed `CallbackEnvelope` and constructor helpers. These callback
   protocols are internal, so all producers, consumers and test doubles change
   together; do not keep a second legacy signature that can bypass validation.
2. Convert Agent decision and Agent generation/TTS callbacks first, with tests
   proving late generation callbacks are rejected.
3. Convert ASR segment and connection callbacks, including the LiveKit epoch.
4. Run a static audit proving no internal producer still calls a callback
   without the envelope. Historical rows remain readable with nullable identity
   columns where the data did not exist.

High-frequency interim, subtitle and text-delta callbacks must not introduce a
database query per chunk. State-changing boundaries validate durable identities;
subsequent progress callbacks validate against the accepted in-memory run/session
identity and the current Actor speech fence.

No public API or WebSocket payload includes the raw envelope. Administrator
diagnostics may show it subject to existing redaction and permissions.

## 7. Persistence

Provider request logs already have the envelope identity columns except a
duplicated `opportunity_generation`. That generation is immutable on the
`FreeDebateOpportunity` row identified by `opportunity_id`, including after the
opportunity is invalidated. Stale diagnostics can retain the complete redacted
envelope in the existing structured `details` field. Therefore this design does
not require a migration. Stale callback diagnostics use the existing
diagnostic/request-log path; no new service or queue is introduced.

## 8. Acceptance

- Static audit: every callback protocol and producer includes `CallbackEnvelope`.
- Unit tests cover mismatches for each identity field and prove no Actor side
  effect, including duplicate/idempotent callbacks.
- Integration tests cover a paused Agent generation, an ASR reconnect with a
  higher connection epoch, a late decision after the three-second deadline, and
  a reset with a new context version.
- Existing Core, Jobs, Web, contract, browser and build gates remain green.
- Staging logs show stale callbacks retained as diagnostics without public
  leakage.

## 9. Rollback

Rollback is source-only because this design adds no migration. Restore the
previous Core source and restart Core; never delete historical diagnostics.

## 2026-10-13 retired Agent result acceptance supplement

Public Agent reset must reject a cancellation-resistant Provider's late success.
The body runtime rechecks its registered run, media fence and immutable generation
after stream/close and before applying a result or sending the full-text callback.
The LLM-ready transaction also rejects an already failed or finalized generation
while holding the existing row lock. A check before Provider construction alone
does not cover this boundary. Tests exercise public start/reset, late success and
the durable transaction guard; a retired result cannot rewrite FAILED as PLAYING,
publish full text, recreate media or affect a replacement generation. This is
completion of the existing stale-result rule, without changing models, retries,
Actor transitions or media timing. Rollback remains source-only.

Retirement cleanup must preserve an already-running formal finalization's resource
ownership. Formal stage-timeout finalization cancels the body task. A separate
public fence may also precede a later finalization callback; this does not imply
the current Actor fences every timeout. Once the finalizer sets `run.finalizing`, it
owns the spool rename, authoritative final commit and subsequent media close;
the cancelled body's cleanup must not detach or delete those resources first.
Public start/fence/timeout-finalize regression coverage must retain the truncated
audio and transcript while reset-only retirement still closes all owned resources.

Apply the same immutable run/generation fence after TTS, playback and playout
awaits and their diagnostic waits. A retired task cannot publish completion or
enter the next retry and create a new generation. Public fencing invalidates
output but retains the registered run and spool until the authoritative reset
or finalization takes ownership, including when the body exits between fencing
and that callback. Reset then closes the retired snapshot within the existing
budget; cleanup snapshots must never close replacement refs. Fencing alone must
not unlink the spool or remove the handle needed by a later formal finalizer.

### Supplement verification (2026-10-13)

- 34 unit cases and 11 real PostgreSQL cases pass. Public start/reset rejects
  cancellation-resistant LLM/TTS/playback results, prevents retired retries and
  keeps replacement runs unchanged. Locked LLM-ready transactions reject retired
  generations, and cancellation preserves the first failure and completion time.
- A real Actor/manager/database pause test holds the host fence during event
  dispatch after PAUSED commits. The old body exits without losing its registered
  handle or spool; the original reset subsequently marks its generation FAILED and
  closes the retired resources. This tests post-commit dispatch, not a new Actor
  transition or a claim that every stage timeout fences playback.
- Formal timeout finalization retains its spool and transcript; fence-only tests
  explicitly hand resources to the subsequent reset/finalizer. Full Core/Jobs and
  repository gates are recorded in MEMORY.md. These are local synthetic-provider
  checks, with no production writes or physical media acceptance claim.

## 10. Local verification (2026-08-23)

- Core tests: 320 passed, 32 skipped.
- Jobs tests: 23 passed, 2 deselected.
- Web tests: 184 passed.
- Tooling/QA tests: 53 passed.
- Ruff, ESLint, Pyright, TypeScript and OpenAPI contract checks passed.
- `pnpm build` passed and generated 34 production routes.
- Browser suite: 228 passed across reference, desktop, compact and wide viewports.
- `git diff --check` passed.

## 11. Production release evidence (2026-08-23)

- Read-only preflight passed before and after sync: four systemd services active,
  Core live/ready 200, migration `0040_formal_4v4_opportunities`, and zero
  non-terminal matches.
- A root-only rollback backup was created at
  `/opt/jixia-backup-before-0217-20260823T151553Z` before source synchronization.
- Only the four Core source files in the implementation slice were synchronized;
  each remote SHA-256 matched the local file. No environment, database, Web
  standalone, or runtime directory permission was overwritten.
- Core compiled and restarted successfully; post-restart `NRestarts=0`.
- Ten 30-second samples over five minutes kept Core, Jobs, Web and LiveKit
  active with live/ready 200 and all restart counters at zero.
- The protected provider-chain probe was attempted twice but both attempts timed
  out in ASR `wait_ready()` before a provider session became ready. TTS/LLM/
  judge/LiveKit probe success is therefore not claimed for this release window;
  this is an external provider-chain acceptance gap requiring follow-up.

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
