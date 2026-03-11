import { api } from './api.js';
import { closeModal, notify, showModal } from './utils.js';

const STORAGE_KEY = 'ai_dialog_page_sessions';
const ACTIVE_SESSION_KEY = 'ai_dialog_page_active_session_id';
const DIALOG_SCOPE = 'evolution_dialog';

/** @type {Array<{id:string,title:string,pinned:boolean,messages:Array<any>,created_at:number,updated_at:number}>} */
let sessions = [];
let activeSessionId = '';
let pageSocket = null;
let isProcessing = false;
let isVoiceRecording = false;
let voiceTickTimer = null;
let voiceStartAt = 0;
let sessionSearchKeyword = '';
let messageSelectMode = false;
const selectedMessageIds = new Set();
let dialogRoles = [];
let activeDialogRoleId = '';

const DIALOG_OUTPUT_SCHEMA_HINT = {
    version: 'v1',
    messages: [
        {
            type: 'text',
            text: '回复内容',
            actions: [
                { type: 'create_task', label: '一键创建任务', payload: { title: '任务标题', description: '任务描述' } },
                { type: 'open_task_detail', label: '打开任务详情', task_id: 1 },
            ],
        },
    ],
};

const BUILTIN_COMMAND_TEMPLATES = [
    { name: '查看系统版本', command: 'cat /etc/os-release', risk: 'low', note: '识别 Linux 发行版与版本信息', tags: ['系统', '版本', 'linux'] },
    { name: '查看内核与架构', command: 'uname -a', risk: 'low', note: '快速确认内核版本、主机架构', tags: ['系统', '内核'] },
    { name: '查看磁盘使用', command: 'df -h', risk: 'low', note: '排查磁盘空间告警常用命令', tags: ['磁盘', '容量'] },
    { name: '查看内存使用', command: 'free -h', risk: 'low', note: '排查内存压力与 swap 使用', tags: ['内存', '性能'] },
    { name: '查看 CPU 负载', command: 'uptime', risk: 'low', note: '观察负载趋势，判断是否过载', tags: ['cpu', '性能'] },
    { name: '查看进程资源', command: 'top -b -n 1 | head -n 20', risk: 'low', note: '定位高占用进程', tags: ['进程', '性能'] },
    { name: '查看端口监听', command: 'ss -lntp', risk: 'low', note: '排查服务端口是否正常监听', tags: ['网络', '端口'] },
    { name: '查看防火墙规则', command: 'iptables -L -n', risk: 'medium', note: '检查网络策略是否拦截流量', tags: ['防火墙', '网络'] },
    { name: '重载 systemd 服务', command: 'systemctl daemon-reload', risk: 'medium', note: '服务单元变更后生效', tags: ['systemd', '服务'] },
    { name: '重启指定服务', command: 'systemctl restart <service_name>', risk: 'medium', note: '会短暂中断服务，请先评估影响', tags: ['systemd', '服务', '重启'] },
    { name: '查看服务状态', command: 'systemctl status <service_name> --no-pager', risk: 'low', note: '确认服务是否启动、是否报错', tags: ['systemd', '服务'] },
    { name: '实时查看日志', command: 'journalctl -u <service_name> -f', risk: 'low', note: '实时跟踪服务日志输出', tags: ['日志', '服务'] },
    { name: '列出网络连接', command: 'netstat -ntlp', risk: 'low', note: '兼容旧系统的端口/进程关联检查', tags: ['网络', '端口'] },
    { name: '批量杀进程（危险示例）', command: 'pkill -f <keyword>', risk: 'high', note: '高风险：可能误杀关键进程', tags: ['高风险', '进程'] },
    { name: '递归删除目录（危险示例）', command: 'rm -rf <path>', risk: 'high', note: '高风险：不可恢复，务必二次确认', tags: ['高风险', '删除'] },
];

function getEl(id) {
    return document.getElementById(id);
}

function genId(prefix) {
    return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function escapeHtml(text) {
    return String(text ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function activeSession() {
    return sessions.find((s) => s.id === activeSessionId);
}

function defaultDialogRoles() {
    return [
        {
            name: '自进化助手',
            system_prompt: '你是自进化控制平面助手，帮助用户规划、实现、验证和复盘系统自进化能力。回答要简洁、可执行，并标注风险与回滚方案。',
            ai_endpoint_id: null,
            is_active: 1,
            role_scope: DIALOG_SCOPE,
            bound_device_types: [],
        },
        {
            name: '架构师',
            system_prompt: '你是系统架构师，关注可扩展性、可维护性和安全边界，回答时给出方案权衡、风险和落地步骤。',
            ai_endpoint_id: null,
            is_active: 0,
            role_scope: DIALOG_SCOPE,
            bound_device_types: [],
        },
    ];
}

async function ensureDialogDefaultRoles() {
    const defaults = defaultDialogRoles();
    for (const role of defaults) {
        await api.addRole(role, { scope: DIALOG_SCOPE });
    }
}

async function loadDialogRoles() {
    dialogRoles = await api.getRoles({ scope: DIALOG_SCOPE });
    if (!Array.isArray(dialogRoles) || !dialogRoles.length) {
        await ensureDialogDefaultRoles();
        dialogRoles = await api.getRoles({ scope: DIALOG_SCOPE });
    }
    const active = dialogRoles.find((r) => Number(r.is_active) === 1);
    activeDialogRoleId = String(active?.id || dialogRoles[0]?.id || '');
}

function currentDialogRole() {
    const current = dialogRoles.find((r) => String(r.id) === String(activeDialogRoleId));
    return current || dialogRoles[0] || null;
}

function renderDialogRoleSelect() {
    const roleSelect = getEl('ai-dialog-role-select');
    if (!roleSelect) return;
    roleSelect.innerHTML = dialogRoles
        .map((r) => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.name || '未命名角色')}</option>`)
        .join('');
    roleSelect.value = String(activeDialogRoleId || '');
}

function renderRoleManageList() {
    const list = getEl('ai-dialog-role-manage-list');
    if (!list) return;
    list.innerHTML = dialogRoles.map((r) => `
        <div class="ai-dialog-role-manage-item ${String(r.id) === String(activeDialogRoleId) ? 'active' : ''}" data-role-manage-id="${r.id}">
            <div class="title">${escapeHtml(r.name || '未命名角色')}</div>
            <div class="meta">${Number(r.is_active) === 1 ? '默认角色' : '普通角色'}</div>
        </div>
    `).join('');
}

function fillRoleForm(role) {
    const idInput = getEl('ai-dialog-role-id');
    const nameInput = getEl('ai-dialog-role-name');
    const promptInput = getEl('ai-dialog-role-prompt');
    if (!(idInput instanceof HTMLInputElement) || !(nameInput instanceof HTMLInputElement) || !(promptInput instanceof HTMLTextAreaElement)) return;
    if (!role) {
        idInput.value = '';
        nameInput.value = '';
        promptInput.value = '';
        return;
    }
    idInput.value = String(role.id || '');
    nameInput.value = String(role.name || '');
    promptInput.value = String(role.system_prompt || '');
}

function openRoleManageModal() {
    renderRoleManageList();
    fillRoleForm(currentDialogRole());
    showModal('ai-dialog-role-manage-modal');
}

function closeRoleManageModal() {
    closeModal('ai-dialog-role-manage-modal');
}

function downloadRolesAsJson() {
    const payload = {
        scope: DIALOG_SCOPE,
        exported_at: new Date().toISOString(),
        schema_hint: DIALOG_OUTPUT_SCHEMA_HINT,
        roles: dialogRoles.map((r) => ({
            name: r.name,
            system_prompt: r.system_prompt,
            ai_endpoint_id: r.ai_endpoint_id ?? null,
            is_active: Number(r.is_active) === 1 ? 1 : 0,
            role_scope: DIALOG_SCOPE,
            bound_device_types: [],
        })),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `ai-dialog-roles-${Date.now()}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
}

async function importRolesFromJson(file) {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const roleList = Array.isArray(parsed) ? parsed : parsed.roles;
    if (!Array.isArray(roleList) || !roleList.length) {
        throw new Error('导入文件没有角色数据');
    }
    const roles = roleList
        .map((item) => ({
            name: String(item.name || '').trim(),
            system_prompt: String(item.system_prompt || '').trim(),
            ai_endpoint_id: item.ai_endpoint_id ?? null,
            is_active: Number(item.is_active) === 1 ? 1 : 0,
            role_scope: DIALOG_SCOPE,
            bound_device_types: [],
        }))
        .filter((item) => item.name && item.system_prompt);
    if (!roles.length) throw new Error('角色数据格式无效');
    await api.importRoles({ roles }, { scope: DIALOG_SCOPE, replace: true });
    await loadDialogRoles();
    renderDialogRoleSelect();
    renderRoleManageList();
    notify(`已导入 ${roles.length} 个角色`, 'success');
}

function startOfDay(ts) {
    const d = new Date(ts);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
}

function getSessionGroupName(updatedAt) {
    const nowStart = startOfDay(Date.now());
    const updatedStart = startOfDay(updatedAt || Date.now());
    const diffDays = Math.floor((nowStart - updatedStart) / (24 * 3600 * 1000));
    if (diffDays <= 0) return '今天';
    if (diffDays <= 7) return '7天内';
    return '更早';
}

function saveState() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.slice(0, 50)));
    localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId || '');
}

