// --- 全局状态 ---
let tabs = []; 
let activeTabId = null;
let aiWs = null;
let currentAiMsgDiv = null;
let fullResponse = '';
let isAiProcessing = false; // 新增：标记 AI 是否正在生成
let activeCommandGroupId = null; // 新增：当前选中的命令分组 ID
let sftpSelectedFile = null; // 新增：SFTP 当前选中的文件对象

// --- 布局状态 ---
let sidebarPercent = 0.15; let aiPercent = 0.25;
let sidebarCollapsed = false; let aiCollapsed = false;

// --- 初始化 ---
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initAIWebSocket();
    initStatsTabs();
    initLayout();
    loadServers();
    loadAIEndpoints();
    loadRoles();
    loadSystemSettings();
    loadConnectionHistory();
    initBottomPanel();
    loadCommandGroups();
    initSftpDragAndDrop();
    
    document.getElementById('add-tab-btn').onclick = () => showQuickConnect();
    document.getElementById('clear-chat-btn').onclick = handleClearChat; // 新增：清空按钮
    document.getElementById('server-form').onsubmit = handleAddServer;
    document.getElementById('ai-form').onsubmit = handleAddAI;
    document.getElementById('role-form').onsubmit = handleAddRole;
    document.getElementById('system-settings-form').onsubmit = handleSaveSystemSettings;
    
    // 初始化日志行数切换
    const linesSelect = document.getElementById('log-lines-select');
    if (linesSelect) linesSelect.onchange = loadLogContent;

    // SFTP 路径栏回车跳转
    const sftpPathInput = document.getElementById('sftp-current-path');
    if (sftpPathInput) {
        sftpPathInput.onkeydown = (e) => {
            if (e.key === 'Enter') {
                loadSftpFiles(e.target.value.trim());
            }
        };
    }

    // 全局点击隐藏右键菜单
    window.addEventListener('click', () => {
        document.getElementById('sftp-context-menu').style.display = 'none';
    });
});

// --- 导航与视图切换 ---
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.onclick = () => {
            const targetViewId = item.getAttribute('data-view');
            navItems.forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            document.getElementById(targetViewId).classList.add('active');
            if (targetViewId === 'terminal-view') {
                const activeTab = tabs.find(t => t.id === activeTabId);
                if (activeTab) setTimeout(() => activeTab.fitAddon.fit(), 100);
            }
        };
    });
}

// --- 终端管理 (多标签版) ---
function createTab(server) {
    const id = 'tab-' + Date.now();
    const tab = {
        id: id,
        server: server,
        term: null,
        fitAddon: null,
        sshWs: null,
        statsWs: null,
        chatHistory: [{ role: 'assistant', content: `您好！已为您连接到 ${server.name}。有什么可以帮您的？` }],
        lastStats: null,
        isCapturing: false,
        captureBuffer: '',
        captureTimer: null,
        sftpCurrentPath: ''
    };

    // 1. 创建 Tab 按钮
    const tabEl = document.createElement('div');
    tabEl.className = 'tab-item';
    tabEl.id = 'btn-' + id;
    tabEl.innerHTML = `
        <i class="fas fa-terminal" style="margin-right:8px; font-size:0.8rem;"></i>
        <span class="tab-title">${server.name}</span>
        <span class="tab-close" onclick="closeTab(event, '${id}')">&times;</span>
    `;
    tabEl.onclick = () => switchTab(id);
    document.getElementById('terminal-tabs').appendChild(tabEl);

    // 2. 创建终端容器
    const container = document.createElement('div');
    container.className = 'terminal-container-item';
    container.id = 'cont-' + id;
    document.getElementById('terminal-stack').appendChild(container);

    // 3. 初始化 xterm.js
    const term = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        theme: { background: '#000000', foreground: '#ffffff', cursor: '#4caf50' }
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);
    fitAddon.fit();

    tab.term = term;
    tab.fitAddon = fitAddon;

    // 4. 建立 SSH WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const sshWs = new WebSocket(`${protocol}//${window.location.host}/ws/ssh/${server.id}`);
    
    sshWs.onopen = () => {
        term.write(`\x1b[32m[XTerm-AI] 正在连接 ${server.host}:${server.port}...\r\n\x1b[37m`);
        sshWs.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
    };
    
    sshWs.onmessage = (e) => {
        term.write(e.data);
        if (tab.isCapturing) {
            tab.captureBuffer += e.data;
            if (tab.captureTimer) clearTimeout(tab.captureTimer);
            tab.captureTimer = setTimeout(() => sendCaptureToAI(id), 1500);
        }
    };
    
    sshWs.onclose = () => {
        term.write('\r\n\x1b[31m[XTerm-AI] \x1b[37mSSH 连接已断开。\r\n');
    };

    term.onData(data => {
        if (sshWs.readyState === WebSocket.OPEN) sshWs.send(data);
    });

    tab.sshWs = sshWs;

    // 5. 建立状态采集 WebSocket
    const statsWs = new WebSocket(`${protocol}//${window.location.host}/ws/stats/${server.id}`);
    statsWs.onmessage = (e) => {
        const data = JSON.parse(e.data);
        tab.lastStats = data;
        if (activeTabId === id) updateStatsUI(data);
    };
    tab.statsWs = statsWs;

    tabs.push(tab);
    saveToHistory(server);
    switchTab(id);
}

function switchTab(id) {
    activeTabId = id;
    const tab = tabs.find(t => t.id === id);
    if (!tab) return;

    // UI 切换
    document.querySelectorAll('.tab-item').forEach(el => el.classList.remove('active'));
    document.getElementById('btn-' + id).classList.add('active');
    
    document.querySelectorAll('.terminal-container-item').forEach(el => el.classList.remove('active'));
    document.getElementById('cont-' + id).classList.add('active');
    
    document.getElementById('quick-connect-view').style.display = 'none';
    document.getElementById('terminal-stack').style.visibility = 'visible';
    document.getElementById('current-server-name').innerText = `已连接: ${tab.server.name}`;

    // 刷新终端大小
    setTimeout(() => {
        tab.fitAddon.fit();
        if (tab.sshWs.readyState === WebSocket.OPEN) {
            tab.sshWs.send(JSON.stringify({ type: 'resize', cols: tab.term.cols, rows: tab.term.rows }));
        }
    }, 100);

    // 联动右侧：恢复 AI 对话历史
    renderChatHistory(tab.chatHistory);
    // 联动右侧：恢复系统状态信息
    if (tab.lastStats) updateStatsUI(tab.lastStats);

    // 联动下方：刷新 SFTP 或命令列表
    const activePanelTab = document.querySelector('.panel-tab.active');
    if (activePanelTab) {
        const targetId = activePanelTab.getAttribute('data-tab');
        if (targetId === 'files-view') loadSftpFiles();
        if (targetId === 'commands-view') loadCommandGroups();
    }
}

function closeTab(e, id) {
    if (e) e.stopPropagation();
    const index = tabs.findIndex(t => t.id === id);
    if (index === -1) return;

    const tab = tabs[index];
    if (tab.sshWs) tab.sshWs.close();
    if (tab.statsWs) tab.statsWs.close();
    if (tab.term) tab.term.dispose();

    document.getElementById('btn-' + id).remove();
    document.getElementById('cont-' + id).remove();
    
    tabs.splice(index, 1);

    if (activeTabId === id) {
        if (tabs.length > 0) {
            switchTab(tabs[tabs.length - 1].id);
        } else {
            showQuickConnect();
        }
    }
}

function showQuickConnect() {
    activeTabId = null;
    document.querySelectorAll('.tab-item').forEach(el => el.classList.remove('active'));
    document.getElementById('quick-connect-view').style.display = 'block';
    document.getElementById('terminal-stack').style.visibility = 'hidden';
    document.getElementById('current-server-name').innerText = '新建连接';
    loadConnectionHistory();
}

// --- 历史记录管理 ---
function saveToHistory(server) {
    let history = JSON.parse(localStorage.getItem('connection_history') || '[]');
    history = history.filter(s => s.id !== server.id);
    history.unshift({
        id: server.id,
        name: server.name,
        host: server.host,
        username: server.username,
        group_name: server.group_name,
        time: new Date().toLocaleString()
    });
    localStorage.setItem('connection_history', JSON.stringify(history.slice(0, 20)));
}

function loadConnectionHistory() {
    const history = JSON.parse(localStorage.getItem('connection_history') || '[]');
    const container = document.getElementById('history-list');
    if (history.length === 0) {
        container.innerHTML = '<div class="qc-empty">暂无连接记录，请从左侧服务器列表发起连接。</div>';
        return;
    }
    container.innerHTML = '';
    history.forEach(s => {
        const el = document.createElement('div');
        el.className = 'qc-item';
        el.innerHTML = `
            <i class="fas fa-server"></i>
            <div style="font-weight:bold">${s.name}</div>
            <div class="qc-host">${s.host}</div>
            <div class="qc-user">${s.group_name || 'default'} / ${s.username}</div>
            <div style="font-size:0.75rem; color:#666; text-align:right">${s.time.split(' ')[0]}</div>
        `;
        el.onclick = async () => {
            const res = await fetch(`/api/servers/${s.id}`);
            if (res.ok) connectToServer(await res.json());
        };
        container.appendChild(el);
    });
}

