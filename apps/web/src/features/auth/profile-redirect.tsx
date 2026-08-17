'use client';

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

import { AuthLoading } from './auth-loading';
import { ProtectedUserPage } from './protected-user-page';

function RedirectToProfileDialog() {
  const router = useRouter();
  useEffect(() => {
    router.replace('/me?edit=profile');
  }, [router]);
  return <AuthLoading label="正在打开个人资料" />;
}

export function ProfileRedirect() {
  return (
    <ProtectedUserPage returnTo="/profile">
      <RedirectToProfileDialog />
    </ProtectedUserPage>
  );
}