function createDefaultSession(tip = '你好，我在这里。可以问我运维、开发、脚本、故障分析等问题。') {
    const sid = genId('session');
    return {
        id: sid,
        title: '新会话',
        pinned: false,
        messages: [{ id: genId('msg'), role: 'system', type: 'system', content: tip, created_at: Date.now() }],
        created_at: Date.now(),
        updated_at: Date.now(),
    };
}

function loadState() {
    try {
        sessions = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    } catch (err) {
        console.warn('解析左侧 AI 对话历史失败，将重置为空:', err);
        sessions = [];
    }
    sessions = sessions.map((s) => ({
        ...s,
        pinned: Boolean(s.pinned),
        messages: Array.isArray(s.messages) ? s.messages : [],
    }));
    if (!sessions.length) sessions.push(createDefaultSession());
    const savedActive = localStorage.getItem(ACTIVE_SESSION_KEY) || '';
    activeSessionId = sessions.some((s) => s.id === savedActive) ? savedActive : sessions[0].id;
}

function appendMessage(role, type, content, extra = {}) {
    const session = activeSession();
    if (!session) return null;
    const msg = { id: genId('msg'), role, type, content, created_at: Date.now(), ...extra };
    session.messages.push(msg);
    session.updated_at = Date.now();
    if (role === 'user' && type === 'text' && session.title === '新会话') {
        session.title = content.slice(0, 16) || '新会话';
    }
    return msg;
}

function removeMessageById(messageId) {
    const session = activeSession();
    if (!session) return;
    const idx = session.messages.findIndex((m) => m.id === messageId);
    if (idx >= 0) {
        session.messages.splice(idx, 1);
        session.updated_at = Date.now();
    }
}

function toMessageText(msg) {
    if (!msg) return '';
    if (msg.type === 'text' || msg.type === 'system') return String(msg.content || '');
    if (msg.type === 'file') return `[文件] ${msg.file_name || ''} ${msg.file_size || ''}`.trim();
    if (msg.type === 'voice') return `[语音] ${msg.duration || ''}`.trim();
    if (msg.type === 'command_card') return `[命令卡] ${msg.command || ''}`.trim();
    if (msg.type === 'task_card') return `[任务卡] ${msg.title || ''} ${msg.status || ''}`.trim();
    if (msg.type === 'status_card') return `[状态卡] ${msg.title || ''} ${msg.status || ''}`.trim();
    return JSON.stringify(msg);
}

function normalizeMessageActions(actions, fallbackTaskId) {
    if (!Array.isArray(actions) || !actions.length) return [];
    return actions
        .map((item) => {
            if (!item || typeof item !== 'object') return null;
            const type = String(item.type || '').trim();
            if (!type) return null;
            const taskId = Number.parseInt(item.task_id ?? fallbackTaskId, 10);
            return {
                type,
                label: String(item.label || '').trim(),
                task_id: Number.isFinite(taskId) ? taskId : undefined,
                command: String(item.command || item?.payload?.command || '').trim(),
                risk: String(item.risk || item?.payload?.risk || '').trim(),
                note: String(item.note || item?.payload?.note || '').trim(),
                payload: item.payload && typeof item.payload === 'object' ? item.payload : undefined,
            };
        })
        .filter(Boolean);
}

function actionLabel(action) {
    if (action?.label) return action.label;
    if (action?.type === 'create_task') return '一键创建任务';
    if (action?.type === 'open_task_detail') return '打开任务详情';
    if (action?.type === 'run_task_async') return '异步执行任务';
    if (action?.type === 'run_command') return '执行命令';
    return '执行动作';
}

function renderInlineActions(msg) {
    const actions = Array.isArray(msg?.actions) ? msg.actions : [];
    if (!actions.length) return '';
    return `
        <div class="ai-inline-actions">
            ${actions.map((action, idx) => `
                <button class="ai-inline-action-btn" data-ai-action="${idx}" data-message-id="${escapeHtml(msg.id)}">
                    ${escapeHtml(actionLabel(action))}
                </button>
            `).join('')}
        </div>
    `;
}

function renderMarkdownContent(text) {
    if (typeof marked === 'undefined') return escapeHtml(text || '');
    return marked.parse(String(text || ''));
}

function beautifyAssistantText(raw) {
    let text = String(raw || '').trim();
    if (!text) return '';
    // 把常见“挤成一行”的编号建议拆分为多行，提升可读性
    text = text.replaceAll(/([^\n])\s*(\d+\.)\s*/g, '$1\n$2 ');
    text = text.replaceAll(/；\s*/g, '；\n');
    text = text.replaceAll(/。\s*(?=[A-Za-z0-9\u4e00-\u9fa5])/g, '。\n');
    // 兜底：连续空白归一
    text = text.replaceAll(/\n{3,}/g, '\n\n');
    return text;
}

function renderMessageActions(msg) {
    return `
        <div class="ai-message-actions" data-message-id="${msg.id}">
            <button class="ai-mini-btn" data-msg-act="copy" title="复制"><i class="fas fa-copy"></i></button>
            <button class="ai-mini-btn" data-msg-act="retry" title="重试"><i class="fas fa-rotate-right"></i></button>
            <button class="ai-mini-btn" data-msg-act="edit" title="编辑后重发"><i class="fas fa-pen"></i></button>
        </div>
    `;
}

