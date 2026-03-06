/**
 * XTerm-AI 核心入口文件 (ES Modules)
 */
import { loadComponents, showModal, closeModal, storage, notify } from './modules/utils.js';
import { api } from './modules/api.js';
import * as terminal from './modules/terminal.js';
import * as ai from './modules/ai_chat.js';
import * as sftp from './modules/sftp.js';
import * as editor from './modules/editor.js';
import * as stats from './modules/stats.js';
import * as settings from './modules/settings.js';

// 全局变量维护
window.getTab = (id) => terminal.tabs.find(t => t.id === id);
window.showModal = showModal;
window.closeModal = closeModal;

// 1. 初始化
async function init() {
    console.log("🚀 XTerm-AI 初始化中...");
    
    // 1. 先加载所有 HTML 组件 (弹窗等)
    await loadComponents();
    
    // 2. 确保 DOM 就绪后再初始化模块
    terminal.initTerminalModule();
    ai.initAIModule();
    sftp.initSFTPModule();
    editor.initEditorModule();
    stats.initStatsModule();
    settings.initSettingsModule();
    
    // 3. 绑定 UI 事件
    initNavigation();
    initLayoutResizer();
    initStatsTabs();
    initBottomPanel();
    
    window.addEventListener('resize', () => terminal.fitActiveTerminal());
    
    loadServers();
    loadConnectionHistory();
    loadCommandGroups();
    
    // 监听命令变更
    window.addEventListener('commandsChanged', () => loadCommandGroups());
    
    console.log("✅ 初始化完成");
}

// --- 命令片段逻辑 ---
async function loadCommandGroups() {
    try {
        const groups = await api.getCommandGroups();
        const container = document.getElementById('command-groups-list');
        if (!container) return;
        
        container.innerHTML = groups.map(g => `
            <div class="commands-sidebar-item" data-id="${g.id}">
                <span>${g.name}</span>
            </div>
        `).join('');
        
        document.querySelectorAll('.commands-sidebar-item').forEach(item => {
            item.onclick = () => {
                document.querySelectorAll('.commands-sidebar-item').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                loadCommands(item.getAttribute('data-id'));
            };
        });
        
        if (groups.length > 0) {
            document.querySelector('.commands-sidebar-item').click();
        }
    } catch (err) { console.error("加载命令分组失败", err); }
}

async function loadCommands(groupId) {
    try {
        const commands = await api.getCommands(groupId);
        const container = document.getElementById('commands-grid');
        if (!container) return;
        
        container.innerHTML = commands.map(cmd => `
            <div class="command-tile" onclick="executeCommand('${btoa(unescape(encodeURIComponent(cmd.content)))}', ${cmd.auto_cr})">
                <div class="command-tile-name">${cmd.name}</div>
                <div class="command-tile-content">${cmd.content.substring(0, 30)}${cmd.content.length > 30 ? '...' : ''}</div>
                <div class="command-tile-actions">
                    <span title="编辑" onclick="event.stopPropagation(); editCommand(${cmd.id})"><i class="fas fa-edit"></i></span>
                    <span title="删除" onclick="event.stopPropagation(); deleteCommand(${cmd.id}, '${cmd.name.replace(/'/g, "\\'")}')"><i class="fas fa-trash-alt"></i></span>
                </div>
            </div>
        `).join('');
        
        window.executeCommand = (encodedContent, autoCr) => {
            const content = decodeURIComponent(escape(atob(encodedContent)));
            const activeTab = window.getTab(terminal.activeTabId);
            if (!activeTab || !activeTab.socket) return notify("请先选择一个活跃的终端标签", "warning");
            activeTab.socket.send(JSON.stringify({ type: 'data', data: content + (autoCr ? '\n' : '') }));
        };

        window.editCommand = (id) => {
            const cmd = commands.find(c => c.id === id);
            if (!cmd) return;
            document.getElementById('command-modal-title').innerText = '编辑快捷命令';
            document.getElementById('command-id').value = cmd.id;
            document.getElementById('command-group-id-hidden').value = cmd.group_id;
            const form = document.getElementById('command-form');
            form.name.value = cmd.name;
            form.content.value = cmd.content;
            form.auto_cr.checked = !!cmd.auto_cr;
            showModal('command-modal');
        };

        window.deleteCommand = async (id, name) => {
            if (!confirm(`确定要删除命令「${name}」吗？`)) return;
            try {
                await api.deleteCommand(id);
                notify(`已删除命令「${name}」`, 'success');
                window.dispatchEvent(new CustomEvent('commandsChanged'));
            } catch (err) { notify('删除失败: ' + err.message, 'error'); }
        };
    } catch (err) { console.error("加载命令列表失败", err); }
}

// --- UI 交互模块 ---

