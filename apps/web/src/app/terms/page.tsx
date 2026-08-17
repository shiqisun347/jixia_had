import { Suspense } from 'react';

import { TermsPageView } from '@/features/auth/auth-forms';

export default function TermsPage() {
  return (
    <Suspense
      fallback={<div className="min-h-[calc(100dvh-var(--jx-header-height))] bg-slate-50" />}
    >
      <TermsPageView />
    </Suspense>
  );
}
