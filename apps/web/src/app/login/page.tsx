import { Suspense } from 'react';

import { LoginForm } from '@/features/auth/auth-forms';

export default function LoginPage() {
  return (
    <Suspense
      fallback={<div className="min-h-[calc(100dvh-var(--jx-header-height))] bg-slate-50" />}
    >
      <LoginForm />
    </Suspense>
  );
}
