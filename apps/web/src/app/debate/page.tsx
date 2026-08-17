import type { Metadata } from 'next';
import { Suspense } from 'react';

import { ProtectedDebate } from '@/features/auth/protected-debate';

export const metadata: Metadata = {
  title: '实时比赛 · 稷下人机交互平台',
  description: '稷下人机实时语音辩论比赛页。',
};

export default function DebatePage() {
  return (
    <Suspense fallback={null}>
      <ProtectedDebate />
    </Suspense>
  );
}
