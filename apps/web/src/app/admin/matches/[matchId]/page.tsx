import { AdminMatchWorkbench } from '@/features/admin/admin-match-workbench';

export default async function AdminMatchWorkbenchRoute({
  params,
}: {
  params: Promise<{ matchId: string }>;
}) {
  const { matchId } = await params;
  return <AdminMatchWorkbench matchId={matchId} />;
}
