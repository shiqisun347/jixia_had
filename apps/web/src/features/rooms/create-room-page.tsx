'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, Bot, CheckCircle2, LoaderCircle, Sparkles, Swords } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast-provider';
import { ApiClientError } from '@/lib/auth-api';
import { roomsApi, type RoomCreatePayload } from '@/lib/rooms-api';
import { useSubmissionGate } from '@/lib/use-submission-gate';

import { selectDefaultRuleId } from './room-experience';

const schema = z
  .object({
    title: z.string().trim().min(1, '请输入比赛名称').max(200),
    label: z.string().trim().min(1).max(32),
    ruleId: z.string().uuid('请选择赛制'),
    topicSource: z.enum(['LIBRARY', 'CUSTOM']),
    topicId: z.string().optional(),
    customTitle: z.string().trim().max(500).optional(),
    affirmativeText: z.string().trim().max(1000).optional(),
    negativeText: z.string().trim().max(1000).optional(),
  })
  .superRefine((value, context) => {
    if (value.topicSource === 'LIBRARY' && !value.topicId) {
      context.addIssue({ code: 'custom', path: ['topicId'], message: '请选择辩题' });
    }
    if (
      value.topicSource === 'CUSTOM' &&
      (!value.customTitle || !value.affirmativeText || !value.negativeText)
    ) {
      context.addIssue({
        code: 'custom',
        path: ['customTitle'],
        message: '请完整填写辩题和双方立场',
      });
    }
  });

type FormValues = z.infer<typeof schema>;

const inputClass =
  'mt-2 min-h-11 w-full rounded-xl border border-blue-100 bg-white px-3.5 py-2.5 text-sm text-slate-950 outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-100';

