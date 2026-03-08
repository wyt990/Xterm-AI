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
import { store } from './modules/store.js';
import './modules/components.js';

// 全局变量维护
window.getTab = (id) => (store ? store.getState('tabs') : []).find(t => t.id === id);
window.showModal = showModal;
window.closeModal = closeModal;

// 1. 初始化
async function init() {
    console.log("🚀 XTerm-AI 初始化中...");
    
    // 1. 先加载所有 HTML 组件 (弹窗等)
    await loadComponents();
    
    // --- 鉴权拦截逻辑 ---
    const token = localStorage.getItem('xterm_token');
    if (!token) {
        showModal('login-modal');
        // 未登录时不再继续执行后续的数据加载
        return;
    }

    // 绑定登录表单 (这里保留，以防从 authError 触发)
    setupLoginForm();

    // 监听全局鉴权错误
    window.addEventListener('authError', () => {
        localStorage.removeItem('xterm_token');
        showModal('login-modal');
    });
    // ------------------

    // 2. 只有有 Token 时才初始化模块并加载数据
    await startApp();
}

function setupLoginForm() {
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.onsubmit = async (e) => {
            e.preventDefault();
            const pwd = document.getElementById('login-password').value;
            const errEl = document.getElementById('login-error');
            try {
                const res = await api.login(pwd);
                localStorage.setItem('xterm_token', res.access_token);
                closeModal('login-modal');
                notify('登录成功', 'success');
                // 登录成功后启动应用
                await startApp();
            } catch (err) {
                if (errEl) errEl.style.display = 'block';
            }
        };
    }
}

