import type { Metadata } from 'next';
import { redirect } from 'next/navigation';

export const metadata: Metadata = { title: '比赛管理' };

export default function AdminExperimentsPage() {
  redirect('/admin/matches');
}
