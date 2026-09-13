import type { Metadata } from 'next';

import { ExperimentHub } from '@/features/experiments/experiment-hub';

export const metadata: Metadata = { title: '实验安排' };

export default function ExperimentsPage() {
  return <ExperimentHub />;
}