async function startApp() {
    // 防止重复初始化
    if (window.isAppStarted) return;
    window.isAppStarted = true;

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
    loadDeviceTypes(); // 加载设备类型

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
            const activeId = store.getState('activeTabId');
            const activeTab = window.getTab(activeId);
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
                const tabs = store ? store.getState('tabs') : [];
                if (tabs && tabs.length > 0) {
                    // 有连接：switchTab 会重新给终端容器加 active 并触发 fit+refresh
                    const targetId = (store ? store.getState('activeTabId') : null) || tabs[tabs.length - 1].id;
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

// 根据设备类型返回 FontAwesome 图标类名
function getServerIcon(deviceType) {
    switch ((deviceType || '').toLowerCase()) {
        case 'windows': return 'fab fa-windows';
        case 'network': return 'fas fa-network-wired';
        default:        return 'fas fa-server';
    }
}
window.getServerIcon = getServerIcon;

async function loadServers() {
    try {
        const servers = await api.getServers();
        const container = document.getElementById('server-list-container');
        if (!container) return;
        container.innerHTML = '';

        // ── 1. 构建树结构 ──────────────────────────────────────────
        // 每个节点形如 { _servers: [], _children: { childName: node } }
        const tree = {};
        servers.forEach(s => {
            const parts = (s.group_name || 'default').split('/');
            let node = tree;
            parts.forEach((part, i) => {
                if (!node[part]) node[part] = { _servers: [], _children: {} };
                if (i === parts.length - 1) {
                    node[part]._servers.push(s);
                } else {
                    node = node[part]._children;
                }
            });
        });

        // ── 2. 读取折叠记忆（key: 路径字符串 → true=折叠） ─────────
        const collapsed = storage.get('server_tree_collapsed', {});

        // ── 3. 统计节点下的服务器总数（含子层） ───────────────────
        function countTotal(node) {
            let n = node._servers.length;
            Object.values(node._children).forEach(c => { n += countTotal(c); });
            return n;
        }

        // ── 4. 递归构建 DOM 节点 ──────────────────────────────────
        function renderNode(name, node, parentPath, level) {
            const path = parentPath ? `${parentPath}/${name}` : name;
            const isCollapsed = !!collapsed[path];
            const total = countTotal(node);
            const hasChildren = Object.keys(node._children).length > 0 || node._servers.length > 0;

            const nodeEl = document.createElement('div');
            nodeEl.className = `tree-node tree-level-${level}${isCollapsed ? ' collapsed' : ''}`;

            // 节点标题行
            const header = document.createElement('div');
            header.className = 'tree-node-header';
            header.innerHTML = `
                <i class="fas fa-chevron-down tree-chevron"></i>
                <i class="fas ${isCollapsed ? 'fa-folder' : 'fa-folder-open'} tree-folder-icon"></i>
                <span class="tree-label" title="${name}">${name}</span>
                <span class="tree-count">${total}</span>
            `;
            header.onclick = () => {
                nodeEl.classList.toggle('collapsed');
                const nowCollapsed = nodeEl.classList.contains('collapsed');
                header.querySelector('.tree-folder-icon').className =
                    `fas ${nowCollapsed ? 'fa-folder' : 'fa-folder-open'} tree-folder-icon`;
                const col = storage.get('server_tree_collapsed', {});
                if (nowCollapsed) { col[path] = true; } else { delete col[path]; }
                storage.set('server_tree_collapsed', col);
            };
            nodeEl.appendChild(header);

            // 子内容区（子分组 + 本层服务器卡片）
            const children = document.createElement('div');
            children.className = 'tree-node-children';

            // 先渲染子分组（字母排序）
            Object.keys(node._children).sort().forEach(childName => {
                children.appendChild(renderNode(childName, node._children[childName], path, level + 1));
            });

            // 再渲染本层直属服务器
            if (node._servers.length > 0) {
                const grid = document.createElement('div');
                grid.className = 'server-grid';
                node._servers.forEach(server => {
                    const card = document.createElement('server-card');
                    card.server = server;
                    grid.appendChild(card);
                });
                children.appendChild(grid);
            }

            nodeEl.appendChild(children);
            return nodeEl;
        }

        // ── 5. 渲染顶层节点（字母排序） ───────────────────────────
        Object.keys(tree).sort().forEach(name => {
            container.appendChild(renderNode(name, tree[name], '', 1));
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

// 动态加载设备类型到下拉框
async function loadDeviceTypes() {
    try {
        const types = await api.getDeviceTypes();
        window.allDeviceTypes = types; // 存入全局供 ai_chat 和各处使用
        
        // 1. 填充服务器编辑弹窗的下拉框
        const selects = document.querySelectorAll('select[name="device_type_id"]');
        selects.forEach(sel => {
            const currentVal = sel.value;
            sel.innerHTML = types.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
            if (currentVal) sel.value = currentVal;
        });

        // 2. 填充系统类型管理中的角色下拉框 (如果是打开状态)
        const dtRoleSelect = document.getElementById('dt-role-select');
        if (dtRoleSelect) {
            const roles = await api.getRoles();
            dtRoleSelect.innerHTML = '<option value="">不绑定（使用系统激活角色）</option>' + 
                roles.map(r => `<option value="${r.id}">${r.name}</option>`).join('');
        }
    } catch (err) { console.error("加载设备类型失败", err); }
}
window.loadDeviceTypes = loadDeviceTypes;

// --- 系统类型管理逻辑 ---
window.showDeviceTypeMgr = async () => {
    showModal('device-type-mgr-modal');
    loadDeviceTypeList();
};

async function loadDeviceTypeList() {
    const tbody = document.getElementById('device-type-list-body');
    if (!tbody) return;
    try {
        const types = await api.getDeviceTypes();
        window.allDeviceTypes = types;
        tbody.innerHTML = types.map(t => `
            <tr>
                <td>${t.name}</td>
                <td><code>${t.value}</code></td>
                <td><i class="${t.icon || 'fas fa-microchip'}"></i></td>
                <td>${t.role_name || '<span style="color:#666">未绑定</span>'}</td>
                <td>
                    <button class="btn-icon" onclick="showEditDeviceType(${t.id})" title="编辑"><i class="fas fa-edit"></i></button>
                    <button class="btn-icon" onclick="deleteDeviceType(${t.id}, '${t.name}')" title="删除"><i class="fas fa-trash-alt"></i></button>
                </td>
            </tr>
        `).join('');
    } catch (err) { notify('加载列表失败', 'error'); }
}

window.showAddDeviceType = async () => {
    document.getElementById('device-type-modal-title').innerText = '新增系统类型';
    const form = document.getElementById('device-type-form');
    form.reset();
    document.getElementById('dt-id').value = '';
    await loadDeviceTypes(); // 确保角色下拉框有数据
    showModal('device-type-edit-modal');
};

window.showEditDeviceType = async (id) => {
    const type = window.allDeviceTypes.find(t => t.id === id);
    if (!type) return;
    document.getElementById('device-type-modal-title').innerText = '编辑系统类型';
    const form = document.getElementById('device-type-form');
    form.id.value = type.id;
    form.name.value = type.name;
    form.value.value = type.value;
    form.icon.value = type.icon || '';
    await loadDeviceTypes(); // 确保角色下拉框有数据
    form.role_id.value = type.role_id || '';
    showModal('device-type-edit-modal');
};

window.deleteDeviceType = async (id, name) => {
    if (!confirm(`确定要删除系统类型「${name}」吗？\n关联该类型的服务器将被设为“未知”。`)) return;
    try {
        await api.deleteDeviceType(id);
        notify('删除成功', 'success');
        loadDeviceTypeList();
        loadDeviceTypes();
        loadServers(); // 刷新服务器列表显示
    } catch (err) { notify('删除失败', 'error'); }
};

// 绑定系统类型表单提交
document.addEventListener('DOMContentLoaded', () => {
    const dtForm = document.getElementById('device-type-form');
    if (dtForm) {
        dtForm.onsubmit = async (e) => {
            e.preventDefault();
            const formData = new FormData(dtForm);
            const data = Object.fromEntries(formData.entries());
            const id = data.id;
            delete data.id;
            if (data.role_id === "") data.role_id = null;

            try {
                if (id) {
                    await api.updateDeviceType(id, data);
                    notify('更新成功', 'success');
                } else {
                    await api.addDeviceType(data);
                    notify('添加成功', 'success');
                }
                closeModal('device-type-edit-modal');
                loadDeviceTypeList();
                loadDeviceTypes();
            } catch (err) { notify('保存失败: ' + err.message, 'error'); }
        };
    }
});

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
function getDeviceIcon(deviceType) {
    switch ((deviceType || '').toLowerCase()) {
        case 'windows': return 'fa-brands fa-windows';
        case 'network': return 'fas fa-network-wired';
        default:        return 'fas fa-server';
    }
}

function getDeviceIconClass(deviceType) {
    switch ((deviceType || '').toLowerCase()) {
        case 'windows': return 'windows';
        case 'network': return 'network';
        case 'linux':   return 'linux';
        default:        return 'linux';
    }
}

function formatRelativeTime(ts) {
    const diff = Date.now() - ts;
    const min = Math.floor(diff / 60000);
    if (min < 1)  return '刚刚';
    if (min < 60) return `${min} 分钟前`;
    const h = Math.floor(min / 60);
    if (h < 24)   return `${h} 小时前`;
    const d = Math.floor(h / 24);
    if (d < 30)   return `${d} 天前`;
    return new Date(ts).toLocaleDateString();
}

function loadConnectionHistory() {
    const history = storage.get('connection_history', []);
    const container = document.getElementById('connection-history-list');
    if (!container) return;
    if (history.length === 0) {
        container.innerHTML = '<div class="empty-tip"><i class="fas fa-plug" style="display:block;font-size:2rem;margin-bottom:12px;color:#333;"></i>暂无最近连接记录</div>';
        return;
    }
    
    container.innerHTML = '';
    history.forEach(item => {
        const card = document.createElement('div');
        card.className = 'history-card';
        const iconCls  = getDeviceIconClass(item.device_type);
        const iconName = getDeviceIcon(item.device_type);
        const groupHtml = item.group_name && item.group_name !== 'default'
            ? `<div class="history-card-group"><i class="fas fa-folder"></i>${item.group_name.replace(/\//g, ' › ')}</div>`
            : '';
        card.innerHTML = `
            <div class="history-card-header">
                <div class="history-card-icon ${iconCls}"><i class="${iconName}"></i></div>
                <div class="history-card-name" title="${item.name}">${item.name}</div>
            </div>
            <div class="history-card-host">${item.host}${item.port && item.port !== 22 ? ':' + item.port : ''}</div>
            ${groupHtml}
            <div class="history-card-footer">
                <span class="history-time" title="${new Date(item.time).toLocaleString()}">${formatRelativeTime(item.time)}</span>
            </div>
        `;
        card.onclick = () => connectToServer(item);
        container.appendChild(card);
    });
}

window.clearConnectionHistory = function() {
    if (!confirm('确定要清空所有最近连接记录吗？')) return;
    storage.set('connection_history', []);
    loadConnectionHistory();
};

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