function renderRichCard(msg) {
    if (msg.type === 'task_card') {
        const openBtn = msg.task_id ? `<button class="ai-mini-btn" data-open-task="${msg.task_id}" title="打开任务"><i class="fas fa-arrow-up-right-from-square"></i></button>` : '';
        return `
            <div class="ai-rich-card ai-task-card">
                <div class="ai-rich-card-title"><i class="fas fa-list-check"></i> ${escapeHtml(msg.title || '任务')} ${openBtn}</div>
                <div class="ai-rich-card-body">${escapeHtml(msg.description || '')}</div>
                <div class="ai-rich-card-meta">状态: ${escapeHtml(msg.status || 'pending')}</div>
                ${renderInlineActions(msg)}
            </div>
        `;
    }
    if (msg.type === 'command_card') {
        const risk = msg.risk || 'medium';
        return `
            <div class="ai-rich-card ai-command-card ${risk === 'high' ? 'dangerous' : ''}">
                <div class="ai-rich-card-title"><i class="fas fa-terminal"></i> 命令卡</div>
                <div class="ai-rich-card-body"><code>${escapeHtml(msg.command || '')}</code></div>
                <div class="ai-rich-card-meta">风险: ${escapeHtml(risk)}</div>
                ${msg.note ? `<div class="ai-rich-card-meta">${escapeHtml(msg.note)}</div>` : ''}
                ${renderInlineActions(msg)}
            </div>
        `;
    }
    if (msg.type === 'status_card') {
        const openBtn = msg.task_id ? `<button class="ai-mini-btn" data-open-task="${msg.task_id}" title="打开任务"><i class="fas fa-arrow-up-right-from-square"></i></button>` : '';
        return `
            <div class="ai-rich-card ai-status-card">
                <div class="ai-rich-card-title"><i class="fas fa-wave-square"></i> ${escapeHtml(msg.title || '执行状态')} ${openBtn}</div>
                <div class="ai-rich-card-meta">状态: ${escapeHtml(msg.status || 'running')}</div>
                <div class="ai-rich-card-body">${escapeHtml(msg.detail || '')}</div>
                ${renderInlineActions(msg)}
            </div>
        `;
    }
    return '';
}

function renderMessage(msg) {
    const list = getEl('ai-dialog-messages');
    if (!list) return;
    const item = document.createElement('div');
    item.className = `message ${msg.role}${msg.streaming ? ' ai-streaming' : ''}`;
    item.dataset.messageId = msg.id;
    const checkedAttr = selectedMessageIds.has(msg.id) ? 'checked' : '';
    const selectBox = messageSelectMode
        ? `<label class="ai-msg-check"><input type="checkbox" data-msg-check="${msg.id}" ${checkedAttr} /></label>`
        : '';
    if (msg.type === 'file') {
        item.innerHTML = `${selectBox}<div class="message-text-content"><i class="fas fa-file"></i> ${escapeHtml(msg.file_name || '文件')} (${escapeHtml(msg.file_size || '-')})</div>${renderMessageActions(msg)}`;
    } else if (msg.type === 'voice') {
        item.innerHTML = `${selectBox}<div class="message-text-content"><i class="fas fa-microphone"></i> 语音消息（${escapeHtml(msg.duration || '0s')}）</div>${renderMessageActions(msg)}`;
    } else if (msg.type === 'task_card' || msg.type === 'command_card' || msg.type === 'status_card') {
        item.innerHTML = `${selectBox}${renderRichCard(msg)}${renderMessageActions(msg)}`;
    } else {
        const pretty = beautifyAssistantText(msg.content || '');
        const textCard = `
            <div class="ai-rich-card ai-text-card">
                <div class="ai-rich-card-title"><i class="fas fa-message"></i> 分析结果</div>
                <div class="message-text-content ai-text-card-body">${renderMarkdownContent(pretty)}</div>
                ${renderInlineActions(msg)}
            </div>
        `;
        item.innerHTML = `${selectBox}${textCard}${renderMessageActions(msg)}`;
    }
    list.appendChild(item);
}

function renderMessages() {
    const list = getEl('ai-dialog-messages');
    if (!list) return;
    list.innerHTML = '';
    const session = activeSession();
    if (!session) return;
    session.messages.forEach((msg) => renderMessage(msg));
    updateBatchBar();
    list.scrollTop = list.scrollHeight;
}

function renderSessionList() {
    const list = getEl('ai-dialog-session-list');
    if (!list) return;
    const ordered = [...sessions].sort((a, b) => {
        if (Boolean(a.pinned) !== Boolean(b.pinned)) return Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
        return (b.updated_at || 0) - (a.updated_at || 0);
    });
    const filtered = ordered.filter((s) => {
        if (!sessionSearchKeyword) return true;
        const kw = sessionSearchKeyword.toLowerCase();
        const inTitle = String(s.title || '').toLowerCase().includes(kw);
        const inMsg = (s.messages || []).some((m) => toMessageText(m).toLowerCase().includes(kw));
        return inTitle || inMsg;
    });
    const grouped = { 今天: [], '7天内': [], 更早: [] };
    filtered.forEach((s) => {
        grouped[getSessionGroupName(s.updated_at)].push(s);
    });
    const renderOne = (s) => `
        <div class="ai-dialog-session-item ${s.id === activeSessionId ? 'active' : ''}" data-session-id="${s.id}">
            <div class="ai-dialog-session-row">
                <div class="ai-dialog-session-title">${s.pinned ? '<i class="fas fa-thumbtack"></i> ' : ''}${escapeHtml(s.title || '会话')}</div>
                <div class="ai-dialog-session-ops">
                    <button class="ai-mini-btn" data-session-act="pin" data-session-id="${s.id}" title="${s.pinned ? '取消置顶' : '置顶'}"><i class="fas fa-thumbtack"></i></button>
                    <button class="ai-mini-btn" data-session-act="rename" data-session-id="${s.id}" title="重命名"><i class="fas fa-pen"></i></button>
                    <button class="ai-mini-btn" data-session-act="delete" data-session-id="${s.id}" title="删除"><i class="fas fa-trash"></i></button>
                </div>
            </div>
            <div class="ai-dialog-session-time">${new Date(s.updated_at || Date.now()).toLocaleString()}</div>
        </div>
    `;
    const blocks = ['今天', '7天内', '更早']
        .filter((g) => grouped[g].length > 0)
        .map((g) => `
            <div class="ai-dialog-session-group">
                <div class="ai-dialog-session-group-title">${g}</div>
                ${grouped[g].map(renderOne).join('')}
            </div>
        `);
    list.innerHTML = blocks.join('') || `
        <div style="color:#8d939a;font-size:12px;padding:8px;">无匹配会话</div>
    `;
}

function setProcessing(processing) {
    isProcessing = processing;
    const btn = getEl('ai-dialog-send-btn');
    if (!btn) return;
    btn.disabled = processing;
    btn.classList.toggle('processing', processing);
    btn.innerHTML = processing ? '<i class="fas fa-spinner fa-spin"></i><span>处理中</span>' : '<i class="fas fa-paper-plane"></i><span>发送</span>';
}

