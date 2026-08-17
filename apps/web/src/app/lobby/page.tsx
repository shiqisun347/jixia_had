import type { Metadata } from 'next';

import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { LobbyPage } from '@/features/rooms';

export const metadata: Metadata = { title: '公开大厅', description: '浏览稷下公开辩论房间。' };

export default function LobbyRoute() {
  return (
    <ProtectedUserPage returnTo="/lobby">
      <LobbyPage />
    </ProtectedUserPage>
  );
}