function clearHistory() {
    localStorage.removeItem('connection_history');
    loadConnectionHistory();
}

// --- 服务器管理 ---
async function loadServers() {
    const res = await fetch('/api/servers');
    const servers = await res.json();
    const container = document.getElementById('server-list-container');
    container.innerHTML = '';

    const root = { subgroups: {}, servers: [] };
    servers.forEach(server => {
        const parts = (server.group_name || 'default').split('/');
        let current = root;
        parts.forEach(part => {
            const name = part.trim();
            if (!current.subgroups[name]) current.subgroups[name] = { subgroups: {}, servers: [] };
            current = current.subgroups[name];
        });
        current.servers.push(server);
    });

    function renderGroup(groupName, groupData, parentPath = '') {
        const fullPath = parentPath ? `${parentPath}/${groupName}` : groupName;
        const section = document.createElement('div');
        section.className = 'group-section';
        const isCollapsed = localStorage.getItem(`group_collapsed_${fullPath}`) === 'true';
        if (isCollapsed) section.classList.add('collapsed');

        section.innerHTML = `
            <div class="group-header" onclick="toggleGroup('${fullPath}', this.parentElement)">
                <i class="fas fa-chevron-down group-toggle-icon"></i>
                <h3 class="group-title">${groupName}</h3>
                <span style="font-size: 0.8rem; color: #666;">(${countServers(groupData)})</span>
            </div>
            <div class="group-content"></div>
        `;
        const content = section.querySelector('.group-content');
        for (const [subName, subData] of Object.entries(groupData.subgroups)) {
            content.appendChild(renderGroup(subName, subData, fullPath));
        }
        if (groupData.servers.length > 0) {
            const grid = document.createElement('div');
            grid.className = 'server-grid';
            groupData.servers.forEach(server => {
                const card = document.createElement('div');
                card.className = 'server-card';
                const typeIcon = server.device_type === 'linux' ? 'fa-linux' : 'fa-network-wired';
                card.innerHTML = `
                    <h4><i class="fab ${typeIcon}"></i> ${server.name} <span class="device-tag tag-${server.device_type}">${server.device_type}</span></h4>
                    <div class="host-info">${server.username}@${server.host}:${server.port}</div>
                    <div class="card-actions">
                        <button class="action-btn" onclick="showEditServerModal(event, ${server.id})"><i class="fas fa-edit"></i></button>
                        <button class="action-btn" onclick="deleteServer(event, ${server.id})"><i class="fas fa-trash"></i></button>
                    </div>
                `;
                card.onclick = () => connectToServer(server);
                grid.appendChild(card);
            });
            content.appendChild(grid);
        }
        return section;
    }

    function countServers(group) {
        let count = group.servers.length;
        for (const sub of Object.values(group.subgroups)) count += countServers(sub);
        return count;
    }

    for (const [name, data] of Object.entries(root.subgroups)) container.appendChild(renderGroup(name, data));
}

function toggleGroup(path, element) {
    const isCollapsed = element.classList.toggle('collapsed');
    localStorage.setItem(`group_collapsed_${path}`, isCollapsed);
}

async function handleAddServer(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    const data = Object.fromEntries(formData.entries());
    const id = data.id; delete data.id;
    const res = await fetch(id ? `/api/servers/${id}` : '/api/servers', {
        method: id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (res.ok) { closeModal('server-modal'); loadServers(); e.target.reset(); }
}

function showAddServerModal() {
    const form = document.getElementById('server-form');
    form.reset();
    document.getElementById('server-id').value = '';
    document.getElementById('server-modal-title').innerText = '添加服务器';
    document.getElementById('server-modal').style.display = 'block';
}

async function showEditServerModal(e, id) {
    e.stopPropagation();
    const res = await fetch(`/api/servers/${id}`);
    if (!res.ok) return;
    const server = await res.json();
    const form = document.getElementById('server-form');
    form.reset();
    document.getElementById('server-id').value = server.id;
    form.name.value = server.name; form.host.value = server.host;
    form.port.value = server.port; form.username.value = server.username;
    form.password.value = server.password || ''; form.group_name.value = server.group_name;
    form.device_type.value = server.device_type; form.description.value = server.description || '';
    document.getElementById('server-modal-title').innerText = '编辑服务器';
    document.getElementById('server-modal').style.display = 'block';
}

async function testSSHFromModal(e) {
    const btn = e.target;
    const form = document.getElementById('server-form');
    const data = { host: form.host.value, port: parseInt(form.port.value), username: form.username.value, password: form.password.value };
    if (!data.host || !data.username) { alert('请先填写主机和账号'); return; }
    const oldText = btn.innerText; btn.innerText = '测试中...'; btn.disabled = true;
    try {
        const res = await fetch('/api/servers/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        const result = await res.json();
        alert(result.success ? '连接成功！' : '连接失败：' + result.message);
    } catch (err) { alert('测试请求失败：' + err.message); }
    finally { btn.innerText = oldText; btn.disabled = false; }
}

async function deleteServer(e, id) {
    e.stopPropagation();
    if (confirm('确定删除吗？')) { await fetch(`/api/servers/${id}`, { method: 'DELETE' }); loadServers(); }
}

function connectToServer(server) {
    // 自动切换到“控制台”视图
    const terminalNavItem = document.querySelector('.nav-item[data-view="terminal-view"]');
    if (terminalNavItem) terminalNavItem.click();

    const existing = tabs.find(t => t.server.id === server.id);
    if (existing) switchTab(existing.id);
    else createTab(server);
}

// --- AI 逻辑 ---
function initAIWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    aiWs = new WebSocket(`${protocol}//${window.location.host}/ws/ai`);
    const aiInput = document.getElementById('ai-input');
    const sendBtn = document.getElementById('send-btn');
    const aiStatus = document.getElementById('ai-status');
    const modeSelect = document.getElementById('ai-mode-select');

    aiWs.onopen = () => aiStatus.style.backgroundColor = '#4caf50';
    aiWs.onclose = () => {
        aiStatus.style.backgroundColor = '#f44336';
        // 自动重连（如果非主动关闭）
        setTimeout(initAIWebSocket, 3000);
    };
    
    aiWs.onmessage = (e) => {
        const activeTab = tabs.find(t => t.id === activeTabId);
        if (!activeTab) return;
        if (e.data === "[DONE]") {
            finishAiProcessing(activeTab);
        } else {
            if (!currentAiMsgDiv) currentAiMsgDiv = addMessage('ai', '');
            fullResponse += e.data;
            currentAiMsgDiv.innerText = fullResponse;
            const container = document.getElementById('ai-chat-messages');
            container.scrollTop = container.scrollHeight;
        }
    };

    function finishAiProcessing(tab) {
        if (fullResponse) {
            tab.chatHistory.push({ role: 'assistant', content: fullResponse });
            if (currentAiMsgDiv) {
                currentAiMsgDiv.classList.add('rendered');
                processAIResponseForCommands(fullResponse, currentAiMsgDiv);
            }
        }
        isAiProcessing = false;
        sendBtn.classList.remove('processing');
        currentAiMsgDiv = null;
        fullResponse = '';
    }

    sendBtn.onclick = () => {
        if (isAiProcessing) {
            handleStopAI();
            return;
        }

        const activeTab = tabs.find(t => t.id === activeTabId);
        if (!activeTab) { alert("请先连接一个服务器"); return; }
        const text = aiInput.value.trim();
        if (!text || aiWs.readyState !== WebSocket.OPEN) return;
        
        aiInput.value = '';
        isAiProcessing = true;
        sendBtn.classList.add('processing');
        
        addMessage('user', text);
        activeTab.chatHistory.push({ role: 'user', content: text });
        
        // 发送带模式的数据：{ mode: 'agent'|'ask', messages: [...] }
        aiWs.send(JSON.stringify({
            mode: modeSelect.value,
            messages: activeTab.chatHistory
        }));
    };

    // 支持 Enter 发送，Shift+Enter 换行
    aiInput.onkeydown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendBtn.click();
        }
    };
}

