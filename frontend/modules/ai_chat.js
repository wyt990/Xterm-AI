/**
 * AI 助手与对话管理模块 (增强版)
 */
import { store } from './store.js';
import { api } from './api.js';
import { notify, showModal, closeModal, setBtnLoading } from './utils.js';

let aiSocket = null;
let isAiProcessing = false;
let autoExecuteCount = 0; // 自动连续执行次数计数器，防止死循环
const MAX_AUTO_EXECUTE = 5;

// 防重复发送：每个 tab 最近一次已自动发送的 capture 指纹（切回控制台时不再重复发送同一内容）
const lastSentCaptureFingerprint = new Map(); 

// 判定命令是否安全（只读，无重定向，无管道写入）
// deviceType: 设备类型，如 linux/windows/h3c/huawei/cisco/ruijie，用于区分放行规则
function isSafeCommand(cmd, deviceType) {
    const unsafeKeywords = [
        'rm', 'kill', 'mv', 'cp', 'chmod', 'chown', 'reboot', 'shutdown',
        'mkfs', 'dd', 'fdisk', 'parted', 'apt', 'yum', 'dnf', 'wget', 'curl',
        'sh', 'bash', 'python', 'perl', 'ruby', 'gcc', 'make', 'install',
        '>>', '>', '|'
    ];
    // Linux/通用只读命令
    const safeReadCommands = [
        'ls', 'cat', 'df', 'free', 'uptime', 'hostname', 'uname', 'grep',
        'tail', 'head', 'ps', 'netstat', 'ss', 'ip', 'ifconfig', 'ping',
        'cat /etc/', 'top', 'htop', 'iotop', 'vmstat', 'iostat', 'lsof'
    ];
    // Windows PowerShell 只读命令
    const safeWindowsCommands = [
        'Get-', 'Get-ChildItem', 'Get-Content', 'Get-Process', 'Get-Service',
        'dir', 'type', 'hostname', 'systeminfo', 'netstat', 'ipconfig'
    ];
    // H3C/华为：display 为只读查询
    const safeNetworkDisplay = ['display'];
    // 思科/锐捷：show 为只读查询
    const safeNetworkShow = ['show'];

    const cmdTrim = cmd.trim();
    if (cmdTrim.includes('>') || cmdTrim.includes('>>')) return false;

    const dt = (deviceType || '').toLowerCase();
    let safePrefixes = safeReadCommands;
    if (dt === 'windows') {
        safePrefixes = safeReadCommands.concat(safeWindowsCommands);
    } else if (['h3c', 'huawei'].includes(dt)) {
        safePrefixes = safeReadCommands.concat(safeNetworkDisplay);
    } else if (['cisco', 'ruijie'].includes(dt)) {
        safePrefixes = safeReadCommands.concat(safeNetworkShow);
    } else if (dt) {
        // 其他网络设备或未明确类型：display 与 show 均放行（兼容多厂商）
        safePrefixes = safeReadCommands.concat(safeNetworkDisplay, safeNetworkShow);
    }

    return safePrefixes.some(s => cmdTrim.startsWith(s)) && !unsafeKeywords.some(u => cmdTrim.includes(` ${u} `) || cmdTrim.startsWith(`${u} `));
}
const aiMessages = document.getElementById('ai-messages');
const aiInput = document.getElementById('ai-input');
const sendBtn = document.getElementById('send-btn');
const roleSelect = document.getElementById('ai-role-select');

// 所有可用角色列表（{id, name, is_active}）
let allRoles = [];
// 系统默认激活角色 ID（全局默认）
let defaultRoleId = null;

// 加载角色列表并填充选择器
async function loadRoles() {
    try {
        const roles = await api.getRoles();
        allRoles = roles;
        roleSelect.innerHTML = '';
        roles.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r.id;
            opt.textContent = r.name;
            if (r.is_active) {
                defaultRoleId = r.id;
                opt.selected = true;
            }
            roleSelect.appendChild(opt);
        });
        // 若无 is_active，fallback 到第一项
        if (!defaultRoleId && roles.length > 0) {
            defaultRoleId = roles[0].id;
            roleSelect.value = defaultRoleId;
        }
    } catch (e) {
        console.error('加载角色列表失败:', e);
    }
}

// 获取当前激活 tab 的角色 ID
function getCurrentRoleId() {
    const tab = store.activeTabId ? window.getTab(store.activeTabId) : null;
    if (tab && tab.roleId) return tab.roleId;
    return defaultRoleId;
}

