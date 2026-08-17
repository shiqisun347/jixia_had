'use client';

import { useEffect, useState } from 'react';

import {
  AdminButton,
  AdminFeedback,
  AdminPageHeader,
  AdminPanel,
  readableAdminError,
  SelectField,
  TextArea,
  type JudgeProfile,
} from '@/features/admin';
import { useOptionalToast } from '@/components/ui/toast-provider';
import { requestJson } from '@/lib/auth-api';
import type { ModelRow } from '@/features/admin';
import { useAdminSubmit } from '@/features/admin/use-admin-submit';

export default function AdminJudgePage() {
  const toast = useOptionalToast();
  const [models, setModels] = useState<ModelRow[]>([]);
  const [judge, setJudge] = useState<JudgeProfile>(null);
  const [modelId, setModelId] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('你是客观、简洁的中文辩论裁判，只输出 JSON。');
  const [judgePrompt, setJudgePrompt] = useState(
    '按 argument/rebuttal/evidence/teamwork/expression 五项评分。每方总计 100；每名辩手 0-20。严格输出 winner、team_scores、participants、team_comments。',
  );
  const [message, setMessage] = useState('');
  const { isSubmitting, submit } = useAdminSubmit();

  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<{ models: ModelRow[] }>('/api/admin/catalog'),
      requestJson<JudgeProfile>('/api/admin/judge-profile'),
    ])
      .then(([catalog, profile]) => {
        if (!active) return;
        setModels(catalog.models);
        setJudge(profile);
        setModelId(profile?.model_profile_id || catalog.models[0]?.id || '');
        if (profile) {
          setSystemPrompt(profile.system_prompt);
          setJudgePrompt(profile.judge_prompt);
        }
      })
      .catch((requestError: unknown) => {
        if (active) setMessage(readableAdminError(requestError));
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="space-y-6">
      <AdminPageHeader
        eyebrow="AI JUDGE"
        title="AI 裁判"
        description="比赛结束后立即按结束时文字版本评分。评分失败不改变比赛状态，可从比赛管理页重新评分。"
      />
      {message ? <AdminFeedback message={message} tone="error" /> : null}
      <AdminPanel
        title="裁判配置"
        description={
          judge ? '当前已有启用配置，保存会更新唯一配置。' : '首次保存会创建唯一裁判配置。'
        }
      >
        <div className="grid gap-4">
          <SelectField
            label="评分模型"
            name="judge-model"
            value={modelId}
            onChange={(event) => setModelId(event.target.value)}
            required
          >
            <option value="">选择模型</option>
            {models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name} · {model.model_id}
              </option>
            ))}
          </SelectField>
          <TextArea
            label="系统提示词"
            name="judge-system"
            value={systemPrompt}
            onChange={(event) => setSystemPrompt(event.target.value)}
          />
          <TextArea
            label="评分提示词"
            name="judge-prompt"
            value={judgePrompt}
            onChange={(event) => setJudgePrompt(event.target.value)}
          />
          <div className="flex flex-wrap items-center gap-3">
            <AdminButton
              tone="primary"
              disabled={!modelId}
              loading={isSubmitting}
              onClick={() => {
                void submit(() =>
                  requestJson('/api/admin/judge-profile', {
                    method: 'PUT',
                    body: JSON.stringify({
                      model_profile_id: modelId,
                      system_prompt: systemPrompt,
                      judge_prompt: judgePrompt,
                      generation_params: { temperature: 0.2 },
                    }),
                  }).then(() => undefined),
                )
                  .then((submitted) => {
                    if (submitted)
                      toast?.showToast({ message: '裁判配置已保存。', tone: 'success' });
                  })
                  .catch((requestError: unknown) =>
                    toast?.showToast({ message: readableAdminError(requestError), tone: 'error' }),
                  );
              }}
            >
              保存裁判配置
            </AdminButton>
            <span className="text-xs text-slate-500">默认 temperature 0.2</span>
          </div>
        </div>
      </AdminPanel>
    </div>
  );
}