function handleStopAI() {
    if (!isAiProcessing) return;
    // 强制关闭 WebSocket 并立即重连，这是停止云端生成最直接的方法
    if (aiWs) {
        aiWs.onclose = null; // 临时移除重连，避免冲突
        aiWs.close();
    }
    isAiProcessing = false;
    document.getElementById('send-btn').classList.remove('processing');
    if (currentAiMsgDiv) {
        currentAiMsgDiv.innerText += " (已停止)";
        // 将不完整的回复也存入历史，以便后续对话有上下文
        const activeTab = tabs.find(t => t.id === activeTabId);
        if (activeTab && fullResponse) {
            activeTab.chatHistory.push({ role: 'assistant', content: fullResponse + " (已停止)" });
        }
    }
    currentAiMsgDiv = null;
    fullResponse = '';
    // 重新连接以便下次使用
    initAIWebSocket();
}

function handleClearChat() {
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab) return;
    if (confirm('确定要清空当前标签页的聊天记录吗？')) {
        activeTab.chatHistory = [{ role: 'assistant', content: `会话已重置。我是 ${activeTab.server.name} 的运维助手，请问有什么可以帮您？` }];
        renderChatHistory(activeTab.chatHistory);
    }
}

function renderChatHistory(history) {
    const container = document.getElementById('ai-chat-messages');
    container.innerHTML = '';
    history.forEach(msg => {
        if (msg.role === 'system') return;
        const msgDiv = addMessage(msg.role, msg.content);
        if (msg.role === 'assistant') processAIResponseForCommands(msg.content, msgDiv);
    });
}

function processAIResponseForCommands(text, msgDiv) {
    try {
        // 标记为已渲染状态，应用 Markdown 换行规则
        msgDiv.classList.add('rendered');
        // 清空容器
        msgDiv.innerHTML = '';
        
        let lastIndex = 0;
        // 预定义搜索 JSON 起始的正则
        const startRegex = /(?:```(?:json)?\s*)?\{[\s\S]*?"type"\s*:\s*"command_request"/g;
        let match;

        while ((match = startRegex.exec(text)) !== null) {
            // 1. 处理 JSON 之前的文本
            const plainText = text.substring(lastIndex, match.index);
            if (plainText.trim()) {
                const textNode = document.createElement('div');
                textNode.className = 'message-text-content';
                textNode.innerHTML = typeof marked !== 'undefined' ? marked.parse(plainText) : plainText;
                msgDiv.appendChild(textNode);
            }

            // 2. 寻找 JSON 的真正终点（处理大括号嵌套）
            const startPos = text.indexOf('{', match.index);
            let braceCount = 0;
            let inString = false;
            let escaped = false;
            let endPos = -1;

            for (let i = startPos; i < text.length; i++) {
                const char = text[i];
                if (escaped) { escaped = false; continue; }
                if (char === '\\') { escaped = true; continue; }
                if (char === '"') { inString = !inString; continue; }
                if (!inString) {
                    if (char === '{') braceCount++;
                    if (char === '}') {
                        braceCount--;
                        if (braceCount === 0) {
                            endPos = i + 1;
                            break;
                        }
                    }
                }
            }

            if (endPos !== -1) {
                const jsonStr = text.substring(startPos, endPos);
                // 检查末尾是否有 Markdown 闭合符
                let fullEndPos = endPos;
                if (text.substring(endPos, endPos + 3) === '```') {
                    fullEndPos += 3;
                }

                try {
                    const cmdData = JSON.parse(jsonStr);
                    const command = cmdData.command.trim();
                    const card = document.createElement('div');
                    card.className = 'command-card';
                    card.innerHTML = `<div class="command-card-header"><i class="fas fa-terminal"></i> <span>SSH 命令执行</span></div>` +
                                     `<div class="command-card-body"><code>${command}</code></div>` +
                                     `<div class="command-card-footer"><div class="command-card-tip"><i class="fas fa-shield-alt"></i> 此操作需要您的确认</div>` +
                                     `<div class="command-actions"><button class="btn btn-sm btn-confirm"><i class="fas fa-check"></i> 同意</button>` +
                                     `<button class="btn btn-sm btn-reject"><i class="fas fa-times"></i> 拒绝</button></div></div>`;
                    
                    card.querySelector('.btn-confirm').onclick = () => executeAICommand(command, card);
                    card.querySelector('.btn-reject').onclick = () => { 
                        card.remove(); 
                        addMessage('system', `已取消执行命令: ${command}`); 
                    };
                    msgDiv.appendChild(card);
                } catch (e) {
                    console.error("JSON 解析失败:", e);
                    // 降级处理：作为文本显示
                    const errNode = document.createElement('div');
                    errNode.className = 'message-text-content';
                    errNode.innerText = text.substring(match.index, fullEndPos);
                    msgDiv.appendChild(errNode);
                }
                
                lastIndex = fullEndPos;
                startRegex.lastIndex = fullEndPos; // 更新正则搜索起始位置
            } else {
                lastIndex = match.index + 1;
            }
        }

        // 3. 处理剩余的文本
        const remainingText = text.substring(lastIndex);
        if (remainingText.trim()) {
            const textNode = document.createElement('div');
            textNode.className = 'message-text-content';
            textNode.innerHTML = typeof marked !== 'undefined' ? marked.parse(remainingText) : remainingText;
            msgDiv.appendChild(textNode);
        }
        
    } catch (e) { 
        console.error("处理 AI 响应命令失败:", e); 
        msgDiv.innerText = text;
    }
}

function executeAICommand(cmd, card) {
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab || activeTab.sshWs.readyState !== WebSocket.OPEN) { alert("SSH 未就绪"); return; }
    card.querySelector('.command-actions').innerHTML = '<span style="color:#0078d4">执行中...</span>';
    activeTab.isCapturing = true;
    activeTab.captureBuffer = '';
    activeTab.sshWs.send(cmd.trim() + '\n');
}

function sendCaptureToAI(tabId) {
    const tab = tabs.find(t => t.id === tabId);
    if (!tab || !tab.isCapturing) return;
    tab.isCapturing = false;
    const output = tab.captureBuffer.trim();
    if (output) {
        addMessage('system', `结果已同步`);
        const feedback = `命令执行结果如下：\n\`\`\`\n${output}\n\`\`\`\n请分析结果并给出下一步建议。`;
        tab.chatHistory.push({ role: 'user', content: feedback });
        if (activeTabId === tabId) {
            // 设置状态为生成中
            isAiProcessing = true;
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) sendBtn.classList.add('processing');
            
            const modeSelect = document.getElementById('ai-mode-select');
            aiWs.send(JSON.stringify({
                mode: modeSelect ? modeSelect.value : 'agent',
                messages: tab.chatHistory
            }));
        }
    }
}

