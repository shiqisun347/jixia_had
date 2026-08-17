export const HUMAN_AVATAR_KEYS = Array.from(
  { length: 16 },
  (_, index) => `human-${String(index + 1).padStart(2, '0')}`,
);

export const AGENT_AVATAR_KEYS = Array.from(
  { length: 12 },
  (_, index) => `agent-${String(index + 1).padStart(2, '0')}`,
);

export function avatarAssetUrl(key: string): string {
  return `/assets/avatars/${key}.webp`;
}
