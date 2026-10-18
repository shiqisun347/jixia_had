import { avatarUrl } from './identities.js';
import { api, send, escape as e, phases, sideNames, brand, toast } from './common.js';
const app = document.querySelector('#app');
const voteMode = new URLSearchParams(location.search).get('mode') === 'vote';
const actualDisplay = !voteMode && window.self === window.top;
let audioWake = null;
let audioWakePending = false;
let screenEvents = null;
let lastScreenEvent = 0;
let audioProbeNo = 0;
let audioUnlocked = false;
let audioRecoveryPending = false;
let openingRepairPending = false;
let lastOpeningRepair = -Infinity;
let repairDelay = 15000;
let lastStateSuccess = performance.now();
let lastServerRecovery = '';
let stateRequest = null;
let stateRequestStarted = 0;
if (actualDisplay && typeof BroadcastChannel === 'function') {
  try {
    audioWake = new BroadcastChannel('jx-showcase-audio');
  } catch {}
}
const displayId = crypto.randomUUID().replaceAll('-', '');
const displayKey =
  crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
let pageClosing = false,
  latest = null,
  signature = '',
  selectedBest = '',
  winner = '',
  submitted = false,
  busy = false;
let displaySequence = 0,
  displayPending = false,
  lastDisplay = null;
let audioDisplayId = crypto.randomUUID().replaceAll('-', '');
let audioSequence = 0,
  audioPending = null,
  audioScope = null,
  audioContext = null;
let buffers = new Map(),
  source = null,
  lastCommand = 0,
  audioNeedsBaseline = true;
let audioStatus = 'blocked',
  currentCommand = 0,
  audioPreparing = false,
  playingParticipant = '';
const seatNumber = (id) => ['一', '二', '三', '四'][Number(id.slice(-1)) - 1];
const isOpening = (state) =>
  actualDisplay && !pageClosing && state?.published && state.content?.phase === 'intro';
