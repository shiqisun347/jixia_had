import type { Metadata } from 'next';

import { MePageView } from '@/features/auth/me-page';

export const metadata: Metadata = { title: '我的页面', description: '查看资料、比赛和设备状态。' };

export default function MePage() {
  return <MePageView />;
}