function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.onclick = () => {
            const viewId = item.getAttribute('data-view');
            document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            const targetView = document.getElementById(viewId);
            if (targetView) targetView.classList.add('active');
            
            // 路由特殊处理
            if (viewId === 'terminal-view') {
                if (terminal.tabs.length > 0) {
                    // 有连接：switchTab 会重新给终端容器加 active 并触发 fit+refresh
                    const targetId = terminal.activeTabId || terminal.tabs[terminal.tabs.length - 1].id;
                    terminal.switchTab(targetId);
                } else {
                    // 无连接：恢复"最近连接"欢迎页（它的 active 也被 querySelectorAll 清掉了）
                    const welcomePage = document.getElementById('quick-connect-page');
                    if (welcomePage) welcomePage.classList.add('active');
                }
            } else if (viewId === 'roles-view') {
                settings.loadRoles();
            } else if (viewId === 'model-settings-view') {
                settings.loadAIEndpoints();
            }
        };
    });
}

// (Resizer, StatsTabs, BottomPanel 逻辑保持不变...)
function initLayoutResizer() {
    const sidebar = document.getElementById('sidebar');
    const sidebarResizer = document.getElementById('sidebar-resizer');
    const aiSection = document.getElementById('ai-section');
    const aiResizer = document.getElementById('ai-resizer');
    const bottomPanel = document.getElementById('bottom-panel');
    const bottomResizer = document.getElementById('bottom-panel-resizer');
    let currentResizer = null;
    sidebarResizer.onmousedown = (e) => { currentResizer = 'sidebar'; document.body.style.cursor = 'col-resize'; e.preventDefault(); };
    aiResizer.onmousedown = (e) => { currentResizer = 'ai'; document.body.style.cursor = 'col-resize'; e.preventDefault(); };
    bottomResizer.onmousedown = (e) => { currentResizer = 'bottom'; document.body.style.cursor = 'row-resize'; e.preventDefault(); };
    window.onmousemove = (e) => {
        if (!currentResizer) return;
        if (currentResizer === 'sidebar') {
            const newWidth = e.clientX;
            if (newWidth > 60 && newWidth < 500) sidebar.style.width = `${newWidth}px`;
        } else if (currentResizer === 'ai') {
            const newWidth = window.innerWidth - e.clientX;
            if (newWidth > 0 && newWidth < 600) aiSection.style.width = `${newWidth}px`;
        } else if (currentResizer === 'bottom') {
            const newHeight = window.innerHeight - e.clientY;
            if (newHeight > 30 && newHeight < window.innerHeight * 0.8) bottomPanel.style.height = `${newHeight}px`;
        }
        // 等浏览器完成 CSS 布局重排后再 fit，避免读到旧尺寸
        requestAnimationFrame(() => terminal.fitActiveTerminal());
    };
    window.onmouseup = () => {
        if (currentResizer) {
            currentResizer = null;
            document.body.style.cursor = 'default';
            // 鼠标释放后再 fit 一次，确保最终尺寸正确
            requestAnimationFrame(() => terminal.fitActiveTerminal());
        }
    };
}

function initStatsTabs() {
    const statsTabs = document.querySelectorAll('.stats-tab');
    statsTabs.forEach(tab => {
        tab.onclick = () => {
            const target = tab.getAttribute('data-tab');
            statsTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.stats-tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(`${target}-view`).classList.add('active');
        };
    });

    // 右侧 AI 面板折叠/展开（使用外部浮动按钮，始终可见）
    const toggleStrip = document.getElementById('ai-toggle-strip');
    const aiSection = document.getElementById('ai-section');
    const aiResizer = document.getElementById('ai-resizer');
    if (toggleStrip && aiSection) {
        toggleStrip.onclick = () => {
            const isCollapsed = aiSection.classList.toggle('collapsed');
            const icon = toggleStrip.querySelector('i');
            if (icon) icon.className = isCollapsed ? 'fas fa-chevron-left' : 'fas fa-chevron-right';
            if (aiResizer) aiResizer.style.display = isCollapsed ? 'none' : '';
            const onEnd = () => {
                terminal.fitActiveTerminal();
                aiSection.removeEventListener('transitionend', onEnd);
            };
            aiSection.addEventListener('transitionend', onEnd);
            setTimeout(() => { terminal.fitActiveTerminal(); aiSection.removeEventListener('transitionend', onEnd); }, 350);
        };
    }
}

function initBottomPanel() {
    const panelTabs = document.querySelectorAll('.panel-tab');
    panelTabs.forEach(tab => {
        tab.onclick = () => {
            const target = tab.getAttribute('data-panel');
            panelTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.bottom-panel-content').forEach(c => c.classList.remove('active'));
            document.getElementById(target).classList.add('active');
        };
    });
    document.getElementById('toggle-bottom-panel').onclick = () => {
        const panel = document.getElementById('bottom-panel');
        const icon = document.querySelector('#toggle-bottom-panel i');
        panel.classList.toggle('collapsed');
        icon.className = panel.classList.contains('collapsed') ? 'fas fa-chevron-up' : 'fas fa-chevron-down';
        // 过渡动画结束后精确 fit，兜底 350ms 保证无 transition 时也能触发
        const onEnd = () => {
            terminal.fitActiveTerminal();
            panel.removeEventListener('transitionend', onEnd);
        };
        panel.addEventListener('transitionend', onEnd);
        setTimeout(() => { terminal.fitActiveTerminal(); panel.removeEventListener('transitionend', onEnd); }, 350);
    };
}

