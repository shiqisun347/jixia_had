import { Children, isValidElement, cloneElement, type ReactNode } from 'react';

const baseTranslations: Record<string, string> = {
  '管理内容': 'Admin content', '无法确认管理权限': 'Cannot confirm administrator access', '需要管理员权限': 'Administrator access required',
  '运营概览': 'Operations overview', '查看比赛': 'View matches', '打开赛制规则': 'Open format rules', '活动比赛': 'Active matches',
  '实时比赛容量': 'Live match capacity', '最近比赛': 'Recent matches', '待处理事项': 'Needs attention', '快捷入口': 'Quick links',
  '比赛与数据': 'Matches & data', '比赛列表': 'Match list', '比赛': 'Match', '状态': 'Status', '创建时间': 'Created', '结束时间': 'Ended', '数据': 'Data', '操作': 'Actions',
  '用户': 'Users', '用户名': 'Username', '真实姓名': 'Real name', '用户 ID': 'User ID', '注册时间': 'Registered',
  '模型': 'Models', '音色': 'Voices', '辩题': 'Topics', '赛制规则': 'Format rules', 'Agent 管理': 'Agent management', '裁判结果': 'Judge results', '问卷': 'Surveys',
  '启用': 'Enabled', '停用': 'Disabled', '进行中': 'Running', '已暂停': 'Paused', '已终止': 'Terminated', '已结束': 'Finished', '等待中': 'Pending', '等待运行时': 'Waiting for runtime', '开赛倒计时': 'Starting countdown', '异常': 'Error', '成功': 'Succeeded', '失败': 'Failed',
  '搜索': 'Search', '刷新': 'Refresh', '重新加载': 'Reload', '重新检查': 'Check again', '取消': 'Cancel', '确认': 'Confirm', '编辑': 'Edit', '保存': 'Save', '删除': 'Delete', '永久删除': 'Delete permanently',
  '终止比赛': 'Terminate match', '重新评分': 'Retry judging', '生成/重试回放': 'Generate/retry playback', '模型诊断': 'Model diagnostics', '打开比赛工作台': 'Open match workbench',
  '自由辩论快速决策': 'Free-debate decisions', '正式发言生成': 'Formal speech generation', '生成参数': 'Generation parameters', '发言动作': 'Speech action', '是否举手': 'Raised hand', '意愿值': 'Willingness', '决策耗时': 'Decision latency',
  '暂无数据': 'No data', '暂无比赛': 'No matches', '没有符合筛选条件的比赛。': 'No matches match the current filters.', '这场比赛还没有 Agent 模型调用。': 'This match has no Agent model calls yet.', '暂无后台任务。': 'No background tasks.', '暂无审计记录。': 'No audit records.',
  '全部': 'All', '全部状态': 'All statuses', '正方': 'Affirmative', '反方': 'Negative', '主持': 'Host', '名称': 'Name', '配置': 'Configuration', '规模': 'Scale', '技术关联': 'Technical linkage', '提交时间': 'Submitted',
  '选择模型': 'Select model', '请选择模型': 'Select a model', '请选择赛制规则': 'Select format rules', '导出 CSV': 'Export CSV', '导出 JSON': 'Export JSON', '审计日志': 'Audit log', '系统设置': 'System settings',
  '正在确认管理权限': 'Checking administrator access',
  '当前账号可以参加和观看比赛，但不能进入管理后台。': 'This account can participate in and watch matches, but cannot open the admin console.',
  '账号服务暂时不可用，请检查网络后重试。系统不会在权限状态未知时进入后台。': 'The account service is temporarily unavailable. Check your connection and try again; the console remains closed while access is unknown.',
  '总览暂时无法加载': 'Overview is temporarily unavailable', '请求失败不会影响正在进行的比赛。': 'A failed request does not affect matches already in progress.',
  '请检查服务状态后重新加载管理数据。': 'Check service status, then reload admin data.', '启用 Agent': 'Enabled Agents', '赛制': 'Format', '系统': 'System',
  '磁盘使用率已达到 {percent}%，请检查音频保留和清理任务。': 'Disk usage has reached {percent}%. Check audio retention and cleanup jobs.',
  '系统不执行自动备份。磁盘使用率达到 90% 后会阻止新比赛开始，但不会中断正在进行的比赛。': 'The system does not run automatic backups. New matches are blocked at 90% disk usage, while matches already in progress continue.',
  '正在加载运营概览': 'Loading operations overview', '正在加载数据': 'Loading data', '正在加载规则…': 'Loading formats…', '正在加载设置…': 'Loading settings…',
  '新建规则': 'Create format', '打开工作区': 'Open workspace', '规则目录': 'Format directory', '估算时长': 'Estimated duration', '无说明': 'No description', '分钟': 'minutes', '历史只读': 'Historical read-only',
  '请填写名称并选择主持音色和默认模型': 'Enter a name and select a host voice and default model', '保存规则': 'Save format',
  '选择': 'Select', '参与统计': 'Participation', '角色': 'Role', '唯一管理员': 'Sole administrator', '普通用户': 'Regular user', '编辑资料': 'Edit profile', '删除用户': 'Delete user', '修改密码': 'Change password', '修改自己的密码': 'Change my password', '旧会话已撤销。': 'Previous sessions revoked.', '还没有用户': 'No users yet', '保存资料': 'Save profile', '保存新密码': 'Save new password', '新密码': 'New password',
  '导入模型': 'Import model', '模型设置': 'Model settings', '模型目录': 'Model directory', '还没有模型配置': 'No model configurations yet', '编辑配置': 'Edit configuration', '测试中…': 'Testing…', '测试连接': 'Test connection', '轮换 API Key': 'Rotate API key', '停用模型': 'Disable model', '启用模型': 'Enable model', '未设置模型 ID': 'Model ID not set', '并发': 'Concurrency', '未配置': 'Not configured', '确认停用': 'Confirm disable', '确认启用': 'Confirm enable', '停用模型？': 'Disable model?', '启用模型？': 'Enable model?', '模型配置已更新。': 'Model configuration updated.', '模型配置已创建。': 'Model configuration created.', '保存配置': 'Save configuration', '配置名称': 'Configuration name', '配置引用': 'Configuration reference', '模型 ID': 'Model ID', '最大并发': 'Max concurrency', '最大 Token 数': 'Max tokens', '确认轮换': 'Confirm rotation', '新 API Key': 'New API key',
  '添加音色': 'Add voice', '语音方案': 'Voice profiles', '音色目录': 'Voice directory', '还没有音色配置': 'No voice configurations yet', '编辑音色': 'Edit voice', '试听/重新生成': 'Preview / regenerate', '生成中…': 'Generating…', '停用音色': 'Disable voice', '启用音色': 'Enable voice', '保存音色': 'Save voice', '未知音色': 'Unknown voice', '语速': 'Rate', '字/秒': 'chars/sec', '自动': 'Automatic', '播放增益': 'Playback gain', 'Agent 头像': 'Agent avatar',
  '辩题管理': 'Topic management', '辩题目录': 'Topic directory', '添加辩题': 'Add topic', '编辑辩题': 'Edit topic', '辩题标题': 'Topic title', '正方立场': 'Affirmative position', '反方立场': 'Negative position', '原始来源文本': 'Original source text', '未填写原始来源': 'Original source not provided', '未取得': 'Not available', '启用辩题': 'Enable topic', '停用辩题': 'Disable topic', '保存辩题': 'Save topic', '还没有辩题': 'No topics yet',
  'API 请求日志': 'API request logs', '调用记录': 'Call records', '全部类型': 'All types', '全部来源': 'All sources', '已取消': 'Canceled', '时间范围': 'Time range', '最近 15 分钟': 'Last 15 minutes', '最近 1 小时': 'Last hour', '最近 6 小时': 'Last 6 hours', '最近 24 小时': 'Last 24 hours', '全部时间': 'All time', '正在加载 API 请求日志': 'Loading API request logs', 'API 请求详情': 'API request details', '请求 JSON': 'Request JSON', '响应 JSON': 'Response JSON', '未记录': 'Not recorded', '关闭': 'Off', '自动刷新间隔': 'Auto-refresh interval', '实时观察': 'Live monitor', '查询模式': 'Query mode',
  '任务队列': 'Task queue', '任务状态': 'Task status', '等待执行': 'Waiting to run', '执行中': 'Running', '评分记录': 'Score records', '比赛数据': 'Match data', '事故': 'Incidents', '运行参数': 'Runtime parameters', '运行日志保留天数': 'Runtime log retention (days)', '临时记录 DEBUG 日志': 'Temporarily record DEBUG logs', 'DEBUG 自动关闭时间': 'DEBUG auto-disable time', '单文件上传上限（MB）': 'Per-file upload limit (MB)', '保存设置': 'Save settings', '排行榜重算任务已排队。': 'Leaderboard recalculation queued.', '立即重算排行榜': 'Recalculate leaderboard now',
  '比赛工作台': 'Match workbench', '概览': 'Overview', '参赛者 / 席位': 'Participants / seats', '运行时间线': 'Runtime timeline', '原始事件': 'Raw events', '请求日志': 'Request logs', '导出': 'Export', '恢复比赛': 'Resume match', '强制终止': 'Force terminate', '重置当前发言': 'Reset current speech', '没有参赛席位记录': 'No participant seats recorded', '没有文字记录': 'No transcript', '没有比赛事件': 'No match events', '还没有可展示的运行时间线。': 'No runtime timeline to display.', '外部请求日志': 'External request logs', '发生时间': 'Occurred', '首结果': 'First result', '总耗时': 'Total duration', '上下文': 'Context', '比赛摘要': 'Match summary', '只读数据': 'Read-only data', '研究导出': 'Research export', '创建 ZIP 导出': 'Create ZIP export', '下载 ZIP': 'Download ZIP', '包含已授权音频': 'Include authorized audio',
  '终止比赛？': 'Terminate match?', '确认终止': 'Confirm termination', '重新评分？': 'Retry judging?', '确认重试': 'Confirm retry', '重新生成回放？': 'Regenerate playback?', '确认排队': 'Confirm queue', '恢复默认保留期？': 'Restore default retention?', '永久保留音频？': 'Retain audio permanently?', '确认修改': 'Confirm change', '永久删除比赛？': 'Permanently delete match?', '确认永久删除': 'Confirm permanent deletion',
  '论文实验管理': 'Paper experiment management', '固定排表、标注完成度、结果门禁与研究导出。': 'Fixed schedules, annotation progress, result gates, and research exports.', '新建批次': 'Create batch', '刷新进度': 'Refresh progress', '停用批次': 'Disable batch', '搜索批次': 'Search batches', '筛选批次状态': 'Filter batch status', '草稿': 'Draft', '已发布': 'Published', '已停用': 'Disabled', '暂无批次': 'No batches yet', '当前批次': 'Current batch', '先选择一个批次': 'Select a batch first', '或者新建一个实验批次开始配置。': 'Or create a batch to begin configuration.', '正式赛完成': 'Formal matches complete', '排表版本': 'Schedule version', '批次状态': 'Batch status', '场次': 'Match', '双方': 'Sides', '参与者标注': 'Participant annotations', '结果': 'Result', '正式赛': 'Formal', '训练赛': 'Training', '已公开': 'Public', '门禁中': 'Gated', '数据与请求日志': 'Data & request logs', '公开': 'Publish', '研究数据与保留期': 'Research data & retention', '生成匿名研究包': 'Generate anonymized research package', '预览到期数据': 'Preview expiring data', '编辑批次基本信息': 'Edit batch basics', '新建实验批次': 'Create experiment batch', '批次编号': 'Batch code', '批次名称': 'Batch name', '实验赛制': 'Experiment format', '每位参与者训练房间上限': 'Training-room limit per participant', '保存修改': 'Save changes', '创建草稿': 'Create draft', '提前公开比赛结果': 'Publish results early', '公开原因': 'Publication reason', '确认公开': 'Confirm publication', 'Agent 与 Prompt 设置': 'Agent & Prompt settings', '固定队伍与 Agent': 'Fixed teams & Agents', '决策 Prompt': 'Decision Prompt', '发言 Prompt': 'Speech Prompt', '暂无模板': 'No template', '打开 Agent 管理': 'Open Agent management', '打开系统日志': 'Open system logs', '删除草稿': 'Delete draft', '删除这个实验草稿？': 'Delete this experiment draft?', '停用实验批次？': 'Disable experiment batch?',
};

