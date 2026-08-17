type RefreshResult = { isError: boolean };

export async function commitAdminAction(
  commit: () => Promise<void>,
  refresh: () => Promise<RefreshResult>,
): Promise<'refreshed' | 'refresh_failed'> {
  await commit();
  const result = await refresh();
  return result.isError ? 'refresh_failed' : 'refreshed';
}