// --- 服务器列表与多级分组逻辑 ---

async function loadServers() {
    try {
        const servers = await api.getServers();
        const container = document.getElementById('server-list-container');
        if (!container) return;
        
        container.innerHTML = '';
        const groups = {};
        servers.forEach(s => {
            if (!groups[s.group_name]) groups[s.group_name] = [];
            groups[s.group_name].push(s);
        });

        // 读取折叠记忆
        const collapsedGroups = storage.get('server_groups_collapsed', []);

        Object.keys(groups).forEach(groupName => {
            const isCollapsed = collapsedGroups.includes(groupName);
            const groupEl = document.createElement('div');
            groupEl.className = `server-group ${isCollapsed ? 'collapsed' : ''}`;
            groupEl.innerHTML = `
                <div class="group-header">
                    <i class="fas fa-chevron-down"></i>
                    <span>${groupName} (${groups[groupName].length})</span>
                </div>
                <div class="group-content"></div>
            `;
            
            // 绑定折叠点击并记忆状态
            groupEl.querySelector('.group-header').onclick = () => {
                groupEl.classList.toggle('collapsed');
                let current = storage.get('server_groups_collapsed', []);
                if (groupEl.classList.contains('collapsed')) {
                    if (!current.includes(groupName)) current.push(groupName);
                } else {
                    current = current.filter(g => g !== groupName);
                }
                storage.set('server_groups_collapsed', current);
            };

            const content = groupEl.querySelector('.group-content');
            groups[groupName].forEach(server => {
                const card = document.createElement('div');
                card.className = 'server-card';
                card.innerHTML = `
                    <div class="server-card-icon"><i class="fas fa-server"></i></div>
                    <div class="server-card-info">
                        <h4>${server.name}</h4>
                        <p>${server.host}:${server.port}</p>
                    </div>
                    <div class="server-card-actions">
                        <button class="btn-icon" title="编辑" onclick="event.stopPropagation(); showEditServerModal(${server.id})"><i class="fas fa-edit"></i></button>
                        <button class="btn-icon" title="删除" onclick="event.stopPropagation(); deleteServer(${server.id}, '${server.name.replace(/'/g, "\\'")}')"><i class="fas fa-trash-alt"></i></button>
                    </div>
                `;
                card.onclick = () => connectToServer(server);
                content.appendChild(card);
            });
            container.appendChild(groupEl);
        });
    } catch (err) { notify("加载服务器失败", 'error'); }
}

function connectToServer(server) {
    console.log("🚀 准备连接服务器:", server);
    document.querySelector('.nav-item[data-view="terminal-view"]').click();
    terminal.createTab(server);
}

window.toggleSidebar = () => {
    const sidebar = document.getElementById('sidebar');
    sidebar.classList.toggle('collapsed');
    setTimeout(() => terminal.fitActiveTerminal(), 300);
};

// 暴露全局加载函数，方便 settings.js 调用
window.loadServers = loadServers;
window.loadConnectionHistory = loadConnectionHistory;
window.connectToServer = connectToServer;
window.deleteServer = async (id, name) => {
    if (!confirm(`确定要删除服务器「${name}」吗？`)) return;
    try {
        await api.deleteServer(id);
        notify(`已删除服务器「${name}」`, 'success');
        loadServers();
    } catch (err) { notify('删除失败: ' + err.message, 'error'); }
};

// 监听服务器变更事件
window.addEventListener('serversChanged', () => {
    loadServers();
});

// --- 历史连接 ---
function loadConnectionHistory() {
    const history = storage.get('connection_history', []);
    const container = document.getElementById('connection-history-list');
    if (!container) return;
    if (history.length === 0) { container.innerHTML = '<div class="empty-tip">暂无最近连接记录</div>'; return; }
    
    container.innerHTML = '';
    history.forEach(item => {
        const card = document.createElement('div');
        card.className = 'history-card';
        card.innerHTML = `
            <div class="history-info"><strong>${item.name}</strong><span>${item.host}</span></div>
            <div class="history-time">${new Date(item.time).toLocaleString()}</div>
        `;
        // 修复：直接绑定 item 对象，避免根据 host 查找导致的闭包/匹配错误
        card.onclick = () => connectToServer(item);
        container.appendChild(card);
    });
}

// --- 系统设置辅助 ---
async function loadSystemSettings() {
    const settingsData = await api.getSettings();
    const form = document.getElementById('system-settings-form');
    if (form) {
        Object.keys(settingsData).forEach(key => {
            if (form[key]) form[key].value = settingsData[key];
        });
    }
}

// 暴露全局
window.showQuickConnect = () => {
    document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('#terminal-stack > .view').forEach(el => el.classList.remove('active'));
    document.getElementById('quick-connect-page').classList.add('active');
};

document.addEventListener('DOMContentLoaded', init);
