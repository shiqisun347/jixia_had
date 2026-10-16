import type { MatchSnapshot } from '@/lib/matches-api';

export interface HumanMicrophoneSession {
  prepareMicrophone(): Promise<void>;
  setMicrophoneEnabled(enabled: boolean): Promise<void>;
}

export interface HumanMicrophoneContext {
  snapshot: MatchSnapshot | null | undefined;
  session: HumanMicrophoneSession | null;
  epoch: number | null;
  userId: string | undefined;
  connectionCurrent: boolean;
}

interface StartClaim {
  session: HumanMicrophoneSession;
  epoch: number;
  userId: string;
  snapshot: MatchSnapshot;
  committedSpeechId?: string | null;
  committed: boolean;
}

function ownsContext(claim: StartClaim, current: HumanMicrophoneContext): boolean {
  const before = claim.snapshot;
  const next = current.snapshot;
  return Boolean(
    current.connectionCurrent &&
    current.session === claim.session &&
    current.epoch === claim.epoch &&
    current.userId === claim.userId &&
    next?.status === 'RUNNING' &&
    next.current_speaker_user_id === claim.userId &&
    next.match_id === before.match_id &&
    next.context_version === before.context_version &&
    next.current_action_index === before.current_action_index &&
    next.current_action?.stage_position === before.current_action?.stage_position &&
    next.current_action?.action_position === before.current_action?.action_position &&
    next.current_action?.action_kind === before.current_action?.action_kind &&
    ((next.action_state === 'HUMAN_SPEAKING' &&
      (claim.committedSpeechId === undefined ||
        next.current_speech_id === claim.committedSpeechId)) ||
      (next.action_state === 'HUMAN_READY_TO_START' &&
        next.current_speech_id === before.current_speech_id)),
  );
}

/** Owns only microphone preparation; Core still commits the speech and its timer. */
export class HumanMicrophoneStart {
  private claim: StartClaim | null = null;
  private owner: StartClaim | null = null;
  private desired = new WeakMap<
    HumanMicrophoneSession,
    { enabled: boolean; promise: Promise<void>; failed: boolean }
  >();
  private inFlight = new WeakMap<HumanMicrophoneSession, Set<Promise<void>>>();

  constructor(private readonly pendingChanged: (pending: boolean) => void) {}

  private release(claim: StartClaim) {
    if (this.claim !== claim) return;
    this.claim = null;
    this.pendingChanged(false);
  }

  setEnabled(session: HumanMicrophoneSession, enabled: boolean): Promise<void> {
    const previous = this.desired.get(session);
    if (previous?.enabled === enabled && !previous.failed) return previous.promise;
    const desired = { enabled, promise: Promise.resolve(), failed: false };
    this.desired.set(session, desired);
    const inFlight = this.inFlight.get(session) ?? new Set<Promise<void>>();
    this.inFlight.set(session, inFlight);
    // A permission/publish operation can finish after a reset or a replacement
    // start. Reconcile to the latest owner intent, never to the old result.
    desired.promise = (async () => {
      let applying = desired;
      for (;;) {
        await session.setMicrophoneEnabled(applying.enabled);
        const latest = this.desired.get(session);
        if (!latest || latest === applying) return;
        applying = latest;
      }
    })()
      .catch((error: unknown) => {
        desired.failed = true;
        throw error;
      })
      .finally(() => {
        inFlight.delete(desired.promise);
      });
    inFlight.add(desired.promise);
    return desired.promise;
  }

  async stop(session: HumanMicrophoneSession): Promise<void> {
    if (this.claim?.session === session) this.release(this.claim);
    if (this.owner?.session === session) this.owner = null;
    await this.setEnabled(session, false);
  }

  submissionGuard(readCurrent: () => HumanMicrophoneContext): () => boolean {
    const claim = this.claim;
    return () => {
      const current = readCurrent();
      return Boolean(
        claim &&
        this.claim === claim &&
        ownsContext(claim, current) &&
        current.snapshot?.action_state === 'HUMAN_READY_TO_START',
      );
    };
  }