const translations: Record<string, string> = {
  ...baseTranslations,
  '查看本地存储门禁并触发已有后台维护任务。MVP 不提供自动备份。': 'Review local storage gates and trigger existing maintenance jobs. The MVP does not provide automatic backups.', '运行参数': 'Runtime parameters', '只影响后续日志写入、清理和上传请求；不改变已发布快照或进行中的比赛。': 'Only future log writes, cleanup, and upload requests are affected; published snapshots and active matches are unchanged.', '系统设置已保存。': 'System settings saved.', '本地存储': 'Local storage', '超过 80% 告警，达到 90% 阻止新比赛开赛。': 'Warn above 80%; block new matches at 90%.', '磁盘已用': 'Disk used', '排行榜维护': 'Leaderboard maintenance', '正常情况下每天自动全量更新；手动操作使用相同的幂等任务。': 'Full updates run daily; manual actions use the same idempotent job.', '当管理员修正赛后评分或需要立即刷新榜单时，可手动排队一次重算。已有快照会保留到新批次成功。': 'Queue a recalculation after correcting a post-match score or when the leaderboard needs an immediate refresh. Existing snapshots remain until the new run succeeds.',
  '查看主持音频、归档、导出与排行榜等有界后台任务的执行状态。': 'Review bounded host-audio, archive, export, and leaderboard jobs.', '尝试': 'Attempt', '重试': 'Retry', '查看比赛数据': 'View match data', '独立结果查询将在赛制裁判配置迁移后启用。': 'Independent result queries will be enabled after format judge configuration is migrated.', '当前请从“比赛与数据”进入单场裁判详情。': 'Open a match from “Match data” to view judge details.',
  '每套赛制拥有独立 Agent 池。选择赛制后，才能查看和编辑其中的 Agent。': 'Each format has its own Agent pool. Select a format to view and edit its Agents.', 'Agent 由启用的全局音色自动生成，不支持手工新增、删除或跨赛制复制。': 'Agents are generated from enabled global voices; manual creation, deletion, and cross-format copying are unavailable.', '选择赛制规则': 'Select format rules', '请选择赛制规则': 'Select format rules', '先选择一套赛制规则，再管理它的 Agent 池。': 'Select a format before managing its Agent pool.', 'Agent 池': 'Agent pool', '正在加载 Agent 池…': 'Loading Agent pool…', '该规则还没有 Agent。请先启用 Agent 音色或检查默认模型。': 'This format has no Agents yet. Enable an Agent voice or check the default model.', '音色': 'Voice', '未知模型': 'Unknown model', '使用赛制默认': 'Use format default', '生成参数': 'Generation parameters', 'Prompt 槽位': 'Prompt slot', '使用赛制默认 Prompt': 'Use format default Prompt', '恢复默认': 'Restore default', '编辑 Agent': 'Edit Agent', 'Agent 配置已保存': 'Agent configuration saved', '基础身份跟随全局音色；这里只编辑规则内运行配置。': 'Identity follows the global voice; only format-level runtime settings are edited here.',
  '查看比赛状态': 'View match status', '结果超过 5000 项，请缩小筛选范围后再全选。': 'More than 5,000 results. Narrow the filter before selecting all.', '请缩小选择范围。': 'Narrow your selection.', '已选择当前筛选结果': 'Selected the current filtered results', '全选当前筛选结果': 'Select all filtered results', '搜索比赛标签、辩题或 ID': 'Search match label, topic, or ID', '筛选比赛状态': 'Filter match status', '赛制版本 ID': 'Format version ID', '实验批次 ID': 'Experiment batch ID', '比赛排序': 'Sort matches', '没有符合筛选条件的比赛。': 'No matches match the current filters.', '正在加载比赛': 'Loading matches', '模型诊断': 'Model diagnostics', '正在加载模型诊断': 'Loading model diagnostics', '自由辩论快速决策': 'Free-debate decisions', '正式发言生成': 'Formal speech generation', '这场比赛还没有 Agent 模型调用。': 'This match has no Agent model calls yet.', '是否举手': 'Raised hand', '调用失败': 'Call failed', '是': 'Yes', '否': 'No', '首 Token': 'First token', '完整响应': 'Complete response', '输出 Token': 'Output tokens', '收起输入与草稿': 'Hide input and draft', '查看输入与草稿': 'View input and draft', '脱敏输入快照': 'Redacted input snapshot', 'LLM 正式草稿': 'LLM formal draft', '尚未产生完整草稿': 'No complete draft yet', '未进入举手队列': 'Not in hand-raise queue', '系统兜底': 'System fallback', '锁定时已有真人举手': 'A human hand was raised at lock',
  '维护公开辩题和正反立场文本；活动比赛使用创建时快照。': 'Maintain public topics and affirmative/negative text; active matches use their creation snapshot.', '活动比赛引用的辩题由服务端保护，停用只影响后续新房间。': 'Topics referenced by active matches are protected server-side; disabling affects only new rooms.', '辩题已更新。': 'Topic updated.', '辩题已创建。': 'Topic created.', '辩题已保存，但目录未同步；请重新进入页面。': 'Topic saved, but the directory did not sync; reopen the page.', '保存后新房间使用最新文本，历史比赛快照不受影响。': 'New rooms use the latest text after saving; historical match snapshots are unchanged.',
};

