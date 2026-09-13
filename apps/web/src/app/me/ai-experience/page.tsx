'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { ProtectedUserPage } from '@/features/auth/protected-user-page';
import { surveyApi, type AiExperienceSurvey } from '@/lib/experiments-api';
import { LocalizedTextBoundary, useLocalizedText } from '@/i18n/localized-text';

function SurveyContent() {
  const localize = useLocalizedText();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ['survey', 'ai-experience'], queryFn: surveyApi.aiExperience });
  const [answers, setAnswers] = useState<Record<string, number | string>>({});
  const save = useMutation({
    mutationFn: ({ submit }: { submit: boolean }) => surveyApi.saveAiExperience(answers, submit),
    onSuccess: (next) => queryClient.setQueryData(['survey', 'ai-experience'], next),
  });
  if (query.isLoading || !query.data) return <LocalizedTextBoundary><main className="jx-page-viewport grid place-items-center">加载问卷中...</main></LocalizedTextBoundary>;
  const survey: AiExperienceSurvey = query.data;
  const values = { ...survey.answers, ...answers };
  return (
    <LocalizedTextBoundary><main className="jx-page-viewport bg-[#f7faff] px-4 py-8 text-slate-950 md:px-8">
      <section className="mx-auto max-w-3xl rounded-2xl border border-blue-100 bg-white p-6 shadow-sm md:p-9">
        <Link className="text-sm font-bold text-blue-700" href="/me">返回我的页面</Link>
        <h1 className="mt-4 text-2xl font-black">{localize(survey.title)}</h1>
        <p className="mt-2 text-sm leading-6 text-slate-600">{localize(survey.description)}</p>
        <div className="mt-7 divide-y divide-slate-100">
          {survey.questions.map((question) => (
            <fieldset className="py-5" key={question.key}>
              <legend className="text-sm font-black">{localize(question.text)}{question.required ? ' *' : ''}</legend>
              {question.type === 'scale' ? (
                <div className="mt-3 grid gap-2">
                  {(question.options ??
                    Array.from({ length: question.scale_max ?? 7 }, (_, index) => ({
                      value: index + 1,
                      text: String(index + 1),
                    }))).map((option) => (
                    <label className={`cursor-pointer rounded-lg border px-3 py-2 text-left text-sm font-black ${values[question.key] === option.value ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-200 text-slate-600'}`} key={option.value}>
                      <input className="sr-only" type="radio" name={question.key} checked={values[question.key] === option.value} onChange={() => setAnswers((current) => ({ ...current, [question.key]: option.value }))} />
                      {option.value} {localize(option.text)}
                    </label>
                  ))}
                </div>
              ) : (
                <textarea className="mt-3 min-h-24 w-full rounded-lg border border-slate-200 p-3 text-sm" maxLength={4000} value={String(values[question.key] ?? '')} onChange={(event) => setAnswers((current) => ({ ...current, [question.key]: event.target.value }))} />
              )}
            </fieldset>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button disabled={save.isPending} onClick={() => save.mutate({ submit: false })}>保存草稿</Button>
          <Button disabled={save.isPending} onClick={() => save.mutate({ submit: true })}>提交问卷</Button>
          {survey.status === 'SUBMITTED' ? <span className="self-center text-sm font-bold text-emerald-700">已提交</span> : null}
        </div>
      </section>
    </main></LocalizedTextBoundary>
  );
}

export default function AiExperiencePage() {
  return <ProtectedUserPage returnTo="/me/ai-experience"><SurveyContent /></ProtectedUserPage>;
}
