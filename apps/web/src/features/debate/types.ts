export const DEBATE_VIEW_STATES = [
  'Waiting',
  'HumanReadyToStart',
  'HumanSpeaking',
  'AgentThinking',
  'AgentSpeaking',
  'FreeDebateHandRaise',
  'Paused',
  'Disconnected',
  'ErrorDrawer',
  'Finished',
] as const;

export type DebateViewState = (typeof DEBATE_VIEW_STATES)[number];
export type DebateSide = 'affirmative' | 'negative';
export type DebateParticipantKind = 'human' | 'agent';
export type DebateSeatStatus = 'online' | 'offline';
export type DebateAvatarTone = 'crimson' | 'blue' | 'ink' | 'silver' | 'violet' | 'amber';

export interface DebateSeat {
  id: string;
  side: DebateSide;
  name: string;
  position: string;
  kind: DebateParticipantKind;
  status: DebateSeatStatus;
  avatarTone: DebateAvatarTone;
  handOrder?: number;
}

export interface DebateTeam {
  side: DebateSide;
  name: string;
  stance: string;
  seats: DebateSeat[];
}

export type TranscriptStatus = 'final' | 'live';

export interface DebateTranscriptEntry {
  id: string;
  stage: string;
  timestamp: string;
  speakerId: string;
  speakerName: string;
  position: string;
  side: DebateSide;
  content: string;
  status: TranscriptStatus;
  editableByViewer?: boolean;
}

export interface PrototypeControlPermission {
  visible: boolean;
  enabled: boolean;
  reason?: string;
}

export interface DebatePrototypePermissions {
  startSpeech: PrototypeControlPermission;
  endSpeech: PrototypeControlPermission;
  resetSpeech: PrototypeControlPermission;
  pauseMatch: PrototypeControlPermission;
  resumeMatch: PrototypeControlPermission;
  raiseHand: PrototypeControlPermission;
  viewTranscript: PrototypeControlPermission;
  exportTranscript: PrototypeControlPermission;
}

export interface DebatePrototypeError {
  code: string;
  userMessage: string;
  retryLabel: string;
  nextStep: string;
}

export interface DebatePrototypePause {
  title: string;
  initiatedBy: string;
  requirements: string[];
  unmetReasons?: string[];
}

export interface DebatePrototypeResult {
  winner: DebateSide;
  winnerLabel: string;
  affirmativeScore: number;
  negativeScore: number;
  summary: string;
}

export interface DebatePrototypeFixture {
  state: DebateViewState;
  match: {
    roomCode: string;
    formatName: string;
    matchLabel: string;
    topic: string;
    stage: string;
    stageIndex: number;
    stageCount: number;
    timerSeconds: number;
    networkLabel: string;
  };
  affirmative: DebateTeam;
  negative: DebateTeam;
  currentSpeakerId?: string;
  viewerSeatId: string;
  transcript: DebateTranscriptEntry[];
  permissions: DebatePrototypePermissions;
  transcriptInitiallyOpen?: boolean;
  error?: DebatePrototypeError;
  pause?: DebatePrototypePause;
  result?: DebatePrototypeResult;
}

export interface DebatePrototypeProps {
  fixture: DebatePrototypeFixture;
  className?: string;
}