export function initAIModule() {
    // 加载角色列表
    loadRoles();

    // 初始化文档按钮状态
    const tab = store.activeTabId ? window.getTab(store.activeTabId) : null;
    updateServerDocButtonState(tab);

    // 绑定发送按钮
    sendBtn.onclick = () => {
        autoExecuteCount = 0;
        handleAISend();
    };
    aiInput.onkeydown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            autoExecuteCount = 0;
            handleAISend();
        }
    };

    // 角色切换：更新当前 tab 的 roleId
    roleSelect.onchange = () => {
        const tab = store.activeTabId ? window.getTab(store.activeTabId) : null;
        if (tab) {
            tab.roleId = parseInt(roleSelect.value);
        }
    };

    // 监听终端输出捕获完成事件，自动发给 AI 分析
    window.addEventListener('captureReady', (e) => {
        const { tabId, output } = e.detail;
        sendCaptureToAI(tabId, output);
    });

    // 所有连接关闭后，清空 AI 对话面板并停止正在进行的流
    window.addEventListener('allTabsClosed', () => {
        stopAI();
        lastSentCaptureFingerprint.clear();
        aiMessages.innerHTML = '<div class="message system" style="text-align:center;color:#666;padding:20px;">暂无活跃连接，请先连接服务器</div>';
    });

    // 标签切换：刷新消息列表 + 同步角色选择器 + 文档按钮状态
    window.addEventListener('tabSwitched', (e) => {
        updateServerDocButtonState(e.detail.tab);
        const tab = e.detail.tab;
        
        // 核心逻辑：场景驱动的角色自动切换
        let roleIdToSelect = tab.roleId; // 优先使用该会话手动选过的角色
        
        if (!roleIdToSelect && window.allDeviceTypes) {
            // 如果没手动选过，根据服务器 device_type 寻找绑定的角色
            const dtype = window.allDeviceTypes.find(t => t.value === (tab.config.device_type_value || tab.config.device_type));
            if (dtype && dtype.role_id) {
                roleIdToSelect = dtype.role_id;
                console.log(`🎯 检测到设备类型 ${tab.config.device_type_value || tab.config.device_type}，自动匹配 AI 角色 ID: ${roleIdToSelect}`);
            }
        }

        // 同步角色选择器：匹配不到绑定关系时使用默认激活角色
        roleSelect.value = roleIdToSelect || defaultRoleId || (allRoles[0] && allRoles[0].id) || '';
        
        // 如果是自动匹配出的角色，且当前 tab 还没记录过，同步给 tab 以保持会话一致性
        if (!tab.roleId && roleIdToSelect) {
            tab.roleId = roleIdToSelect;
        }

        // 重渲染消息历史（replayOnly=true 避免重复执行 command_request 和 document_update）
        aiMessages.innerHTML = '';
        tab.chatHistory.forEach(msg => {
            const div = createMessageDiv(msg.role);
            if (msg.role === 'assistant') {
                processAIResponseForCommands(msg.content, div, tab, { replayOnly: true });
            } else {
                div.textContent = msg.content;
            }
        });
        aiMessages.scrollTop = aiMessages.scrollHeight;
    });

    window.addEventListener('allTabsClosed', () => updateServerDocButtonState(null));

    // 角色在"AI 角色"页面被修改后，重新加载角色列表
    window.addEventListener('rolesChanged', () => loadRoles());

    // 暴露清空对话、文档弹窗给全局
    window.clearChat = clearChat;
    window.showServerDocModal = showServerDocModal;
    window.saveServerDoc = saveServerDoc;
}