const isIntro = isOpening;
function beacon(path, body) {
  navigator.sendBeacon(path, new Blob([JSON.stringify(body)], { type: 'application/json' }));
}
function releaseDisplay() {
  if (!actualDisplay || !lastDisplay) return;
  beacon('/api/display', {
    ...lastDisplay,
    display_id: displayId,
    sequence: ++displaySequence,
    active: false,
  });
  lastDisplay = null;
}
async function maintainDisplay(state) {
  if (
    !actualDisplay ||
    document.visibilityState !== 'visible' ||
    !state.published ||
    state.content.phase !== 'vote'
  ) {
    releaseDisplay();
    return;
  }
  if (displayPending) return;
  displayPending = true;
  const body = {
    round_id: state.round_id,
    display_epoch: state.display_epoch,
    display_id: displayId,
    sequence: ++displaySequence,
    active: true,
  };
  lastDisplay = { round_id: state.round_id, display_epoch: state.display_epoch };
  try {
    const result = await send('/api/display', body);
    state.collecting = result.collecting;
    state.active_displays = result.active_displays;
    state.content.voting_open = result.collecting;
  } catch {
  } finally {
    displayPending = false;
  }
}
function audioReady() {
  return buffers.size === 4 && audioContext?.state === 'running';
}
function stopSound(status = 'idle') {
  const previous = source;
  source = null;
  if (previous) {
    previous.onended = null;
    try {
      previous.stop();
      previous.disconnect();
    } catch {}
  }
  playingParticipant = '';
  audioStatus = status;
  updateAudioUI();
}
function connectionScopeCurrent(state) {
  return (
    isOpening(state) &&
    audioScope?.round_id === state.round_id &&
    audioScope?.display_epoch === state.display_epoch &&
    latest?.round_id === state.round_id &&
    latest?.display_epoch === state.display_epoch
  );
}
function scopeCurrent(state) {
  return isIntro(state) && connectionScopeCurrent(state);
}
function releaseAudio() {
  audioNeedsBaseline = true;
  stopSound(audioReady() ? 'idle' : 'blocked');
  if (!audioScope || !actualDisplay) return;
  beacon('/api/audio-display', {
    ...audioScope,
    display_key: displayKey,
    display_id: audioDisplayId,
    sequence: ++audioSequence,
    active: false,
    ready: false,
    status: audioStatus,
    command_id: currentCommand,
  });
  audioScope = null;
  audioDisplayId = crypto.randomUUID().replaceAll('-', '');
}
function updateAudioUI() {
  const slot = document.querySelector('#audio-tools');
  if (!slot) return;
  const ready = audioReady();
  const labels = {
    idle: '现场声音已就绪',
    playing: `${latest?.greetings?.find((g) => g.id === playingParticipant)?.name || '辩手'}正在打招呼`,
    ended: '播放结束',
    blocked: '请先启用现场声音',
    error: '音频未就绪，请重试',
  };
  if (!slot.querySelector('.audio-status')) {
    slot.innerHTML =
      '<span class="audio-status" role="status"></span><button id="enable-audio" class="button primary">启用现场声音</button><span class="audio-ready" hidden>声音已启用</span>';
    slot.querySelector('#enable-audio').addEventListener('click', enableAudio);
  }
  slot.querySelector('.audio-status').textContent = audioPreparing
    ? '正在准备音频…'
    : labels[audioStatus] || labels.idle;
  const enableButton = slot.querySelector('#enable-audio');
  enableButton.hidden = ready;
  enableButton.disabled = audioPreparing;
  enableButton.textContent = audioPreparing ? '正在准备…' : '启用现场声音';
  slot.querySelector('.audio-ready').hidden = !ready;
  document
    .querySelectorAll('.speaker-card')
    .forEach((el) =>
      el.classList.toggle('is-speaking', el.dataset.participant === playingParticipant),
    );
}
async function enableAudio(mode) {
  if (audioPreparing || !actualDisplay || !isIntro(latest)) return;
  if (mode === 'recovery' && audioContext?.state !== 'running') {
    // Never await a new gesture from an automatic operation: keep the local
    // enable button usable when the browser cannot resume without interaction.
    recoverAudio();
    audioStatus = 'blocked';
    updateAudioUI();
    return;
  }
  audioPreparing = true;
  try {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) throw Error('unsupported');
    if (!audioContext || audioContext.state === 'closed') {
      audioContext = new Context({ latencyHint: 'playback' });
      buffers.clear();
    }
    // resume is invoked within the display computer's explicit click gesture.
    await audioContext.resume();
    audioUnlocked = audioContext.state === 'running';
    audioContext.onstatechange = () => {
      if (audioContext.state !== 'running') {
        releaseAudio();
        audioStatus = 'blocked';
        updateAudioUI();
      } else {
        audioRecoveryPending = false;
        if (isOpening(latest)) {
          if (audioUnlocked && buffers.size !== 4 && !audioPreparing) void enableAudio('recovery');
          else void maintainAudio(latest);
        }
      }
      recoverAudio();
      updateAudioUI();
    };
    updateAudioUI();
    const greetings = latest.greetings || [];
    if (greetings.length !== 4 || greetings.some((g) => !g.available)) throw Error('unavailable');
    const decoded = await Promise.all(
      greetings.map(async (g) => {
        if (buffers.has(g.id)) return [g.id, buffers.get(g.id)];
        const response = await fetch(g.url, {
          credentials: 'same-origin',
          cache: 'reload',
          signal: AbortSignal.timeout(12000),
        });
        if (!response.ok) throw Error('download');
        const buffer = await audioContext.decodeAudioData(await response.arrayBuffer());
        if (!(buffer.duration > 0)) throw Error('empty');
        return [g.id, buffer];
      }),
    );
    buffers = new Map(decoded);
    if (!audioReady()) throw Error('blocked');
    audioStatus = 'idle';
    if (latest) await maintainAudio(latest);
  } catch {
    audioStatus = 'error';
    toast('声音尚未准备好。请检查大屏电脑的网络与声音设置，再点击重试。');
  } finally {
    audioPreparing = false;
    updateAudioUI();
  }
}
function recoverAudio() {
  if (
    !audioUnlocked ||
    !isOpening(latest) ||
    !navigator.onLine ||
    audioRecoveryPending ||
    !audioContext ||
    audioContext.state === 'running' ||
    audioContext.state === 'closed'
  )
    return;
  const recovering = audioContext;
  audioRecoveryPending = true;
  // A previously granted audio context may be resumed without a new page visit.
  // A browser that requires another gesture keeps the page visibly blocked.
  recovering
    .resume()
    .then(() => {
      if (audioContext === recovering && recovering.state === 'running' && isOpening(latest)) {
        if (!source) audioStatus = 'idle';
        void maintainAudio(latest);
      }
    })
    .catch(() => {})
    .finally(() => {
      audioRecoveryPending = false;
      updateAudioUI();
    });
}
async function repairOpening({ resources = false, remote = false } = {}) {
  if (
    !isOpening(latest) ||
    !navigator.onLine ||
    openingRepairPending ||
    (!remote && (source || performance.now() - lastOpeningRepair < repairDelay))
  )
    return;
  openingRepairPending = true;
  lastOpeningRepair = performance.now();
  repairDelay = Math.min(60000, repairDelay * 2);
  try {
    // Retain the locally unlocked context: navigation can lose its audio grant.
    closeScreenEvents();
    startScreenEvents();
    if (stateRequest && performance.now() - stateRequestStarted > 12000) stateRequest.abort();
    if (resources) {
      releaseAudio();
      buffers.clear();
      if (audioUnlocked) await enableAudio('recovery');
    } else {
      recoverAudio();
    }
    wakeScreen();
  } finally {
    openingRepairPending = false;
  }
}
function checkOpeningHealth() {
  if (!isOpening(latest) || !navigator.onLine || source) return;
  const now = performance.now();
  const transportStale = now - lastScreenEvent > 12000 || now - lastStateSuccess > 12000;
  const audioFailed =
    audioUnlocked &&
    (audioStatus === 'error' ||
      (audioContext?.state === 'running' && buffers.size !== 4 && !audioPreparing));
  if (transportStale || audioFailed) void repairOpening({ resources: audioFailed });
}
function wakeScreen() {
  if (!actualDisplay || pageClosing || !navigator.onLine) return;
  recoverAudio();
  if (poll.running) audioWakePending = true;
  else void refreshState();
}
function startScreenEvents() {
  if (
    !actualDisplay ||
    pageClosing ||
    !navigator.onLine ||
    screenEvents ||
    typeof EventSource !== 'function'
  )
    return;
  screenEvents = new EventSource('/api/screen-events');
  screenEvents.addEventListener('refresh', (event) => {
    try {
      const data = JSON.parse(event.data);
      if (Number.isSafeInteger(data.probe_no) && data.probe_no >= 0) audioProbeNo = data.probe_no;
    } catch {}
    lastScreenEvent = performance.now();
    checkOpeningHealth();
    wakeScreen();
  });
  screenEvents.onopen = wakeScreen;
}
function closeScreenEvents() {
  screenEvents?.close();
  screenEvents = null;
  lastScreenEvent = 0;
}
function processCommand(command, state) {
  if (!command || command.id <= lastCommand) return;
  lastCommand = command.id;
  if (
    !scopeCurrent(state) ||
    command.round_id !== state.round_id ||
    command.display_epoch !== state.display_epoch ||
    !command.display_ids?.includes(audioDisplayId)
  )
    return;
  currentCommand = command.id;
  stopSound(audioReady() ? 'idle' : 'blocked');
  if (command.action === 'stop') {
    void reportAudio(state);
    return;
  }
  const buffer = buffers.get(command.participant_id);
  if (!audioReady() || !buffer) {
    audioStatus = 'blocked';
    updateAudioUI();
    void reportAudio(state);
    return;
  }
  try {
    const next = audioContext.createBufferSource();
    next.buffer = buffer;
    next.connect(audioContext.destination);
    next.onended = () => {
      if (source !== next) return;
      source = null;
      next.disconnect();
      playingParticipant = '';
      audioStatus = 'ended';
      updateAudioUI();
      void reportAudio(state);
    };
    source = next;
    playingParticipant = command.participant_id;
    next.start();
    audioStatus = 'playing';
    updateAudioUI();
    void reportAudio(state);
  } catch {
    stopSound('error');
    void reportAudio(state);
  }
}
async function reportAudio(state) {
  if (!connectionScopeCurrent(state)) return null;
  const reportingDisplayId = audioDisplayId;
  const hidden = document.visibilityState !== 'visible';
  const started = performance.now();
  try {
    const response = await send('/api/audio-display', {
      ...audioScope,
      display_key: displayKey,
      display_id: audioDisplayId,
      sequence: ++audioSequence,
      active: true,
      ready: audioReady(),
      probe_no: audioProbeNo,
      status: hidden && !audioReady() ? 'hidden' : audioStatus,
      command_id: currentCommand,
    });
    if (
      response.playback &&
      (response.playback.valid_for_seconds ?? 0) * 1000 <= performance.now() - started
    )
      response.playback = null;
    return response;
  } catch {
    if (connectionScopeCurrent(state) && audioDisplayId === reportingDisplayId) {
      releaseAudio();
      audioStatus = 'error';
      updateAudioUI();
    }
    return null;
  }
}
async function maintainAudio(state) {
  if (!isOpening(state) || !navigator.onLine) {
    releaseAudio();
    return;
  }
  recoverAudio();
  const scope = { round_id: state.round_id, display_epoch: state.display_epoch };
  if (
    audioScope &&
    (audioScope.round_id !== scope.round_id || audioScope.display_epoch !== scope.display_epoch)
  ) {
    releaseAudio();
  }
  audioScope = scope;
  if (audioNeedsBaseline) {
    lastCommand = Math.max(lastCommand, state.playback?.id || 0);
    audioNeedsBaseline = false;
  }
  if (audioPending === audioDisplayId) return;
  const reportingDisplayId = audioDisplayId;
  audioPending = reportingDisplayId;
  try {
    const result = await reportAudio(state);
    if (
      audioDisplayId === reportingDisplayId &&
      isIntro(state) &&
      audioScope?.display_epoch === scope.display_epoch &&
      latest?.display_epoch === state.display_epoch
    )
      processCommand(result?.playback, state);
  } catch {
    if (audioDisplayId === reportingDisplayId) stopSound('error');
  } finally {
    if (audioPending === reportingDisplayId) audioPending = null;
  }
}
window.addEventListener('pagehide', () => {
  pageClosing = true;
  closeScreenEvents();
  releaseDisplay();
  releaseAudio();
});
window.addEventListener('pageshow', () => {
  pageClosing = false;
  startScreenEvents();
  wakeScreen();
});
window.addEventListener('offline', () => {
  closeScreenEvents();
  releaseDisplay();
  releaseAudio();
});
window.addEventListener('online', () => {
  startScreenEvents();
  wakeScreen();
  checkOpeningHealth();
});
document.addEventListener('freeze', () => {
  closeScreenEvents();
  releaseDisplay();
  releaseAudio();
});
document.addEventListener('resume', () => {
  startScreenEvents();
  wakeScreen();
  checkOpeningHealth();
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') {
    releaseDisplay();
    if (latest) void maintainAudio(latest);
  } else if (latest) {
    void maintainDisplay(latest);
    void maintainAudio(latest);
  }
});
function seats(c, side) {
  return `<aside class="team ${side}"><div class="team-heading"><span class="side-tag">${side === 'aff' ? '正方' : '反方'}</span><span>TEAM ${side === 'aff' ? 'AFFIRMATIVE' : 'NEGATIVE'}</span></div><h2 class="team-position">${e(side === 'aff' ? c.affirmative : c.negative)}</h2><div class="seat-stack">${c.participants
    .filter((p) => p.id.startsWith(side))
    .map(
      (p) =>
        `<div class="seat speaker-card" data-participant="${e(p.id)}"><img src="${e(avatarUrl(p))}" alt=""><div><small>${side === 'aff' ? '正方' : '反方'} · ${seatNumber(p.id)}辩</small><h3>${e(p.name)}</h3><span class="kind ${p.kind === 'AGENT' ? 'agent' : ''}">${p.kind === 'AGENT' ? '✧ 智能体' : '● 真人辩手'}</span></div></div>`,
    )
    .join('')}</div></aside>`;
}
function stageHeader(c) {
  return `<div class="stage-kicker"><span></span> POST-MATCH · 赛后现场 <span></span></div><h2 class="stage-title">${phases[c.phase]}</h2>`;
}
function voteStage(state, c) {
  return `${stageHeader(c)}<div class="qr-card"><img class="vote-qr" src="${e(state.qr_url)}" alt="扫码进入本场观众投票" width="540" height="540"><div class="qr-caption">扫码投票<span>无需登录 · 本轮每个浏览器限投一次</span></div></div><p class="vote-instruction">选出获胜方与一位最佳辩手</p><div class="vote-counter"><b>${state.total_votes}</b><span>份有效投票 · ${state.collecting ? '正在收集' : '连接中'}</span></div><a class="vote-entry" href="/?mode=vote">${e(new URL(state.vote_url).host)} · 观众投票 ↗</a>`;
}
function reviewStage(c) {
  return `${stageHeader(c)}<div class="review-live"><img class="review-art" src="/assets/judges-commentary-v2.webp" alt="" width="768" height="512"><h3>请听评委评议</h3><p>评议进行中</p></div>`;
}
function audienceTeamAward(votes, winners) {
  const a = votes || { total: 0, winners: { AFFIRMATIVE: 0, NEGATIVE: 0 } };
  const aff = a.winners.AFFIRMATIVE,
    neg = a.winners.NEGATIVE;
  const winner = !a.total ? '暂无结果' : !winners.length ? '暂无胜方票' : winners.join(' · ');
  const kind = aff > neg ? 'aff' : neg > aff ? 'neg' : 'tied';
  const scale = Math.min(1, 3 / Math.max(String(aff).length, String(neg).length));
  const side = (name, count, sideKind, won) =>
    `<div class="score-team ${sideKind}${won ? ' is-winner' : ''}"><span class="score-side">${name}${won ? '<span class="score-winner-tag">胜出</span>' : ''}</span><strong class="score-value">${count}</strong></div>`;
  return `<div class="award-copy audience-team-copy"><div class="award-identity winner-${kind}"><span class="award-label">胜方队伍</span><h3 class="audience-winner-name${winners.length > 1 ? ' tied-winner' : ''}">${e(winner)}</h3><span class="winner-underline" aria-hidden="true"></span>${winners.length > 1 ? '<span class="award-rule">双方票数并列</span>' : ''}</div><div class="award-stat-panel"><span class="result-stat-label">观众票数</span><div class="score-duel" data-score-scale="${scale}" role="img" aria-label="正方 ${aff} 票，反方 ${neg} 票">${side('正方', aff, 'aff', aff > neg)}${side('反方', neg, 'neg', neg > aff)}</div><div class="score-share${!a.total ? ' is-empty' : ''}" aria-hidden="true"><span class="aff" data-score-count="${a.total ? aff : 1}"></span><span class="neg" data-score-count="${a.total ? neg : 1}"></span></div></div></div>`;
}
function styleAudienceScores() {
  app.querySelectorAll('[data-score-scale]').forEach((node) => {
    node.style.setProperty('--score-scale', node.dataset.scoreScale);
  });
  app.querySelectorAll('[data-score-count]').forEach((node) => {
    node.style.flexGrow = node.dataset.scoreCount;
  });
  app.querySelectorAll('[data-vote-share]').forEach((node) => {
    node.style.width = `${node.dataset.voteShare}%`;
  });
}
function audienceBestAward(votes, best) {
  const a = votes || { total: 0, ranking: [] };
  const points = best.length ? a.ranking.find((r) => r.id === best[0].id)?.points || 0 : 0;
  const share = a.total ? Math.min(100, Math.max(0, (points / a.total) * 100)) : 0;
  const scale = Math.min(1, 3 / Math.max(String(points).length, String(a.total).length));
  const names = best.length
    ? `<div class="audience-best-list${best.some((p) => p.name.length > 12) ? ' long-best' : ''}${best.length > 2 ? ' many-best' : best.length > 1 ? ' two-best' : ''}">${best.map((p) => `<div class="audience-best-person"><strong class="audience-best-name" title="${e(p.name)}">${e(p.name)}</strong></div>`).join('')}</div>`
    : '<strong class="audience-best-empty">暂无结果</strong>';
  const stats = best.length
    ? `<div class="best-vote-count" data-score-scale="${scale}" aria-label="${best.length > 1 ? '每位并列最佳辩手' : e(best[0].name)}获得 ${points} 票，总票数 ${a.total} 票"><b>${points}</b><span class="vote-slash">/</span><span class="vote-total">${a.total}</span><small>票</small></div><span class="result-stat-label">${best.length > 1 ? '每位' : ''}得票 / 总票数</span><span class="best-vote-meter" aria-hidden="true"><i data-vote-share="${share}"></i></span>`
    : '<strong class="empty-vote-stat">—</strong><span class="result-stat-label">等待有效投票</span>';
  return `<div class="award-copy audience-best-copy"><div class="award-identity"><span class="award-label">${best.length > 1 ? '并列最佳辩手' : '最佳辩手'}</span>${names}</div><div class="award-stat-panel">${stats}</div></div>`;
}
function resultAwards(c, source, winnerNames, best, note, empty = false, votes = null) {
  const multiple = best.length > 1;
  const audience = source === '大众票选';
  const sourceName = audience ? '大众评选' : '评委评选';
  const sourceKind = audience ? 'audience' : 'judge';
  const sourceNote = audience ? '现场观众 · 一人一票' : '现场评委 · 评委评定';
  return `<header class="result-heading" data-result-source="${sourceKind}"><div class="result-context"><span class="result-source-tag">${audience ? '现场观众' : '现场评委'}</span><span>结果公布</span></div><h2 class="result-eyebrow result-source-title">${sourceName}</h2><p class="result-source-note">${sourceNote}</p></header><div class="award-stack"><article class="award-row team-award${audience ? ' audience-award' : ''}"><img class="award-art" src="/assets/award-trophy-v1.png" alt="" width="200" height="200">${audience ? audienceTeamAward(votes, winnerNames) : `<div class="award-copy"><span class="award-label">${sourceName} · ${winnerNames.length > 1 ? '获胜方 · 票数并列' : '获胜方'}</span><h3 class="winner-name ${winnerNames.length > 1 ? 'multiple-winners' : ''}">${winnerNames.length ? winnerNames.map(e).join(' · ') : empty ? '暂无结果' : '待公布'}</h3><span class="award-rule">${empty ? '本轮暂无有效投票' : source === '大众票选' ? '按观众胜方票数评选' : '评委评选结果'}</span></div>`}</article><article class="award-row debater-award${audience ? ' audience-award' : ''}"><img class="award-art" src="/assets/award-medal-v1.png" alt="" width="200" height="200">${audience ? audienceBestAward(votes, best) : `<div class="award-copy"><span class="award-label">${sourceName} · ${multiple ? '并列最佳辩手' : '最佳辩手'}</span><div class="award-names ${best.length > 2 ? 'many-names' : multiple ? 'two-names' : ''}">${best.length ? best.map((p) => `<strong title="${e(p.name)}">${e(p.name)}</strong>`).join('') : `<strong>${empty ? '暂无结果' : '待公布'}</strong>`}</div><span class="award-rule">${multiple ? '观众得票相同，共享此项荣誉' : best.length ? `${best[0].id.startsWith('aff') ? '正方' : '反方'} · ${['一', '二', '三', '四'][Number(best[0].id.slice(-1)) - 1]}辩` : ''}</span></div>`}</article></div><p class="results-foot">${note}</p>`;
}
function audienceResults(state, c) {
  const a = state.audience;
  if (!a?.total) return resultAwards(c, '大众票选', [], [], '等待有效投票产生结果', true, a);
  const max = Math.max(...Object.values(a.winners));
  const winners = Object.keys(a.winners)
    .filter((k) => max > 0 && a.winners[k] === max)
    .map((k) => sideNames[k]);
  const highest = Math.max(...a.ranking.map((r) => r.points));
  const best = a.ranking
    .filter((r) => highest > 0 && r.points === highest)
    .map((r) => c.participants.find((p) => p.id === r.id));
  return resultAwards(
    c,
    '大众票选',
    winners,
    best,
    `${a.total} 份有效投票 · 每人一票 · 同票并列`,
    false,
    a,
  );
}
function judgeResults(c) {
  const best = c.participants.find((p) => p.id === c.best);
  return resultAwards(
    c,
    '评委评选',
    c.winner ? [sideNames[c.winner]] : [],
    best ? [best] : [],
    '评委评选结果',
  );
}
function openingStage(c) {
  return `<div class="stage-kicker"><span></span> OPENING · 开场介绍 <span></span></div><h2 class="stage-title">${e(phases.intro)}</h2>${actualDisplay ? '<div id="audio-tools" class="audio-tools stage-bottom"></div>' : '<div class="preview-audio-note stage-bottom">开场介绍 · 声音由现场大屏播放</div>'}`;
}
function display(state) {
  const c = state.content;
  if (!c) {
    app.innerHTML = `<div class="screen-shell unpublished">${brand()}<div class="waiting"><span class="eyebrow">JIXIA · LIVE SHOWCASE</span><h1>思想交汇<br>精彩即将呈现。</h1><p>等待主持人发布本场信息</p><div class="waiting-line"></div></div><footer>人与智能，共赴思想之约</footer></div>`;
    return;
  }
  const content =
    c.phase === 'intro'
      ? openingStage(c)
      : c.phase === 'vote'
        ? voteStage(state, c)
        : c.phase === 'review'
          ? reviewStage(c)
          : c.result_view === 'audience'
            ? audienceResults(state, c)
            : judgeResults(c);
  const opening = c.phase === 'intro';
  app.innerHTML = `<div class="screen-shell ${opening ? 'opening-shell' : ''}"><header class="screen-header">${brand(true)}<div class="topic"><small>${e(c.title)}</small><h1 title="${e(c.topic)}">${e(c.topic)}</h1></div><span class="live-pill"><i></i> ${opening ? '开场现场' : '赛后现场'}</span></header><div class="screen-grid">${seats(c, 'aff')}<section class="main-stage ${c.phase === 'results' ? `results-stage${c.result_view === 'audience' ? ' audience-results-stage' : ''}` : ''} ${opening ? 'opening-stage' : ''}">${content}</section>${seats(c, 'neg')}</div><footer class="screen-footer"><span>JIXIA · 人与智能的思想交锋</span><div class="phase-track">${Object.entries(
    phases,
  )
    .map(
      ([key, label], i) =>
        `<span class="${c.phase === key ? 'active' : ''}"><b>0${i}</b>${label}</span>`,
    )
    .join('<i></i>')}</div><span>让每一种观点被听见</span></footer></div>`;
  styleAudienceScores();
  updateAudioUI();
}
function renderVote(state) {
  const c = state.content;
  if (!c) {
    app.innerHTML = `<div class="mobile-wrap">${brand()}<div class="vote-panel"><h1>投票尚未开放</h1><p>请等待主持人发布本场投票。</p></div></div>`;
    return;
  }
  const closed = c.phase !== 'vote' || !state.collecting;
  const done = state.has_voted || submitted;
  app.innerHTML = `<div class="mobile-wrap">${brand()}<header class="mobile-heading"><span class="eyebrow">AUDIENCE CHOICE</span><h1>观众投票</h1><p>${e(c.topic)}</p></header>${
    done
      ? `<section class="vote-panel success"><span>✓</span><h2>投票已提交</h2><p>感谢参与。本浏览器已完成本轮投票。</p></section>`
      : closed
        ? `<section class="vote-panel"><h2>本轮投票${c.phase === 'vote' ? '暂未收集' : '已结束'}</h2><p>大屏进入观众投票画面后即可参与。</p></section>`
        : `<form id="vote-form"><section class="vote-panel"><div class="section-number">01</div><h2>选择获胜方</h2><div class="winner-options">${Object.entries(
            { AFFIRMATIVE: sideNames.AFFIRMATIVE, NEGATIVE: sideNames.NEGATIVE },
          )
            .map(
              ([key, label]) =>
                `<label class="${winner === key ? 'selected' : ''}"><input type="radio" name="winner" value="${key}" ${winner === key ? 'checked' : ''} required><span>${label}</span></label>`,
            )
            .join(
              '',
            )}</div></section><section class="vote-panel"><div class="section-number">02</div><h2>选择最佳辩手</h2><p class="helper">请选择一位你心中的本场最佳辩手。</p><div class="best-options">${c.participants
            .map(
              (p) =>
                `<label class="best-option ${selectedBest === p.id ? 'selected' : ''}"><img src="${e(avatarUrl(p))}" alt=""><span><strong>${e(p.name)}</strong><small>${p.id.startsWith('aff') ? '正方' : '反方'} · ${seatNumber(p.id)}辩 · ${p.kind === 'AGENT' ? '智能体' : '真人'}</small></span><input type="radio" name="best" value="${e(p.id)}" ${selectedBest === p.id ? 'checked' : ''} required aria-label="${e(p.name)}"></label>`,
            )
            .join(
              '',
            )}</div></section><button class="button primary vote-submit" type="submit" ${busy ? 'disabled' : ''}>${busy ? '正在提交…' : '提交投票'}</button><p class="vote-privacy">无需登录 · 本浏览器每轮限投一次<br>请确认选择，提交后无法修改。</p></form>`
  }</div>`;
  app.querySelectorAll('input[name="winner"]').forEach((el) =>
    el.addEventListener('change', () => {
      winner = el.value;
      app
        .querySelectorAll('.winner-options label')
        .forEach((l) => l.classList.toggle('selected', l.querySelector('input').checked));
    }),
  );
  app.querySelectorAll('input[name="best"]').forEach((el) =>
    el.addEventListener('change', () => {
      selectedBest = el.value;
      app
        .querySelectorAll('.best-option')
        .forEach((label) =>
          label.classList.toggle('selected', label.querySelector('input').checked),
        );
    }),
  );
  app.querySelector('#vote-form')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (busy || !winner || !selectedBest) return;
    busy = true;
    renderVote(latest);
    try {
      await send('/api/vote', {
        round_id: state.round_id,
        winner,
        best: selectedBest,
      });
      submitted = true;
      await poll();
    } catch (err) {
      toast(err.message);
    } finally {
      busy = false;
      renderVote(latest);
    }
  });
}
async function poll() {
  const request = new AbortController();
  stateRequest = request;
  stateRequestStarted = performance.now();
  try {
    const state = await api('/api/state', {
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(8000)]),
    });
    if (latest && state.revision < latest.revision) return;
    if (latest && latest.round_id !== state.round_id) {
      selectedBest = '';
      winner = '';
      submitted = false;
    }
    await maintainDisplay(state);
    latest = state;
    lastStateSuccess = performance.now();
    const recovery = state.audio_recovery;
    if (
      isOpening(state) &&
      recovery?.id &&
      recovery.id !== lastServerRecovery &&
      recovery.round_id === state.round_id &&
      recovery.display_epoch === state.display_epoch
    ) {
      lastServerRecovery = recovery.id;
      void repairOpening({ resources: true, remote: true });
    }
    await maintainAudio(state);
    const next = JSON.stringify(state);
    if (next !== signature) {
      signature = next;
      voteMode ? renderVote(state) : display(state);
    }
    updateAudioUI();
    document.querySelector('#connection').hidden = true;
    checkOpeningHealth();
  } catch {
    releaseDisplay();
    releaseAudio();
    document.querySelector('#connection').hidden = false;
    checkOpeningHealth();
  } finally {
    if (stateRequest === request) stateRequest = null;
  }
}
const fullscreen = document.querySelector('#fullscreen');
fullscreen.hidden = voteMode || window.self !== window.top;
async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch {
    toast('请使用浏览器的全屏功能。');
  }
}
fullscreen.addEventListener('click', toggleFullscreen);
document.addEventListener('keydown', (ev) => {
  if (!voteMode && ev.key.toLowerCase() === 'f' && !ev.ctrlKey && !ev.metaKey) toggleFullscreen();
});
document.addEventListener('fullscreenchange', () =>
  fullscreen.setAttribute(
    'aria-label',
    document.fullscreenElement ? '退出全屏展示' : '进入全屏展示',
  ),
);
if (voteMode) document.body.classList.add('vote-mode');
function refreshState() {
  if (!poll.running) {
    poll.running = true;
    return poll().finally(() => {
      poll.running = false;
      if (audioWakePending) {
        audioWakePending = false;
        if (actualDisplay && !pageClosing && navigator.onLine) void refreshState();
      }
    });
  }
}
if (audioWake) {
  audioWake.onmessage = (event) => {
    if (event.data === 'refresh' && isOpening(latest)) {
      if (poll.running) audioWakePending = true;
      else void refreshState();
    }
  };
}
await refreshState();
startScreenEvents();
setInterval(() => {
  checkOpeningHealth();
  if (!actualDisplay || !screenEvents || performance.now() - lastScreenEvent > 5000)
    void refreshState();
}, 1200);