function normalizeCommandQueryKeyword(rawKeyword) {
    const kw = String(rawKeyword || '').trim();
    if (!kw) return '';
    const lowered = kw.toLowerCase();
    const compact = kw.replaceAll(/\s+/g, '');
    const asHelp = new Set(['?', '？', 'help', '/help', 'cmd', '/cmd']);
    const asList = new Set(['命令查询', '查询命令', '命令列表', '可用命令', '查看命令', '命令帮助', '帮助']);
    if (asHelp.has(lowered) || asList.has(compact)) return '';
    return kw;
}

function parseRichInput(text) {
    const trimmed = String(text || '').trim();
    if (/^\/cmd\s*([?？])?$/i.test(trimmed)) {
        return { type: 'command_query', payload: { keyword: '' } };
    }
    if (trimmed.startsWith('/task ')) {
        const data = trimmed.slice('/task '.length);
        const [idOrTitle, description, status] = data.split('|').map((s) => s.trim());
        const taskId = Number.parseInt(idOrTitle, 10);
        if (Number.isFinite(taskId)) {
            return { type: 'task_card', payload: { task_id: taskId, title: `任务 #${taskId}`, description: description || '', status: status || 'pending' } };
        }
        return { type: 'task_card', payload: { title: idOrTitle || '任务', description: description || '', status: status || 'pending' } };
    }
    if (trimmed.startsWith('/cmd ')) {
        const data = trimmed.slice('/cmd '.length);
        if (!data) return { type: 'command_query', payload: { keyword: '' } };
        if (!data.includes('|')) return { type: 'command_query', payload: { keyword: normalizeCommandQueryKeyword(data) } };
        const [command, risk, note] = data.split('|').map((s) => s.trim());
        return {
            type: 'command_card',
            payload: {
                command: command || '',
                risk: (risk || 'medium').toLowerCase(),
                note: note || '',
            },
        };
    }
    if (trimmed === '/cmd?' || trimmed === '/cmd？' || trimmed === '/cmdhelp' || trimmed === '/cmd-help' || trimmed === '/cmd help') {
        return { type: 'command_query', payload: { keyword: '' } };
    }
    if (trimmed.startsWith('/status ')) {
        const data = trimmed.slice('/status '.length);
        const [title, status, detail] = data.split('|').map((s) => s.trim());
        const maybeId = Number.parseInt(title, 10);
        if (Number.isFinite(maybeId)) {
            return { type: 'status_card', payload: { task_id: maybeId, title: `任务 #${maybeId} 执行状态`, status: status || 'running', detail: detail || '' } };
        }
        return { type: 'status_card', payload: { title: title || '执行状态', status: status || 'running', detail: detail || '' } };
    }
    return null;
}

function queryCommandTemplates(keyword) {
    const kw = String(keyword || '').trim().toLowerCase();
    if (!kw) return BUILTIN_COMMAND_TEMPLATES.slice(0, 8);
    const scored = BUILTIN_COMMAND_TEMPLATES.map((item) => {
        const hay = `${item.name} ${item.command} ${item.note} ${(item.tags || []).join(' ')}`.toLowerCase();
        let score = 0;
        if (item.name.toLowerCase().includes(kw)) score += 4;
        if (item.command.toLowerCase().includes(kw)) score += 3;
        if ((item.tags || []).some((t) => String(t).toLowerCase().includes(kw))) score += 2;
        if (hay.includes(kw)) score += 1;
        return { item, score };
    })
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score);
    return scored.slice(0, 8).map((x) => x.item);
}

function showCommandQueryResult(rawInput, keyword) {
    appendMessage('user', 'text', rawInput);
    const matches = queryCommandTemplates(keyword);
    if (!matches.length) {
        appendMessage('assistant', 'text', `未找到与“${keyword}”匹配的命令模板。你可以尝试关键词：端口、服务、日志、磁盘、高风险。`);
        return;
    }
    appendMessage(
        'assistant',
        'text',
        keyword
            ? `命令查询结果（关键词：${keyword}），共 ${matches.length} 条：`
            : '命令查询结果（默认展示常用命令），你也可以输入：/cmd 端口 或 /cmd 日志',
    );
    matches.forEach((x) => {
        appendMessage('assistant', 'command_card', '', {
            command: x.command,
            risk: x.risk,
            note: `${x.name}：${x.note}`,
        });
    });
}

function tryParseJson(text) {
    try {
        return JSON.parse(text);
    } catch (parseErr) {
        console.debug('AI 对话结构化解析失败，将回退纯文本渲染:', parseErr);
        return null;
    }
}

function extractJsonCandidate(raw) {
    const text = String(raw || '').trim();
    if (!text) return '';
    const fenced = /```(?:json)?\s*([\s\S]*?)```/i.exec(text);
    if (fenced?.[1]) return fenced[1].trim();
    return text;
}