export function CreateRoomPage() {
  const router = useRouter();
  const { showToast } = useToast();
  const createGate = useSubmissionGate();
  const catalogQuery = useQuery({ queryKey: ['rooms', 'catalog'], queryFn: roomsApi.catalog });
  const termsQuery = useQuery({
    queryKey: ['legal', 'human-participation'],
    queryFn: roomsApi.humanTerms,
  });
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      title: '',
      label: '训练赛',
      ruleId: '',
      topicSource: 'LIBRARY',
      topicId: '',
      customTitle: '',
      affirmativeText: '',
      negativeText: '',
    },
  });
  const topicSource = useWatch({ control: form.control, name: 'topicSource' });
  const selectedRuleId = useWatch({ control: form.control, name: 'ruleId' });
  const selectedTopicId = useWatch({ control: form.control, name: 'topicId' });
  const customTopicTitle = useWatch({ control: form.control, name: 'customTitle' });
  const selectedRule = catalogQuery.data?.rules.find((rule) => rule.id === selectedRuleId);
  const selectedTopic = catalogQuery.data?.topics.find((topic) => topic.id === selectedTopicId);
  const mutation = useMutation({
    mutationFn: roomsApi.create,
    onSuccess: (room) => router.push(`/rooms/${room.id}?created=1`),
    onError: (error) => {
      createGate.release();
      showToast({
        message: error instanceof ApiClientError ? error.message : '创建房间失败，请检查填写内容。',
        tone: 'error',
      });
    },
  });
  const createRoom = useCallback(
    (values: FormValues) => {
      if (!createGate.tryStart()) return;
      const payload: RoomCreatePayload = {
        title: values.title,
        label: values.label,
        rule_id: values.ruleId,
        is_all_agent: false,
        agent_assignments: [],
        topic_id: values.topicSource === 'LIBRARY' ? values.topicId : null,
        custom_topic_title: values.topicSource === 'CUSTOM' ? values.customTitle : null,
        affirmative_text: values.topicSource === 'CUSTOM' ? values.affirmativeText : null,
        negative_text: values.topicSource === 'CUSTOM' ? values.negativeText : null,
        human_participation_terms_version: termsQuery.data?.version,
      };
      mutation.mutate(payload);
    },
    [createGate, mutation, termsQuery.data?.version],
  );
  const submit = form.handleSubmit(createRoom);
  useEffect(() => {
    if (catalogQuery.isError) {
      showToast({
        message: '无法加载可用赛制、辩题和 Agent，请稍后刷新页面。',
        tone: 'error',
      });
    }
  }, [catalogQuery.isError, showToast]);
  useEffect(() => {
    const rules = catalogQuery.data?.rules;
    if (!rules || rules.length === 0 || form.getValues('ruleId')) return;
    form.setValue('ruleId', selectDefaultRuleId(rules), { shouldValidate: true });
  }, [catalogQuery.data?.rules, form]);
  useEffect(() => {
    if (catalogQuery.isSuccess && catalogQuery.data.rules.length === 0) {
      showToast({ message: '当前没有可用赛制，请联系管理员启用赛制。', tone: 'error' });
    }
  }, [catalogQuery.data?.rules.length, catalogQuery.isSuccess, showToast]);

  return (
    <main className="jx-page-grid jx-page-viewport px-6 py-7 sm:px-10">
      <div className="mx-auto w-full max-w-6xl">
        <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_21rem]">
          <section className="rounded-[2rem] border border-blue-100/90 bg-white/90 p-6 shadow-[0_28px_80px_rgba(40,76,142,0.11)] sm:p-9">
            <Link
              className="inline-flex items-center gap-2 text-sm font-bold text-slate-500 hover:text-blue-700"
              href="/lobby"
            >
              <ArrowLeft className="size-4" /> 返回大厅
            </Link>
            <p className="jx-kicker mt-8">CREATE ROOM</p>
            <h1 className="mt-3 text-4xl font-black tracking-[-0.055em] text-slate-950">
              创建一场辩论
            </h1>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              先确定赛制和辩题。系统会填满空席，创建后再选择你的辩手席位。
            </p>

            <form className="mt-8 space-y-7" onSubmit={submit}>
              <div className="flex items-center gap-3 border-b border-blue-100 pb-3">
                <span className="grid size-7 place-items-center rounded-full bg-blue-600 text-xs font-black text-white">
                  1
                </span>
                <strong className="text-sm text-slate-900">比赛信息</strong>
              </div>
              <div className="grid gap-5 sm:grid-cols-2">
                <label className="text-sm font-bold text-slate-700">
                  比赛名称
                  <input
                    className={inputClass}
                    {...form.register('title')}
                    placeholder="例如：春季人机辩论实验"
                  />
                  {form.formState.errors.title ? (
                    <span className="mt-1 block text-xs text-red-600">
                      {form.formState.errors.title.message}
                    </span>
                  ) : null}
                </label>
                <label className="text-sm font-bold text-slate-700">
                  比赛标签
                  <select className={inputClass} {...form.register('label')}>
                    <option value="训练赛">训练赛</option>
                    <option value="正式赛">正式赛</option>
                    <option value="实验场">实验场</option>
                  </select>
                </label>
              </div>
              <label className="block text-sm font-bold text-slate-700">
                赛制
                <select
                  className={inputClass}
                  disabled={catalogQuery.isPending || !catalogQuery.data?.rules.length}
                  {...form.register('ruleId')}
                >
                  {catalogQuery.isPending ? <option value="">正在加载赛制…</option> : null}
                  {catalogQuery.data?.rules.map((rule) => (
                    <option key={rule.id} value={rule.id}>
                      {rule.name} · {rule.side_size}v{rule.side_size}
                    </option>
                  ))}
                </select>
                {form.formState.errors.ruleId ? (
                  <span className="mt-1 block text-xs text-red-600">
                    {form.formState.errors.ruleId.message}
                  </span>
                ) : null}
              </label>
              <fieldset>
                <legend className="flex items-center gap-3 border-b border-blue-100 pb-3 text-sm font-bold text-slate-900">
                  <span className="grid size-7 place-items-center rounded-full bg-blue-600 text-xs font-black text-white">
                    2
                  </span>
                  选择辩题
                </legend>
                <div className="mt-3 flex gap-3">
                  <label className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-2.5 text-sm font-bold">
                    <input
                      className="mr-2"
                      type="radio"
                      value="LIBRARY"
                      {...form.register('topicSource')}
                    />
                    题库
                  </label>
                  <label className="rounded-xl border border-blue-100 bg-white px-4 py-2.5 text-sm font-bold">
                    <input
                      className="mr-2"
                      type="radio"
                      value="CUSTOM"
                      {...form.register('topicSource')}
                    />
                    自定义
                  </label>
                </div>
                {topicSource === 'LIBRARY' ? (
                  <label className="mt-4 block text-sm font-bold text-slate-700">
                    选择辩题
                    <select
                      aria-describedby={
                        form.formState.errors.topicId ? 'create-topic-error' : undefined
                      }
                      aria-invalid={form.formState.errors.topicId ? 'true' : undefined}
                      className={inputClass}
                      {...form.register('topicId')}
                    >
                      <option value="">请选择</option>
                      {catalogQuery.data?.topics.map((topic) => (
                        <option key={topic.id} value={topic.id}>
                          {topic.title}
                        </option>
                      ))}
                    </select>
                    {form.formState.errors.topicId ? (
                      <span
                        className="mt-1 block text-xs text-red-600"
                        id="create-topic-error"
                        role="alert"
                      >
                        {form.formState.errors.topicId.message}
                      </span>
                    ) : null}
                  </label>
                ) : (
                  <div className="mt-4 grid gap-4">
                    <label className="text-sm font-bold text-slate-700">
                      辩题
                      <input
                        aria-describedby={
                          form.formState.errors.customTitle
                            ? 'create-custom-topic-error'
                            : undefined
                        }
                        aria-invalid={form.formState.errors.customTitle ? 'true' : undefined}
                        className={inputClass}
                        {...form.register('customTitle')}
                      />
                    </label>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <label className="text-sm font-bold text-red-700">
                        正方立场
                        <textarea
                          aria-describedby={
                            form.formState.errors.customTitle
                              ? 'create-custom-topic-error'
                              : undefined
                          }
                          aria-invalid={form.formState.errors.customTitle ? 'true' : undefined}
                          className={`${inputClass} min-h-24 resize-y`}
                          {...form.register('affirmativeText')}
                        />
                      </label>
                      <label className="text-sm font-bold text-blue-700">
                        反方立场
                        <textarea
                          aria-describedby={
                            form.formState.errors.customTitle
                              ? 'create-custom-topic-error'
                              : undefined
                          }
                          aria-invalid={form.formState.errors.customTitle ? 'true' : undefined}
                          className={`${inputClass} min-h-24 resize-y`}
                          {...form.register('negativeText')}
                        />
                      </label>
                    </div>
                  </div>
                )}
                {form.formState.errors.customTitle ? (
                  <span
                    className="mt-2 block text-xs text-red-600"
                    id="create-custom-topic-error"
                    role="alert"
                  >
                    {form.formState.errors.customTitle.message}
                  </span>
                ) : null}
              </fieldset>
              <Button
                className="w-full"
                disabled={createGate.isPending || catalogQuery.isPending || termsQuery.isPending}
                size="lg"
                type="submit"
                variant="primary"
              >
                {createGate.isPending ? (
                  <LoaderCircle className="size-5 animate-spin" />
                ) : (
                  <Sparkles className="size-5" />
                )}
                {createGate.isPending ? '正在创建' : '创建并进入房间'}
              </Button>
            </form>
          </section>
          <aside className="h-fit overflow-hidden rounded-[1.75rem] border border-blue-100 bg-white/92 shadow-[0_24px_68px_rgba(40,76,142,0.13)] lg:sticky lg:top-7">
            <div className="bg-gradient-to-br from-blue-600 to-violet-500 p-6 text-white">
              <div className="flex items-center justify-between">
                <Swords className="size-8" />
                <span className="rounded-full bg-white/16 px-3 py-1 text-[11px] font-black tracking-[0.12em]">
                  ROOM PREVIEW
                </span>
              </div>
              <h2 className="mt-5 text-xl font-black">本场设置</h2>
              <p className="mt-2 text-sm text-blue-50">创建前快速确认，不需要来回翻找表单。</p>
            </div>
            <dl className="space-y-4 p-6 text-sm">
              <div>
                <dt className="text-xs font-bold text-slate-500">赛制</dt>
                <dd className="mt-1 font-black text-slate-950">
                  {selectedRule
                    ? `${selectedRule.name} · ${selectedRule.side_size}v${selectedRule.side_size}`
                    : '正在载入默认 4v4 赛制'}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-bold text-slate-500">辩题</dt>
                <dd className="mt-1 line-clamp-3 font-bold leading-6 text-slate-800">
                  {topicSource === 'LIBRARY'
                    ? (selectedTopic?.title ?? '创建前请选择题库辩题')
                    : customTopicTitle || '将使用自定义辩题'}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-bold text-slate-500">我的身份</dt>
                <dd className="mt-1 font-bold text-slate-800">房主 · 创建后选择辩手席位</dd>
              </div>
              <div>
                <dt className="text-xs font-bold text-slate-500">其他席位</dt>
                <dd className="mt-1 flex items-center gap-2 font-bold text-slate-800">
                  <Bot className="size-4 text-violet-600" />
                  自动用不同的可用 Agent 填满
                </dd>
              </div>
            </dl>
            <div className="border-t border-blue-100 bg-blue-50/55 p-6">
              <p className="flex items-start gap-2 text-xs leading-5 text-slate-600">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                房间创建后公开显示在大厅；标签只用于数据区分，不改变比赛规则。
              </p>
            </div>
          </aside>
        </div>
      </div>
    </main>
  );
}
