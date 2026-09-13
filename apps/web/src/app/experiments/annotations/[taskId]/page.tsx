import { ParticipantAnnotationPage } from '@/features/experiments/participant-annotation-page';

export default async function AnnotationPage({
  params,
}: Readonly<{ params: Promise<{ taskId: string }> }>) {
  const { taskId } = await params;
  return <ParticipantAnnotationPage taskId={taskId} />;
}
