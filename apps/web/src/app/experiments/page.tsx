import type { Metadata } from 'next';
import { redirect } from 'next/navigation';

export const metadata: Metadata = { title: '比赛大厅' };

export default function ExperimentsPage() {
  redirect('/lobby');
}