// 服务器文档模板（按 device_type 选择）
const SERVER_DOC_TEMPLATES = {
    linux: `# {{name}} 服务器环境文档

> 连接信息: {{username}}@{{host}}:{{port}}
> 创建时间: {{create_time}}

## 📋 服务器基本信息

- **主机名**: 
- **端口**: {{port}}
- **用户**: {{username}}
- **操作系统发行版**: （例如：Ubuntu 22.04 / CentOS 7 / Debian 12）
- **内核版本**: 
- **机器类型**: （物理机 / 虚拟机 / 容器 / 云主机）
- **其他说明**: 

## 🧩 系统与资源概况（由 AI 自动补充）

- **CPU / 内存**: 
- **磁盘挂载与容量**: 
- **网络环境**: （公网 / 内网 / VPN 等）
- **安全/限制**: （如 SELinux、ulimit、AppArmor 等）

## 📦 已安装软件与运行方式（由 AI 自动补充）

### 普通安装（包管理器 / 源码）


### Docker / 容器化服务


## 📁 目录与路径（由 AI 自动补充）

- **应用目录**: 
- **日志目录**: 
- **配置目录**: 
- **数据目录**: 
- **备份目录**: 

## 📝 配置文件与日志（由 AI 自动补充）


## 🚀 服务与启动方式（由 AI 自动补充）


## 🧯 常见问题与排查记录（由 AI 自动补充）


## ✅ 待办事项 / 后续优化（由 AI 自动补充）

- [ ] 

---
*本文档由 AI SSH Assistant 自动生成，AI 会在对话过程中逐步补充和更新上述信息。*
`,
    windows: `# {{name}} 服务器环境文档

> 连接信息: {{username}}@{{host}}:{{port}}
> 创建时间: {{create_time}}

## 📋 服务器基本信息

- **主机名**: 
- **端口**: {{port}}
- **用户**: {{username}}
- **操作系统**: （例如：Windows Server 2019 / Windows 10）
- **机器类型**: （物理机 / 虚拟机 / 云主机）
- **其他说明**: 

## 🧩 系统与资源概况（由 AI 自动补充）

- **CPU / 内存**: 
- **磁盘挂载与容量**: 
- **网络环境**: 

## 📦 已安装软件与运行方式（由 AI 自动补充）


## 📁 目录与路径（由 AI 自动补充）


## 🚀 服务与启动方式（由 AI 自动补充）


## 🧯 常见问题与排查记录（由 AI 自动补充）


## ✅ 待办事项

- [ ] 

---
*本文档由 AI SSH Assistant 自动生成。*
`,
    network: `# {{name}} 网络设备环境文档

> 连接信息: {{username}}@{{host}}:{{port}}
> 设备类型: {{device_type_name}}
> 创建时间: {{create_time}}

## 📋 设备基本信息

- **设备型号**: 
- **主机名**: 
- **管理 IP**: {{host}}
- **MAC 地址**: 
- **序列号**: 
- **运行时间**: 

## 🔌 接口配置（由 AI 自动补充）


## 🌐 网络配置（由 AI 自动补充）


## 🔒 安全配置（由 AI 自动补充）


## 💾 存储与配置（由 AI 自动补充）


## 🚨 监控与日志（由 AI 自动补充）


## 🔧 常用命令（由 AI 自动补充）


## 🧯 常见问题与排查记录（由 AI 自动补充）


## ✅ 待办事项

- [ ] 

---
*本文档由 AI SSH Assistant 自动生成。*
`
};

// 根据 device_type 选择模板，network 包含 h3c/huawei/cisco/ruijie 等
function getDocTemplateForDeviceType(deviceType) {
    const dt = (deviceType || '').toLowerCase();
    if (dt === 'windows') return SERVER_DOC_TEMPLATES.windows;
    if (['h3c', 'huawei', 'cisco', 'ruijie'].includes(dt)) return SERVER_DOC_TEMPLATES.network;
    return SERVER_DOC_TEMPLATES.linux; // linux、other 及未知类型
}

function fillDocTemplate(template, config) {
    const now = new Date();
    const createTime = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    const placeholders = {
        name: config?.name || config?.host || '服务器',
        host: config?.host || '',
        port: config?.port ?? 22,
        username: config?.username || 'root',
        create_time: createTime,
        device_type_name: config?.device_type_name || config?.device_type_value || config?.device_type || '网络设备'
    };
    let out = template;
    for (const [k, v] of Object.entries(placeholders)) {
        out = out.replace(new RegExp(`\\{\\{${k}\\}\\}`, 'g'), String(v));
    }
    return out;
}

// 更新文档按钮启用状态与提示
function updateServerDocButtonState(tab) {
    const btn = document.getElementById('server-doc-btn');
    if (!btn) return;
    const hasServer = tab?.config?.id;
    btn.disabled = !hasServer;
    btn.title = hasServer ? '服务器环境文档（查看/编辑）' : '服务器环境文档（需先连接服务器）';
}

// 打开服务器文档弹窗
async function showServerDocModal() {
    const tab = store.activeTabId ? window.getTab(store.activeTabId) : null;
    if (!tab?.config?.id) {
        notify('请先连接服务器', 'warning');
        return;
    }
    const serverId = tab.config.id;
    const serverName = tab.config.name || tab.config.host || '服务器';
    const titleEl = document.getElementById('server-doc-modal-title');
    const contentEl = document.getElementById('server-doc-content');
    if (titleEl) titleEl.textContent = `${serverName} - 环境文档`;
    contentEl.value = '';
    contentEl.dataset.serverId = String(serverId);
    showModal('server-doc-modal');
    try {
        const doc = await api.getServerDoc(serverId);
        if (doc?.content != null) contentEl.value = doc.content;
    } catch (e) {
        if (e?.message?.includes('404') || (e?.message && e.message.toLowerCase().includes('not found'))) {
            const deviceType = tab.config?.device_type_value || tab.config?.device_type;
            const template = getDocTemplateForDeviceType(deviceType);
            contentEl.value = fillDocTemplate(template, tab.config);
        } else {
            notify('加载文档失败: ' + (e?.message || '未知错误'), 'error');
        }
    }
    contentEl.focus();
    contentEl.onkeydown = (e) => {
        if (e.key === 's' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            saveServerDoc();
        }
    };
}

