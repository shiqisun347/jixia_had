type ReplayLike = {
  status: string;
  error_code?: string | null;
};

export function judgeStatusText(status: string, hasJudge: boolean): string | null {
  if (hasJudge) return null;
  return status === 'TERMINATED' ? '比赛已终止，不进行 AI 评分。' : '评分任务尚未创建';
}

export function judgeFailureText(errorCode?: string | null): string {
  const copy: Record<string, string> = {
    judge_result_invalid: '裁判返回的评分格式不符合要求，请重新评分。',
    judge_profile_unavailable: 'AI 裁判配置暂不可用，请联系管理员检查模型设置。',
    llm_capacity_full: '当前评分任务较多，请稍后重新评分。',
    llm_first_token_timeout: '评分服务响应超时，请重新评分。',
    llm_stream_stalled: '评分服务响应中断，请重新评分。',
    llm_provider_failed: '评分服务暂时不可用，请稍后重新评分。',
  };
  return (errorCode && copy[errorCode]) || 'AI 评分未完成，请稍后重新评分。';
}

export function replayStatusText(
  replay: ReplayLike | undefined,
  speechCount: number,
): { tone: 'neutral' | 'info' | 'warning'; text: string } | null {
  if (!replay) return { tone: 'neutral', text: '该比赛尚无可用的整场回放。' };
  if (replay.status === 'PROCESSING') {
    return { tone: 'info', text: '音频正在赛后处理中，完成后可直接播放和下载。' };
  }
  if (replay.status !== 'FAILED') return null;
  if (replay.error_code === 'audio_sources_missing' && speechCount === 0) {
    return { tone: 'neutral', text: '本场没有可生成的回放。' };
  }
  return { tone: 'warning', text: '音频处理失败，请联系房主或管理员重试。' };
}