function buildJsonCandidates(raw) {
    const text = String(raw || '').trim();
    if (!text) return [];
    const candidates = [];
    const direct = extractJsonCandidate(text);
    if (direct) candidates.push(direct);
    const firstObj = text.indexOf('{');
    const lastObj = text.lastIndexOf('}');
    if (firstObj >= 0 && lastObj > firstObj) {
        candidates.push(text.slice(firstObj, lastObj + 1).trim());
    }
    const firstArr = text.indexOf('[');
    const lastArr = text.lastIndexOf(']');
    if (firstArr >= 0 && lastArr > firstArr) {
        candidates.push(text.slice(firstArr, lastArr + 1).trim());
    }
    const cleaned = text.replace(/^[`'"\s]+/, '').replace(/[`'"\s]+$/, '');
    if (cleaned && cleaned !== text) {
        candidates.push(cleaned);
    }
    // 去重，按顺序尝试
    return Array.from(new Set(candidates));
}

function normalizeStructuredMessage(item) {
    if (!item || typeof item !== 'object') return null;
    const type = String(item.type || '').trim();
    switch (type) {
        case 'text':
            return {
                role: 'assistant',
                type: 'text',
                content: String(item.text || '').trim() || '（空响应）',
                actions: normalizeMessageActions(item.actions),
            };
        case 'task_card':
            {
                const taskId = Number.isFinite(Number(item.task_id)) ? Number(item.task_id) : undefined;
                return {
                    role: 'assistant',
                    type: 'task_card',
                    content: '',
                    task_id: taskId,
                    title: String(item.title || '任务'),
                    description: String(item.description || ''),
                    status: String(item.status || 'pending'),
                    actions: normalizeMessageActions(item.actions, taskId),
                };
            }
        case 'command_card':
            return {
                role: 'assistant',
                type: 'command_card',
                content: '',
                command: String(item.command || ''),
                risk: String(item.risk || 'medium'),
                note: String(item.note || ''),
                actions: normalizeMessageActions(item.actions),
            };
        case 'status_card':
            {
                const taskId = Number.isFinite(Number(item.task_id)) ? Number(item.task_id) : undefined;
                return {
                    role: 'assistant',
                    type: 'status_card',
                    content: '',
                    task_id: taskId,
                    title: String(item.title || '执行状态'),
                    status: String(item.status || 'running'),
                    detail: String(item.detail || ''),
                    actions: normalizeMessageActions(item.actions, taskId),
                };
            }
        default:
            return null;
    }
}

function normalizeStructuredMessages(payload) {
    if (!payload || typeof payload !== 'object') return [];
    const list = Array.isArray(payload.messages) ? payload.messages : [];
    return list
        .map((item) => normalizeStructuredMessage(item))
        .filter(Boolean);
}

function parseStructuredAssistantResponse(raw) {
    const candidates = buildJsonCandidates(raw);
    for (const candidate of candidates) {
        const parsed = tryParseJson(candidate);
        const messages = normalizeStructuredMessages(parsed);
        if (messages.length) return withFallbackTaskActions(messages, raw);
    }
    return [{ role: 'assistant', type: 'text', content: String(raw || '').trim() || '（空响应）' }];
}

function hasCreateTaskAction(msg) {
    const actions = Array.isArray(msg?.actions) ? msg.actions : [];
    return actions.some((a) => String(a?.type || '').trim() === 'create_task');
}

function parseQuotedValue(raw, keyPattern) {
    const re = new RegExp(`(?:^|\\n|\\r)\\s*(?:[-*]\\s*)?(?:${keyPattern})\\s*[:：]\\s*(.+)`, 'i');
    const m = re.exec(String(raw || ''));
    if (!m?.[1]) return '';
    return String(m[1]).trim().replace(/^["'`]+|["'`]+$/g, '');
}

function parseArrayJsonValue(raw, keyPattern) {
    const re = new RegExp(`(?:^|\\n|\\r)\\s*(?:[-*]\\s*)?(?:${keyPattern})\\s*[:：]\\s*(\\[[^\\n\\r]*\\])`, 'i');
    const m = re.exec(String(raw || ''));
    if (!m?.[1]) return [];
    try {
        const v = JSON.parse(m[1]);
        return Array.isArray(v) ? v.map((x) => String(x || '').trim()).filter(Boolean) : [];
    } catch {
        return [];
    }
}

function parseNumberedCommands(raw, sectionKeyPattern) {
    const text = String(raw || '');
    const startRe = new RegExp(`(?:^|\\n|\\r)\\s*(?:[-*]\\s*)?(?:${sectionKeyPattern})\\s*[:：]\\s*(?:\\n|\\r|$)`, 'i');
    const startMatch = startRe.exec(text);
    if (!startMatch) return [];
    const startIdx = startMatch.index + startMatch[0].length;
    const tail = text.slice(startIdx);
    const stopIdx = tail.search(/\n\s*(?:[-*]\s*)?(?:payload_json\.)?(?:verify_commands|allow_write_paths)\s*[:：]/i);
    const block = stopIdx >= 0 ? tail.slice(0, stopIdx) : tail;
    const lines = block.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    const list = [];
    for (const line of lines) {
        const m = /^(\d+[\.\)]|[-*])\s*(.+)$/.exec(line);
        if (!m?.[2]) continue;
        const cmd = String(m[2]).trim().replace(/^["'`]+|["'`]+$/g, '');
        if (cmd) list.push(cmd);
    }
    return list;
}

function extractCreateTaskPayloadFromRaw(raw, fallbackTitle) {
    const title = parseQuotedValue(raw, 'title|task\\.title') || String(fallbackTitle || '').trim() || 'AI动作创建任务';
    const taskType = parseQuotedValue(raw, 'task_type|task\\.type') || 'fix';
    const scope = parseQuotedValue(raw, 'scope') || 'backend';
    const riskLevel = parseQuotedValue(raw, 'risk_level|risk\\.level') || 'medium';
    const acceptance = parseQuotedValue(raw, 'acceptance_criteria|acceptance\\.criteria');
    const rollback = parseQuotedValue(raw, 'rollback_plan|rollback\\.plan');
    const allowWritePaths = parseArrayJsonValue(raw, 'payload_json\\.allow_write_paths|allow_write_paths');
    const commands = parseNumberedCommands(raw, 'payload_json\\.commands|commands');
    const verifyCommands = parseNumberedCommands(raw, 'payload_json\\.verify_commands|verify_commands');
    if (!commands.length) return null;
    return {
        title,
        task_type: taskType,
        scope,
        risk_level: riskLevel,
        acceptance_criteria: acceptance,
        rollback_plan: rollback,
        payload_json: {
            commands,
            verify_commands: verifyCommands,
            allow_write_paths: allowWritePaths.length ? allowWritePaths : ['frontend'],
        },
    };
}

function withFallbackTaskActions(messages, raw) {
    if (!Array.isArray(messages) || !messages.length) return [];
    const hasAnyAction = messages.some((m) => Array.isArray(m?.actions) && m.actions.length);
    if (hasAnyAction) return messages;
    const firstTaskCard = messages.find((m) => m?.type === 'task_card' && !Number.isFinite(Number(m?.task_id)));
    if (!firstTaskCard || hasCreateTaskAction(firstTaskCard)) return messages;
    const payload = extractCreateTaskPayloadFromRaw(raw, firstTaskCard?.title);
    if (!payload) return messages;
    firstTaskCard.actions = normalizeMessageActions([
        { type: 'create_task', label: '创建任务', payload },
        { type: 'run_task_async', label: '异步执行任务' },
    ]);
    return messages;
}

function openSocket(messages) {
    if (pageSocket) {
        pageSocket.onclose = null;
        pageSocket.close();
    }
    const protocol = globalThis.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const mode = getEl('ai-dialog-mode-select')?.value || 'agent';
    const selectedRole = currentDialogRole();
    const token = localStorage.getItem('xterm_token') || '';
    const query = [`mode=${encodeURIComponent(mode)}`, 'profile=evolution_dialog'];
    if (token) query.push(`token=${encodeURIComponent(token)}`);
    const wsUrl = `${protocol}//${globalThis.location.host}/ws/ai?${query.join('&')}`;

    setProcessing(true);
    pageSocket = new WebSocket(wsUrl);
    let full = '';
    const placeholder = appendMessage('assistant', 'text', '', { streaming: true });
    saveState();
    renderMessages();

    pageSocket.onopen = () => {
        pageSocket.send(JSON.stringify({
            mode,
            messages,
            dialog_role_name: selectedRole?.name || '自进化助手',
            dialog_system_prompt: selectedRole?.system_prompt || '',
        }));
    };

    pageSocket.onmessage = (event) => {
        const raw = event.data;
        if (raw === '[DONE]') {
            if (placeholder?.id) removeMessageById(placeholder.id);
            const structured = parseStructuredAssistantResponse(full);
            structured.forEach((msg) => {
                appendMessage(msg.role, msg.type, msg.content || '', msg);
            });
            saveState();
            renderMessages();
            renderSessionList();
            setProcessing(false);
            return;
        }
        if (raw.startsWith('[AI Error:')) {
            notify(raw, 'error');
            if (placeholder?.id) removeMessageById(placeholder.id);
            appendMessage('assistant', 'system', raw);
            saveState();
            renderMessages();
            renderSessionList();
            setProcessing(false);
            return;
        }
        full += raw;
        const target = activeSession()?.messages.find((m) => m.id === placeholder?.id);
        if (target) target.content = full;
        renderMessages();
    };

    pageSocket.onclose = () => {
        setProcessing(false);
        const target = activeSession()?.messages.find((m) => m.id === placeholder?.id);
        if (target) delete target.streaming;
    };
}

function sendMessage(textOverride = '') {
    const input = getEl('ai-dialog-input');
    if (!input || isProcessing) return;
    const text = (textOverride || input.value).trim();
    if (!text) return;
    input.value = '';

    const rich = parseRichInput(text);
    if (rich) {
        if (rich.type === 'command_query') {
            showCommandQueryResult(text, rich.payload?.keyword || '');
            saveState();
            renderMessages();
            renderSessionList();
            return;
        }
        appendMessage('user', rich.type, '', rich.payload);
        saveState();
        renderMessages();
        renderSessionList();
        notify('已插入富消息卡片', 'success');
        return;
    }

    appendMessage('user', 'text', text);
    saveState();
    renderMessages();
    renderSessionList();
    const messages = (activeSession()?.messages || [])
        .filter((m) => m.type === 'text' || m.type === 'system')
        .map((m) => ({ role: m.role, content: String(m.content || '') }));
    openSocket(messages);
}

function createSession() {
    const session = createDefaultSession('已创建新会话，你可以开始提问。');
    sessions.unshift(session);
    activeSessionId = session.id;
    saveState();
    renderSessionList();
    renderMessages();
}

function setVoiceStateLabel(text) {
    const el = getEl('ai-dialog-voice-status');
    if (el) el.textContent = text;
}

function toggleVoiceRecord() {
    if (!isVoiceRecording) {
        isVoiceRecording = true;
        voiceStartAt = Date.now();
        setVoiceStateLabel('录音中 00:00');
        const btn = getEl('ai-dialog-voice-btn');
        if (btn) btn.classList.add('active');
        voiceTickTimer = setInterval(() => {
            const sec = Math.max(0, Math.floor((Date.now() - voiceStartAt) / 1000));
            const mm = String(Math.floor(sec / 60)).padStart(2, '0');
            const ss = String(sec % 60).padStart(2, '0');
            setVoiceStateLabel(`录音中 ${mm}:${ss}`);
        }, 500);
        return;
    }
    isVoiceRecording = false;
    const btn = getEl('ai-dialog-voice-btn');
    if (btn) btn.classList.remove('active');
    if (voiceTickTimer) clearInterval(voiceTickTimer);
    voiceTickTimer = null;
    const durationSec = Math.max(1, Math.floor((Date.now() - voiceStartAt) / 1000));
    const duration = `${durationSec}s`;
    appendMessage('user', 'voice', '', { duration });
    saveState();
    renderMessages();
    renderSessionList();
    setVoiceStateLabel(`语音已发送（${duration}）`);
}

async function addUploadTask(file) {
    const id = genId('upload');
    const list = getEl('ai-dialog-upload-list');
    if (!list) return;
    const row = document.createElement('div');
    row.className = 'ai-upload-item';
    row.id = id;
    row.innerHTML = `
        <div class="ai-upload-name">${escapeHtml(file.name)}</div>
        <div class="ai-upload-bar"><i style="width:0%"></i></div>
        <div class="ai-upload-pct">0%</div>
    `;
    list.prepend(row);

    const bar = row.querySelector('.ai-upload-bar i');
    const pct = row.querySelector('.ai-upload-pct');
    const setProgress = (n) => {
        const val = Math.max(0, Math.min(100, Number(n) || 0));
        if (bar instanceof HTMLElement) bar.style.width = `${val}%`;
        if (pct instanceof HTMLElement) pct.textContent = `${val}%`;
    };

    const session = activeSession();
    if (!session) return;
    try {
        const chunkSize = 256 * 1024;
        const init = await api.initAIDialogUpload({
            session_id: session.id,
            file_name: file.name,
            file_size: file.size,
            chunk_size: chunkSize,
            mime_type: file.type || '',
        });
        const uploadId = init.upload_id;
        const totalChunks = init.total_chunks;
        const uploadedInfo = await api.getAIDialogUploadChunks(uploadId);
        const uploadedSet = new Set((uploadedInfo?.uploaded_chunk_indexes || []).map(Number));
        const poller = setInterval(async () => {
            try {
                const progress = await api.getAIDialogUploadProgress(uploadId);
                setProgress(progress.progress);
            } catch (pollErr) {
                console.warn('上传进度拉取失败:', pollErr);
            }
        }, 500);
        for (let i = 0; i < totalChunks; i += 1) {
            if (uploadedSet.has(i)) continue;
            const start = i * chunkSize;
            const end = Math.min(file.size, start + chunkSize);
            const blob = file.slice(start, end);
            await api.uploadAIDialogChunk(uploadId, i, blob, `${file.name}.part`);
            const progress = await api.getAIDialogUploadProgress(uploadId);
            setProgress(progress.progress);
        }
        await api.completeAIDialogUpload(uploadId, session.id);
        clearInterval(poller);
        setProgress(100);
        appendMessage('user', 'file', '', { file_name: file.name, file_size: `${Math.max(1, Math.ceil(file.size / 1024))}KB` });
        saveState();
        renderMessages();
        renderSessionList();
        notify(`文件上传完成: ${file.name}`, 'success');
        setTimeout(() => row.remove(), 1000);
    } catch (err) {
        setProgress(0);
        notify(`文件上传失败: ${err?.message || err}`, 'error');
    }
}

function updateBatchBar() {
    const bar = getEl('ai-dialog-batch-bar');
    const countEl = getEl('ai-dialog-batch-count');
    if (!bar || !countEl) return;
    if (!messageSelectMode) {
        bar.style.display = 'none';
        return;
    }
    bar.style.display = 'flex';
    countEl.textContent = `已选 ${selectedMessageIds.size} 条`;
}

function getSelectedMessages() {
    const session = activeSession();
    if (!session) return [];
    return session.messages.filter((m) => selectedMessageIds.has(m.id));
}

async function openTaskFromCard(msg) {
    const taskId = Number.parseInt(msg?.task_id, 10);
    if (!Number.isFinite(taskId)) return false;
    const nav = document.querySelector('.nav-item[data-view="task-center-view"]');
    if (nav instanceof HTMLElement) nav.click();
    try {
        if (typeof globalThis.loadEvolutionTasks === 'function') {
            await globalThis.loadEvolutionTasks();
        }
        if (typeof globalThis.showEvolutionTaskDetail === 'function') {
            await globalThis.showEvolutionTaskDetail(taskId);
        }
        return true;
    } catch (err) {
        notify(`打开任务失败: ${err?.message || err}`, 'error');
        return false;
    }
}

async function createTaskFromAction(action) {
    const payload = action?.payload && typeof action.payload === 'object' ? action.payload : null;
    if (!payload) {
        notify('创建任务失败：缺少 payload', 'error');
        return false;
    }
    const taskData = {
        title: String(payload.title || 'AI动作创建任务').trim(),
        description: String(payload.description || '').trim(),
        task_type: String(payload.task_type || 'fix').trim() || 'fix',
        scope: String(payload.scope || 'backend').trim() || 'backend',
        risk_level: String(payload.risk_level || 'medium').trim() || 'medium',
        max_retries: Number(payload.max_retries || 100) || 100,
        source: 'ai',
        payload_json: payload.payload_json && typeof payload.payload_json === 'object' ? payload.payload_json : (payload.payload_json || {}),
    };
    const commands = Array.isArray(taskData.payload_json?.commands) ? taskData.payload_json.commands.filter(Boolean) : [];
    if (!commands.length) {
        notify('创建任务失败：payload_json.commands 为空，请让 AI 返回可执行步骤', 'error');
        return null;
    }
    const ret = await api.createEvolutionTask(taskData);
    const taskId = Number.parseInt(ret?.id, 10);
    if (!Number.isFinite(taskId)) {
        notify('任务已创建，但未返回任务ID', 'warning');
        return null;
    }
    notify(`已创建任务 #${taskId}`, 'success');
    await openTaskFromCard({ task_id: taskId });
    return taskId;
}

async function runTaskAsyncFromAction(action, msg) {
    const fallbackTaskId = Number.parseInt(msg?.task_id, 10);
    const taskId = Number.parseInt(action?.task_id, 10);
    const resolvedId = Number.isFinite(taskId) ? taskId : fallbackTaskId;
    if (!Number.isFinite(resolvedId)) {
        notify('异步执行失败：缺少 task_id', 'error');
        return false;
    }
    await api.runEvolutionTaskAsync(resolvedId);
    notify(`任务 #${resolvedId} 已加入异步执行`, 'success');
    await openTaskFromCard({ task_id: resolvedId });
    return true;
}

async function runCommandFromAction(action, msg) {
    const command = String(action?.command || action?.payload?.command || msg?.command || '').trim();
    if (!command) {
        notify('执行命令失败：缺少 command', 'error');
        return false;
    }
    const activeTab = globalThis.store?.activeTab;
    if (activeTab?.socket) {
        try {
            activeTab.socket.send(JSON.stringify({ type: 'data', data: `${command}\r` }));
            notify('命令已发送到当前终端', 'success');
            return true;
        } catch (err) {
            notify(`发送命令失败: ${err?.message || err}`, 'error');
            return false;
        }
    }
    try {
        await navigator.clipboard.writeText(command);
        notify('当前无活跃终端，命令已复制到剪贴板', 'warning');
    } catch {
        notify('当前无活跃终端，请手动复制命令执行', 'warning');
    }
    appendMessage('assistant', 'command_card', '', {
        command,
        risk: action?.risk || action?.payload?.risk || 'medium',
        note: action?.note || action?.payload?.note || '请在终端中手动执行该命令',
    });
    saveState();
    renderMessages();
    return false;
}

async function executeInlineAction(action, msg) {
    const type = String(action?.type || '').trim();
    if (!type) return false;
    if (type === 'create_task') {
        const createdTaskId = await createTaskFromAction(action);
        if (Number.isFinite(createdTaskId)) {
            // 让同一条消息后续动作可直接复用 task_id（open/run）
            action.task_id = createdTaskId;
            if (msg && typeof msg === 'object') {
                msg.task_id = createdTaskId;
                if (Array.isArray(msg.actions)) {
                    msg.actions.forEach((a) => {
                        const t = String(a?.type || '').trim();
                        if (t === 'open_task_detail' || t === 'run_task_async') {
                            a.task_id = createdTaskId;
                        }
                    });
                }
            }
            saveState();
            renderMessages();
            return true;
        }
        return false;
    }
    if (type === 'open_task_detail') return openTaskFromCard({ task_id: action?.task_id ?? msg?.task_id });
    if (type === 'run_task_async') return runTaskAsyncFromAction(action, msg);
    if (type === 'run_command') return runCommandFromAction(action, msg);
    notify(`暂不支持动作类型: ${type}`, 'warning');
    return false;
}

function bindSessionListClick() {
    const list = getEl('ai-dialog-session-list');
    if (!list) return;
    const refreshAfterSessionAction = () => {
        saveState();
        renderSessionList();
        renderMessages();
    };
    const handleSessionAction = (act, session) => {
        if (act === 'pin') {
            session.pinned = !session.pinned;
            session.updated_at = Date.now();
            refreshAfterSessionAction();
            return true;
        }
        if (act === 'rename') {
            const next = prompt('输入会话新名称', session.title || '会话');
            if (!next) return true;
            session.title = next.trim().slice(0, 64) || session.title;
            session.updated_at = Date.now();
            refreshAfterSessionAction();
            return true;
        }
        if (act === 'delete') {
            if (!confirm('确认删除该会话？此操作不可撤销。')) return true;
            sessions = sessions.filter((s) => s.id !== session.id);
            if (!sessions.length) sessions.push(createDefaultSession('会话已删除，已自动创建新会话。'));
            if (!sessions.some((s) => s.id === activeSessionId)) activeSessionId = sessions[0].id;
            refreshAfterSessionAction();
            return true;
        }
        return false;
    };
    list.addEventListener('click', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const action = target.closest('[data-session-act]');
        if (action instanceof HTMLElement) {
            const act = action.dataset.sessionAct || '';
            const sid = action.dataset.sessionId || '';
            const session = sessions.find((s) => s.id === sid);
            if (!session) return;
            if (handleSessionAction(act, session)) return;
            return;
        }
        const item = target.closest('[data-session-id]');
        if (!(item instanceof HTMLElement)) return;
        const sessionId = item.dataset.sessionId || '';
        if (!sessionId || sessionId === activeSessionId) return;
        activeSessionId = sessionId;
        saveState();
        renderSessionList();
        renderMessages();
    });
}

function bindMessageActions() {
    const list = getEl('ai-dialog-messages');
    if (!list) return;
    const handleMessageAction = async (act, msg) => {
        if (act === 'copy') {
            await navigator.clipboard.writeText(toMessageText(msg));
            notify('已复制消息内容', 'success');
            return;
        }
        if (act === 'retry') {
            const retryText = toMessageText(msg);
            const input = getEl('ai-dialog-input');
            if (input) input.value = retryText;
            sendMessage(retryText);
            return;
        }
        if (act === 'edit') {
            const edited = prompt('编辑后重发', toMessageText(msg));
            if (!edited) return;
            const input = getEl('ai-dialog-input');
            if (input) input.value = edited;
            sendMessage(edited);
        }
    };
    list.addEventListener('click', async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const actionBtn = target.closest('[data-ai-action]');
        if (actionBtn instanceof HTMLElement) {
            const msgId = actionBtn.dataset.messageId || '';
            const actionIndex = Number.parseInt(actionBtn.dataset.aiAction || '', 10);
            const msg = activeSession()?.messages.find((m) => m.id === msgId);
            const action = msg?.actions?.[actionIndex];
            if (!msg || !action) return;
            await executeInlineAction(action, msg);
            return;
        }
        const openTaskBtn = target.closest('[data-open-task]');
        if (openTaskBtn instanceof HTMLElement) {
            const taskId = Number.parseInt(openTaskBtn.dataset.openTask || '', 10);
            if (Number.isFinite(taskId)) await openTaskFromCard({ task_id: taskId });
            return;
        }
        const btn = target.closest('[data-msg-act]');
        if (!(btn instanceof HTMLElement)) return;
        const act = btn.dataset.msgAct || '';
        const row = btn.closest('[data-message-id]');
        if (!(row instanceof HTMLElement)) return;
        const msgId = row.dataset.messageId || '';
        const msg = activeSession()?.messages.find((m) => m.id === msgId);
        if (!msg) return;
        await handleMessageAction(act, msg);
    });
    list.addEventListener('change', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement)) return;
        const id = target.dataset.msgCheck || '';
        if (!id) return;
        if (target.checked) selectedMessageIds.add(id);
        else selectedMessageIds.delete(id);
        updateBatchBar();
    });
    list.addEventListener('dblclick', async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const row = target.closest('[data-message-id]');
        if (!(row instanceof HTMLElement)) return;
        const msgId = row.dataset.messageId || '';
        const msg = activeSession()?.messages.find((m) => m.id === msgId);
        if (!msg) return;
        if (msg.type === 'task_card' || msg.type === 'status_card') {
            await openTaskFromCard(msg);
        }
    });
}