function addMessage(role, content) {
    const container = document.getElementById('ai-chat-messages');
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerText = content;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

// --- AI 端点、角色、系统设置管理 ---
async function loadAIEndpoints() {
    const res = await fetch('/api/ai_endpoints');
    const endpoints = await res.json();
    const container = document.getElementById('ai-list-container');
    const roleSelect = document.getElementById('role-ai-select');
    container.innerHTML = '';
    roleSelect.innerHTML = '<option value="">使用系统默认激活端点</option>';
    endpoints.forEach(ai => {
        const opt = document.createElement('option');
        opt.value = ai.id; opt.innerText = ai.name;
        roleSelect.appendChild(opt);
        const item = document.createElement('div');
        item.className = 'ai-endpoint-item';
        item.style = `background: #2d2d2d; padding: 15px; border-radius: 6px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; border: 1px solid ${ai.is_active ? '#0078d4' : '#3c3c3c'};`;
        const caps = Array.isArray(ai.capabilities) ? ai.capabilities : ['text'];
        const capTags = caps.map(c => `<span style="font-size:0.7rem; background:#444; padding:2px 5px; border-radius:3px; margin-right:5px;">${c}</span>`).join('');
        item.innerHTML = `<div><div style="font-weight: bold;">${ai.name} ${ai.is_active ? '<span style="color: #4caf50; font-size: 0.8rem;">(当前默认)</span>' : ''}</div><div style="font-size: 0.8rem; color: #999; margin: 5px 0;">${ai.model} | ${ai.base_url}</div><div>${capTags}</div></div>` +
                         `<div class="actions"><button class="btn btn-sm btn-secondary" onclick="testAIFromList(${JSON.stringify(ai).replace(/"/g, '&quot;')}, event)">测试</button><button class="btn btn-sm btn-secondary" onclick="showEditAIModal(${ai.id})">编辑</button>${!ai.is_active ? `<button class="btn btn-sm btn-primary" onclick="activateAI(${ai.id})">激活默认</button>` : ''}<button class="btn btn-sm btn-danger" onclick="deleteAI(${ai.id})"><i class="fas fa-trash"></i></button></div>`;
        container.appendChild(item);
    });
}

async function handleAddAI(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    const data = Object.fromEntries(formData.entries());
    const capabilities = [];
    e.target.querySelectorAll('input[name="capabilities"]:checked').forEach(cb => capabilities.push(cb.value));
    data.capabilities = capabilities;
    const id = data.id; delete data.id;
    const res = await fetch(id ? `/api/ai_endpoints/${id}` : '/api/ai_endpoints', { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    if (res.ok) { closeModal('ai-modal'); loadAIEndpoints(); }
}

async function showEditAIModal(id) {
    const res = await fetch(`/api/ai_endpoints/${id}`);
    const ai = await res.json();
    const form = document.getElementById('ai-form');
    form.reset();
    document.getElementById('ai-id').value = id;
    form.name.value = ai.name; form.base_url.value = ai.base_url;
    form.api_key.value = ai.api_key; form.model.value = ai.model;
    const caps = Array.isArray(ai.capabilities) ? ai.capabilities : ['text'];
    form.querySelectorAll('input[name="capabilities"]').forEach(cb => { cb.checked = caps.includes(cb.value); });
    document.getElementById('ai-modal-title').innerText = '编辑 AI 端点';
    document.getElementById('ai-modal').style.display = 'block';
}

function showAddAIModal() { document.getElementById('ai-form').reset(); document.getElementById('ai-id').value = ''; document.getElementById('ai-modal-title').innerText = '添加 AI 端点'; document.getElementById('ai-modal').style.display = 'block'; }

async function testAIConnection(data, btn) {
    const oldText = btn.innerText; btn.innerText = '测试中...'; btn.disabled = true;
    try {
        const res = await fetch('/api/ai_endpoints/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        const result = await res.json(); alert(result.success ? '连接成功！' : '连接失败：' + result.message);
    } catch (e) { alert('异常：' + e.message); }
    finally { btn.innerText = oldText; btn.disabled = false; }
}

async function testAIFromList(ai, e) { await testAIConnection({ base_url: ai.base_url, api_key: ai.api_key, model: ai.model }, e.target); }
async function testAIFromModal(e) {
    const form = document.getElementById('ai-form');
    const data = { base_url: form.base_url.value, api_key: form.api_key.value, model: form.model.value };
    if (!data.base_url || !data.api_key || !data.model) { alert('请先填写完整信息'); return; }
    await testAIConnection(data, e.target);
}

async function activateAI(id) { await fetch(`/api/ai_endpoints/${id}/activate`, { method: 'POST' }); loadAIEndpoints(); }
async function deleteAI(id) { if (confirm('确定删除吗？')) { await fetch(`/api/ai_endpoints/${id}`, { method: 'DELETE' }); loadAIEndpoints(); } }

async function loadRoles() {
    const res = await fetch('/api/roles');
    const roles = await res.json();
    const container = document.getElementById('role-list-container');
    container.innerHTML = '';
    roles.forEach(role => {
        if (role.is_active) document.getElementById('active-role-badge').innerText = role.name;
        const card = document.createElement('div');
        card.className = `role-card ${role.is_active ? 'active' : ''}`;
        card.innerHTML = `<h4>${role.name} ${role.is_active ? '<span style="color:#4caf50; font-size:0.8rem;">(当前激活)</span>' : ''}</h4><div class="role-model-info"><i class="fas fa-robot"></i> ${role.ai_name || '默认端点'}</div><div class="role-prompt-preview">${role.system_prompt}</div><div class="role-card-footer">${!role.is_active ? `<button class="btn btn-sm btn-primary" onclick="activateRole(${role.id})">激活</button>` : ''}<button class="btn btn-sm btn-secondary" onclick="showEditRoleModal(${role.id})">编辑</button><button class="btn btn-sm btn-danger" onclick="deleteRole(${role.id})"><i class="fas fa-trash"></i></button></div>`;
        container.appendChild(card);
    });
}

function showAddRoleModal() { document.getElementById('role-form').reset(); document.getElementById('role-id').value = ''; document.getElementById('role-modal-title').innerText = '创建新角色'; document.getElementById('role-modal').style.display = 'block'; }

async function showEditRoleModal(id) {
    const res = await fetch(`/api/roles/${id}`);
    const role = await res.json();
    const form = document.getElementById('role-form');
    form.reset();
    document.getElementById('role-id').value = id;
    form.name.value = role.name; form.ai_endpoint_id.value = role.ai_endpoint_id || '';
    form.system_prompt.value = role.system_prompt;
    document.getElementById('role-modal-title').innerText = '编辑角色';
    document.getElementById('role-modal').style.display = 'block';
}

async function handleAddRole(e) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    const id = data.id; delete data.id;
    if (data.ai_endpoint_id === "") data.ai_endpoint_id = null;
    const res = await fetch(id ? `/api/roles/${id}` : '/api/roles', { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    if (res.ok) { closeModal('role-modal'); loadRoles(); }
}

async function activateRole(id) { await fetch(`/api/roles/${id}/activate`, { method: 'POST' }); loadRoles(); if (confirm('角色已切换，刷新页面以应用提示词？')) window.location.reload(); }
async function deleteRole(id) { if (confirm('确定删除该角色吗？')) { await fetch(`/api/roles/${id}`, { method: 'DELETE' }); loadRoles(); } }

async function loadSystemSettings() {
    const res = await fetch('/api/system_settings');
    const settings = await res.json();
    const form = document.getElementById('system-settings-form');
    for (const [key, value] of Object.entries(settings)) { if (form[key]) form[key].value = value; }
}

async function handleSaveSystemSettings(e) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    const res = await fetch('/api/system_settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    if (res.ok) alert('系统设置已保存');
}

// --- 日志管理逻辑 ---
async function showLogsModal() {
    const modal = document.getElementById('logs-modal');
    modal.style.display = 'block';
    const select = document.getElementById('log-file-select');
    select.innerHTML = '<option value="">加载中...</option>';
    
    // 给 select 添加一个 change 事件监听，以便切换文件时自动刷新
    if (!select.dataset.listener) {
        select.onchange = loadLogContent;
        select.dataset.listener = "true";
    }
    
    try {
        const res = await fetch('/api/logs');
        const logs = await res.json();
        
        if (logs && logs.length > 0) {
            select.innerHTML = logs.map(log => 
                `<option value="${log.name}">${log.name} (${log.size} KB)</option>`
            ).join('');
            // 自动加载第一份
            if (!select.value) {
                select.value = logs[0].name;
                loadLogContent();
            }
        } else {
            select.innerHTML = '<option value="">暂无日志文件</option>';
            document.getElementById('log-content-viewer').innerText = '当前没有生成的日志文件。';
        }
    } catch (e) {
        console.error('加载日志列表失败:', e);
        select.innerHTML = '<option value="">加载失败</option>';
    }
}

async function loadLogContent() {
    const filename = document.getElementById('log-file-select').value;
    const lines = document.getElementById('log-lines-select').value;
    const viewer = document.getElementById('log-content-viewer');
    
    if (!filename) return;
    
    viewer.innerText = '正在读取日志内容，请稍候...';
    try {
        const res = await fetch(`/api/logs/content?filename=${encodeURIComponent(filename)}&lines=${lines}`);
        const data = await res.json();
        if (data.content) {
            viewer.innerText = data.content;
            // 滚动到底部
            viewer.scrollTop = viewer.scrollHeight;
        } else {
            viewer.innerText = '日志内容为空。';
        }
    } catch (e) {
        console.error('加载日志内容失败:', e);
        viewer.innerText = '读取失败：' + e.message;
    }
}

async function handleClearLogs() {
    if (!confirm('确定要清空所有系统日志文件吗？此操作不可恢复。')) return;
    
    try {
        const res = await fetch('/api/logs', { method: 'DELETE' });
        if (res.ok) {
            alert('所有日志已清空');
            // 如果日志查看器开着，刷新一下
            if (document.getElementById('logs-modal').style.display === 'block') {
                showLogsModal();
            }
        }
    } catch (e) {
        alert('清空失败：' + e.message);
    }
}

// --- 底部工具面板逻辑 (SFTP/快捷命令) ---
function initBottomPanel() {
    const bottomPanel = document.getElementById('bottom-panel');
    const toggleBtn = document.getElementById('toggle-bottom-panel');
    const panelTabs = document.querySelectorAll('.panel-tab');
    const resizer = document.getElementById('bottom-panel-resizer');

    // 1. 标签切换
    panelTabs.forEach(tab => {
        tab.onclick = () => {
            panelTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const targetId = tab.getAttribute('data-tab');
            document.querySelectorAll('.panel-content').forEach(c => c.classList.remove('active'));
            document.getElementById(targetId).classList.add('active');
            // 如果切换到命令，加载命令
            if (targetId === 'commands-view') loadCommandGroups();
            // 如果切换到文件，加载文件
            if (targetId === 'files-view') loadSftpFiles();
        };
    });

    // 2. 显隐切换
    toggleBtn.onclick = () => {
        bottomPanel.classList.toggle('collapsed');
        setTimeout(() => {
            const activeTab = tabs.find(t => t.id === activeTabId);
            if (activeTab) activeTab.fitAddon.fit();
        }, 250);
    };

    // 3. 高度调整 (Resizer)
    let isResizing = false;
    resizer.addEventListener('mousedown', (e) => {
        isResizing = true;
        resizer.classList.add('resizing');
        document.body.style.cursor = 'ns-resize';
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const container = document.querySelector('.terminal-section');
        const containerRect = container.getBoundingClientRect();
        const newHeight = containerRect.bottom - e.clientY;
        
        if (newHeight >= 40 && newHeight <= containerRect.height * 0.8) {
            bottomPanel.style.height = `${newHeight}px`;
            const activeTab = tabs.find(t => t.id === activeTabId);
            if (activeTab) activeTab.fitAddon.fit();
        }
    });

    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            resizer.classList.remove('resizing');
            document.body.style.cursor = 'default';
        }
    });

    // 弹窗表单处理
    document.getElementById('command-group-form').onsubmit = handleAddCommandGroup;
    document.getElementById('command-form').onsubmit = handleAddCommand;
}

// --- 快捷命令数据逻辑 ---
async function loadCommandGroups() {
    const res = await fetch('/api/command_groups');
    const groups = await res.json();
    const container = document.getElementById('command-groups-list');
    container.innerHTML = '';
    
    groups.forEach(group => {
        const div = document.createElement('div');
        div.className = 'cmd-group-item';
        if (activeCommandGroupId === group.id) div.classList.add('active');
        div.innerHTML = `
            <span><i class="far fa-folder" style="margin-right:8px;"></i>${group.name}</span>
            <div class="group-actions">
                <i class="fas fa-edit" onclick="showEditCommandGroupModal(event, ${group.id}, '${group.name}')" style="margin-right:5px; font-size:0.7rem;"></i>
                <i class="fas fa-trash" onclick="handleDeleteCommandGroup(event, ${group.id})" style="font-size:0.7rem;"></i>
            </div>
        `;
        div.onclick = () => {
            activeCommandGroupId = group.id;
            document.querySelectorAll('.cmd-group-item').forEach(i => i.classList.remove('active'));
            div.classList.add('active');
            document.getElementById('current-group-name').innerText = group.name;
            loadCommands(group.id);
        };
        container.appendChild(div);
    });

    if (groups.length > 0 && !activeCommandGroupId) {
        container.firstChild.click();
    }
}

async function loadCommands(groupId) {
    const res = await fetch(`/api/commands/${groupId}`);
    const commands = await res.json();
    const container = document.getElementById('commands-list');
    container.innerHTML = '';
    
    commands.forEach(cmd => {
        const div = document.createElement('div');
        div.className = 'command-tile';
        div.innerHTML = `
            <span class="cmd-name">${cmd.name}</span>
            <span class="cmd-preview">${cmd.content}</span>
            <div class="cmd-actions">
                <button class="cmd-action-btn" onclick="showEditCommandModal(event, ${JSON.stringify(cmd).replace(/"/g, '&quot;')})"><i class="fas fa-edit"></i></button>
                <button class="cmd-action-btn" onclick="handleDeleteCommand(event, ${cmd.id})"><i class="fas fa-trash"></i></button>
            </div>
        `;
        div.onclick = () => sendCommandToTerminal(cmd.content, cmd.auto_cr);
        container.appendChild(div);
    });
}

function sendCommandToTerminal(content, autoCr) {
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab || !activeTab.sshWs || activeTab.sshWs.readyState !== WebSocket.OPEN) {
        alert('请先连接服务器');
        return;
    }
    const finalCmd = autoCr ? content.trim() + '\n' : content;
    activeTab.sshWs.send(finalCmd);
}

// 弹窗控制
function showAddCommandGroupModal() {
    const form = document.getElementById('command-group-form');
    form.reset();
    document.getElementById('command-group-id').value = '';
    document.getElementById('command-group-modal-title').innerText = '添加分类';
    document.getElementById('command-group-modal').style.display = 'block';
}

function showEditCommandGroupModal(e, id, name) {
    e.stopPropagation();
    document.getElementById('command-group-id').value = id;
    document.getElementById('command-group-form').name.value = name;
    document.getElementById('command-group-modal-title').innerText = '编辑分类';
    document.getElementById('command-group-modal').style.display = 'block';
}

async function handleAddCommandGroup(e) {
    e.preventDefault();
    const id = document.getElementById('command-group-id').value;
    const name = e.target.name.value;
    const method = id ? 'PUT' : 'POST';
    const url = id ? `/api/command_groups/${id}` : '/api/command_groups';
    
    const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
    if (res.ok) {
        closeModal('command-group-modal');
        loadCommandGroups();
    }
}

async function handleDeleteCommandGroup(e, id) {
    e.stopPropagation();
    if (!confirm('确定要删除该分类及其下的所有命令吗？')) return;
    const res = await fetch(`/api/command_groups/${id}`, { method: 'DELETE' });
    if (res.ok) {
        if (activeCommandGroupId === id) activeCommandGroupId = null;
        loadCommandGroups();
    }
}

function showAddCommandModal() {
    if (!activeCommandGroupId) { alert('请先选择或创建一个分类'); return; }
    const form = document.getElementById('command-form');
    form.reset();
    document.getElementById('command-id').value = '';
    document.getElementById('command-group-id-hidden').value = activeCommandGroupId;
    document.getElementById('command-modal-title').innerText = '添加快捷命令';
    document.getElementById('command-modal').style.display = 'block';
}

function showEditCommandModal(e, cmd) {
    e.stopPropagation();
    const form = document.getElementById('command-form');
    document.getElementById('command-id').value = cmd.id;
    document.getElementById('command-group-id-hidden').value = cmd.group_id;
    form.name.value = cmd.name;
    form.content.value = cmd.content;
    form.auto_cr.checked = cmd.auto_cr === 1;
    document.getElementById('command-modal-title').innerText = '编辑快捷命令';
    document.getElementById('command-modal').style.display = 'block';
}

async function handleAddCommand(e) {
    e.preventDefault();
    const id = document.getElementById('command-id').value;
    const formData = new FormData(e.target);
    const data = {
        name: formData.get('name'),
        content: formData.get('content'),
        group_id: parseInt(formData.get('group_id')),
        auto_cr: formData.get('auto_cr') ? 1 : 0
    };
    
    const method = id ? 'PUT' : 'POST';
    const url = id ? `/api/commands/${id}` : '/api/commands';
    
    const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (res.ok) {
        closeModal('command-modal');
        loadCommands(activeCommandGroupId);
    }
}

async function handleDeleteCommand(e, id) {
    e.stopPropagation();
    if (!confirm('确定要删除该命令吗？')) return;
    const res = await fetch(`/api/commands/${id}`, { method: 'DELETE' });
    if (res.ok) {
        loadCommands(activeCommandGroupId);
    }
}

// --- SFTP 文件管理逻辑 ---
async function loadSftpFiles(path = '') {
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab) return;
    
    const container = document.getElementById('sftp-file-list');
    const pathInput = document.getElementById('sftp-current-path');
    
    // 如果没有传入路径，则使用 tab 记录的路径
    const targetPath = path || activeTab.sftpCurrentPath;
    
    container.innerHTML = '<div style="padding:20px; color:#888;">正在加载文件列表...</div>';
    
    try {
        const url = `/api/sftp/list?server_id=${activeTab.server.id}&path=${encodeURIComponent(targetPath)}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(await res.text());
        
        const data = await res.json();
        activeTab.sftpCurrentPath = data.path;
        pathInput.value = data.path;
        
        container.innerHTML = '';
        
        // 为容器本身添加右键菜单支持（点击空白处）
        container.oncontextmenu = (e) => {
            if (e.target === container) {
                e.preventDefault();
                e.stopPropagation();
                sftpSelectedFile = null; // 清空选中，表示在空白处点击
                const menu = document.getElementById('sftp-context-menu');
                document.getElementById('sftp-file-ops').style.display = 'none';
                document.getElementById('sftp-delete-op').style.display = 'none';
                menu.style.display = 'block';
                showContextMenu(e);
            }
        };

        data.files.forEach(file => {
            const div = document.createElement('div');
            div.className = 'sftp-item';
            
            // 根据文件类型选择图标
            let icon = 'fa-file-alt';
            if (file.is_dir) icon = 'fa-folder';
            else if (file.name.match(/\.(sh|py|js|json|php|html|css)$/)) icon = 'fa-file-code';
            else if (file.name.match(/\.(zip|tar|gz|7z|rar)$/)) icon = 'fa-file-archive';
            else if (file.name.match(/\.(png|jpg|jpeg|gif|svg)$/)) icon = 'fa-file-image';
            
            const sizeStr = file.is_dir ? '-' : formatBytes(file.size);
            const mtimeStr = new Date(file.mtime * 1000).toLocaleString();
            
            div.innerHTML = `
                <div class="col-name"><i class="fas ${icon}"></i>${file.name}</div>
                <div class="col-size">${sizeStr}</div>
                <div class="col-mtime">${mtimeStr}</div>
                <div class="col-mode">${file.mode}</div>
            `;
            
            div.ondblclick = () => {
                if (file.is_dir) {
                    const newPath = activeTab.sftpCurrentPath === '/' ? `/${file.name}` : `${activeTab.sftpCurrentPath}/${file.name}`;
                    loadSftpFiles(newPath);
                } else {
                    // 检查是否为可编辑文件类型
                    const editableExtensions = ['.txt', '.log', '.py', '.js', '.html', '.css', '.sh', '.conf', '.ini', '.yaml', '.yml', '.json', '.md', '.sql', '.php', '.lua', '.xml', '.java', '.c', '.cpp', '.h', '.go', '.rs'];
                    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
                    if (editableExtensions.includes(ext) || !file.name.includes('.')) {
                        openFileInEditor(file);
                    } else {
                        addMessage('system', `文件类型 [${ext}] 暂不支持在线编辑。`);
                    }
                }
            };

            // 右键菜单支持
            div.oncontextmenu = (e) => {
                e.preventDefault();
                e.stopPropagation();
                sftpSelectedFile = file;
                const menu = document.getElementById('sftp-context-menu');
                document.getElementById('sftp-file-ops').style.display = 'block';
                document.getElementById('sftp-delete-op').style.display = 'block';
                menu.style.display = 'block';
                showContextMenu(e);
            };
            
            container.appendChild(div);
        });
        
    } catch (e) {
        container.innerHTML = `<div style="padding:20px; color:#f44336;">加载失败: ${e.message}</div>`;
    }
}

// SFTP 操作处理
// 提取通用的右键菜单显示逻辑
function showContextMenu(e) {
    const menu = document.getElementById('sftp-context-menu');
    // 边界检查：防止菜单超出底部
    const menuHeight = menu.offsetHeight || 240; // 增加了新建项后的预估高度
    const windowHeight = window.innerHeight;
    const windowWidth = window.innerWidth;
    const menuWidth = menu.offsetWidth || 160;
    
    let top = e.pageY;
    let left = e.pageX;
    
    if (top + menuHeight > windowHeight) {
        top = top - menuHeight;
    }
    if (left + menuWidth > windowWidth) {
        left = left - menuWidth;
    }
    
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
}

async function sftpAction(type) {
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab) return;

    // 如果选了文件，构造路径；否则使用当前目录
    const fileName = sftpSelectedFile ? sftpSelectedFile.name : '';
    const filePath = sftpSelectedFile ? (activeTab.sftpCurrentPath === '/' ? `/${fileName}` : `${activeTab.sftpCurrentPath}/${fileName}`) : activeTab.sftpCurrentPath;

    switch (type) {
        case 'download':
            if (!sftpSelectedFile) return;
            window.location.href = `/api/sftp/download?server_id=${activeTab.server.id}&path=${encodeURIComponent(filePath)}`;
            break;
        case 'rename':
            if (!sftpSelectedFile) return;
            document.getElementById('sftp-rename-old-path').value = filePath;
            document.getElementById('sftp-new-name').value = sftpSelectedFile.name;
            document.getElementById('sftp-rename-modal').style.display = 'block';
            break;
        case 'edit':
            if (!sftpSelectedFile) return;
            openFileInEditor(sftpSelectedFile);
            break;
        case 'chmod':
            if (!sftpSelectedFile) return;
            document.getElementById('sftp-chmod-path').value = filePath;
            document.getElementById('sftp-chmod-filename').innerText = sftpSelectedFile.name;
            const currentMode = sftpSelectedFile.mode || '0644';
            document.getElementById('sftp-new-mode').value = currentMode;
            setChmodCheckboxes(currentMode);
            document.getElementById('sftp-chmod-modal').style.display = 'block';
            break;
        case 'newfile':
        case 'newdir':
            document.getElementById('sftp-create-type').value = type === 'newfile' ? 'file' : 'dir';
            document.getElementById('sftp-create-title').innerText = type === 'newfile' ? '新建文件' : '新建目录';
            document.getElementById('sftp-create-label').innerText = type === 'newfile' ? '文件名称' : '目录名称';
            document.getElementById('sftp-create-name').value = '';
            document.getElementById('sftp-create-modal').style.display = 'block';
            setTimeout(() => document.getElementById('sftp-create-name').focus(), 100);
            break;
        case 'delete':
            if (!sftpSelectedFile) return;
            if (confirm(`确定要删除 ${sftpSelectedFile.is_dir ? '目录' : '文件'} [${sftpSelectedFile.name}] 吗？`)) {
                try {
                    const res = await fetch('/api/sftp/delete', {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            server_id: activeTab.server.id,
                            path: filePath,
                            is_dir: sftpSelectedFile.is_dir
                        })
                    });
                    if (res.ok) sftpRefresh();
                    else alert('删除失败: ' + await res.text());
                } catch (e) { alert('删除失败: ' + e.message); }
            }
            break;
    }
}

// 处理上传
function showUploadModal() {
    document.getElementById('sftp-upload-input').click();
}

async function handleSftpUpload(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    await uploadFiles(files);
    e.target.value = ''; // 清空选择
}

document.getElementById('sftp-create-form').onsubmit = async (e) => {
    e.preventDefault();
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab) return;

    const name = document.getElementById('sftp-create-name').value.trim();
    const type = document.getElementById('sftp-create-type').value;
    if (!name) return;

    try {
        const targetPath = activeTab.sftpCurrentPath === '/' ? `/${name}` : `${activeTab.sftpCurrentPath}/${name}`;
        const res = await fetch('/api/sftp/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                server_id: activeTab.server.id,
                path: targetPath,
                type: type
            })
        });
        if (res.ok) {
            closeModal('sftp-create-modal');
            sftpRefresh();
        } else {
            alert('创建失败: ' + await res.text());
        }
    } catch (err) {
        alert('创建异常: ' + err.message);
    }
};

async function uploadFiles(files) {
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab) return;

    for (let file of files) {
        const uploadFormData = new FormData();
        uploadFormData.append('server_id', activeTab.server.id);
        uploadFormData.append('path', activeTab.sftpCurrentPath || '/');
        uploadFormData.append('file', file);

        addMessage('system', `正在上传: ${file.name}...`);
        
        try {
            const res = await fetch('/api/sftp/upload', {
                method: 'POST',
                body: uploadFormData
            });
            if (res.ok) {
                addMessage('system', `上传成功: ${file.name}`);
                sftpRefresh();
            } else {
                addMessage('system', `上传失败: ${file.name}`);
            }
        } catch (err) {
            addMessage('system', `上传异常: ${file.name} - ${err.message}`);
        }
    }
}

function initSftpDragAndDrop() {
    const dropZone = document.getElementById('sftp-file-list');
    if (!dropZone) return;

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
        }, false);
    });

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => {
            dropZone.classList.add('drag-over');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => {
            dropZone.classList.remove('drag-over');
        }, false);
    });

    dropZone.addEventListener('drop', (e) => {
        const files = e.dataTransfer.files;
        if (files && files.length > 0) {
            uploadFiles(files);
        }
    }, false);
}

// 初始化 SFTP 弹窗表单
document.getElementById('sftp-rename-form').onsubmit = async (e) => {
    e.preventDefault();
    const activeTab = tabs.find(t => t.id === activeTabId);
    const oldPath = document.getElementById('sftp-rename-old-path').value;
    const newName = document.getElementById('sftp-new-name').value;
    const parentPath = oldPath.substring(0, oldPath.lastIndexOf('/')) || '/';
    const newPath = parentPath === '/' ? `/${newName}` : `${parentPath}/${newName}`;

    try {
        const res = await fetch('/api/sftp/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                server_id: activeTab.server.id,
                old_path: oldPath,
                new_path: newPath
            })
        });
        if (res.ok) {
            closeModal('sftp-rename-modal');
            sftpRefresh();
        } else alert('重命名失败');
    } catch (e) { alert('重命名失败: ' + e.message); }
};

document.getElementById('sftp-chmod-form').onsubmit = async (e) => {
    e.preventDefault();
    const activeTab = tabs.find(t => t.id === activeTabId);
    const path = document.getElementById('sftp-chmod-path').value;
    const mode = document.getElementById('sftp-new-mode').value;

    try {
        const res = await fetch('/api/sftp/chmod', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                server_id: activeTab.server.id,
                path: path,
                mode: mode
            })
        });
        if (res.ok) {
            closeModal('sftp-chmod-modal');
            sftpRefresh();
        } else alert('修改权限失败');
    } catch (e) { alert('修改权限失败: ' + e.message); }
};

function sftpGoBack() {
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab || activeTab.sftpCurrentPath === '/' || !activeTab.sftpCurrentPath) return;
    
    const parts = activeTab.sftpCurrentPath.split('/');
    parts.pop();
    const parentPath = parts.join('/') || '/';
    loadSftpFiles(parentPath);
}

function sftpRefresh() {
    loadSftpFiles();
}

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// --- 状态采集与标签页逻辑 ---
function initStatsTabs() {
    const statsTabs = document.querySelectorAll('.sidebar-tab');
    statsTabs.forEach(tab => {
        tab.onclick = () => {
            statsTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const targetId = tab.getAttribute('data-tab');
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(targetId).classList.add('active');
        };
    });
    document.querySelectorAll('.proc-table th.sortable').forEach(th => {
        th.onclick = () => {
            const key = th.getAttribute('data-sort');
            if (currentSort.key === key) currentSort.order = currentSort.key === 'cmd' ? (currentSort.order === 'asc' ? 'desc' : 'asc') : (currentSort.order === 'desc' ? 'asc' : 'desc');
            else { currentSort.key = key; currentSort.order = key === 'cmd' ? 'asc' : 'desc'; }
            document.querySelectorAll('.proc-table th i').forEach(i => i.className = 'fas fa-sort');
            th.querySelector('i').className = `fas fa-sort-${currentSort.order === 'asc' ? 'up' : 'down'}`;
            const activeTab = tabs.find(t => t.id === activeTabId);
            if (activeTab && activeTab.lastStats) updateStatsUI(activeTab.lastStats);
        };
    });
}

let currentSort = { key: 'cpu', order: 'desc' };

function updateStatsUI(data) {
    document.getElementById('info-ip').innerText = data.ip;
    document.getElementById('info-uptime').innerText = data.uptime;
    document.getElementById('info-load').innerText = data.load;
    document.getElementById('info-cpu-val').innerText = `${Math.round(data.cpu)}%`;
    document.getElementById('info-cpu-bar').style.width = `${data.cpu}%`;
    document.getElementById('info-cpu-bar').style.backgroundColor = getUsageColor(data.cpu);
    document.getElementById('info-mem-val').innerText = data.mem;
    document.getElementById('info-mem-bar').style.width = `${data.mem_p}%`;
    document.getElementById('info-mem-bar').style.backgroundColor = getUsageColor(data.mem_p);
    document.getElementById('info-disk-val').innerText = data.disk;
    document.getElementById('info-disk-bar').style.width = `${data.disk_p}%`;
    document.getElementById('info-disk-bar').style.backgroundColor = getUsageColor(data.disk_p);
    const tbody = document.getElementById('proc-list');
    tbody.innerHTML = '';
    const sortedProcs = [...data.procs].sort((a, b) => {
        let valA, valB;
        if (currentSort.key === 'cpu') { valA = a.cpu_raw; valB = b.cpu_raw; }
        else if (currentSort.key === 'mem') { valA = a.mem_raw; valB = b.mem_raw; }
        else { valA = a.cmd.toLowerCase(); valB = b.cmd.toLowerCase(); }
        if (valA < valB) return currentSort.order === 'asc' ? -1 : 1;
        if (valA > valB) return currentSort.order === 'asc' ? 1 : -1;
        return 0;
    });
    sortedProcs.forEach(p => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td title="${p.mem}">${p.mem}</td><td title="${p.cpu}">${p.cpu}</td><td title="${p.cmd}">${p.cmd}</td>`;
        tbody.appendChild(tr);
    });
}

