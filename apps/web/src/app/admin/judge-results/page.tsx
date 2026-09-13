'use client';

import { ArrowRight } from 'lucide-react';
import Link from 'next/link';

import { AdminEmpty, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';

export default function AdminJudgeResultsPage() {
  return (
    <div className="space-y-6">
      <AdminPageHeader
        actions={
          <Link
            className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 text-sm font-bold text-slate-700 hover:border-blue-300 hover:text-blue-700"
            href="/admin/matches"
          >
            查看比赛数据 <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
        }
        description="跨赛制查询 AI 裁判评分与调用状态；单场详情继续从比赛工作台查看。"
        eyebrow="JUDGE RESULTS"
        title="裁判结果"
      />
      <AdminPanel title="评分记录" description="独立结果查询将在赛制裁判配置迁移后启用。">
        <AdminEmpty>当前请从“比赛与数据”进入单场裁判详情。</AdminEmpty>
      </AdminPanel>
    </div>
  );
}