// 保存服务器文档
async function saveServerDoc() {
    const contentEl = document.getElementById('server-doc-content');
    const serverId = contentEl?.dataset?.serverId;
    if (!serverId || !contentEl) return;
    const saveBtn = document.getElementById('server-doc-save-btn');
    setBtnLoading(saveBtn, true);
    try {
        await api.updateServerDoc(parseInt(serverId, 10), contentEl.value || '');
        notify('文档已保存', 'success');
        closeModal('server-doc-modal');
    } catch (e) {
        notify('保存失败: ' + (e?.message || '未知错误'), 'error');
    } finally {
        setBtnLoading(saveBtn, false);
    }
}

// 核心功能：处理发送消息
async function handleAISend() {
    if (isAiProcessing || !aiInput.value.trim() || !store.activeTabId) return;

    const prompt = aiInput.value.trim();
    aiInput.value = '';
    
    const tab = window.getTab(store.activeTabId); 
    if (!tab) return;

    appendMessage('user', prompt, tab);
    startAIStream(prompt, tab);
}

// 核心：建立 AI WebSocket 流，messages 为完整消息数组（已包含本次用户消息）
function openAISocket(messages, tab) {
    if (aiSocket) {
        aiSocket.onclose = null;  // 防止旧 socket 的 onclose 干扰新一轮的状态
        aiSocket.close();
    }

    isAiProcessing = true;
    sendBtn.classList.add('processing');
    sendBtn.innerHTML = '<i class="fas fa-stop"></i>';
    sendBtn.onclick = stopAI;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const mode = document.getElementById('ai-mode-select').value;
    const roleId = getCurrentRoleId();
    const token = localStorage.getItem('xterm_token');
    
    // 注入服务器上下文
    const serverId = tab.config?.id || '';
    const deviceType = tab.config?.device_type || 'unknown';
    const serverName = encodeURIComponent(tab.config?.name || '');

    const roleParam = roleId ? `&role_id=${roleId}` : '';
    const tokenParam = token ? `&token=${token}` : '';
    const contextParam = `&device_type=${deviceType}&server_name=${serverName}` + (serverId ? `&server_id=${serverId}` : '');
    
    const wsUrl = `${protocol}//${window.location.host}/ws/ai?mode=${mode}${roleParam}${tokenParam}${contextParam}`;
    if (typeof console !== 'undefined' && console.debug) {
        console.debug('[AI] 连接中:', wsUrl.replace(/token=[^&]+/, 'token=***'));
    }

    aiSocket = new WebSocket(wsUrl);
    const currentAiMsgDiv = createMessageDiv('ai');
    let fullResponse = '';

    aiSocket.onopen = () => {
        if (typeof console !== 'undefined' && console.debug) console.debug('[AI] WebSocket 已连接');
        aiSocket.send(JSON.stringify({ mode, messages }));
    };

    // 后端协议：发送原始文本片段，以 [DONE] 结束，错误以 [AI Error: ...] 开头
    aiSocket.onmessage = (event) => {
        const raw = event.data;
        if (raw === '[DONE]') {
            finishAIProcessing(fullResponse, currentAiMsgDiv, tab);
            return;
        }
        if (raw.startsWith('[AI Error:')) {
            if (typeof console !== 'undefined' && console.error) console.error('[AI] 后端错误:', raw);
            fullResponse += `\n${raw}`;
            currentAiMsgDiv.innerText = fullResponse;
            finishAIProcessing(fullResponse, currentAiMsgDiv, tab);
            return;
        }
        fullResponse += raw;
        currentAiMsgDiv.innerText = fullResponse;
        aiMessages.scrollTop = aiMessages.scrollHeight;
    };

    aiSocket.onclose = (e) => {
        if (typeof console !== 'undefined' && console.debug) {
            console.debug('[AI] WebSocket 关闭:', e.code, e.reason || '');
        }
        if (isAiProcessing) finishAIProcessing(fullResponse, currentAiMsgDiv, tab);
    };
}

function startAIStream(prompt, tab) {
    // appendMessage 已将用户消息推入 chatHistory，直接发送完整历史
    openAISocket(tab.chatHistory, tab);
}

// 清洗终端原始输出：去除 ANSI/VT100 转义序列、控制字符，统一换行符
function cleanTerminalOutput(raw) {
    return raw
        .replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '')          // CSI 序列（颜色、光标移动等）
        .replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, '') // OSC 序列（标题设置等）
        .replace(/\x1b[()][A-Z0-9]/g, '')                 // 字符集切换序列
        .replace(/\x1b[A-Za-z]/g, '')                     // 其他单字节 ESC 序列
        .replace(/[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]/g, '') // 退格、Bell、NUL 等控制字符
        .replace(/\r\n/g, '\n')                            // Windows CRLF → LF
        .replace(/\r/g, '\n')                              // 单独 CR → LF
        .replace(/\n{3,}/g, '\n\n')                        // 压缩连续空行
        .trim();
}

