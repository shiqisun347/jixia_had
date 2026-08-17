import type { useAdminSubmit } from './use-admin-submit';

type Submit = ReturnType<typeof useAdminSubmit>['submit'];

export async function submitCatalogSave(
  submit: Submit,
  save: () => Promise<unknown>,
  refresh: () => Promise<{ isError: boolean }>,
): Promise<'not_started' | 'refreshed' | 'refresh_failed'> {
  let refreshResult: 'refreshed' | 'refresh_failed' = 'refreshed';
  const submitted = await submit(async () => {
    await save();
    refreshResult = (await refresh()).isError ? 'refresh_failed' : 'refreshed';
  });
  return submitted ? refreshResult : 'not_started';
}
