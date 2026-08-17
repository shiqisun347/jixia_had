export function shouldConnectMatchAudio(status: string | null | undefined): boolean {
  return Boolean(status && !['FINISHED', 'TERMINATED'].includes(status));
}