function getUsageColor(percent) { if (percent > 80) return '#f44336'; if (percent > 50) return '#ff9800'; return '#4caf50'; }

// --- 布局初始化 (拖拽与收起) ---
function initLayout() {
    const sidebar = document.getElementById('sidebar');
    const sidebarResizer = document.getElementById('sidebar-resizer');
    const aiSection = document.getElementById('ai-section');
    const aiResizer = document.getElementById('ai-resizer');
    const toggleSidebar = document.getElementById('toggle-sidebar');
    const toggleAI = document.getElementById('toggle-ai');
    const expandAIBtn = document.getElementById('expand-ai-btn');

    toggleSidebar.onclick = () => {
        sidebarCollapsed = !sidebarCollapsed;
        sidebar.classList.toggle('collapsed');
        toggleSidebar.innerHTML = sidebarCollapsed ? '<i class="fas fa-angle-right"></i>' : '<i class="fas fa-angle-left"></i>';
        sidebarResizer.style.display = sidebarCollapsed ? 'none' : 'block';
        const activeTab = tabs.find(t => t.id === activeTabId);
        if (activeTab) setTimeout(() => activeTab.fitAddon.fit(), 200);
    };

    toggleAI.onclick = () => {
        aiCollapsed = true;
        aiSection.classList.add('collapsed');
        aiResizer.style.display = 'none';
        expandAIBtn.style.display = 'inline-block';
        const activeTab = tabs.find(t => t.id === activeTabId);
        if (activeTab) setTimeout(() => activeTab.fitAddon.fit(), 200);
    };

    expandAIBtn.onclick = () => {
        aiCollapsed = false;
        aiSection.classList.remove('collapsed');
        aiResizer.style.display = 'block';
        expandAIBtn.style.display = 'none';
        const activeTab = tabs.find(t => t.id === activeTabId);
        if (activeTab) setTimeout(() => activeTab.fitAddon.fit(), 200);
    };

    function setupResizer(resizer, targetElement, direction = 'left', type) {
        let startX, startWidth;
        resizer.onmousedown = (e) => {
            startX = e.clientX; startWidth = targetElement.offsetWidth;
            document.body.style.cursor = 'col-resize'; resizer.classList.add('dragging');
            const onMouseMove = (e) => {
                let delta = e.clientX - startX;
                if (direction === 'right') delta = -delta;
                let newWidth = startWidth + delta;
                if (type === 'sidebar') { if (newWidth < 60) newWidth = 60; if (newWidth > 600) newWidth = 600; }
                else { if (newWidth < 200) newWidth = 200; if (newWidth > 800) newWidth = 800; }
                targetElement.style.width = `${newWidth}px`;
                const activeTab = tabs.find(t => t.id === activeTabId);
                if (activeTab) activeTab.fitAddon.fit();
            };
            const onMouseUp = () => {
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
                document.body.style.cursor = 'default'; resizer.classList.remove('dragging');
                updateProportionalWidths();
            };
            document.addEventListener('mousemove', onMouseMove); document.addEventListener('mouseup', onMouseUp);
        };
    }
    setupResizer(sidebarResizer, sidebar, 'left', 'sidebar');
    setupResizer(aiResizer, aiSection, 'right', 'ai');
    window.addEventListener('resize', () => {
        if (!sidebarCollapsed) applyProportionalWidth(sidebar, 'sidebar');
        if (!aiCollapsed) applyProportionalWidth(aiSection, 'ai');
    });
    updateProportionalWidths();
}

