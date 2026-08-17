'use client';

import Image from 'next/image';
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Download,
  FileText,
  Flag,
  Hand,
  Mic,
  Pause,
  Play,
  Radio,
  RefreshCcw,
  RotateCcw,
  Search,
  Signal,
  Trophy,
  WifiOff,
  X,
} from 'lucide-react';
import { useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type {
  DebatePrototypeFixture,
  DebatePrototypePermissions,
  DebatePrototypeProps,
  DebateSeat,
  DebateSide,
  DebateTranscriptEntry,
  DebateViewState,
  PrototypeControlPermission,
} from './types';
import styles from './debate-prototype.module.css';

const WAVEFORM_HEIGHTS = [
  18, 32, 24, 58, 44, 72, 38, 26, 64, 84, 46, 30, 68, 52, 24, 42, 78, 54, 34, 62, 28, 48, 74, 40,
  22, 56, 82, 48, 30, 66, 36, 18,
] as const;

const DEFAULT_PROTOTYPE_NOTICE = '这是本地 UI 原型，不连接真实房间、麦克风或模型服务。';

interface StatePresentation {
  eyebrow: string;
  title: string;
  description: string;
  tone: 'neutral' | 'active' | 'thinking' | 'paused' | 'danger' | 'finished';
  waveformActive: boolean;
}

const STATE_PRESENTATION: Record<DebateViewState, StatePresentation> = {
  Waiting: {
    eyebrow: '等待开赛',
    title: '等待所有辩手准备',
    description: '房间已就绪。比赛开始后，发言权与计时由规则统一控制。',
    tone: 'neutral',
    waveformActive: false,
  },
  HumanReadyToStart: {
    eyebrow: '正方一辩立论',
    title: '轮到你发言了！',
    description: '请手动点击“开始发言”。开启后才会启动麦克风、ASR 与正式计时。',
    tone: 'active',
    waveformActive: false,
  },
  HumanSpeaking: {
    eyebrow: '实时语音识别中',
    title: '你的发言正在进行',
    description: '实时文字只读。你可以提前结束发言，但发言中不提供静音与重新开麦。',
    tone: 'active',
    waveformActive: true,
  },
  AgentThinking: {
    eyebrow: '自由辩论 · 反方',
    title: 'Agent 正在思考中',
    description: '系统正在生成唯一候选的正式发言稿，完成后将直接开始流式播放。',
    tone: 'thinking',
    waveformActive: false,
  },
  AgentSpeaking: {
    eyebrow: '流式语音合成中',
    title: 'Agent 正在发言',
    description: '文字稿由 Agent 直接生成，字幕按实际播放进度逐步展示。',
    tone: 'active',
    waveformActive: true,
  },
  FreeDebateHandRaise: {
    eyebrow: '自由辩论 · 举手窗口',
    title: '你已申请下一次发言',
    description: '人类申请优先。再次点击“取消举手”即可退出队列。',
    tone: 'active',
    waveformActive: false,
  },
  Paused: {
    eyebrow: '计时与语音链路已冻结',
    title: '比赛已暂停',
    description: '暂停期间不会继续 ASR、Agent 调用或 TTS。满足条件后可申请恢复。',
    tone: 'paused',
    waveformActive: false,
  },
  Disconnected: {
    eyebrow: '连接状态异常',
    title: '一名辩手已离线',
    description: '系统正在等待重连。若正好轮到该辩手，当前发言倒计时会暂停。',
    tone: 'danger',
    waveformActive: false,
  },
  ErrorDrawer: {
    eyebrow: '核心链路连续失败',
    title: '比赛已安全暂停',
    description: '右侧错误信息说明了发生了什么，以及恢复比赛前需要完成的检查。',
    tone: 'danger',
    waveformActive: false,
  },
  Finished: {
    eyebrow: 'AI 裁判已完成评分',
    title: '本场辩论已结束',
    description: '请审阅自己的最终文字记录。排行榜将在每日快照更新后变化。',
    tone: 'finished',
    waveformActive: false,
  },
};

const DEFAULT_PAUSE = {
  title: '比赛已暂停',
  initiatedBy: '当前用户发起暂停',
  requirements: ['全部人类选手在线', '麦克风与扬声器可用', '所有选手仍在房间'],
};

type ConfirmationAction = 'pause' | 'reset';

function cx(...classNames: Array<string | false | null | undefined>): string {
  return classNames.filter(Boolean).join(' ');
}

function formatTimer(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}

function sideLabel(side: DebateSide): string {
  return side === 'affirmative' ? '正' : '反';
}

function participantInitials(seat: DebateSeat): string {
  if (seat.kind === 'agent') return 'AI';
  return seat.name.slice(-2);
}

interface ParticipantAvatarProps {
  seat: DebateSeat;
  size?: 'small' | 'large';
}

function ParticipantAvatar({ seat, size = 'small' }: ParticipantAvatarProps) {
  return (
    <span
      className={cx(
        styles.avatar,
        styles[`avatar_${seat.avatarTone}`],
        size === 'large' && styles.avatarLarge,
      )}
      role="img"
      aria-label={`${seat.name}，${seat.kind === 'agent' ? 'Agent' : '人类辩手'}`}
    >
      {seat.kind === 'agent' ? <Bot aria-hidden="true" /> : participantInitials(seat)}
    </span>
  );
}

interface SeatCardProps {
  seat: DebateSeat;
  current: boolean;
  viewer: boolean;
  handRaised: boolean;
}

function SeatCard({ seat, current, viewer, handRaised }: SeatCardProps) {
  const displayedHandOrder = viewer
    ? handRaised
      ? (seat.handOrder ?? 1)
      : undefined
    : seat.handOrder;
  return (
    <li
      className={cx(
        styles.seatCard,
        styles[`seatCard_${seat.side}`],
        current && styles.seatCardCurrent,
        seat.status === 'offline' && styles.seatCardOffline,
      )}
    >
      <ParticipantAvatar seat={seat} />
      <span className={styles.seatIdentity}>
        <span className={styles.seatNameRow}>
          <strong>{seat.name}</strong>
          <span className={styles.kindBadge}>{seat.kind === 'agent' ? 'AI' : '人类'}</span>
        </span>
        <span className={styles.seatPosition}>{seat.position}</span>
        <span className={styles.seatStatusText}>
          <span
            className={cx(styles.onlineDot, seat.status === 'offline' && styles.offlineDot)}
            aria-hidden="true"
          />
          {seat.status === 'offline' ? '离线' : '在线'}
        </span>
      </span>
      {current ? <span className={styles.currentSpeakerBadge}>当前发言</span> : null}
      {displayedHandOrder ? (
        <span
          className={styles.handBadge}
          role="status"
          aria-label={`申请发言顺序 ${displayedHandOrder}`}
        >
          <Hand aria-hidden="true" />
          {displayedHandOrder}
        </span>
      ) : null}
    </li>
  );
}

interface TeamColumnProps {
  team: DebatePrototypeFixture['affirmative'];
  currentSpeakerId?: string;
  viewerSeatId: string;
  handRaised: boolean;
}

function TeamColumn({ team, currentSpeakerId, viewerSeatId, handRaised }: TeamColumnProps) {
  return (
    <section className={cx(styles.teamColumn, styles[`teamColumn_${team.side}`])}>
      <header className={styles.teamHeader}>
        <span className={styles.teamFlag} aria-hidden="true">
          <Flag />
        </span>
        <span>
          <strong>{team.name}</strong>
          <small>
            {team.seats.length}/{team.seats.length}
          </small>
        </span>
        <p>{team.stance}</p>
      </header>
      <ol className={styles.seatList} aria-label={`${team.name}席位`}>
        {team.seats.map((seat) => (
          <SeatCard
            key={seat.id}
            seat={seat}
            current={seat.id === currentSpeakerId}
            viewer={seat.id === viewerSeatId}
            handRaised={handRaised}
          />
        ))}
      </ol>
    </section>
  );
}

interface WaveformProps {
  active: boolean;
  side: DebateSide | 'neutral';
  compact?: boolean;
}

function Waveform({ active, side, compact = false }: WaveformProps) {
  return (
    <span
      className={cx(
        styles.waveform,
        styles[`waveform_${side}`],
        compact && styles.waveformCompact,
        active && styles.waveformActive,
      )}
      aria-hidden="true"
    >
      {WAVEFORM_HEIGHTS.map((height, index) => (
        <span
          key={`${height}-${index}`}
          style={{ height: `${height}%`, animationDelay: `${(index % 8) * -70}ms` }}
        />
      ))}
    </span>
  );
}

interface ControlButtonProps {
  permission: PrototypeControlPermission;
  icon: ReactNode;
  label: string;
  hint: string;
  onClick: () => void;
  accent?: boolean;
  active?: boolean;
  disabled?: boolean;
}

function ControlButton({
  permission,
  icon,
  label,
  hint,
  onClick,
  accent = false,
  active = false,
  disabled = false,
}: ControlButtonProps) {
  if (!permission.visible) return null;
  const isDisabled = disabled || !permission.enabled;
  const reason = permission.reason ?? (isDisabled ? '当前状态不可用' : hint);
  return (
    <button
      type="button"
      className={cx(
        styles.controlButton,
        accent && styles.controlButtonAccent,
        active && styles.controlButtonActive,
      )}
      disabled={isDisabled}
      onClick={onClick}
      title={reason}
    >
      <span className={styles.controlIcon}>{icon}</span>
      <span>
        <strong>{label}</strong>
        <small>{reason}</small>
      </span>
    </button>
  );
}

interface TranscriptDrawerProps {
  open: boolean;
  entries: DebateTranscriptEntry[];
  currentSpeakerId?: string;
  error: DebatePrototypeFixture['error'];
  permissions: DebatePrototypePermissions;
  onClose: () => void;
  onPrototypeAction: (message: string) => void;
}

function TranscriptDrawer({
  open,
  entries,
  currentSpeakerId,
  error,
  permissions,
  onClose,
  onPrototypeAction,
}: TranscriptDrawerProps) {
  const [query, setQuery] = useState('');
  const [stage, setStage] = useState('全部阶段');
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const wasOpenRef = useRef(open);

  useEffect(() => {
    if (open && !wasOpenRef.current) closeButtonRef.current?.focus();
    wasOpenRef.current = open;
  }, [open]);

  const stageOptions = ['全部阶段', ...Array.from(new Set(entries.map((entry) => entry.stage)))];
  const groupedEntries = useMemo(() => {
    const filtered = entries.filter((entry) => {
      const matchesStage = stage === '全部阶段' || entry.stage === stage;
      const searchable = `${entry.speakerName} ${entry.position} ${entry.content}`.toLowerCase();
      return matchesStage && (!deferredQuery || searchable.includes(deferredQuery));
    });
    return filtered.reduce<Array<{ stage: string; entries: DebateTranscriptEntry[] }>>(
      (groups, entry) => {
        const latest = groups.at(-1);
        if (!latest || latest.stage !== entry.stage) {
          groups.push({ stage: entry.stage, entries: [entry] });
        } else {
          latest.entries.push(entry);
        }
        return groups;
      },
      [],
    );
  }, [deferredQuery, entries, stage]);

  if (!open) return null;

  return (
    <aside
      className={styles.transcriptDrawer}
      aria-label="文字记录"
      onKeyDown={(event) => {
        if (event.key === 'Escape') onClose();
      }}
    >
      <header className={styles.drawerHeader}>
        <div>
          <span className={styles.drawerEyebrow}>完整辩论过程</span>
          <h2>文字记录</h2>
        </div>
        <button
          ref={closeButtonRef}
          type="button"
          className={styles.iconButton}
          onClick={onClose}
          aria-label="关闭文字记录"
        >
          <X aria-hidden="true" />
        </button>
      </header>

      {error ? (
        <section className={styles.errorPanel} aria-labelledby="prototype-error-title">
          <span className={styles.errorIcon} aria-hidden="true">
            <AlertTriangle />
          </span>
          <div>
            <span className={styles.errorCode}>{error.code}</span>
            <h3 id="prototype-error-title">实时服务未恢复</h3>
            <p>{error.userMessage}</p>
            <dl>
              <div>
                <dt>重试</dt>
                <dd>{error.retryLabel}</dd>
              </div>
              <div>
                <dt>下一步</dt>
                <dd>{error.nextStep}</dd>
              </div>
            </dl>
          </div>
        </section>
      ) : null}

      <div className={styles.drawerTools}>
        <label className={styles.searchField}>
          <Search aria-hidden="true" />
          <span className={styles.visuallyHidden}>搜索发言内容</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索发言内容"
          />
        </label>
        <label className={styles.stageSelect}>
          <span className={styles.visuallyHidden}>筛选阶段</span>
          <select value={stage} onChange={(event) => setStage(event.target.value)}>
            {stageOptions.map((option) => (
              <option key={option}>{option}</option>
            ))}
          </select>
          <ChevronDown aria-hidden="true" />
        </label>
      </div>

      <div className={styles.transcriptScroll} tabIndex={0} aria-label="按阶段分组的发言记录">
        {groupedEntries.length ? (
          groupedEntries.map((group) => (
            <section key={group.stage} className={styles.transcriptStage}>
              <header>
                <h3>{group.stage}</h3>
                <span>{group.entries.at(-1)?.timestamp}</span>
              </header>
              {group.entries.map((entry) => (
                <article
                  key={entry.id}
                  className={cx(
                    styles.transcriptEntry,
                    styles[`transcriptEntry_${entry.side}`],
                    entry.speakerId === currentSpeakerId && styles.transcriptEntryCurrent,
                  )}
                >
                  <div className={styles.transcriptMeta}>
                    <span className={styles.sideToken}>{sideLabel(entry.side)}</span>
                    <strong>{entry.speakerName}</strong>
                    <span>{entry.position}</span>
                    <time>{entry.timestamp}</time>
                    {entry.status === 'live' ? (
                      <span className={styles.liveBadge}>识别中 · 只读</span>
                    ) : null}
                  </div>
                  <p>{entry.content}</p>
                  {entry.editableByViewer && entry.status === 'final' ? (
                    <button
                      type="button"
                      className={styles.textAction}
                      onClick={() =>
                        onPrototypeAction('文字审阅入口仅作原型展示，未保存任何数据。')
                      }
                    >
                      审阅我的文字
                    </button>
                  ) : null}
                </article>
              ))}
            </section>
          ))
        ) : (
          <div className={styles.emptyTranscript}>
            <Search aria-hidden="true" />
            <strong>没有匹配的发言</strong>
            <span>尝试清除关键词或切换阶段。</span>
          </div>
        )}
      </div>

      {permissions.exportTranscript.visible ? (
        <button
          type="button"
          className={styles.exportButton}
          disabled={!permissions.exportTranscript.enabled}
          onClick={() => onPrototypeAction('导出操作仅作原型展示。')}
        >
          <Download aria-hidden="true" />
          导出记录（TXT）
        </button>
      ) : null}
    </aside>
  );
}

interface ConfirmationOverlayProps {
  action: ConfirmationAction;
  onCancel: () => void;
  onConfirm: () => void;
}

function ConfirmationOverlay({ action, onCancel, onConfirm }: ConfirmationOverlayProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const isReset = action === 'reset';

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  return (
    <div className={styles.dialogBackdrop}>
      <div
        ref={panelRef}
        className={styles.confirmDialog}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-description"
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onCancel();
        }}
      >
        <span className={styles.confirmIcon} aria-hidden="true">
          {isReset ? <RotateCcw /> : <Pause />}
        </span>
        <div>
          <span className={styles.drawerEyebrow}>需要确认</span>
          <h2 id="confirm-title">{isReset ? '重置本次发言？' : '暂停整场比赛？'}</h2>
          <p id="confirm-description">
            {isReset
              ? '当前未结束片段会被清除。本原型不会记录重置事件。'
              : '计时、ASR、Agent 调用与 TTS 都会立即冻结，无需填写暂停原因。'}
          </p>
        </div>
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} onClick={onCancel}>
            取消
          </button>
          <button type="button" className={styles.dangerButton} onClick={onConfirm}>
            {isReset ? '确认重置' : '确认暂停'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ResultPanel({ fixture }: { fixture: DebatePrototypeFixture }) {
  const result = fixture.result;
  if (!result) return null;
  return (
    <section className={styles.resultPanel} aria-label="AI 裁判结果">
      <Trophy aria-hidden="true" />
      <div>
        <span>AI 裁判结果</span>
        <strong>{result.winnerLabel}</strong>
        <p>{result.summary}</p>
      </div>
      <div className={styles.scorePair}>
        <span>
          正方 <strong>{result.affirmativeScore}</strong>
        </span>
        <span>
          反方 <strong>{result.negativeScore}</strong>
        </span>
      </div>
    </section>
  );
}

function PauseRequirements({ pause }: { pause: NonNullable<DebatePrototypeFixture['pause']> }) {
  return (
    <section className={styles.pauseRequirements}>
      <header>
        <Pause aria-hidden="true" />
        <span>
          <strong>{pause.title}</strong>
          <small>{pause.initiatedBy}</small>
        </span>
      </header>
      <ul>
        {pause.requirements.map((requirement) => (
          <li key={requirement}>
            <CheckCircle2 aria-hidden="true" />
            {requirement}
          </li>
        ))}
      </ul>
      {pause.unmetReasons?.map((reason) => (
        <p key={reason} className={styles.unmetReason}>
          <AlertTriangle aria-hidden="true" />
          {reason}
        </p>
      ))}
    </section>
  );
}

function DebatePrototypeSession({ fixture, className }: DebatePrototypeProps) {
  const [viewState, setViewState] = useState<DebateViewState>(fixture.state);
  const [stateBeforePause, setStateBeforePause] = useState<DebateViewState>('HumanReadyToStart');
  const [transcriptOpen, setTranscriptOpen] = useState(
    fixture.transcriptInitiallyOpen ?? fixture.state === 'ErrorDrawer',
  );
  const [errorDismissed, setErrorDismissed] = useState(false);
  const [handRaised, setHandRaised] = useState(fixture.state === 'FreeDebateHandRaise');
  const [confirmation, setConfirmation] = useState<ConfirmationAction | null>(null);
  const [notice, setNotice] = useState(DEFAULT_PROTOTYPE_NOTICE);
  const [finalizing, setFinalizing] = useState(false);
  const [hiddenTranscriptIds, setHiddenTranscriptIds] = useState<Set<string>>(() => new Set());
  const finalizingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (finalizingTimer.current) clearTimeout(finalizingTimer.current);
    },
    [],
  );

  const presentation = STATE_PRESENTATION[viewState];
  const allSeats = [...fixture.affirmative.seats, ...fixture.negative.seats];
  const currentSeat = allSeats.find((seat) => seat.id === fixture.currentSpeakerId);
  const fallbackSeat = allSeats.find((seat) => seat.id === fixture.viewerSeatId) ?? allSeats[0];
  const displayedSeat = currentSeat ?? fallbackSeat;
  const visibleTranscript = fixture.transcript.filter(
    (entry) => !hiddenTranscriptIds.has(entry.id),
  );
  const drawerOpen = transcriptOpen || (viewState === 'ErrorDrawer' && !errorDismissed);
  const interactionLocked = viewState === 'Disconnected' || viewState === 'Finished' || finalizing;

  function prototypeAction(message: string): void {
    setNotice(message);
  }

  function startSpeech(): void {
    setViewState('HumanSpeaking');
    setNotice('原型状态已切换：麦克风、ASR 与正式计时开始。');
  }

  function finishSpeech(): void {
    if (finalizing) return;
    setFinalizing(true);
    setNotice('正在等待 ASR 最终结果（原型演示）…');
    finalizingTimer.current = setTimeout(() => {
      setFinalizing(false);
      setViewState('Waiting');
      setNotice('发言已结束，最终文字已加入记录（原型演示）。');
      finalizingTimer.current = null;
    }, 650);
  }

  function toggleHand(): void {
    setHandRaised((current) => {
      setNotice(current ? '已取消发言申请。' : '已申请发言，当前顺序为 1。');
      return !current;
    });
  }

  function confirmAction(): void {
    if (confirmation === 'pause') {
      setStateBeforePause(viewState);
      setViewState('Paused');
      setNotice('比赛已暂停：计时与实时语音链路全部冻结（原型演示）。');
    } else if (confirmation === 'reset') {
      const currentEntry = fixture.transcript.find(
        (entry) => entry.speakerId === fixture.currentSpeakerId && entry.status === 'live',
      );
      if (currentEntry) {
        setHiddenTranscriptIds((current) => new Set(current).add(currentEntry.id));
      }
      setViewState('HumanReadyToStart');
      setNotice('本次未结束发言已清空，请重新手动开始（原型演示）。');
    }
    setConfirmation(null);
  }

  function resumeMatch(): void {
    const resumedState =
      stateBeforePause === 'HumanSpeaking'
        ? 'HumanReadyToStart'
        : stateBeforePause === 'AgentSpeaking'
          ? 'AgentThinking'
          : stateBeforePause === 'Paused'
            ? 'HumanReadyToStart'
            : stateBeforePause;
    setViewState(resumedState);
    setNotice('恢复条件已满足。预设提示后将进行 3 秒倒计时（原型演示）。');
  }

  return (
    <main className={cx(styles.prototype, className)} data-state={viewState}>
      <header className={styles.topbar}>
        <button
          type="button"
          className={styles.brand}
          onClick={() => prototypeAction('导航仅作原型展示。')}
          aria-label="稷下人机自动辩论实验平台"
        >
          <span className={styles.brandMark} aria-hidden="true">
            <Image src="/assets/logo-ui.webp" alt="" width={40} height={40} priority />
          </span>
          <span>
            <strong>稷下人机交互平台</strong>
          </span>
        </button>
        <nav className={styles.prototypeNav} aria-label="原型导航">
          {['公开大厅', '我的房间', '历史记录', '帮助中心'].map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => prototypeAction(`${item}仅作原型展示。`)}
            >
              {item}
            </button>
          ))}
        </nav>
        <div className={styles.connectionSummary}>
          <span className={styles.prototypeBadge}>原型演示</span>
          <span
            className={cx(
              styles.networkStatus,
              viewState === 'Disconnected' && styles.networkStatusOffline,
            )}
          >
            {viewState === 'Disconnected' ? <WifiOff /> : <Signal />}
            {fixture.match.networkLabel}
          </span>
        </div>
      </header>

      <section className={styles.matchSummary} aria-label="比赛信息">
        <div className={styles.roomCard}>
          <span>房间号</span>
          <strong>{fixture.match.roomCode}</strong>
          <small>{fixture.match.formatName}</small>
        </div>
        <div className={styles.topicCard}>
          <span className={styles.topicEyebrow}>{fixture.match.matchLabel}</span>
          <h1>{fixture.match.topic}</h1>
          <span className={styles.stagePill}>
            <Radio aria-hidden="true" />
            {fixture.match.stage}
          </span>
        </div>
      </section>

      <div className={styles.arenaFrame}>
        <TeamColumn
          team={fixture.affirmative}
          currentSpeakerId={fixture.currentSpeakerId}
          viewerSeatId={fixture.viewerSeatId}
          handRaised={handRaised}
        />

        <section className={styles.speakerStage} aria-label="当前发言状态">
          <div
            className={styles.stageProgress}
            aria-label={`赛程 ${fixture.match.stageIndex}/${fixture.match.stageCount}`}
          >
            <span>阶段</span>
            {Array.from({ length: fixture.match.stageCount }, (_, index) => (
              <i
                key={index}
                className={cx(index + 1 <= fixture.match.stageIndex && styles.stageProgressDone)}
              >
                {index + 1}
              </i>
            ))}
          </div>
          <div className={cx(styles.turnBanner, styles[`turnBanner_${presentation.tone}`])}>
            <span aria-hidden="true" />
            <strong>{finalizing ? '正在确认最终文字…' : presentation.title}</strong>
            <span aria-hidden="true" />
          </div>
          <p className={styles.stateEyebrow}>{presentation.eyebrow}</p>

          <div className={styles.voiceStage}>
            <Waveform
              active={presentation.waveformActive && !finalizing}
              side={displayedSeat?.side ?? 'neutral'}
            />
            {displayedSeat ? (
              <div className={cx(styles.speakerHalo, styles[`speakerHalo_${displayedSeat.side}`])}>
                <ParticipantAvatar seat={displayedSeat} size="large" />
                {presentation.waveformActive ? (
                  <span className={styles.liveMic} role="img" aria-label="麦克风或语音播放中">
                    {displayedSeat.kind === 'human' ? <Mic /> : <Radio />}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className={styles.speakerIdentity}>
            <strong>{displayedSeat?.name ?? '等待发言者'}</strong>
            <span>
              {displayedSeat?.position ?? fixture.match.stage}
              {displayedSeat ? ` · ${displayedSeat.kind === 'agent' ? 'Agent' : '人类辩手'}` : ''}
            </span>
          </div>

          <p className={styles.stateDescription}>{presentation.description}</p>

          {viewState === 'Finished' ? <ResultPanel fixture={fixture} /> : null}
          {viewState === 'Paused' || viewState === 'Disconnected' ? (
            <PauseRequirements pause={fixture.pause ?? DEFAULT_PAUSE} />
          ) : null}

          {viewState !== 'Finished' ? (
            <div className={styles.timer}>
              <span>
                <Clock3 aria-hidden="true" />
                发言计时 · 演示
              </span>
              <strong>{formatTimer(fixture.match.timerSeconds)}</strong>
            </div>
          ) : null}
        </section>

        <TeamColumn
          team={fixture.negative}
          currentSpeakerId={fixture.currentSpeakerId}
          viewerSeatId={fixture.viewerSeatId}
          handRaised={handRaised}
        />
      </div>

      <section className={styles.controls} aria-label="比赛控制">
        {viewState === 'HumanReadyToStart' ? (
          <ControlButton
            permission={fixture.permissions.startSpeech}
            icon={<Mic aria-hidden="true" />}
            label="开始发言"
            hint="手动开启麦克风"
            onClick={startSpeech}
            accent
            disabled={interactionLocked}
          />
        ) : null}
        {viewState === 'HumanSpeaking' ? (
          <ControlButton
            permission={fixture.permissions.endSpeech}
            icon={<Flag aria-hidden="true" />}
            label={finalizing ? '正在结束' : '提前结束发言'}
            hint="等待 ASR 最终结果"
            onClick={finishSpeech}
            accent
            disabled={interactionLocked}
          />
        ) : null}
        {viewState === 'FreeDebateHandRaise' ? (
          <ControlButton
            permission={fixture.permissions.raiseHand}
            icon={<Hand aria-hidden="true" />}
            label={handRaised ? '取消举手' : '申请发言'}
            hint={handRaised ? '当前顺序 1' : '人类申请优先'}
            onClick={toggleHand}
            accent={!handRaised}
            active={handRaised}
            disabled={interactionLocked}
          />
        ) : null}
        {viewState === 'Paused' || viewState === 'ErrorDrawer' ? (
          <ControlButton
            permission={fixture.permissions.resumeMatch}
            icon={<Play aria-hidden="true" />}
            label="申请恢复"
            hint="先检查全部恢复条件"
            onClick={resumeMatch}
            accent
          />
        ) : null}
        {viewState !== 'Waiting' &&
        viewState !== 'Finished' &&
        viewState !== 'Paused' &&
        viewState !== 'ErrorDrawer' ? (
          <ControlButton
            permission={fixture.permissions.resetSpeech}
            icon={<RefreshCcw aria-hidden="true" />}
            label="重置本次发言"
            hint="清空当前未结束片段"
            onClick={() => setConfirmation('reset')}
            disabled={interactionLocked}
          />
        ) : null}
        {viewState !== 'Waiting' &&
        viewState !== 'Finished' &&
        viewState !== 'Paused' &&
        viewState !== 'ErrorDrawer' ? (
          <ControlButton
            permission={fixture.permissions.pauseMatch}
            icon={<Pause aria-hidden="true" />}
            label="暂停比赛"
            hint="暂停整场比赛"
            onClick={() => setConfirmation('pause')}
            disabled={interactionLocked}
          />
        ) : null}
        <ControlButton
          permission={fixture.permissions.viewTranscript}
          icon={<FileText aria-hidden="true" />}
          label="文字记录"
          hint={drawerOpen ? '关闭右侧抽屉' : '查看完整辩论过程'}
          onClick={() => {
            setTranscriptOpen((current) => !current);
            setErrorDismissed(true);
          }}
          active={drawerOpen}
        />
      </section>

      <TranscriptDrawer
        open={drawerOpen}
        entries={visibleTranscript}
        currentSpeakerId={fixture.currentSpeakerId}
        error={viewState === 'ErrorDrawer' ? fixture.error : undefined}
        permissions={fixture.permissions}
        onClose={() => {
          setTranscriptOpen(false);
          setErrorDismissed(true);
        }}
        onPrototypeAction={prototypeAction}
      />

      <div className={styles.prototypeNotice} role="status" aria-live="polite">
        <AlertTriangle aria-hidden="true" />
        {notice}
      </div>

      {confirmation ? (
        <ConfirmationOverlay
          action={confirmation}
          onCancel={() => setConfirmation(null)}
          onConfirm={confirmAction}
        />
      ) : null}
    </main>
  );
}

export function DebatePrototype(props: DebatePrototypeProps) {
  const { fixture } = props;
  const fixtureKey = [
    fixture.state,
    fixture.match.roomCode,
    fixture.currentSpeakerId ?? 'none',
    fixture.affirmative.seats.length,
    fixture.negative.seats.length,
    fixture.transcriptInitiallyOpen ? 'drawer-open' : 'drawer-closed',
  ].join(':');

  return <DebatePrototypeSession key={fixtureKey} {...props} />;
}
