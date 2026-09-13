import type { Metadata } from 'next';

import { AdminExperimentPage } from '@/features/experiments/admin-experiment-page';

export const metadata: Metadata = { title: '论文实验管理' };

export default function AdminExperimentsPage() {
  return <AdminExperimentPage />;
}