function updateProportionalWidths() {
    const totalWidth = window.innerWidth;
    const sidebar = document.getElementById('sidebar');
    const aiSection = document.getElementById('ai-section');
    if (sidebar && !sidebarCollapsed) sidebarPercent = sidebar.offsetWidth / totalWidth;
    if (aiSection && !aiCollapsed) aiPercent = aiSection.offsetWidth / totalWidth;
}

function applyProportionalWidth(element, type) {
    const totalWidth = window.innerWidth;
    let targetPercent = (type === 'sidebar') ? sidebarPercent : aiPercent;
    let minW = (type === 'sidebar') ? 60 : 200;
    let newWidth = totalWidth * targetPercent;
    if (newWidth < minW) newWidth = minW;
    element.style.width = `${newWidth}px`;
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (activeTab) activeTab.fitAddon.fit();
}

function closeModal(id) { document.getElementById(id).style.display = 'none'; }
window.onclick = (e) => { if (e.target.classList.contains('modal')) e.target.style.display = 'none'; };

// 权限修改辅助函数
function updateChmodValue() {
    const roles = ['owner', 'group', 'others'];
    const types = ['read', 'write', 'execute'];
    const values = { 'read': 4, 'write': 2, 'execute': 1 };
    
    let octalStr = '';
    roles.forEach(role => {
        let sum = 0;
        types.forEach(type => {
            const cb = document.querySelector(`input[data-role="${role}"][data-type="${type}"]`);
            if (cb && cb.checked) {
                sum += values[type];
            }
        });
        octalStr += sum.toString();
    });
    
    const input = document.getElementById('sftp-new-mode');
    const currentVal = input.value;
    // 如果原值是 4 位（带前导 0 或特殊位），保留第一位
    if (currentVal.length === 4) {
        input.value = currentVal[0] + octalStr;
    } else {
        input.value = octalStr;
    }
}

