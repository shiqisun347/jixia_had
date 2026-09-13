import type { Metadata } from 'next';
import { GuideWorkspace } from './guide-workspace';

export const metadata: Metadata = { title: '使用指南', description: '从入场到赛后，一次看懂稷下多人多智能体实时语音辩论。' };

export default function GuidePage() {
  return (
    <main className="jx-page-viewport overflow-x-hidden bg-[#f7faff] px-4 py-8 text-slate-950 sm:px-6 lg:px-10 lg:py-12">
      <GuideWorkspace />
    </main>
  );
}
