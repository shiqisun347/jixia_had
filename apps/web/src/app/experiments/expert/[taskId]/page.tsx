import { ExpertWorkspace } from '@/features/experiments/expert-workspace';

export default async function ExpertPage({
  params,
}: Readonly<{ params: Promise<{ taskId: string }> }>) {
  const { taskId } = await params;
  return <ExpertWorkspace taskId={taskId} />;
}
