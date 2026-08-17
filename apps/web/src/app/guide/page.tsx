import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowRight, CheckCircle2, Clock3, Hand, Headphones, PauseCircle } from 'lucide-react';

export const metadata: Metadata = { title: '使用指南', description: '用一页了解稷下 4v4 辩论。' };

const stages = [
  ['正方一辩立论', '3:00', '正方一辩'],
  ['反方一辩立论', '3:00', '反方一辩'],
  ['正方二辩陈词', '1:30', '正方二辩'],
  ['反方二辩陈词', '1:30', '反方二辩'],
  ['正方三辩陈词', '1:30', '正方三辩'],
  ['反方三辩陈词', '1:30', '反方三辩'],
  ['自由辩论', '双方 3:00', '每次最多 30 秒'],
  ['反方四辩总结', '3:00', '反方四辩'],
  ['正方四辩总结', '3:00', '正方四辩'],
] as const;

export default function GuidePage() {
  return (
    <main className="jx-page-viewport bg-[#f7faff] px-6 py-10 text-slate-950 xl:px-10">
      <div className="mx-auto max-w-[1200px]">
        <section className="rounded-[2rem] border border-blue-100 bg-white p-8 shadow-[0_24px_70px_rgba(31,71,128,0.10)] md:p-12">
          <p className="jx-kicker">QUICK START</p>
          <h1 className="mt-3 max-w-2xl text-4xl font-black tracking-[-0.05em] md:text-5xl">
            一页掌握一场 4v4 辩论
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-8 text-slate-600">
            从进入房间到赛后归档，跟着页面提示完成每一步。比赛中的所有计时和权限由服务端统一控制。
          </p>
          <div className="mt-8 grid gap-3 md:grid-cols-4">
            {[
              ['01', '加入房间', '输入房间号或打开邀请链接'],
              ['02', '选身份与席位', '选择辩手或观众，再选席位'],
              ['03', '检测并准备', '一键检查麦克风、扬声器和连接'],
              ['04', '开始辩论', '轮到你时手动开启麦克风'],
            ].map(([number, title, description]) => (
              <div key={number} className="rounded-2xl border border-blue-100 bg-[#f6f9fe] p-4">
                <span className="text-xs font-black tracking-[0.18em] text-blue-600">{number}</span>
                <h2 className="mt-4 text-base font-black">{title}</h2>
                <p className="mt-2 text-sm leading-6 text-slate-500">{description}</p>
              </div>
            ))}
          </div>
        </section>

        <section
          className="mt-6 rounded-[2rem] border border-blue-100 bg-white p-8 shadow-sm md:p-10"
          aria-labelledby="stages-title"
        >
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="jx-kicker">4V4 FORMAT</p>
              <h2 id="stages-title" className="mt-2 text-2xl font-black">
                比赛流程
              </h2>
            </div>
            <span className="rounded-full border border-lime-200 bg-lime-50 px-3 py-1.5 text-xs font-bold text-lime-800">
              线性阶段 · 9 步完成
            </span>
          </div>
          <ol className="mt-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {stages.map(([title, duration, speaker], index) => (
              <li
                key={title}
                className="flex min-h-28 gap-4 rounded-2xl border border-slate-100 bg-slate-50/70 p-4"
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-slate-950 text-xs font-black text-white">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span>
                  <strong className="block text-sm font-black">{title}</strong>
                  <span className="mt-2 flex items-center gap-1 text-xs font-bold text-blue-700">
                    <Clock3 className="size-3.5" />
                    {duration}
                  </span>
                  <small className="mt-1 block text-xs text-slate-500">{speaker}</small>
                </span>
              </li>
            ))}
          </ol>
        </section>

        <section className="mt-6 grid gap-6 md:grid-cols-2">
          <div className="rounded-[2rem] border border-blue-100 bg-white p-8 shadow-sm">
            <p className="jx-kicker">IN THE MATCH</p>
            <h2 className="mt-2 text-2xl font-black">比赛中怎么操作</h2>
            <ul className="mt-6 grid gap-4 text-sm leading-6 text-slate-600">
              <li className="flex gap-3">
                <Headphones className="mt-0.5 size-5 shrink-0 text-blue-600" />
                <span>
                  <strong className="text-slate-950">固定发言</strong>
                  ：主持音频结束后，点击“开始发言”才会打开麦克风并开始计时。
                </span>
              </li>
              <li className="flex gap-3">
                <Hand className="mt-0.5 size-5 shrink-0 text-lime-600" />
                <span>
                  <strong className="text-slate-950">自由辩论</strong>
                  ：对方发言结束后的窗口内可以举手，申请顺序会显示在头像旁。
                </span>
              </li>
              <li className="flex gap-3">
                <PauseCircle className="mt-0.5 size-5 shrink-0 text-amber-600" />
                <span>
                  <strong className="text-slate-950">暂停与恢复</strong>：暂停会冻结计时、识别和
                  Agent 调用；满足条件后由有权限者恢复。
                </span>
              </li>
            </ul>
          </div>
          <div className="rounded-[2rem] border border-blue-100 bg-white p-8 shadow-sm">
            <p className="jx-kicker">REMEMBER</p>
            <h2 className="mt-2 text-2xl font-black">遇到问题时</h2>
            <ul className="mt-6 grid gap-4 text-sm leading-6 text-slate-600">
              <li className="flex gap-3">
                <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-lime-600" />
                <span>掉线后重新打开原房间即可恢复；短暂离线不会要求重新检测。</span>
              </li>
              <li className="flex gap-3">
                <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-lime-600" />
                <span>听不到声音时先检查浏览器播放权限和设备状态，再使用页面内重试。</span>
              </li>
              <li className="flex gap-3">
                <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-lime-600" />
                <span>比赛结束后可审阅、修改并提交自己的最终文字。</span>
              </li>
            </ul>
            <Link
              className="mt-7 inline-flex min-h-11 items-center gap-2 rounded-xl bg-blue-600 px-5 py-3 text-sm font-black text-white shadow-lg shadow-blue-200 transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-200"
              href="/lobby"
            >
              进入比赛大厅 <ArrowRight className="size-4" />
            </Link>
          </div>
        </section>
      </div>
    </main>
  );
}