// 生成 capture 指纹，用于防重复发送（同一内容只自动发送一次）
function captureFingerprint(cleanOutput) {
    if (!cleanOutput) return '';
    const len = cleanOutput.length;
    const head = cleanOutput.slice(0, 300);
    const tail = len > 600 ? cleanOutput.slice(-300) : '';
    return `${len}:${head}:${tail}`;
}

// 捕获终端输出后自动提交给 AI 分析
function sendCaptureToAI(tabId, output) {
    const tab = window.getTab(tabId);
    if (!tab || isAiProcessing) return;

    // 仅当捕获来源 tab 为当前激活 tab 时才发送，避免用户在切换标签后仍收到旧 tab 的 capture
    if (tabId !== store.activeTabId) return;

    // 仅当用户当前在主视图「控制台」时才自动发送（切到服务器管理/AI角色等时 terminal-view 被隐藏）
    const terminalViewActive = document.getElementById('terminal-view')?.classList.contains('active');
    if (!terminalViewActive) return;

    // 仅当用户正在查看「AI 对话」面板时才自动发送，避免切到「系统信息」时误触发
    const aiChatActive = document.querySelector('.stats-tab.active')?.getAttribute('data-tab') === 'ai-chat';
    if (!aiChatActive) return;

    const cleanOutput = cleanTerminalOutput(output);
    const fingerprint = captureFingerprint(cleanOutput);

    // 防重复发送：同一 capture 只自动发送一次（解决切到侧边栏再切回控制台时重复发送的问题）
    const lastFp = lastSentCaptureFingerprint.get(tabId);
    if (fingerprint && lastFp === fingerprint) return;
    lastSentCaptureFingerprint.set(tabId, fingerprint);

    const feedback = `命令执行结果如下：\n\`\`\`\n${cleanOutput}\n\`\`\`\n请分析结果并给出下一步建议。`;

    // 在聊天区显示"结果已同步"提示
    const sysDiv = createMessageDiv('system');
    sysDiv.textContent = '结果已同步，AI 正在分析...';
    aiMessages.scrollTop = aiMessages.scrollHeight;

    // 将 feedback 加入历史（作为 user 消息），然后发送给 AI
    tab.chatHistory.push({ role: 'user', content: feedback });
    openAISocket(tab.chatHistory, tab);
}

function stopAI() {
    if (aiSocket) {
        aiSocket.onclose = null;  // 避免触发兜底处理
        aiSocket.close();
    }
    isAiProcessing = false;
    sendBtn.classList.remove('processing');
    sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i>';
    sendBtn.onclick = handleAISend;
}

function finishAIProcessing(fullResponse, msgDiv, tab) {
    if (!isAiProcessing) return;   // 防止 [DONE] 和 onclose 双重触发
    isAiProcessing = false;
    sendBtn.classList.remove('processing');
    sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i>';
    sendBtn.onclick = handleAISend;

    if (fullResponse && tab) {
        const trimmed = fullResponse.trim();
        tab.chatHistory.push({ role: 'assistant', content: trimmed });
        // 仅当消息所属 tab 仍为当前激活 tab 时挂载/渲染，避免切走时误插入到其他 tab 的视图
        const isActiveTab = store.activeTabId === tab.id;
        if (isActiveTab && !aiMessages.contains(msgDiv)) {
            aiMessages.appendChild(msgDiv);
        }
        requestAnimationFrame(() => {
            // 若已被 tabSwitched 重建或用户已切走，跳过渲染（切回时会从 chatHistory 重建）
            if (!aiMessages.contains(msgDiv)) return;
            if (store.activeTabId !== tab.id) return;
            processAIResponseForCommands(trimmed, msgDiv, tab);
            // 渲染完成后滚动到底部，确保总结报告可见
            requestAnimationFrame(() => {
                aiMessages.scrollTop = aiMessages.scrollHeight;
            });
        });
    }
}

// 消息 DOM 创建
function createMessageDiv(role) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    aiMessages.appendChild(div);
    return div;
}

export function appendMessage(role, content, tab) {
    const div = createMessageDiv(role);
    div.textContent = content;
    if (tab) tab.chatHistory.push({ role, content });
    aiMessages.scrollTop = aiMessages.scrollHeight;
    return div;
}

