import type { Metadata } from 'next';

import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { JoinRoomPage } from '@/features/rooms';

export const metadata: Metadata = { title: '加入房间' };

export default async function JoinRoomRoute({
  params,
}: Readonly<{ params: Promise<{ roomCode: string }> }>) {
  const { roomCode } = await params;
  const returnTo = `/join/${encodeURIComponent(roomCode)}`;
  return (
    <ProtectedUserPage returnTo={returnTo}>
      <JoinRoomPage roomCode={roomCode} />
    </ProtectedUserPage>
  );
}
