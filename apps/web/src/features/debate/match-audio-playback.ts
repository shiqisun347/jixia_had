export type RemoteAudioSource =
  { kind: 'HUMAN'; userId: string } | { kind: 'AGENT' } | { kind: 'OTHER' };

export function classifyRemoteAudioSource(
  identity: string,
  matchId: string,
  knownHumanUserIds: readonly string[],
): RemoteAudioSource {
  const humanPrefix = `jx-human-${matchId}-`;
  if (identity.startsWith(humanPrefix)) {
    const userId = knownHumanUserIds.find((candidate) =>
      identity.startsWith(`${humanPrefix}${candidate}-`),
    );
    if (userId) return { kind: 'HUMAN', userId };
  }
  if (identity.startsWith(`jx-agent-${matchId}-`)) return { kind: 'AGENT' };
  return { kind: 'OTHER' };
}

export function shouldMuteRemoteAudio(
  outputMuted: boolean,
  source: RemoteAudioSource,
  mutedHumanUserIds: ReadonlySet<string>,
): boolean {
  if (outputMuted) return true;
  return source.kind === 'HUMAN' && mutedHumanUserIds.has(source.userId);
}

export function humanAudioMuteStorageKey(matchId: string): string {
  return `jx:human-audio-muted:v1:${matchId}`;
}

export function parseMutedHumanUserIds(value: string | null): string[] {
  if (!value) return [];
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    return [
      ...new Set(
        parsed.filter((item): item is string => typeof item === 'string' && item.length > 0),
      ),
    ];
  } catch {
    return [];
  }
}

export function serializeMutedHumanUserIds(userIds: readonly string[]): string {
  return JSON.stringify([...new Set(userIds)]);
}