// --- 核心：JSON 指令解析渲染（正则 + 字符串感知大括号计数）---
// options.replayOnly: true=历史重放（tab切换等），仅渲染不执行命令、不重复更新文档
function processAIResponseForCommands(text, msgDiv, tab, options = {}) {
    const replayOnly = !!options.replayOnly;
    const t = (typeof text === 'string' ? text : '').trim();
    if (!t) return;
    try {
        msgDiv.classList.add('rendered');
        msgDiv.innerHTML = '';

        let lastIndex = 0;
        // 匹配 command_request、summary_report 或 document_update
        const startRegex = /(?:```(?:json)?\s*)?\{[\s\S]*?"type"\s*:\s*"(command_request|summary_report|document_update)"/g;
        let match;
        let commandFound = false;

        while ((match = startRegex.exec(t)) !== null) {
            // 1. 渲染 JSON 之前的文本
            const plainText = normalizeNewlines(t.substring(lastIndex, match.index));
            if (plainText.trim()) {
                const textNode = document.createElement('div');
                textNode.className = 'message-text-content';
                textNode.innerHTML = typeof marked !== 'undefined' ? marked.parse(plainText) : plainText;
                msgDiv.appendChild(textNode);
            }

            // 2. 找到 JSON 结束位置
            const startPos = t.indexOf('{', match.index);
            let braceCount = 0, inString = false, escaped = false, endPos = -1;
            for (let i = startPos; i < t.length; i++) {
                const char = t[i];
                if (escaped) { escaped = false; continue; }
                if (char === '\\') { escaped = true; continue; }
                if (char === '"') { inString = !inString; continue; }
                if (!inString) {
                    if (char === '{') braceCount++;
                    if (char === '}') { braceCount--; if (braceCount === 0) { endPos = i + 1; break; } }
                }
            }

            if (endPos !== -1) {
                const jsonStr = t.substring(startPos, endPos);
                let fullEndPos = endPos;
                if (t.substring(endPos).trimStart().startsWith('```')) {
                    fullEndPos = t.indexOf('```', endPos) + 3;
                }

                try {
                    const data = JSON.parse(jsonStr);
                    if (data.type === 'command_request' && !commandFound) {
                        commandFound = true; // 每一轮回复只处理第一个指令
                        const command = data.command.trim();
                        const mode = document.getElementById('ai-mode-select').value;
                        const deviceType = tab?.config?.device_type_value || tab?.config?.device_type || '';

                        // 历史重放时仅渲染卡片，不执行命令（避免 tab 切换触发重复执行）
                        if (replayOnly) {
                            renderCommandCard(command, msgDiv, tab, { executed: true });
                        } else if (mode === 'agent' && isSafeCommand(command, deviceType) && autoExecuteCount < MAX_AUTO_EXECUTE) {
                            autoExecuteCount++;
                            renderAutoExecuteCard(command, data.intent, msgDiv, tab);
                            executeAICommand(command, tab);
                        } else {
                            renderCommandCard(command, msgDiv, tab);
                        }
                    } else if (data.type === 'summary_report') {
                        renderSummaryReport(data.content, msgDiv, replayOnly);
                        if (!replayOnly) autoExecuteCount = 0; // 任务达成，重置计数
                    } else if (data.type === 'document_update' && data.content != null) {
                        const card = renderDocumentUpdateCard(msgDiv, replayOnly ? true : 'pending');
                        if (!replayOnly) {
                            const serverId = tab?.config?.id;
                            if (serverId && api) {
                                api.updateServerDoc(serverId, String(data.content))
                                    .then(() => {
                                        notify('服务器环境文档已更新', 'success');
                                        updateDocumentUpdateCard(card, true);
                                    })
                                    .catch(err => {
                                        notify('文档更新失败: ' + (err?.message || '未知错误'), 'error');
                                        updateDocumentUpdateCard(card, false);
                                    });
                            } else {
                                notify('无法更新文档：未关联服务器', 'warning');
                                updateDocumentUpdateCard(card, false);
                            }
                        }
                    }
                } catch (e) {
                    // 解析失败，降级显示
                    const errNode = document.createElement('div');
                    errNode.className = 'message-text-content';
                    errNode.innerText = t.substring(match.index, fullEndPos);
                    msgDiv.appendChild(errNode);
                }

                lastIndex = fullEndPos;
                startRegex.lastIndex = fullEndPos;
            } else {
                // 大括号计数失败时，尝试用 ```json ... ``` 块提取
                const codeBlockMatch = t.substring(match.index).match(/^```(?:json)?\s*([\s\S]*?)```/);
                if (codeBlockMatch) {
                    const jsonStr = codeBlockMatch[1].trim();
                    try {
                        const data = JSON.parse(jsonStr);
                        if (data.type === 'command_request' && !commandFound) {
                            commandFound = true;
                            const command = (data.command || '').trim();
                            const mode = document.getElementById('ai-mode-select').value;
                            const deviceType = tab?.config?.device_type_value || tab?.config?.device_type || '';
                            if (replayOnly) {
                                renderCommandCard(command, msgDiv, tab, { executed: true });
                            } else if (mode === 'agent' && isSafeCommand(command, deviceType) && autoExecuteCount < MAX_AUTO_EXECUTE) {
                                autoExecuteCount++;
                                renderAutoExecuteCard(command, data.intent, msgDiv, tab);
                                executeAICommand(command, tab);
                            } else {
                                renderCommandCard(command, msgDiv, tab);
                            }
                        } else if (data.type === 'summary_report') {
                            renderSummaryReport(data.content || '', msgDiv, replayOnly);
                            if (!replayOnly) autoExecuteCount = 0;
                        } else if (data.type === 'document_update' && data.content != null) {
                            const card = renderDocumentUpdateCard(msgDiv, replayOnly ? true : 'pending');
                            if (!replayOnly && tab?.config?.id && api) {
                                api.updateServerDoc(tab.config.id, String(data.content))
                                    .then(() => { notify('服务器环境文档已更新', 'success'); updateDocumentUpdateCard(card, true); })
                                    .catch(err => { notify('文档更新失败: ' + (err?.message || '未知错误'), 'error'); updateDocumentUpdateCard(card, false); });
                            } else if (!replayOnly) {
                                notify('无法更新文档：未关联服务器', 'warning');
                                updateDocumentUpdateCard(card, false);
                            }
                        }
                    } catch (_) { /* 忽略解析错误 */ }
                    const fullEnd = match.index + codeBlockMatch[0].length;
                    lastIndex = fullEnd;
                    startRegex.lastIndex = fullEnd;
                } else {
                    lastIndex = match.index + 1;
                }
            }
        }

        // 3. 渲染剩余文本
        const remainingText = normalizeNewlines(t.substring(lastIndex));
        if (remainingText.trim()) {
            const textNode = document.createElement('div');
            textNode.className = 'message-text-content';
            textNode.innerHTML = typeof marked !== 'undefined' ? marked.parse(remainingText) : remainingText;
            msgDiv.appendChild(textNode);
        }
    } catch (e) {
        console.error('处理 AI 响应失败:', e);
        msgDiv.innerText = t;
    }
}

