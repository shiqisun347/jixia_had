import type { Metadata } from 'next';

import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { CreateRoomPage } from '@/features/rooms';

export const metadata: Metadata = { title: '创建房间' };

export default function CreateRoomRoute() {
  return (
    <ProtectedUserPage returnTo="/rooms/create">
      <CreateRoomPage />
    </ProtectedUserPage>
  );
}
