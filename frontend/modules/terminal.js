/**
 * SSH 终端与多标签管理模块
 */
import { api } from './api.js';
import { storage, notify } from './utils.js';
import { store } from './store.js';

// 获取状态（快捷访问）
const getTabs = () => (store ? store.getState('tabs') : []);
const getActiveTabId = () => (store ? store.getState('activeTabId') : null);

// 初始化标签栏与容器
const tabBar = document.getElementById('tab-bar');
const terminalStack = document.getElementById('terminal-stack');

export function initTerminalModule() {
    // 关闭标签页的函数需要通过 innerHTML onclick 调用，必须挂到 window
    window.closeTab = closeTab;
}

export function createTab(serverConfig) {
    const tabId = 'tab-' + Date.now();
    const tab = {
        id: tabId,
        config: serverConfig,
        terminal: null,
        socket: null,
        fitAddon: null,
        chatHistory: [],
        roleId: null,          // 该会话选用的角色 ID，null 表示使用系统激活角色
        sftpCurrentPath: '',   // 空字符串 → 后端自动解析为用户家目录
        isCapturing: false,    // AI 指令执行后捕获终端输出的标志
        captureBuffer: '',     // 捕获的原始终端输出
        captureTimer: null,    // 空闲检测定时器
        stats: {
            hostname: '-', os: '-', uptime: '-', ip: '-', load: '-',
            cpu: 0, mem: { used: 0, total: 0 }, disk: { used: 0, total: 0 },
            processes: []
        }
    };

    const tabs = [...getTabs(), tab];
    store.setState('tabs', tabs);
    renderTabUI(tab);
    switchTab(tabId);
    connectTerminal(tab);
    
    // 保存到连接历史
    saveToHistory(serverConfig);
    return tab;
}

function renderTabUI(tab) {
    // 创建顶部标签按钮
    const tabEl = document.createElement('div');
    tabEl.className = 'tab';
    tabEl.id = `btn-${tab.id}`;
    tabEl.onclick = () => switchTab(tab.id);
    tabEl.innerHTML = `
        <i class="fas fa-${tab.config.device_type === 'linux' ? 'server' : 'network-wired'}"></i>
        <span>${tab.config.name}</span>
        <div class="tab-close" onclick="event.stopPropagation(); closeTab('${tab.id}')">&times;</div>
    `;
    tabBar.insertBefore(tabEl, tabBar.querySelector('.add-tab-btn'));

    // 创建终端容器
    const container = document.createElement('div');
    container.id = `container-${tab.id}`;
    container.className = 'view';
    container.innerHTML = `<div id="xterm-${tab.id}" style="width:100%; height:100%;"></div>`;
    terminalStack.appendChild(container);
}

export function switchTab(tabId) {
    const tabs = getTabs();
    const tab = tabs.find(t => t.id === tabId);
    if (!tab) return;

    store.setState('activeTabId', tabId);

    // UI 切换
    document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
    document.getElementById(`btn-${tabId}`).classList.add('active');

    document.querySelectorAll('#terminal-stack > .view').forEach(el => el.classList.remove('active'));
    document.getElementById(`container-${tabId}`).classList.add('active');

    // 刷新终端尺寸并强制重绘（解决标签切换/从隐藏恢复时的黑屏）
    if (tab.fitAddon && tab.terminal) {
        // 双 rAF：等待浏览器完成 CSS 布局（display:none → flex）后再操作
        requestAnimationFrame(() => requestAnimationFrame(() => {
            try {
                tab.fitAddon.fit();
                // refresh 强制重绘所有行
                if (typeof tab.terminal.refresh === 'function') {
                    tab.terminal.refresh(0, tab.terminal.rows - 1);
                }
            } catch (e) { /* xterm 版本兼容保护 */ }
        }));
        // 额外兜底：100ms 后再试一次（处理部分浏览器的布局延迟）
        setTimeout(() => {
            try { tab.fitAddon.fit(); } catch (e) {}
        }, 100);
    }

    // 触发全局事件，让其他模块（AI、SFTP）同步
    window.dispatchEvent(new CustomEvent('tabSwitched', { detail: { tab } }));
}