// 渲染自动执行卡片
function renderAutoExecuteCard(command, intent, container, tab) {
    const card = document.createElement('div');
    card.className = 'command-card auto-executing';
    card.style.borderLeft = '3px solid #0078d4';
    card.innerHTML = `
        <div class="command-card-header" style="color:#0078d4; background: rgba(0,120,212,0.05)">
            <i class="fas fa-robot"></i> <span>智能体自动执行中...</span>
        </div>
        <div class="command-card-body">
            <div style="font-size:12px;color:#888;margin-bottom:5px;">意图：${intent || '自动探测系统状态'}</div>
            <code>${command}</code>
        </div>
    `;
    container.appendChild(card);
}

// 渲染文档更新卡片（pending=更新中，success/failure 由 updateDocumentUpdateCard 更新）
function renderDocumentUpdateCard(container, status) {
    const card = document.createElement('div');
    card.className = 'document-update-card';
    const isPending = status === 'pending';
    const color = isPending ? '#0078d4' : (status ? '#4caf50' : '#f44336');
    card.style.cssText = 'margin-top:10px;border-radius:6px;overflow:hidden;border:1px solid ' + color;
    card.innerHTML = `
        <div style="background:${color};color:white;padding:6px 12px;font-weight:bold;font-size:13px;">
            <i class="fas fa-file-alt"></i> ${isPending ? '正在更新服务器环境文档...' : (status ? '服务器环境文档已更新' : '文档更新未完成')}
        </div>
        <div style="padding:8px 12px;background:rgba(0,0,0,0.1);font-size:12px;color:#ccc;">
            ${isPending ? '正在保存到数据库...' : (status ? '文档已保存，可通过侧边栏文档按钮查看或编辑。' : '可能因未关联服务器或网络问题导致更新失败。')}
        </div>
    `;
    container.appendChild(card);
    return card;
}

function updateDocumentUpdateCard(card, success) {
    if (!card) return;
    const color = success ? '#4caf50' : '#f44336';
    card.style.borderColor = color;
    card.innerHTML = `
        <div style="background:${color};color:white;padding:6px 12px;font-weight:bold;font-size:13px;">
            <i class="fas fa-file-alt"></i> ${success ? '服务器环境文档已更新' : '文档更新未完成'}
        </div>
        <div style="padding:8px 12px;background:rgba(0,0,0,0.1);font-size:12px;color:#ccc;">
            ${success ? '文档已保存，可通过侧边栏文档按钮查看或编辑。' : '可能因未关联服务器或网络问题导致更新失败。'}
        </div>
    `;
}

// 规范化内容中的换行符：处理 API 可能返回的字面 \n（双转义）为真实换行
function normalizeNewlines(text) {
    if (typeof text !== 'string') return text;
    return text.replace(/\\n/g, '\n').replace(/\\r/g, '\r');
}

