'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowRight, CalendarDays, ClipboardList, LoaderCircle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast-provider';
import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { ApiClientError, authApi } from '@/lib/auth-api';
import { experimentsApi, type ExperimentAppointment } from '@/lib/experiments-api';

function message(error: unknown) {
  return error instanceof ApiClientError ? error.message : '操作失败，请稍后重试。';
}

const sideName = (side: string) => (side === 'AFFIRMATIVE' ? '正方' : '反方');

function statusName(status: string) {
  return (
    {
      SCHEDULED: '待开赛',
      WAITING: '等待中',
      RUNNING: '进行中',
      PAUSED: '已暂停',
      COMPLETED: '已完成',
      TERMINATED: '已终止',
    }[status] ?? status
  );
}

function AppointmentRow({ appointment }: Readonly<{ appointment: ExperimentAppointment }>) {
  const router = useRouter();
  const { showToast } = useToast();
  const enter = useMutation({
    mutationFn: async () => {
      const terms = await authApi.currentTerms();
      return experimentsApi.enter(appointment.scheduled_match_id, terms.version);
    },
    onSuccess: (result) => router.push(`/rooms/${result.room_id}`),
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });

  return (
    <article className="grid gap-4 border-b border-slate-100 py-5 last:border-b-0 md:grid-cols-[1fr_auto] md:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <strong className="text-base font-black text-slate-950">
            第 {appointment.round_no} 轮 · 第 {appointment.match_no} 场
          </strong>
          <span className="rounded-md bg-blue-50 px-2 py-1 text-xs font-black text-blue-700">
            {appointment.kind === 'FORMAL' ? '正式赛' : '训练赛'}
          </span>
          <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600">
            {statusName(appointment.status)}
          </span>
        </div>
        <p className="mt-2 text-sm font-bold text-slate-700">
          {sideName(appointment.side)} {appointment.seat_no} 辩 · 固定席位
        </p>
        <p className="mt-1 text-xs text-slate-500">
          {appointment.scheduled_at
            ? new Date(appointment.scheduled_at).toLocaleString('zh-CN')
            : '时间由实验组织者另行确认'}
        </p>
      </div>
      <Button disabled={enter.isPending} onClick={() => enter.mutate()}>
        {enter.isPending ? (
          <LoaderCircle className="size-4 animate-spin" />
        ) : (
          <ArrowRight className="size-4" />
        )}
        进入固定房间
      </Button>
    </article>
  );
}

function ExperimentHubContent() {
  const appointments = useQuery({
    queryKey: ['experiments', 'appointments'],
    queryFn: experimentsApi.appointments,
  });
  const tasks = useQuery({
    queryKey: ['experiments', 'participant-tasks'],
    queryFn: experimentsApi.participantTasks,
  });
  const expertTasks = useQuery({
    queryKey: ['experiments', 'expert-tasks'],
    queryFn: experimentsApi.expertTasks,
  });

  return (
    <main className="jx-page-viewport bg-[#f7faff] px-5 py-8 text-slate-950 md:px-8">
      <div className="mx-auto max-w-6xl">
        <header className="flex flex-wrap items-end justify-between gap-4 border-b border-blue-100 pb-6">
          <div>
            <p className="jx-kicker">PAPER EXPERIMENT</p>
            <h1 className="mt-2 text-3xl font-black">我的实验安排</h1>
          </div>
          <span className="rounded-lg bg-white px-3 py-2 text-xs font-bold text-slate-600 shadow-sm">
            席位由排表固定
          </span>
          <Link
            className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-[#d4e2f0] bg-white px-3 text-sm font-bold text-[#1e2a3a] shadow-sm hover:border-[#a9c6e7] hover:text-blue-700"
            href="/rooms/create"
          >
            创建论文同款普通房间
          </Link>
        </header>

        <section className="mt-7 bg-white px-5 py-2 shadow-sm md:px-7">
          <div className="flex items-center gap-2 border-b border-slate-100 py-4">
            <CalendarDays className="size-5 text-blue-600" />
            <h2 className="text-lg font-black">预约比赛</h2>
          </div>
          {appointments.isPending ? (
            <p className="py-10 text-center text-sm text-slate-500">正在加载排表…</p>
          ) : appointments.isError ? (
            <p className="py-10 text-center text-sm text-red-700">{message(appointments.error)}</p>
          ) : appointments.data?.length ? (
            appointments.data.map((appointment) => (
              <AppointmentRow appointment={appointment} key={appointment.scheduled_match_id} />
            ))
          ) : (
            <p className="py-10 text-center text-sm text-slate-500">当前账号暂无实验预约。</p>
          )}
        </section>

        <section className="mt-7 grid gap-5 md:grid-cols-2">
          <div className="bg-white p-6 shadow-sm">
            <div className="flex items-center gap-2">
              <ClipboardList className="size-5 text-blue-600" />
              <h2 className="text-lg font-black">赛后标注</h2>
            </div>
            <div className="mt-4 divide-y divide-slate-100">
              {tasks.data?.length ? (
                tasks.data.map((task) => (
                  <a
                    className="flex items-center justify-between gap-3 py-3 text-sm font-bold hover:text-blue-700"
                    href={`/experiments/annotations/${task.id}`}
                    key={task.id}
                  >
                    <span>{task.scheduled_match_kind === 'FORMAL' ? '正式赛' : '训练赛'}任务</span>
                    <span>
                      {task.status === 'SUBMITTED' ? '已提交' : `${task.items.length} 个事件`}
                    </span>
                  </a>
                ))
              ) : (
                <p className="py-5 text-sm text-slate-500">暂无待标注比赛。</p>
              )}
            </div>
          </div>
          {expertTasks.data?.length ? (
            <div className="bg-white p-6 shadow-sm">
              <h2 className="text-lg font-black">专家标注</h2>
              <div className="mt-4 divide-y divide-slate-100">
                {expertTasks.data.map((task) => (
                  <a
                    className="flex items-center justify-between gap-3 py-3 text-sm font-bold hover:text-blue-700"
                    href={`/experiments/expert/${task.id}`}
                    key={task.id}
                  >
                    <span>批次机会标注</span>
                    <span>
                      {task.status === 'SUBMITTED' ? '已提交' : `${task.items.length} 项`}
                    </span>
                  </a>
                ))}
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}

export function ExperimentHub() {
  return (
    <ProtectedUserPage returnTo="/experiments">
      <ExperimentHubContent />
    </ProtectedUserPage>
  );
}