function bindActions() {
    bindSessionListClick();
    bindMessageActions();
    getEl('ai-dialog-new-session-btn')?.addEventListener('click', createSession);
    getEl('ai-dialog-role-select')?.addEventListener('change', async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLSelectElement)) return;
        activeDialogRoleId = target.value;
        if (!activeDialogRoleId) return;
        await api.setActiveRole(activeDialogRoleId, { scope: DIALOG_SCOPE });
        await loadDialogRoles();
        renderDialogRoleSelect();
    });
    getEl('ai-dialog-role-manage-btn')?.addEventListener('click', openRoleManageModal);
    getEl('ai-dialog-role-close-btn')?.addEventListener('click', closeRoleManageModal);
    getEl('ai-dialog-role-manage-list')?.addEventListener('click', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const item = target.closest('[data-role-manage-id]');
        if (!(item instanceof HTMLElement)) return;
        const roleId = item.dataset.roleManageId || '';
        const role = dialogRoles.find((r) => String(r.id) === roleId);
        if (!role) return;
        activeDialogRoleId = String(role.id);
        fillRoleForm(role);
        renderRoleManageList();
    });
    getEl('ai-dialog-role-new-btn')?.addEventListener('click', () => {
        fillRoleForm(null);
    });
    getEl('ai-dialog-role-save-btn')?.addEventListener('click', async () => {
        const idInput = getEl('ai-dialog-role-id');
        const nameInput = getEl('ai-dialog-role-name');
        const promptInput = getEl('ai-dialog-role-prompt');
        if (!(idInput instanceof HTMLInputElement) || !(nameInput instanceof HTMLInputElement) || !(promptInput instanceof HTMLTextAreaElement)) return;
        const roleId = idInput.value.trim();
        const payload = {
            name: nameInput.value.trim(),
            system_prompt: promptInput.value.trim(),
            ai_endpoint_id: null,
            is_active: 0,
            role_scope: DIALOG_SCOPE,
            bound_device_types: [],
        };
        if (!payload.name || !payload.system_prompt) {
            notify('角色名和提示词不能为空', 'warning');
            return;
        }
        if (roleId) {
            await api.updateRole(roleId, payload, { scope: DIALOG_SCOPE });
        } else {
            await api.addRole(payload, { scope: DIALOG_SCOPE });
        }
        await loadDialogRoles();
        renderDialogRoleSelect();
        renderRoleManageList();
        fillRoleForm(currentDialogRole());
        notify('角色已保存', 'success');
    });
    getEl('ai-dialog-role-delete-btn')?.addEventListener('click', async () => {
        const idInput = getEl('ai-dialog-role-id');
        if (!(idInput instanceof HTMLInputElement)) return;
        const roleId = idInput.value.trim();
        if (!roleId) return;
        if (!confirm('确认删除该角色？')) return;
        await api.deleteRole(roleId, { scope: DIALOG_SCOPE });
        await loadDialogRoles();
        renderDialogRoleSelect();
        renderRoleManageList();
        fillRoleForm(currentDialogRole());
        notify('角色已删除', 'success');
    });
    getEl('ai-dialog-role-activate-btn')?.addEventListener('click', async () => {
        const idInput = getEl('ai-dialog-role-id');
        if (!(idInput instanceof HTMLInputElement)) return;
        const roleId = idInput.value.trim();
        if (!roleId) return;
        await api.setActiveRole(roleId, { scope: DIALOG_SCOPE });
        await loadDialogRoles();
        renderDialogRoleSelect();
        renderRoleManageList();
        fillRoleForm(currentDialogRole());
        notify('已设置为默认角色', 'success');
    });
    getEl('ai-dialog-role-export-btn')?.addEventListener('click', downloadRolesAsJson);
    getEl('ai-dialog-role-import-btn')?.addEventListener('click', () => getEl('ai-dialog-role-import-file')?.click());
    getEl('ai-dialog-role-import-file')?.addEventListener('change', async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement)) return;
        const file = target.files?.[0];
        if (!file) return;
        try {
            await importRolesFromJson(file);
        } catch (err) {
            notify(`导入失败: ${err.message || err}`, 'error');
        } finally {
            target.value = '';
        }
    });
    getEl('ai-dialog-session-search')?.addEventListener('input', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement)) return;
        sessionSearchKeyword = target.value.trim();
        renderSessionList();
    });
    getEl('ai-dialog-select-mode-btn')?.addEventListener('click', () => {
        messageSelectMode = !messageSelectMode;
        if (!messageSelectMode) selectedMessageIds.clear();
        renderMessages();
    });
    getEl('ai-dialog-send-btn')?.addEventListener('click', () => sendMessage());
    getEl('ai-dialog-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    getEl('ai-dialog-clear-btn')?.addEventListener('click', () => {
        const session = activeSession();
        if (!session) return;
        session.messages = [{ id: genId('msg'), role: 'system', type: 'system', content: '会话已清空。', created_at: Date.now() }];
        session.updated_at = Date.now();
        saveState();
        renderMessages();
        renderSessionList();
        notify('已清空当前会话', 'success');
    });
    getEl('ai-dialog-attach-btn')?.addEventListener('click', () => getEl('ai-dialog-file-input')?.click());
    getEl('ai-dialog-voice-btn')?.addEventListener('click', toggleVoiceRecord);
    getEl('ai-dialog-file-btn')?.addEventListener('click', () => getEl('ai-dialog-file-input')?.click());
    getEl('ai-dialog-file-input')?.addEventListener('change', (e) => {
        const target = e.target;
        const file = target?.files?.[0];
        if (!file) return;
        addUploadTask(file);
        target.value = '';
    });
    getEl('ai-dialog-batch-copy')?.addEventListener('click', async () => {
        const msgs = getSelectedMessages();
        const text = msgs.map((m) => toMessageText(m)).join('\n\n');
        if (!text) return;
        await navigator.clipboard.writeText(text);
        notify(`已复制 ${msgs.length} 条消息`, 'success');
    });
    getEl('ai-dialog-batch-delete')?.addEventListener('click', () => {
        const session = activeSession();
        if (!session || !selectedMessageIds.size) return;
        session.messages = session.messages.filter((m) => !selectedMessageIds.has(m.id));
        selectedMessageIds.clear();
        session.updated_at = Date.now();
        saveState();
        renderMessages();
        renderSessionList();
        notify('已删除选中消息', 'success');
    });
    getEl('ai-dialog-batch-retry')?.addEventListener('click', () => {
        const msgs = getSelectedMessages();
        if (!msgs.length) return;
        const merged = msgs.map((m) => toMessageText(m)).join('\n');
        const input = getEl('ai-dialog-input');
        if (input) input.value = merged;
        sendMessage(merged);
    });
}

export async function loadAIDialogPage() {
    loadState();
    await loadDialogRoles();
    renderDialogRoleSelect();
    renderSessionList();
    renderMessages();
    setVoiceStateLabel('语音未启动');
}

export function initAIDialogPageModule() {
    bindActions();
}
