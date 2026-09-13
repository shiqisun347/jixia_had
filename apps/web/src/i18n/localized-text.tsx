'use client';

import { Children, cloneElement, isValidElement, type ReactNode } from 'react';

import { useAppLocale } from './locale-provider';

const english: Record<string, string> = {
  'AI 辩论感受': 'AI debate experience',
  '返回我的页面': 'Back to my profile',
  '返回问卷列表': 'Back to questionnaires',
  '返回实验安排': 'Back to experiment schedule',
  '赛后发言问卷': 'Post-match speech questionnaire',
  '单场赛后发言问卷': 'Single-match post-speech questionnaire',
  '每场比赛独立审阅。完成本场问卷后，下一场比赛资格自动解除。':
    'Review each match independently. Completing this questionnaire unlocks eligibility for the next match.',
  '填写独立 AI 辩论体验问卷': 'Complete the independent AI debate experience questionnaire',
  '正在加载问卷…': 'Loading questionnaire…',
  '加载问卷中...': 'Loading questionnaire…',
  '问卷加载失败，请刷新页面重试。': 'The questionnaire could not be loaded. Refresh and try again.',
  '暂无单场赛后问卷。': 'There are no single-match post-match questionnaires.',
  '正式比赛': 'Formal match',
  '辩题：': 'Topic: ',
  '已完成': 'Completed',
  '待完成': 'Pending',
  '查看并修改标注 →': 'Review and edit annotations →',
  '继续填写本场问卷 →': 'Continue this questionnaire →',
  '保存草稿': 'Save draft',
  '提交问卷': 'Submit questionnaire',
  '已提交': 'Submitted',
  '问卷不存在或暂时无法访问。': 'This questionnaire does not exist or is temporarily unavailable.',
  '完整辩论记录': 'Full debate transcript',
  '当前标注': 'Current annotation',
  '修改标注': 'Edit annotation',
  '正在保存…': 'Saving…',
  '保存失败': 'Save failed',
  '待作答': 'Awaiting response',
  '已保存': 'Saved',
  '重试保存': 'Retry save',
  '加载最新进度': 'Load latest progress',
  '完成并继续': 'Complete and continue',
  '完成修改': 'Finish editing',
  '提交本场问卷': 'Submit this questionnaire',
  '本场问卷已经提交。': 'This questionnaire has been submitted.',
  '自由辩论标注已完成': 'Free-debate annotations are complete',
  '确认无误后提交本场问卷。': 'Submit this questionnaire once you have confirmed the answers.',
  '本场没有需要标注的自由辩论发言。': 'There are no free-debate speeches to annotate in this match.',
  '填写中': 'In progress',
  '当前第 {sequence} 条': 'Item {sequence}',
  '条已标注': ' annotations complete',
  '完成右侧判断后显示本段发言': 'This speech is shown after you complete the assessment on the right.',
  '点击气泡可修改标注': 'Select a bubble to edit its annotation',
  '离开当前问卷？': 'Leave this questionnaire?',
  '当前选择尚未保存，离开后可能需要重新填写。': 'Current selections are not saved and may need to be entered again after leaving.',
  '确认离开': 'Leave questionnaire',
  '自动保存失败，请重试。': 'Automatic save failed. Please retry.',
  '已加载最新问卷进度。': 'The latest questionnaire progress has been loaded.',
  '重新加载失败，请稍后重试。': 'Could not reload. Please try again shortly.',
  '问卷已提交，下一场资格已解除。': 'Questionnaire submitted. Eligibility for the next match is unlocked.',
  '提交失败，请稍后重试。': 'Submission failed. Please try again shortly.',
  '辩论记录': 'Debate transcript',
  '辩手': 'Debater',
  '本方发言': 'Team speech',
  '真人': 'Human',
  '正方': 'Affirmative',
  '反方': 'Negative',
  '我的实际发言': 'My speech',
  '本方 AI 发言': 'Our AI speech',
  '发言前状态': 'State before the speech',
  '实际发言': 'Actual speech',
  '文字记录': 'Transcript',
  '（无可用文字）': '(No transcript available)',
  '（本次发言没有可用文字）': '(No transcript is available for this speech)',
  '该时点没有更早的辩论记录。': 'There are no earlier debate records at this point.',
  '本题已保存。': 'This item has been saved.',
  '保存本题': 'Save this item',
  '锁定后才会显示 AI 的实际发言，且不能修改这一判断。':
    'The AI speech is shown only after locking, and this assessment cannot then be changed.',
  '事前判断已锁定': 'Pre-speech assessment locked',
  '赛后辩手体验问卷': 'Post-match debater experience questionnaire',
  '请根据刚才这场比赛的整体感受，选择最符合你想法的答案。':
    'Choose the answer that best reflects your overall experience of the match.',
  '保存赛后辩手体验问卷': 'Save post-match experience questionnaire',
  '赛后辩手体验问卷已保存。': 'Post-match debater experience questionnaire saved.',
  '本场标注已提交，答案已锁定。': 'Annotations submitted. Answers are locked.',
  '没有找到该标注任务。': 'This annotation task was not found.',
  '事件标注与赛后辩手体验问卷': 'Event annotation and post-match debater experience questionnaire',
  '事件': 'Event',
  '问卷': 'Questionnaire',
  '待填写': 'Pending',
  '上一项': 'Previous',
  '下一项': 'Next',
  '提交全部标注': 'Submit all annotations',
  '标注步骤': 'Annotation steps',
  'Q1 当时你为什么选择自己发言，而不是把这个机会留给 AI 或其他队友？请选择最主要的原因。':
    'Q1 Why did you choose to speak rather than leave this opportunity to AI or another teammate? Select the main reason.',
  'Q2 你这次发言最主要想完成什么？': 'Q2 What was the primary goal of this speech?',
  'Q1 在发言之前，当时你觉得 AI 这个时候发言合适吗':
    'Q1 Before the speech, did you think it was appropriate for AI to speak then?',
  'Q2 看完 AI 的实际发言后，你认为这次发言与当时团队需要的匹配程度如何？（不仅看内容）':
    'Q2 After reviewing the AI speech, how well did it fit what the team needed at that time? Consider more than its content.',
  '自己更适合处理这个问题。': 'I was better suited to handle this issue.',
  '担心AI 不适合或者处理不好这个问题。': 'I was concerned that AI was not suited to handle this issue well.',
  '没人回答或者其他担忧被迫回答': 'No one else answered, or I had another concern that required me to respond.',
  '其他。': 'Other.',
  '回应对手': 'Respond to the opponent',
  '接续队友': 'Build on a teammate',
  '补足缺口': 'Address a gap',
  '主动推进': 'Advance our argument',
  '调整方向': 'Redirect the discussion',
  '其他': 'Other',
  '不合适，比如有此时其他人更合适发言：': 'Not appropriate, for example because someone else was better suited to speak:',
  '发言不发言都合适：': 'Either speaking or not speaking would be appropriate:',
  '很适合 AI 发言': 'Very appropriate for AI to speak',
  '无法判断：': 'Unable to judge:',
  '很好，处理了当时团队真正需要处理的问题': 'Very well: it addressed what the team truly needed at the time.',
  '一般，内容有点冗余，重复表达，新增价值很小': 'Average: it was somewhat redundant and added little new value.',
  '一般，有点跑偏或者钻牛角尖，内容可能有价值，但不是当时最需要处理的问题':
    'Average: it may have been useful, but it was off track or not what the team most needed then.',
  '不好，为后续带来了额外的修复负担': 'Poor: it created additional work for the team to repair later.',
  '其他不好或者一般的原因': 'Another reason it was poor or average',
  '无法判断': 'Unable to judge',
  '这场比赛里，AI 更像一个会和队友配合的辩手，还是只顾自己对抗的辩手？':
    'In this match, did AI act more like a collaborating teammate or a debater focused only on its own opposition?',
  '完全只顾自己，几乎不管队友': 'Entirely self-focused, with little regard for teammates',
  '大多只顾自己，偶尔配合队友': 'Mostly self-focused, with occasional coordination',
  '两方面差不多': 'About equally both',
  '大多能配合队友': 'Usually coordinated with teammates',
  '很像人类队友，会主动补充、接续和配合': 'Very much like a human teammate: proactively adds, builds on, and coordinates',
  'AI 的发言通常有没有接住队友刚才说的内容和场上的情况？':
    'Did AI usually connect with what teammates had just said and the current state of the debate?',
  '几乎没有，常常各说各的': 'Almost never; it often spoke independently',
  '很少接得上': 'Rarely connected',
  '有时接得上，有时接不上': 'Sometimes connected and sometimes did not',
  '大多数时候接得上': 'Connected most of the time',
  '几乎总能接住并继续推进': 'Almost always connected and advanced the discussion',
  'AI 的发言对我们团队有多大帮助？': 'How helpful were AI’s speeches to our team?',
  '明显没帮助，甚至添乱': 'Clearly unhelpful; it even caused disruption',
  '帮助很少': 'Very little help',
  '有一点帮助': 'Some help',
  '比较有帮助': 'Quite helpful',
  '帮助很大': 'Very helpful',
  '这场比赛里，AI 有没有给你带来额外负担？比如它说得不清楚、有漏洞、和队友重复，你还要花力气去理解、补充或修正。':
    'Did AI create extra work for you in this match, for example through unclear, flawed, or repetitive speech that you had to understand, supplement, or correct?',
  '几乎没有': 'Almost none',
  '很少': 'Very little',
  '有一点': 'Some',
  '不少': 'Quite a lot',
  '很多': 'A great deal',
  '如果下一场还要和这个 AI 一起辩，你愿意继续把它当作队友吗？':
    'If you debated with this AI again, would you be willing to keep it as a teammate?',
  '完全不愿意': 'Not at all willing',
  '不太愿意': 'Not very willing',
  '说不上': 'Neither willing nor unwilling',
  '比较愿意': 'Quite willing',
  '非常愿意': 'Very willing',
};