// 渲染总结报告
// replayOnly: 历史重放时跳过 notify 避免重复弹窗
function renderSummaryReport(content, container, replayOnly = false) {
    const report = document.createElement('div');
    report.className = 'summary-report-card';
    report.style.marginTop = '10px';
    report.style.border = '1px solid #4caf50';
    report.style.borderRadius = '6px';
    report.style.overflow = 'hidden';
    const normalized = normalizeNewlines(content);
    report.innerHTML = `
        <div class="report-header" style="background:#4caf50; color:white; padding:6px 12px; font-weight:bold; font-size:13px;">
            <i class="fas fa-clipboard-check"></i> 任务阶段性总结
        </div>
        <div class="report-body message-text-content" style="padding:12px; background: rgba(76,175,80,0.05)">
            ${typeof marked !== 'undefined' ? marked.parse(normalized) : normalized}
        </div>
    `;
    container.appendChild(report);
    if (!replayOnly) notify('任务已达成，查看总结报告', 'success');
}

// 渲染指令卡片
// options.executed: true=仅展示已执行状态，不渲染确认/拒绝按钮（用于历史重放）
function renderCommandCard(command, container, tab, options = {}) {
    const executed = !!options.executed;
    const dangerousPatterns = [
        /rm\s+-rf\s+\//, /rm\s+-rf\s+\*/, /mkfs/, /dd\s+if=/, /:\(\)\{\s*:\s*\|\s*:\s*&\s*\}\s*;/, // 叉子炸弹
        />\s*\/dev\/sd/, /chmod\s+-R\s+777\s+\//, /chown\s+-R\s+.*?\s+\//, /shred/, /format\s+/
    ];
    const isDangerous = dangerousPatterns.some(p => p.test(command));

    const card = document.createElement('div');
    card.className = `command-card${isDangerous ? ' dangerous' : ''}`;
    if (executed) {
        card.innerHTML = `<div class="command-card-header" style="color:#4caf50"><i class="fas fa-check-circle"></i> 命令已执行</div><div class="command-card-body"><code>${command}</code></div>`;
        container.appendChild(card);
        return;
    }
    card.innerHTML = `
        <div class="command-card-header">
            <i class="fas ${isDangerous ? 'fa-exclamation-triangle' : 'fa-terminal'}"></i> 
            <span>${isDangerous ? '高危命令执行确认' : 'SSH 命令执行'}</span>
        </div>
        <div class="command-card-body"><code>${command}</code></div>
        <div class="command-card-footer">
            <div class="command-card-tip">
                <i class="fas fa-shield-alt"></i> 
                ${isDangerous ? '<span style="color:#ff4d4f;font-weight:bold">警告：此操作具有破坏性！</span>' : '需要确认'}
            </div>
            <div class="command-actions">
                <button class="btn btn-sm ${isDangerous ? 'btn-danger' : 'btn-primary'} btn-confirm">
                    <i class="fas fa-check"></i> ${isDangerous ? '强制执行' : '同意'}
                </button>
                <button class="btn btn-sm btn-secondary btn-reject"><i class="fas fa-times"></i> 拒绝</button>
            </div>
        </div>
    `;

    const confirmBtn = card.querySelector('.btn-confirm');
    const rejectBtn = card.querySelector('.btn-reject');

    confirmBtn.onclick = () => {
        if (isDangerous) {
            if (!confirm(`【极高风险警告】\n\n您正准备执行一条可能具有破坏性的命令：\n\n${command}\n\n该操作无法撤销，可能导致系统损坏或数据丢失。确定要继续吗？`)) {
                return;
            }
        }
        card.innerHTML = `<div class="command-card-header" style="color:#4caf50"><i class="fas fa-check-circle"></i> 命令已发送执行</div><div class="command-card-body"><code>${command}</code></div>`;
        executeAICommand(command, tab);
    };

    rejectBtn.onclick = () => {
        card.innerHTML = `<div class="command-card-header" style="color:#f1707b"><i class="fas fa-times-circle"></i> 您拒绝了此命令执行</div><div class="command-card-body"><code>${command}</code></div>`;
    };

    container.appendChild(card);
}

// 执行命令：发送到终端并启动输出捕获
function executeAICommand(command, tab) {
    if (!tab || !tab.socket || tab.socket.readyState !== WebSocket.OPEN) {
        notify('SSH 未就绪，无法执行命令', 'error');
        return;
    }

    // 启动捕获模式：terminal.js 的 onmessage 会累积输出（含网络设备分页自动翻页）
    tab.isCapturing = true;
    tab.captureBuffer = '';
    tab.capturePagerCount = 0;
    if (tab.captureTimer) {
        clearTimeout(tab.captureTimer);
        tab.captureTimer = null;
    }

    // 发送命令到终端（\r 对应 xterm.js 真实 Enter 键，Linux PTY 和 Windows SSH 均可识别）
    tab.socket.send(JSON.stringify({ type: 'data', data: command.trim() + '\r' }));
    notify('命令已发送，等待执行结果...');
}

// 清空对话
export function clearChat() {
    if (!store.activeTabId) return;
    const tab = window.getTab(store.activeTabId);
    if (tab) {
        tab.chatHistory = [];
        lastSentCaptureFingerprint.delete(tab.id);
        aiMessages.innerHTML = '';
        appendMessage('system', '会话历史已清空。');
    }
}