export function translateAdminText(value: string, locale: string): string {
  if (locale !== 'en') return value;
  const exact = translations[value];
  if (exact) return exact;
  return value
    .replace(/编辑(模型|音色|辩题|用户|Agent) · /g, (_, kind: string) => `Edit ${kind === '模型' ? 'model' : kind === '音色' ? 'voice' : kind === '辩题' ? 'topic' : kind === '用户' ? 'user' : 'Agent'} · `)
    .replace(/轮换 API Key · /g, 'Rotate API key · ')
    .replace(/已(启用|停用)\。/g, (_, state: string) => `${state === '启用' ? 'Enabled' : 'Disabled'}.`)
    .replace(/连接成功，首 Token /g, ' connected; first token ')
    .replace(/ 已更新。/g, ' updated.')
    .replace(/ 已删除。/g, ' deleted.')
    .replace(/ 的旧会话已撤销。/g, '’s previous sessions were revoked.')
    .replace(/的状态将改变；活动比赛引用时服务端会拒绝操作。/g, '’s status will change; the server rejects changes while active matches reference it.')
    .replace(/终止后不可恢复，但已完成的数据会保留。/g, ' cannot be restored after termination, but completed data will remain available.')
    .replace(/将按截止当前时刻的文字版本重新评分。/g, ' will be rejudged using the transcript available at the cutoff.')
    .replace(/的整场回放任务将重新排队。/g, '’s full-match playback job will be queued again.')
    .replace(/仅修改 .*? 的音频保留策略，不改变比赛文字和评分。/g, 'Only audio retention changes; the transcript and score remain unchanged.')
    .replace(/的文字、评分、音频和房间数据将被删除；审计日志仍保留。/g, '’s transcript, score, audio, and room data will be deleted; audit logs remain.')
    .replace(/模型状态已修改，但目录未同步；请重新进入页面。/g, 'Model status changed, but the directory did not sync; reopen the page.')
    .replace(/音色状态已修改，但目录未同步；请重新进入页面。/g, 'Voice status changed, but the directory did not sync; reopen the page.')
    .replace(/已选择当前筛选结果 (\d+) 场比赛。/g, 'Selected $1 matches from the current filter.')
    .replace(/已完成 (\d+) 项批量操作。/g, '$1 bulk actions completed.')
    .replace(/批量操作完成：成功 (\d+) 项，失败 (\d+) 项。/g, 'Bulk actions completed: $1 succeeded, $2 failed.')
    .replace(/共 (\d+) 个最近任务/g, '$1 recent tasks')
    .replace(/共 (\d+) 份回答/g, '$1 responses')
    .replace(/共 (\d+) 条当前页记录/g, '$1 records on this page')
    .replace(/参与 (\d+) · 完赛 (\d+) · 胜\s*(\d+) · (\d+) 分/g, '$1 matches · $2 finished · $3 wins · $4 points')
    .replace(/第 (\d+) 轮 · 第 (\d+) 场/g, 'Round $1 · Match $2')
    .replace(/第 (\d+) 次尝试/g, 'Attempt $1')
    .replace(/(\d+) 个模型配置/g, '$1 model configurations')
    .replace(/(\d+) 个音色配置/g, '$1 voice configurations')
    .replace(/(\d+) 个辩题配置/g, '$1 topic configurations')
    .replace(/(\d+) 套规则/g, '$1 formats')
    .replace(/(\d+) 场比赛/g, '$1 matches')
    .replace(/(\d+) 条记录/g, '$1 records')
    .replace(/剩余 ([\d.]+) GB/g, '$1 GB remaining')
    .replace(/约 (\d+) 天/g, 'about $1 days')
    .replace(/第 (\d+) 阶段/g, 'Stage $1');
}

export function translateAdminNode(node: ReactNode, locale: string): ReactNode {
  if (locale !== 'en') return node;
  return Children.map(node, (child) => {
    if (typeof child === 'string') return translateAdminText(child, locale);
    if (isValidElement(child)) {
      const props = child.props as Record<string, unknown> & { children?: ReactNode };
      const translatedProps: Record<string, unknown> = {};
      for (const key of [
        'aria-label',
        'confirmLabel',
        'description',
        'emptyDescription',
        'emptyTitle',
        'label',
        'placeholder',
        'title',
      ]) {
        if (typeof props[key] === 'string') {
          translatedProps[key] = translateAdminText(props[key], locale);
        }
      }
      return cloneElement(child, translatedProps, translateAdminNode(props.children, locale));
    }
    return child;
  });
}
