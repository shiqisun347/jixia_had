'use client';

import { useEffect, useState } from 'react';

import { AdminButton } from '@/features/admin/admin-controls';
import { AdminFeedback, AdminPageHeader, AdminPanel } from '@/features/admin/admin-ui';
import { requestJson } from '@/lib/auth-api';

type SurveyAdmin = {
  version: { id: string; version: number; title: string; description: string; questions: Array<Record<string, unknown>>; status: string };
  responses: Array<{ id: string; user_id: string; status: string; answers: Record<string, unknown>; submitted_at: string | null }>;
};

export default function AdminSurveysPage() {
  const [data, setData] = useState<SurveyAdmin | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [questionsJson, setQuestionsJson] = useState('');
  useEffect(() => {
    void requestJson<SurveyAdmin>('/api/admin/surveys/personal').then((next) => { setData(next); setQuestionsJson(JSON.stringify(next.version.questions, null, 2)); }).catch(() => setError('问卷数据加载失败。'));
  }, []);
  async function publish() {
    if (!data) return;
    setSaving(true);
    try {
      await requestJson(`/api/admin/surveys/personal/${data.version.id}/publish`, { method: 'POST' });
      setData({ ...data, version: { ...data.version, status: 'PUBLISHED' } });
    } catch {
      setError('发布问卷失败。');
    } finally {
      setSaving(false);
    }
  }
  async function saveDraft() {
    if (!data) return;
    setSaving(true);
    try {
      const questions = JSON.parse(questionsJson) as Array<Record<string, unknown>>;
      await requestJson('/api/admin/surveys/personal', { method: 'PUT', body: JSON.stringify({ title: data.version.title, description: data.version.description, questions }) });
      const next = await requestJson<SurveyAdmin>('/api/admin/surveys/personal');
      setData(next);
      setQuestionsJson(JSON.stringify(next.version.questions, null, 2));
      setError('草稿已创建，现在可以发布。');
    } catch { setError('保存问卷草稿失败，请检查 JSON 格式。'); } finally { setSaving(false); }
  }
  return (
    <div className="space-y-5">
      <AdminPageHeader description="查看个人 AI 辩论感受问卷版本与回答。" eyebrow="SURVEYS" title="问卷管理" />
      {error ? <AdminFeedback message={error} tone="error" /> : null}
      {data ? <>
        <AdminPanel description={`${data.version.questions.length} 道题 · 当前状态 ${data.version.status}`} title={`个人问卷 v${data.version.version}`}>
          <p className="text-sm text-slate-600">{data.version.description}</p>
          <label className="mt-4 grid gap-2 text-sm font-bold">题目配置（JSON）<textarea className="min-h-48 rounded-lg border border-slate-200 p-3 font-mono text-xs font-normal" value={questionsJson} onChange={(event) => setQuestionsJson(event.target.value)} /></label>
          <ol className="mt-4 space-y-2 text-sm">{data.version.questions.map((question, index) => <li className="rounded-lg border border-slate-200 p-3" key={String(question.key ?? index)}>{index + 1}. {String(question.text ?? '')}</li>)}</ol>
          <div className="mt-4 flex flex-wrap gap-3"><AdminButton loading={saving} onClick={() => void saveDraft()}>保存新草稿</AdminButton>
          <AdminButton className="mt-4" loading={saving} onClick={() => void publish()} tone="primary">发布当前版本</AdminButton>
          <AdminButton onClick={() => { window.location.href = '/api/admin/surveys/personal/export?format=csv'; }}>导出 CSV</AdminButton><AdminButton onClick={() => { window.location.href = '/api/admin/surveys/personal/export?format=json'; }}>导出 JSON</AdminButton></div>
        </AdminPanel>
        <AdminPanel description={`共 ${data.responses.length} 份回答`} title="回答记录">
          <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-slate-200"><th className="p-2">用户 ID</th><th className="p-2">状态</th><th className="p-2">提交时间</th></tr></thead><tbody>{data.responses.map((response) => <tr className="border-b border-slate-100" key={response.id}><td className="p-2 font-mono text-xs">{response.user_id}</td><td className="p-2">{response.status}</td><td className="p-2">{response.submitted_at ?? '未提交'}</td></tr>)}</tbody></table></div>
        </AdminPanel>
      </> : null}
    </div>
  );
}
