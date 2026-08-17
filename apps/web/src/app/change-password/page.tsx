import { Suspense } from 'react';

import { AuthLoading } from '@/features/auth/auth-loading';
import { ChangePasswordPageView } from '@/features/auth/change-password-page';

export default function ChangePasswordPage() {
  return (
    <Suspense fallback={<AuthLoading label="正在加载改密页面" />}>
      <ChangePasswordPageView />
    </Suspense>
  );
}