function setChmodCheckboxes(octal) {
    // 统一处理为 3 位有效位
    const mode = octal.length === 4 ? octal.substring(1) : octal.padStart(3, '0');
    const roles = ['owner', 'group', 'others'];
    const values = { 'read': 4, 'write': 2, 'execute': 1 };
    
    roles.forEach((role, index) => {
        const val = parseInt(mode[index]);
        Object.keys(values).forEach(type => {
            const cb = document.querySelector(`input[data-role="${role}"][data-type="${type}"]`);
            if (cb) {
                cb.checked = (val & values[type]) !== 0;
            }
        });
    });
}

// 监听权限值输入，实时更新勾选状态
const chmodModeInput = document.getElementById('sftp-new-mode');
if (chmodModeInput) {
    chmodModeInput.addEventListener('input', (e) => {
        const val = e.target.value;
        if (/^[0-7]{3,4}$/.test(val)) {
            setChmodCheckboxes(val);
        }
    });
}

// 暴露到全局以便 HTML 调用
window.updateChmodValue = updateChmodValue;
window.setChmodCheckboxes = setChmodCheckboxes;
window.toggleEditorSearch = toggleEditorSearch;

// --- 文件编辑器逻辑 ---
let editor = null;
let currentEditingPath = '';

function initAceEditor() {
    if (editor) return;
    ace.config.set('basePath', '/static/lib/ace');
    editor = ace.edit("ace-editor");
    
    const savedTheme = localStorage.getItem('editor-theme') || 'monokai';
    document.getElementById('editor-theme-select').value = savedTheme;
    editor.setTheme(`ace/theme/${savedTheme}`);
    
    editor.session.setMode("ace/mode/text");
    editor.setOptions({
        fontSize: "14px",
        enableBasicAutocompletion: true,
        enableLiveAutocompletion: true,
        enableSnippets: false, // 默认关闭代码段，防止输入干扰
        showPrintMargin: false,
        useSoftTabs: true,
        tabSize: 4,
        showInvisibles: false,
        wrap: false
    });
    
    // 监听光标变化更新行列信息
    editor.selection.on('changeCursor', () => {
        const pos = editor.getCursorPosition();
        document.getElementById('editor-status-info').innerText = `Ln: ${pos.row + 1}, Col: ${pos.column + 1}`;
    });

    // 绑定 Ctrl+S 快捷键
    editor.commands.addCommand({
        name: 'save',
        bindKey: {win: 'Ctrl-S',  mac: 'Command-S'},
        exec: function(editor) {
            saveEditorContent();
        },
        readOnly: false
    });

    // 绑定 Ctrl+F 搜索
    editor.commands.addCommand({
        name: 'find',
        bindKey: {win: 'Ctrl-F',  mac: 'Command-F'},
        exec: function(editor) {
            ace.require("ace/ext/searchbox").Search(editor);
        },
        readOnly: false
    });
}

