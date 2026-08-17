import type { Metadata } from 'next';

import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { RoomPage } from '@/features/rooms';

export const metadata: Metadata = { title: '比赛房间' };

export default async function RoomRoute({
  params,
  searchParams,
}: Readonly<{
  params: Promise<{ roomId: string }>;
  searchParams: Promise<{ recheck?: string; created?: string }>;
}>) {
  const { roomId } = await params;
  const { recheck, created } = await searchParams;
  const query = new URLSearchParams();
  if (recheck === '1') query.set('recheck', '1');
  if (created === '1') query.set('created', '1');
  const returnTo = `/rooms/${roomId}${query.size ? `?${query.toString()}` : ''}`;
  return (
    <ProtectedUserPage returnTo={returnTo}>
      <RoomPage roomId={roomId} recheck={recheck === '1'} created={created === '1'} />
    </ProtectedUserPage>
  );
}
