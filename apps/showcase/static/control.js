import { api, send, escape as e, phases, sideNames, nameOf, toast } from './common.js';
const workspace = document.querySelector('#workspace'),
  login = document.querySelector('#login-panel');
let audioWake = null;
if (typeof BroadcastChannel === 'function') {
  try {
    audioWake = new BroadcastChannel('jx-showcase-audio');
  } catch {}
}
function wakeDisplays() {
  try {
    audioWake?.postMessage('refresh');
  } catch {}
}
let state = null,
  dirty = false,
  working = false,
  previewObserver,
  audioWorking = false;
function markDirty() {
  dirty = true;
  document.querySelector('#save-indicator').textContent = '有尚未保存的修改';
}
const options = (map, current) =>
  Object.entries(map)
    .map(
      ([v, label]) => `<option value="${v}" ${v === current ? 'selected' : ''}>${label}</option>`,
    )
    .join('');
function collect() {
  const c = structuredClone(state.content);
  workspace
    .querySelectorAll('[data-field]')
    .forEach((el) => (c[el.dataset.field] = el.type === 'checkbox' ? el.checked : el.value));
  c.participants = c.participants.map((p) => {
    const name = workspace.querySelector(`[data-name="${p.id}"]`).value.trim();
    const kind = workspace.querySelector(`[data-kind="${p.id}"]`).value;
    return {
      ...p,
      name,
      kind,
      avatar: `${kind === 'AGENT' ? 'agent' : 'human'}-0${p.id.slice(-1)}`,
    };
  });
  return c;
}
function audienceStats(c, a) {
  return `<div class="vote-stats"><div><b>${a.total}</b><span>有效投票</span></div>${Object.entries(
    a.winners,
  )
    .map(([key, n]) => `<div><b>${n}</b><span>${sideNames[key]}</span></div>`)
    .join(
      '',
    )}</div>${a.total ? `<ol class="admin-ranking">${a.ranking.map((r) => `<li><b>${r.rank}</b><span>${e(nameOf(c, r.id))}${r.tied ? ' · 同票' : ''}</span><strong>${r.points} 票</strong></li>`).join('')}</ol>` : '<p class="helper">暂无投票。大屏进入投票画面后，观众即可扫码免登录参与。</p>'}`;
}
function greetingControls() {
  return `<section class="editor-card greeting-card"><div class="card-title"><span>00</span><h2>主持开场与声音</h2></div><p class="helper">先将大屏切到“00 主持开场”，在大屏电脑点击一次“启用现场声音”。这里的按钮只控制大屏播放。</p><div class="audio-control-status" role="status"><strong id="audio-control-state"></strong><span id="audio-display-count"></span></div><div class="greeting-buttons">${(state.greetings || []).map((g) => `<div class="greeting-person"><div><strong>${e(g.name)}</strong><small>${g.id.startsWith('aff') ? '正方' : '反方'}${g.id.slice(-1)}辩${g.duration ? ` · ${Number(g.duration).toFixed(0)} 秒` : ''}</small></div><p>${e(g.text)}</p><button class="button light" data-greeting="${e(g.id)}" disabled>播放招呼</button></div>`).join('')}</div><div class="audio-control-footer"><button id="stop-greeting" class="button light" disabled>停止声音</button><button id="recover-opening" class="button light" disabled>重连候场大屏</button><p id="playback-feedback" role="status"></p></div></section>`;
}
function updateAudioControls() {
  const slot = document.querySelector('#audio-control-state');
  if (!slot || !state) return;
  const displays = state.audio_displays || [];
  const ready = displays.filter((d) => d.ready);
  const allHidden = displays.length && displays.every((d) => d.status === 'hidden');
  const command = state.playback;
  const matching = command ? displays.filter((d) => d.command_id === command.id) : [];
  const intro = state.published_content?.phase === 'intro';
  let message = !intro
    ? '切到 00 主持开场后可播放'
    : !displays.length
      ? '大屏未连接'
      : allHidden
        ? '大屏已连接，尚未启用现场声音'
        : !ready.length
          ? '大屏已连接，等待启用现场声音'
          : '大屏声音已就绪';
  if (intro && matching.some((d) => d.status === 'error'))
    message = '大屏播放失败，请检查现场声音后重试';
  else if (intro && matching.some((d) => d.status === 'playing'))
    message = `${state.greetings?.find((g) => g.id === command?.participant_id)?.name || '辩手'}正在打招呼`;
  else if (intro && matching.length && matching.every((d) => d.status === 'ended'))
    message = '播放结束';
  else if (
    intro &&
    command?.action === 'play' &&
    matching.length &&
    matching.every((d) => d.status === 'idle')
  )
    message = '指令已发送，等待大屏播放';
  else if (
    intro &&
    command?.action === 'stop' &&
    matching.length &&
    !allHidden &&
    matching.every((d) => d.status !== 'playing')
  )
    message = '声音已停止';
  slot.textContent = message;
  document.querySelector('#audio-display-count').textContent = displays.length
    ? `${displays.length} 个大屏连接 · ${ready.length} 个声音就绪`
    : '请在现场电脑打开大屏';
  document.querySelectorAll('[data-greeting]').forEach((button) => {
    const greeting = state.greetings?.find((g) => g.id === button.dataset.greeting);
    button.disabled = working || audioWorking || !intro || !greeting?.available;
  });
  document.querySelector('#recover-opening').disabled = working || audioWorking || !intro;
  document.querySelector('#stop-greeting').disabled =
    working || audioWorking || !intro || !displays.length;
}
async function recoverOpening() {
  if (working || audioWorking || state?.published_content?.phase !== 'intro') return;
  audioWorking = true;
  updateAudioControls();
  const feedback = document.querySelector('#playback-feedback');
  feedback.textContent = '正在请求候场大屏重连…';
  try {
    const scope = { round_id: state.round_id, display_epoch: state.display_epoch };
    await send('/api/admin/audio-recover', scope);
    const result = await send('/api/admin/audio-probe', scope);
    state.audio_displays = result.audio_displays;
    state.playback = result.playback;
    feedback.textContent = result.probe_ready
      ? '候场大屏声音已重新就绪，请点击需要播放的辩手。'
      : '重连请求已发出，尚未收到声音就绪回报。大屏恢复网络后会自动处理；未启音时仍需现场点击启音。';
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    audioWorking = false;
    updateAudioControls();
  }
}
async function playGreeting(participantId) {
  if (audioWorking || working) return;
  audioWorking = true;
  const feedback = document.querySelector('#playback-feedback');
  feedback.textContent = participantId ? '正在确认大屏声音…' : '正在停止声音…';
  updateAudioControls();
  try {
    const scope = { round_id: state.round_id, display_epoch: state.display_epoch };
    wakeDisplays();
    let fresh = participantId
      ? await send('/api/admin/audio-probe', scope)
      : await api('/api/admin');
    if (participantId && !fresh.probe_ready) {
      feedback.textContent = '正在请求候场大屏重连，再确认一次…';
      await send('/api/admin/audio-recover', scope);
      fresh = await send('/api/admin/audio-probe', scope);
    }
    if (fresh.round_id !== scope.round_id || fresh.display_epoch !== scope.display_epoch)
      throw new Error('现场已切换轮次或画面，请重新载入控制台。');
    if (participantId && !fresh.probe_ready) {
      state.audio_displays = fresh.audio_displays;
      state.playback = fresh.playback;
      const hidden = fresh.audio_displays.some((display) => display.status === 'hidden');
      throw new Error(
        hidden
          ? '大屏声音尚未启用或被浏览器暂停。请在大屏点击一次“启用现场声音”，之后可切回控制页播放。'
          : fresh.audio_displays.length
            ? '大屏已连接，但声音尚未启用或被浏览器暂停。请在大屏点击“启用现场声音”，再回来播放。'
            : '未收到大屏实时响应，可能已断网、冻结或休眠。请恢复大屏连接；旧页面需刷新后启用声音。',
      );
    }
    const response = await send('/api/admin/playback', {
      round_id: scope.round_id,
      display_epoch: scope.display_epoch,
      request_id: crypto.randomUUID().replaceAll('-', ''),
      action: participantId ? 'play' : 'stop',
      participant_id: participantId || '',
      probe_no: participantId ? fresh.probe_no : 0,
    });
    wakeDisplays();
    state.audio_displays = response.audio_displays;
    state.playback = response.playback;
    if (response.playback?.display_ids?.length) {
      feedback.textContent = participantId ? '正在等待大屏确认播放…' : '正在等待大屏确认停止…';
      const confirmed = await send('/api/admin/audio-confirm', {
        ...scope,
        command_id: response.command_id,
      });
      state.audio_displays = confirmed.audio_displays;
      state.playback = confirmed.playback;
      if (confirmed.superseded) throw new Error('已有更新的播放操作，本次指令已替换。');
      if (!confirmed.acknowledged)
        throw new Error('大屏未确认执行，已取消本次指令。请恢复大屏连接后重试。');
      feedback.textContent = `${confirmed.confirmed}/${confirmed.targets} 个大屏已确认${participantId ? '播放' : '停止'}。`;
    } else {
      feedback.textContent = '停止指令已发送，当前没有连接的大屏。';
    }
  } catch (error) {
    feedback.textContent = error.message;
    toast(error.message);
  } finally {
    audioWorking = false;
    updateAudioControls();
  }
}
function render() {
  previewObserver?.disconnect();
  const c = state.content,
    pub = state.published_content,
    a = state.audience;
  workspace.innerHTML = `<div class="workspace-top"><div><span class="eyebrow">SHOW CONTROL</span><h1>现场控制</h1><p>当前大屏：<b>${pub ? phases[pub.phase] + (pub.phase === 'results' ? ' · ' + (pub.result_view === 'audience' ? '大众评选' : '评委评选') : '') : '尚未发布'}</b><span class="status-dot"></span> ${a.total} 份有效投票</p></div><span id="save-indicator">内容已同步 · 版本 ${state.revision}</span></div><div class="phase-buttons">${Object.entries(
    phases,
  )
    .map(
      ([key, label], i) =>
        `<button class="phase-button ${c.phase === key ? 'selected' : ''}" data-phase="${key}"><span>0${i}</span><strong>${label}</strong><small>保存并切换大屏</small></button>`,
    )
    .join(
      '',
    )}</div><div class="control-grid"><div class="editor-column">${greetingControls()}<section class="editor-card"><div class="card-title"><span>01</span><h2>现场信息</h2></div><label>会议名称<input data-field="title" value="${e(c.title)}" maxlength="80" required></label><label>本场辩题<textarea data-field="topic" rows="2" maxlength="180" ${a.total ? 'disabled' : ''}>${e(c.topic)}</textarea></label><div class="two-cols"><label>正方主张<input data-field="affirmative" value="${e(c.affirmative)}" maxlength="140" ${a.total ? 'disabled' : ''}></label><label>反方主张<input data-field="negative" value="${e(c.negative)}" maxlength="140" ${a.total ? 'disabled' : ''}></label></div><div class="roster-editor">${[
    'aff',
    'neg',
  ]
    .map(
      (side) =>
        `<div><h3 class="${side}-text">${side === 'aff' ? '正方阵容' : '反方阵容'}</h3>${c.participants
          .filter((p) => p.id.startsWith(side))
          .map(
            (p) =>
              `<div class="person-input"><span>${p.id.slice(-1)} 辩</span><input aria-label="${side === 'aff' ? '正' : '反'}方${p.id.slice(-1)}辩姓名" data-name="${p.id}" value="${e(p.name)}" maxlength="30" ${a.total ? 'disabled' : ''}><select aria-label="${side === 'aff' ? '正' : '反'}方${p.id.slice(-1)}辩类型" data-kind="${p.id}" ${a.total ? 'disabled' : ''}>${options({ HUMAN: '真人', AGENT: '智能体' }, p.kind)}</select></div>`,
          )
          .join('')}</div>`,
    )
    .join(
      '',
    )}</div>${a.total ? '<p class="helper">本轮已有投票，辩题与名单已锁定。更换比赛请新建轮次。</p>' : ''}</section><section class="editor-card"><div class="card-title"><span>02</span><h2>展示阶段与评委结果</h2></div><div class="two-cols"><label>展示阶段<select data-field="phase">${options(Object.fromEntries(Object.entries(phases).map(([key, label], i) => [key, `0${i} ${label}`])), c.phase)}</select></label><label>结果画面<select data-field="result_view">${options({ audience: '大众评选结果', judge: '评委获胜方 / 最佳辩手' }, c.result_view)}</select></label></div><div class="automatic-note"><strong id="collection-state">${state.collecting ? '正在收集投票' : '当前未收集投票'}</strong><p>大屏进入观众投票画面自动开始，切走或关闭后暂停；切回来继续累计。后台预览不计作实际大屏。</p></div><div class="result-shortcuts"><button class="button light" data-result="judge">展示评委评选结果 →</button><button class="button light" data-result="audience">展示大众评选结果 →</button></div><div class="two-cols"><label>评委选出的获胜方<select data-field="winner">${options({ '': '尚未公布', ...sideNames }, c.winner)}</select></label><label>最佳辩手<select data-field="best">${options({ '': '尚未公布', ...Object.fromEntries(c.participants.map((p) => [p.id, `${p.name} · ${p.id.startsWith('aff') ? '正' : '反'}方${p.id.slice(-1)}辩`])) }, c.best)}</select></label></div><p class="helper">点评阶段展示现场评议画面，无需录入点评。评委结果由上方两项填写；大众评选结果自动汇总。</p></section><section class="editor-card"><div class="card-title"><span>03</span><h2>观众选择</h2></div><div id="audience-stats">${audienceStats(c, a)}</div><a class="text-link" href="/?mode=vote" target="_blank" rel="noopener">打开观众投票入口 ↗</a><p class="helper">获胜方正反二选一，最佳辩手八选一；每人每项一票，同票并列。本浏览器每轮限投一次。</p></section></div><aside class="preview-column"><section class="preview-card"><div class="preview-label"><span class="status-dot"></span> 当前公开画面 <a href="/" target="_blank" rel="noopener">全屏打开 ↗</a></div><div class="preview-frame"><iframe src="/" title="当前大屏预览" tabindex="-1"></iframe></div><p>这里显示的是已发布内容，草稿不会出现在大屏上。</p></section><section class="publish-card"><h2>发布到现场</h2><p>先保存草稿，或将当前全部内容发布到大屏。</p><button id="publish" class="button primary">保存并发布到大屏 →</button><button id="save" class="button light">仅保存草稿</button><div class="secondary-actions"><button id="withdraw">撤回公开展示</button><button id="reload">重新载入</button></div><p id="operation-error" role="alert"></p></section><section class="round-card"><h3>新建轮次</h3><p>新建轮次将关闭当前投票并清空展示结果，已有投票保留归档。</p><button id="new-round" class="button light">新建轮次</button></section></aside></div>`;
  workspace
    .querySelectorAll('input,select,textarea')
    .forEach((el) => el.addEventListener('input', markDirty));
  workspace
    .querySelectorAll('[data-phase]')
    .forEach((el) => el.addEventListener('click', () => act('publish', el.dataset.phase)));
  workspace
    .querySelectorAll('[data-result]')
    .forEach((el) =>
      el.addEventListener('click', () => act('publish', 'results', el.dataset.result)),
    );
  workspace
    .querySelectorAll('[data-greeting]')
    .forEach((button) =>
      button.addEventListener('click', () => playGreeting(button.dataset.greeting)),
    );
  document.querySelector('#stop-greeting').onclick = () => playGreeting(null);
  document.querySelector('#recover-opening').onclick = recoverOpening;
  updateAudioControls();
  document.querySelector('#save').onclick = () => act('save');
  document.querySelector('#publish').onclick = () => act('publish');
  document.querySelector('#withdraw').onclick = () => act('withdraw');
  document.querySelector('#reload').onclick = () => {
    if (!dirty || confirm('重新载入会放弃尚未保存的修改，确定继续？')) load();
  };
  document.querySelector('#new-round').onclick = () => {
    if (confirm('确定开始新一轮？现有投票将保留归档，大屏会回到待发布状态。')) act('new-round');
  };
  const frame = document.querySelector('.preview-frame');
  previewObserver = new ResizeObserver((entries) => {
    const w = entries[0].contentRect.width;
    frame.querySelector('iframe').style.transform = `scale(${w / 1600})`;
  });
  previewObserver.observe(frame);
}
async function act(action, phase, resultView) {
  if (working) return;
  working = true;
  const c = collect();
  if (phase) c.phase = phase;
  if (resultView) c.result_view = resultView;
  workspace.querySelectorAll('button').forEach((el) => (el.disabled = true));
  try {
    if (action === 'save' || action === 'publish')
      state = await send('/api/admin/draft', { revision: state.revision, content: c }, 'PUT');
    if (action !== 'save') state = await send('/api/admin/' + action, { revision: state.revision });
    dirty = false;
    render();
    toast(
      action === 'save'
        ? '草稿已保存，公开画面未变。'
        : action === 'publish'
          ? '大屏已更新。'
          : action === 'withdraw'
            ? '展示已撤回。'
            : '新一轮已准备，历史投票已保留。',
    );
  } catch (err) {
    if (err.status === 401) {
      login.hidden = false;
      workspace.hidden = true;
      document.querySelector('#logout').hidden = true;
    } else {
      document.querySelector('#operation-error').textContent = err.message;
    }
    toast(err.message);
  } finally {
    working = false;
    workspace.querySelectorAll('button').forEach((el) => (el.disabled = false));
    updateAudioControls();
  }
}
async function load() {
  try {
    state = await api('/api/admin');
    dirty = false;
    login.hidden = true;
    workspace.hidden = false;
    document.querySelector('#logout').hidden = false;
    render();
  } catch (err) {
    if (err.status === 401) {
      login.hidden = false;
      workspace.hidden = true;
    } else toast('读取失败，请检查网络后重试。');
  }
}
document.querySelector('#login-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const btn = ev.target.querySelector('button');
  btn.disabled = true;
  try {
    await send('/api/login', {
      password: document.querySelector('#password').value,
    });
    document.querySelector('#password').value = '';
    document.querySelector('#login-error').textContent = '';
    await load();
  } catch (err) {
    document.querySelector('#login-error').textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});