export function closeTab(tabId) {
    const tabs = [...getTabs()];
    const index = tabs.findIndex(t => t.id === tabId);
    if (index === -1) return;

    const tab = tabs[index];
    if (tab.socket) tab.socket.close();
    if (tab.terminal) tab.terminal.dispose();

    document.getElementById(`btn-${tabId}`).remove();
    document.getElementById(`container-${tabId}`).remove();

    tabs.splice(index, 1);
    store.setState('tabs', tabs);

    if (getActiveTabId() === tabId) {
        if (tabs.length > 0) {
            switchTab(tabs[tabs.length - 1].id);
        } else {
            store.setState('activeTabId', null);
            document.getElementById('quick-connect-page').classList.add('active');
            // 通知其他模块：所有连接已关闭，清空相关面板
            window.dispatchEvent(new CustomEvent('allTabsClosed'));
        }
    }
}

function connectTerminal(tab) {
    const term = new Terminal({
        cursorBlink: true,
        fontFamily: '"Fira Code", monospace',
        fontSize: 14,
        theme: { background: '#000' }
    });
    
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById(`xterm-${tab.id}`));
    fitAddon.fit();

    tab.terminal = term;
    tab.fitAddon = fitAddon;

    // 稍微延迟连接，确保 DOM 完全渲染
    setTimeout(() => {
        if (!tab.config || !tab.config.id) {
            console.error("❌ 无法建立连接: 服务器配置中缺少 ID", tab.config);
            term.write('\r\n\x1b[31m连接失败: 未找到服务器 ID，请从服务器列表重新连接\x1b[0m\r\n');
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const token = localStorage.getItem('xterm_token');
        // 后端路由是 /ws/ssh/{server_id}，使用 server_id 作为路径参数，并带上鉴权 token
        const wsUrl = `${protocol}//${window.location.host}/ws/ssh/${tab.config.id}${token ? '?token=' + token : ''}`;

        console.log(`🔌 正在发起 SSH WebSocket 连接: ${tab.config.host} (${wsUrl})`);
        const socket = new WebSocket(wsUrl);
        tab.socket = socket;

        socket.onopen = () => {
            term.focus();
            socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
        };

        socket.onmessage = (event) => {
            term.write(event.data);
            // AI 指令执行后，捕获终端输出并在空闲 1.5 秒后通知 AI 分析
            if (tab.isCapturing) {
                tab.captureBuffer += event.data;
                if (tab.captureTimer) clearTimeout(tab.captureTimer);
                tab.captureTimer = setTimeout(() => {
                    if (!tab.isCapturing) return;
                    tab.isCapturing = false;
                    tab.captureTimer = null;
                    const output = tab.captureBuffer.trim();
                    tab.captureBuffer = '';
                    if (output) {
                        window.dispatchEvent(new CustomEvent('captureReady', {
                            detail: { tabId: tab.id, output }
                        }));
                    }
                }, 1500);
            }
        };

        socket.onclose = () => {
            term.write('\r\n\x1b[31m连接已断开\x1b[0m\r\n');
        };

        term.onData(data => {
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ type: 'data', data }));
            }
        });
    }, 100);
}

// 自适应终端尺寸（拖拽调整时使用）
export function fitActiveTerminal() {
    const activeId = getActiveTabId();
    if (!activeId) return;
    const tab = getTabs().find(t => t.id === activeId);
    if (tab && tab.fitAddon) {
        tab.fitAddon.fit();
        if (tab.socket && tab.socket.readyState === WebSocket.OPEN) {
            tab.socket.send(JSON.stringify({ 
                type: 'resize', 
                cols: tab.terminal.cols, 
                rows: tab.terminal.rows 
            }));
        }
    }
}

// 从隐藏状态恢复时使用：fit + 强制重绘（解决黑屏问题）
export function refreshActiveTerminal() {
    const activeId = getActiveTabId();
    if (!activeId) return;
    const tab = getTabs().find(t => t.id === activeId);
    if (!tab || !tab.fitAddon || !tab.terminal) return;

    tab.fitAddon.fit();
    // 强制 xterm.js 重绘所有行，解决从 display:none 恢复后的黑屏
    tab.terminal.refresh(0, tab.terminal.rows - 1);

    if (tab.socket && tab.socket.readyState === WebSocket.OPEN) {
        tab.socket.send(JSON.stringify({ 
            type: 'resize', 
            cols: tab.terminal.cols, 
            rows: tab.terminal.rows 
        }));
    }
}

// 辅助：连接历史
function saveToHistory(server) {
    let history = storage.get('connection_history', []);
    history = history.filter(item => item.host !== server.host);
    history.unshift({ ...server, time: Date.now() });
    storage.set('connection_history', history.slice(0, 20));
}