function toggleEditorSettingsMenu() {
    document.getElementById('editor-settings-menu').classList.toggle('show');
}

// 点击外部关闭下拉菜单
window.addEventListener('click', (e) => {
    if (!e.target.matches('.dropdown-toggle') && !e.target.closest('.dropdown-toggle')) {
        const menu = document.getElementById('editor-settings-menu');
        if (menu && menu.classList.contains('show')) {
            menu.classList.remove('show');
        }
    }
});

function toggleEditorOption(option, checked) {
    if (!editor) return;
    switch(option) {
        case 'wrap':
            editor.session.setUseWrapMode(checked);
            break;
        case 'autocomplete':
            editor.setOptions({ enableBasicAutocompletion: checked, enableLiveAutocompletion: checked });
            break;
        case 'snippets':
            editor.setOptions({ enableSnippets: checked });
            break;
        case 'invisible':
            editor.setShowInvisibles(checked);
            break;
        case 'linenumbers':
            editor.setOption("showLineNumbers", checked);
            editor.renderer.setShowGutter(checked);
            break;
    }
}

function changeEditorTabSize(size) {
    if (editor) editor.session.setTabSize(parseInt(size));
}

function toggleEditorSearch(mode) {
    if (!editor) return;
    const sb = document.querySelector('.ace_search');
    // 如果搜索框存在且不是隐藏状态
    if (sb && sb.style.display !== 'none') {
        // 检查当前显示的是搜索还是替换
        const replaceForm = sb.querySelector('.ace_replace_form');
        const isReplaceVisible = replaceForm && replaceForm.style.display !== 'none';
        
        // 如果点击的模式与当前显示模式一致，则关闭
        if ((mode === 'find' && !isReplaceVisible) || (mode === 'replace' && isReplaceVisible)) {
            const closeBtn = sb.querySelector('.ace_searchbtn_close');
            if (closeBtn) closeBtn.click();
            return;
        }
    }
    // 否则打开或切换模式
    editor.execCommand(mode);
}

function showJumpLine() {
    document.getElementById('editor-jump-line').value = '';
    document.getElementById('editor-jump-modal').style.display = 'block';
    setTimeout(() => document.getElementById('editor-jump-line').focus(), 100);
}

// 初始化跳转行表单
document.getElementById('editor-jump-form').onsubmit = (e) => {
    e.preventDefault();
    const line = document.getElementById('editor-jump-line').value;
    if (line && !isNaN(line)) {
        editor.gotoLine(parseInt(line));
        closeModal('editor-jump-modal');
    }
};

function showShortcuts() {
    document.getElementById('editor-shortcuts-modal').style.display = 'block';
}

async function openFileInEditor(file) {
    if (file.is_dir) return;
    
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab) return;

    const filePath = activeTab.sftpCurrentPath === '/' ? `/${file.name}` : `${activeTab.sftpCurrentPath}/${file.name}`;
    currentEditingPath = filePath;

    // 显示加载中提示
    addMessage('system', `正在读取文件: ${file.name}...`);
    
    try {
        const res = await fetch(`/api/sftp/read?server_id=${activeTab.server.id}&path=${encodeURIComponent(filePath)}`);
        const data = await res.json();
        
        if (!res.ok) {
            alert(data.detail || '读取文件失败');
            return;
        }

        initAceEditor();
        editor.setValue(data.content, -1);
        editor.setReadOnly(data.readonly);
        
        // 设置模式 (语法高亮)
        const modelist = ace.require("ace/ext/modelist");
        const modeInfo = modelist.getModeForPath(filePath);
        editor.session.setMode(modeInfo.mode);
        document.getElementById('editor-mode-display').innerText = `语言: ${modeInfo.caption}`;
        
        document.getElementById('editor-filename').innerText = file.name;
        document.getElementById('editor-status-path').innerText = filePath;
        document.getElementById('editor-modal').style.display = 'block';
        
        if (data.readonly) {
            addMessage('system', `提示: 文件 [${file.name}] 超过 3MB，已进入只读模式。`);
        }

    } catch (err) {
        alert('读取文件异常: ' + err.message);
    }
}

async function saveEditorContent() {
    if (!editor || editor.getReadOnly()) {
        if (editor && editor.getReadOnly()) alert('文件处于只读模式，无法保存');
        return;
    }

    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab) return;

    const content = editor.getValue();
    
    try {
        const res = await fetch('/api/sftp/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                server_id: activeTab.server.id,
                path: currentEditingPath,
                content: content
            })
        });
        
        if (res.ok) {
            addMessage('system', `文件保存成功: ${currentEditingPath}`);
            // 可以添加一个简单的 UI 反馈
            const saveBtn = document.querySelector('.btn-primary[onclick="saveEditorContent()"]');
            const originalText = saveBtn.innerHTML;
            saveBtn.innerHTML = '<i class="fas fa-check"></i> 已保存';
            setTimeout(() => saveBtn.innerHTML = originalText, 2000);
        } else {
            const data = await res.json();
            alert('保存失败: ' + (data.detail || '未知错误'));
        }
    } catch (err) {
        alert('保存异常: ' + err.message);
    }
}

function refreshEditorContent() {
    if (confirm('刷新将丢失未保存的更改，确定吗？')) {
        const filename = document.getElementById('editor-filename').innerText;
        openFileInEditor({ name: filename, is_dir: false });
    }
}

function changeEditorTheme(theme) {
    if (editor) {
        editor.setTheme(`ace/theme/${theme}`);
        // 可以考虑保存用户偏好
        localStorage.setItem('editor-theme', theme);
    }
}

function changeEditorFontSize(size) {
    if (editor) editor.setOptions({ fontSize: size });
}

function closeEditorModal() {
    document.getElementById('editor-modal').style.display = 'none';
}

// 修改 loadSftpFiles 中的双击逻辑
function updateSftpItemsDblClick() {
    // 已经在 loadSftpFiles 中处理了，只需确保它是调用 openFileInEditor
}