document.querySelector('#logout').onclick = async () => {
  if (dirty && !confirm('还有未保存的修改，确定退出？')) return;
  try {
    await send('/api/logout', {});
    workspace.hidden = true;
    login.hidden = false;
    document.querySelector('#logout').hidden = true;
    dirty = false;
  } catch (err) {
    toast(err.message);
  }
};
window.addEventListener('beforeunload', (ev) => {
  if (dirty) {
    ev.preventDefault();
    ev.returnValue = '';
  }
});
await load();
setInterval(async () => {
  if (!state || workspace.hidden || working) return;
  try {
    const next = await api('/api/admin');
    state.audio_displays = next.audio_displays;
    state.playback = next.playback;
    state.greetings = next.greetings;
    if (next.round_id === state.round_id && next.revision === state.revision) {
      state.published_content = next.published_content;
      state.display_epoch = next.display_epoch;
    }
    updateAudioControls();
    document.querySelector('#collection-state').textContent = next.collecting
      ? '正在收集投票'
      : '当前未收集投票';
    if (next.revision !== state.revision) {
      document.querySelector('#save-indicator').textContent = '其他控制台已更新，请重新载入';
    } else {
      if (next.audience.total !== state.audience.total) {
        state.audience = next.audience;
        document.querySelector('#audience-stats').innerHTML = audienceStats(
          state.content,
          next.audience,
        );
      }
      const label = workspace.querySelector('.workspace-top p');
      if (label) label.lastChild.textContent = ` ${next.audience.total} 份有效投票`;
    }
  } catch {}
}, 1200);
