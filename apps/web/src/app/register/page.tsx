import { Suspense } from 'react';

import { RegisterForm } from '@/features/auth/auth-forms';

export default function RegisterPage() {
  return (
    <Suspense
      fallback={<div className="min-h-[calc(100dvh-var(--jx-header-height))] bg-slate-50" />}
    >
      <RegisterForm />
    </Suspense>
  );
}