  async sync(
    current: HumanMicrophoneContext,
    manuallyClosedSpeechId: string | null,
  ): Promise<void> {
    const claim = this.claim;
    if (claim && !ownsContext(claim, current)) this.release(claim);
    if (this.owner && !ownsContext(this.owner, current)) this.owner = null;
    if (this.owner && current.snapshot?.action_state === 'HUMAN_SPEAKING') {
      this.owner.committedSpeechId = current.snapshot.current_speech_id;
    }
    if (!current.session) return;
    // Preparing and waiting for ACK must keep the real publication muted.
    // A snapshot can arrive before the pending command promise is resolved.
    if (this.claim && !this.claim.committed) return;
    const speaking =
      current.connectionCurrent &&
      current.snapshot?.status === 'RUNNING' &&
      current.snapshot.action_state === 'HUMAN_SPEAKING' &&
      Boolean(current.userId) &&
      current.snapshot.current_speaker_user_id === current.userId &&
      (!current.snapshot.current_speech_id ||
        current.snapshot.current_speech_id !== manuallyClosedSpeechId);
    await this.setEnabled(current.session, speaking);
  }

  async start(
    readCurrent: () => HumanMicrophoneContext,
    command: () => Promise<boolean>,
    committed: () => void,
  ): Promise<boolean> {
    const current = readCurrent();
    if (
      this.claim ||
      !current.session ||
      current.epoch === null ||
      !current.connectionCurrent ||
      !current.userId ||
      current.snapshot?.status !== 'RUNNING' ||
      !current.snapshot.current_action ||
      current.snapshot.action_state !== 'HUMAN_READY_TO_START' ||
      current.snapshot.current_speaker_user_id !== current.userId
    )
      return false;
    const claim: StartClaim = {
      session: current.session,
      epoch: current.epoch,
      userId: current.userId,
      snapshot: { ...current.snapshot, current_action: { ...current.snapshot.current_action } },
      committed: false,
    };
    this.claim = claim;
    this.owner = claim;
    this.pendingChanged(true);
    let succeeded = false;
    try {
      const previousOperations = [...(this.inFlight.get(claim.session) ?? [])];
      const muted = this.setEnabled(claim.session, false);
      // A new claim must not publish while an older native unmute can still
      // complete. Each old operation reconciles to the false intent above.
      await Promise.all([Promise.allSettled(previousOperations), muted]);
      if (this.claim !== claim || !ownsContext(claim, readCurrent())) return false;
      await claim.session.prepareMicrophone();
      const ready = readCurrent();
      if (
        this.claim !== claim ||
        !ownsContext(claim, ready) ||
        ready.snapshot?.action_state !== 'HUMAN_READY_TO_START'
      )
        return false;
      const acknowledged = await command();
      const after = readCurrent();
      const accepted = Boolean(
        acknowledged &&
        this.claim === claim &&
        ownsContext(claim, after) &&
        after.snapshot?.action_state === 'HUMAN_SPEAKING',
      );
      if (accepted) {
        claim.committed = true;
        claim.committedSpeechId = after.snapshot?.current_speech_id;
        committed();
        await this.setEnabled(claim.session, true);
        succeeded = this.claim === claim && ownsContext(claim, readCurrent());
      }
      return succeeded;
    } catch (error) {
      if (this.owner !== claim || !ownsContext(claim, readCurrent())) return false;
      throw error;
    } finally {
      this.release(claim);
      const successorOwnsSession =
        this.owner !== null && this.owner !== claim && this.owner.session === claim.session;
      if (!succeeded && !successorOwnsSession) {
        if (this.owner === claim) this.owner = null;
        try {
          await this.setEnabled(claim.session, false);
        } catch (error) {
          const current = readCurrent();
          // The old Room can reject cleanup after disconnect. Its failure
          // cannot change the replacement session's microphone feedback.
          if (
            current.session === claim.session &&
            current.epoch === claim.epoch &&
            current.connectionCurrent
          )
            throw error;
        }
      }
    }
  }
}