export function localizeText(value: string, locale: string): string {
  if (locale !== 'en') return value;
  return (
    english[value] ??
    value
      .replace(/比赛 ([a-f0-9]{8})/g, 'Match $1')
      .replace(/(\d+) 条已标注/g, '$1 annotations complete')
      .replace(/(\d+)\/(\d+) 条/g, '$1/$2 items')
      .replace(/(\d+) 辩/g, 'Speaker $1')
      .replace(/当前第 (\d+) 条/g, 'Item $1')
  );
}

export function useLocalizedText() {
  const { locale } = useAppLocale();
  return (value: string) => localizeText(value, locale);
}

export function LocalizedTextBoundary({ children }: { children: ReactNode }) {
  const { locale } = useAppLocale();
  if (locale !== 'en') return <>{children}</>;
  const visit = (node: ReactNode): ReactNode =>
    Children.map(node, (child) => {
      if (typeof child === 'string') return localizeText(child, locale);
      if (!isValidElement(child)) return child;
      const props = child.props as Record<string, unknown> & { children?: ReactNode };
      const translatedProps: Record<string, unknown> = {};
      for (const key of ['aria-label', 'placeholder', 'title', 'description', 'confirmLabel']) {
        if (typeof props[key] === 'string') translatedProps[key] = localizeText(props[key], locale);
      }
      return cloneElement(child, translatedProps, visit(props.children));
    });
  return <>{visit(children)}</>;
}
