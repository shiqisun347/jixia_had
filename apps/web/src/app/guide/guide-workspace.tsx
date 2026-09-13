'use client';

import Link from 'next/link';
import { AlertCircle, ArrowRight, AudioLines, CheckCircle2, ChevronDown, Clock3, Hand, Headphones, HelpCircle, LayoutList, Mic, ShieldCheck, UsersRound, Volume2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

type Role = 'debater' | 'spectator' | 'admin';

const roles: Record<Role, { label: string; title: string; description: string }> = {
  debater: { label: '真人辩手', title: '按提示入席，轮到你时主动发言', description: '从设备检测到赛后标注，比赛页会把当前可执行的动作放在最前面。' },
  spectator: { label: '观众', title: '进入大厅，安静观看整场交锋', description: '观众只订阅比赛音频和公开记录，不占用辩手席位，也不会发布麦克风。' },
  admin: { label: '管理员', title: '配置规则，守住每场比赛的边界', description: '规则、Agent、排表和发布流程在后台完成；比赛进行时由 Core 统一控制状态。' },
};

const steps = [
  ['01', '登录并进入房间', '从比赛大厅或实验安排进入指定房间。实验参与者不能自行改席位。', UsersRound],
  ['02', '确认身份与席位', '普通房间选择辩手或观众，再选择可用席位；实验房间按排表自动分配。', ShieldCheck],
  ['03', '完成设备检测', '一次性检查麦克风、扬声器和网络。检测通过后才能作为真人辩手准备开赛。', Mic],
  ['04', '跟随阶段推进', '主持音频提示当前阶段。计时、权限和下一位发言者以比赛页的服务端状态为准。', Clock3],
  ['05', '主动开始发言', '固定发言需点击“开始发言”才会开麦；系统不会自动打开你的麦克风。', AudioLines],
  ['06', '参与自由辩论', '正方先开始首个机会。对方发言结束后的窗口内申请发言，真人按受理顺序优先于 Agent。', Hand],
  ['07', '等待文字定稿', '发言结束后等待 ASR 完成。最终文字可以在赛后审阅时修改。', LayoutList],
  ['08', '完成本场标注', '比赛结束后从弹窗进入单场问卷，逐段判断并自动保存；提交后才解除下一场门禁。', CheckCircle2],
] as const;

function Expandable({ title, children }: Readonly<{ title: string; children: ReactNode }>) {
  const [open, setOpen] = useState(false);
  return <div className="border-b border-slate-200 last:border-b-0"><button type="button" className="flex min-h-14 w-full items-center justify-between gap-4 text-left text-sm font-black text-slate-950" onClick={() => setOpen((value) => !value)} aria-expanded={open}>{title}<ChevronDown className={`size-4 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" /></button>{open ? <div className="pb-5 text-sm leading-7 text-slate-600">{children}</div> : null}</div>;
}

function RoleContent({ role }: Readonly<{ role: Role }>) {
  if (role === 'admin') return <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-5 md:flex md:items-center md:justify-between md:gap-6"><div><h3 className="font-black text-slate-950">管理员从后台开始</h3><p className="mt-2 text-sm leading-6 text-slate-700">先发布已完成音频审核的 4v4 规则，再配置 Agent、排表和参赛账号。发布后，已被比赛引用的快照不会被改写。</p></div><Link className="mt-4 inline-flex shrink-0 items-center gap-2 rounded-xl bg-slate-950 px-4 py-3 text-sm font-black text-white hover:bg-slate-800 md:mt-0" href="/admin/experiments">打开管理后台 <ArrowRight className="size-4" /></Link></div>;
  if (role === 'spectator') {
    const cards: ReadonlyArray<readonly [string, string, LucideIcon]> = [['进入大厅', '打开公开房间，确认观战席仍有名额。', UsersRound], ['听见现场', '浏览器允许播放后，可听到主持、真人和 Agent 音频。', Headphones], ['查看记录', '只显示公开阶段与已完成发言，不会泄露队内决策。', LayoutList]];
    return <div className="grid gap-4 md:grid-cols-3">{cards.map(([title, text, Icon]) => <div key={title} className="rounded-2xl border border-slate-200 bg-white p-5"><Icon className="size-5 text-blue-600" aria-hidden="true" /><h3 className="mt-4 font-black text-slate-950">{title}</h3><p className="mt-2 text-sm leading-6 text-slate-600">{text}</p></div>)}</div>;
  }
  return <ol className="grid gap-3 md:grid-cols-2">{steps.map(([number, title, text, Icon]) => <li key={number} className="flex gap-4 rounded-2xl border border-slate-200 bg-white p-5"><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-slate-950 text-xs font-black text-white">{number}</span><div><div className="flex items-center gap-2"><Icon className="size-4 text-blue-600" aria-hidden="true" /><h3 className="font-black text-slate-950">{title}</h3></div><p className="mt-2 text-sm leading-6 text-slate-600">{text}</p></div></li>)}</ol>;
}

export function GuideWorkspace() {
  const [role, setRole] = useState<Role>('debater');
  const current = roles[role];
  return <div className="mx-auto max-w-[1240px]">
    <section className="rounded-[2rem] border border-blue-100 bg-white p-6 shadow-[0_24px_70px_rgba(31,71,128,0.10)] md:p-10 lg:p-12"><div className="max-w-3xl"><p className="jx-kicker">JIXIA DEBATE · QUICK GUIDE</p><h1 className="mt-3 text-3xl font-black tracking-[-0.04em] sm:text-4xl lg:text-5xl">从入场到赛后，一次看懂稷下辩论</h1><p className="mt-5 max-w-2xl text-base leading-8 text-slate-600">多人、多智能体实时语音交锋。你只需要跟随当前提示操作；比赛状态、计时和权限由服务端统一控制。</p><div className="mt-7 flex flex-wrap gap-3"><Link className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-blue-600 px-5 py-3 text-sm font-black text-white shadow-lg shadow-blue-200 hover:bg-blue-700" href="/lobby">进入比赛大厅 <ArrowRight className="size-4" /></Link><Link className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-5 py-3 text-sm font-black text-slate-800 hover:border-blue-300 hover:text-blue-700" href="/experiments">查看我的实验安排 <LayoutList className="size-4" /></Link></div></div></section>
    <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]"><div className="min-w-0 space-y-6">
      <section id="roles" className="rounded-[2rem] border border-blue-100 bg-white p-6 shadow-sm md:p-8" aria-labelledby="role-title"><div className="flex flex-wrap items-end justify-between gap-4"><div><p className="jx-kicker">CHOOSE YOUR VIEW</p><h2 id="role-title" className="mt-2 text-2xl font-black">先看与你相关的流程</h2></div><span className="text-sm text-slate-500">当前：{current.label}</span></div><div className="mt-6 grid grid-cols-3 gap-2 rounded-xl bg-slate-100 p-1" role="tablist" aria-label="角色视图">{(Object.keys(roles) as Role[]).map((item) => <button key={item} type="button" role="tab" aria-selected={role === item} onClick={() => setRole(item)} className={`min-h-11 rounded-lg px-2 text-sm font-black transition ${role === item ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-900'}`}>{roles[item].label}</button>)}</div><div className="mt-6"><p className="jx-kicker">{role === 'debater' ? 'FOR DEBATERS' : role === 'spectator' ? 'FOR SPECTATORS' : 'FOR ADMINS'}</p><h3 className="mt-2 text-xl font-black">{current.title}</h3><p className="mt-2 text-sm leading-6 text-slate-600">{current.description}</p></div><div className="mt-6"><RoleContent role={role} /></div></section>
      <section id="format" className="rounded-[2rem] border border-blue-100 bg-white p-6 shadow-sm md:p-8" aria-labelledby="format-title"><p className="jx-kicker">STANDARD 4V4 FORMAT</p><div className="mt-2 flex flex-wrap items-start justify-between gap-4"><h2 id="format-title" className="text-2xl font-black">一场比赛怎样展开</h2><span className="rounded-full bg-blue-50 px-3 py-1.5 text-xs font-black text-blue-700">正方先进入自由辩论</span></div><div className="mt-6 grid gap-3 sm:grid-cols-3"><div className="rounded-2xl bg-red-50 p-4"><p className="text-xs font-black text-red-700">正方一辩立论</p><p className="mt-2 text-2xl font-black text-red-950">90 秒</p></div><div className="rounded-2xl bg-blue-50 p-4"><p className="text-xs font-black text-blue-700">反方一辩立论</p><p className="mt-2 text-2xl font-black text-blue-950">90 秒</p></div><div className="rounded-2xl bg-slate-100 p-4"><p className="text-xs font-black text-slate-600">自由辩论</p><p className="mt-2 text-2xl font-black text-slate-950">双方各 6 分钟</p></div></div><p className="mt-4 flex items-start gap-2 text-sm leading-6 text-slate-600"><AlertCircle className="mt-1 size-4 shrink-0 text-blue-600" />这是当前正式规则的示例。每个房间使用创建时冻结的规则快照，实际阶段、时长和权限以比赛页显示为准；单次自由辩论发言最多 30 秒。</p></section>
      <section id="controls" className="rounded-[2rem] border border-blue-100 bg-white p-6 shadow-sm md:p-8" aria-labelledby="controls-title"><p className="jx-kicker">DURING THE MATCH</p><h2 id="controls-title" className="mt-2 text-2xl font-black">比赛页上的关键操作</h2><div className="mt-6 divide-y divide-slate-200"><Expandable title="开始、结束和重置发言">固定发言或获得自由辩论机会后，点击“开始发言”才会开麦并计时。可以提前结束；需要重来时使用“重置”，本次未完成内容会被丢弃。</Expandable><Expandable title="申请、取消和等待自由辩论">在申请窗口内点击“申请发言”或“取消举手”。服务端按受理顺序选择真人；Agent 只有决策成功且被选中才会发言。</Expandable><Expandable title="暂停、恢复和声音">暂停会冻结计时、音频接收和新的 Agent 调用。满足在线、设备检测和权限条件后才能恢复。声音菜单中的静音只影响当前浏览器播放。</Expandable></div></section>
      <section id="after" className="rounded-[2rem] border border-blue-100 bg-white p-6 shadow-sm md:p-8" aria-labelledby="after-title"><p className="jx-kicker">AFTER THE MATCH</p><h2 id="after-title" className="mt-2 text-2xl font-black">赛后问卷是两个独立入口</h2><div className="mt-6 grid gap-4 md:grid-cols-2"><div className="rounded-2xl border border-blue-200 bg-blue-50/60 p-5"><span className="inline-flex items-center gap-2 text-sm font-black text-blue-800"><CheckCircle2 className="size-4" />单场赛后发言问卷</span><p className="mt-3 text-sm leading-6 text-slate-700">比赛正常结束后，比赛页弹窗会带你进入本场任务。先完成当时判断，再查看实际文字；选择会自动保存，完成一段后自动进入下一段，点击气泡可修改。</p><p className="mt-3 text-xs font-bold text-blue-800">未提交前，Core 会阻止进入下一场正式比赛。</p></div><div className="rounded-2xl border border-slate-200 bg-slate-50 p-5"><span className="inline-flex items-center gap-2 text-sm font-black text-slate-800"><Volume2 className="size-4" />AI 辩论体验问卷</span><p className="mt-3 text-sm leading-6 text-slate-700">这是账号级、长期可编辑的独立问卷，不绑定某一场比赛，也不参与下一场门禁。</p><Link className="mt-4 inline-flex items-center gap-2 text-sm font-black text-blue-700" href="/me/ai-experience">打开体验问卷 <ArrowRight className="size-4" /></Link></div></div></section>
      <section id="help" className="rounded-[2rem] border border-amber-200 bg-amber-50/70 p-6 md:p-8" aria-labelledby="help-title"><div className="flex gap-4"><HelpCircle className="mt-1 size-6 shrink-0 text-amber-700" /><div><h2 id="help-title" className="text-xl font-black">遇到异常，按这个顺序处理</h2><p className="mt-3 text-sm leading-7 text-slate-700">听不到声音：检查浏览器播放权限、输出设备和页面声音菜单。麦克风无权限或网络检测失败：按页面提示重新检测。短暂断线：重新打开原房间，不要新建替代房间。ASR 或页面请求失败：使用“重试”。比赛显示暂停或无法进入：联系本场一辩或管理员，等待服务端恢复。</p></div></div></section>
    </div><aside className="hidden lg:block"><div className="sticky top-24 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center gap-2"><LayoutList className="size-4 text-blue-600" /><h2 className="font-black">快速查阅</h2></div><nav className="mt-4 grid gap-1 text-sm" aria-label="指南章节">{[['角色与入场','#roles'], ['标准赛制','#format'], ['比赛操作','#controls'], ['赛后问卷','#after'], ['异常处理','#help']].map(([label, href]) => <a key={href} className="rounded-lg px-3 py-2 text-slate-600 hover:bg-blue-50 hover:text-blue-700" href={href}>{label}</a>)}</nav><div className="mt-5 border-t border-slate-200 pt-5"><p className="text-xs leading-5 text-slate-500">需要直接开始？</p><Link className="mt-2 inline-flex items-center gap-1 text-sm font-black text-blue-700" href="/lobby">进入大厅 <ArrowRight className="size-4" /></Link></div></div></aside></div>
  </div>;
}
