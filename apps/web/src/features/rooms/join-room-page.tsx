'use client';

import { useQuery } from '@tanstack/react-query';
import { CircleAlert, LoaderCircle, TicketCheck } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

import { ApiClientError } from '@/lib/auth-api';
import { roomsApi } from '@/lib/rooms-api';

export function JoinRoomPage({ roomCode }: Readonly<{ roomCode: string }>) {
  const router = useRouter();
  const normalizedCode = roomCode.trim().toUpperCase();
  const query = useQuery({
    queryKey: ['rooms', 'lookup', normalizedCode],
    queryFn: () => roomsApi.lookup(normalizedCode),
    retry: false,
  });

  useEffect(() => {
    if (query.data) router.replace(`/rooms/${query.data.room_id}`);
  }, [query.data, router]);

  const errorMessage =
    query.error instanceof ApiClientError
      ? query.error.message
      : '暂时无法解析房间号，请稍后重试。';

  return (
    <main className="jx-page-grid jx-page-viewport px-6 py-7">
      <div className="mx-auto w-full max-w-4xl">
        <section className="mt-8 grid min-h-[34rem] place-items-center rounded-[2rem] border border-blue-100 bg-white/90 px-6 text-center shadow-[0_26px_80px_rgba(40,76,142,0.12)]">
          <div className="max-w-md">
            {query.isError ? (
              <CircleAlert className="mx-auto size-10 text-red-500" />
            ) : query.data ? (
              <TicketCheck className="mx-auto size-10 text-lime-600" />
            ) : (
              <LoaderCircle className="mx-auto size-10 animate-spin text-blue-600" />
            )}
            <p className="mt-5 font-mono text-sm font-black tracking-[0.2em] text-blue-600">
              {query.isError ? '加入失败' : normalizedCode || '房间邀请'}
            </p>
            <h1 className="mt-3 text-3xl font-black tracking-[-0.05em] text-slate-950">
              {query.isError ? '无法进入这个房间' : '正在打开邀请房间'}
            </h1>
            <p className="mt-4 text-sm leading-7 text-slate-600">
              {query.isError
                ? errorMessage
                : '正在确认房间状态。进入后，你可以选择辩手或观众身份。'}
            </p>
            {query.isError ? (
              <div className="mt-7 flex flex-wrap justify-center gap-3">
                <button
                  className="min-h-11 rounded-xl border border-blue-100 bg-white px-4 text-sm font-bold text-slate-700"
                  onClick={() => void query.refetch()}
                  type="button"
                >
                  重新尝试
                </button>
                <Link
                  className="inline-flex min-h-11 items-center rounded-xl bg-slate-950 px-4 text-sm font-black !text-white"
                  href="/lobby?join=1"
                >
                  返回房间大厅
                </Link>
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </main>
  );
}
