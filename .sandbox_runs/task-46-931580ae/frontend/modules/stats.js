/**
 * 实时系统指标监控模块 (增强版)
 * 包含：实时指标、历史趋势图、进程管理、性能优化
 */
import { store } from './store.js';
import { api } from './api.js';
import { notify } from './utils.js';

let statsSocket = null;
let currentTabId = null;
let cpuChart = null;
let memChart = null;
let historyTimer = null;

export function initStatsModule() {
    globalThis.addEventListener('statsCleared', (e) => {
        const tab = currentTabId ? globalThis.getTab(currentTabId) : null;
        if (!tab?.config?.id) return;
        if (e.detail.clearAll || tab.config.id === e.detail.serverId) loadStatsHistory(tab.config.id);
    });

    globalThis.addEventListener('tabSwitched', (e) => {
        const tab = e.detail.tab;
        if (tab.id !== currentTabId) {
            startStatsMonitoring(tab);
            loadStatsHistory(tab.config.id);
        }
    });

    globalThis.addEventListener('allTabsClosed', () => {
        stopStatsMonitoring();
        clearStatsUI();
    });

    // 性能优化：当页面不可见时，暂停监控以节省资源
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
            console.log("页面不可见，暂停指标采集");
            stopStatsMonitoring();
        } else if (currentTabId) {
            const tab = globalThis.getTab(currentTabId);
            if (tab) {
                console.log("页面恢复可见，重启指标采集");
                startStatsMonitoring(tab);
            }
        }
    });

    // 初始化图表对象
    initCharts();
}

function stopStatsMonitoring() {
    if (statsSocket) {
        statsSocket.close();
        statsSocket = null;
    }
    if (historyTimer) {
        clearInterval(historyTimer);
        historyTimer = null;
    }
}

function startStatsMonitoring(tab) {
    stopStatsMonitoring();
    
    if (!tab?.config?.id) return;
    currentTabId = tab.id;
    
    // 1. 启动 WebSocket 实时采集
    setTimeout(() => {
        if (currentTabId !== tab.id || document.visibilityState === 'hidden') return;

        const protocol = globalThis.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const token = localStorage.getItem('xterm_token');
        const wsUrl = `${protocol}//${globalThis.location.host}/ws/stats/${tab.config.id}${token ? '?token=' + token : ''}`;

        statsSocket = new WebSocket(wsUrl);
        statsSocket.onmessage = (event) => {
            try {
                const stats = JSON.parse(event.data);
                tab.stats = stats;
                if (store.activeTabId === tab.id) {
                    updateStatsUI(stats, tab.config.id);
                }
            } catch (e) {
                console.debug('解析实时状态数据失败:', e);
            }
        };
        statsSocket.onclose = () => console.log("指标监控连接已断开");
    }, 500);

    // 2. 启动历史数据定期拉取 (每分钟一次，用于更新趋势图)
    historyTimer = setInterval(() => {
        if (document.visibilityState === 'visible') {
            loadStatsHistory(tab.config.id);
        }
    }, 60000);
}

async function loadStatsHistory(serverId) {
    try {
        const history = await api.getStatsHistory(serverId, 30);
        updateCharts(history);
    } catch (err) {
        console.error("加载历史指标失败", err);
    }
}

function initCharts() {
    const chartOptions = (label, color) => ({
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: label,
                data: [],
                borderColor: color,
                backgroundColor: color + '22',
                borderWidth: 2,
                pointRadius: 0,
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
            scales: {
                x: { display: true, grid: { display: false }, ticks: { color: '#666', fontSize: 10, maxRotation: 0 } },
                y: { display: true, min: 0, max: 100, grid: { color: '#333' }, ticks: { color: '#666', fontSize: 10, callback: v => v + '%' } }
            },
            animation: { duration: 0 }
        }
    });

    const cpuCtx = document.getElementById('cpu-trend-chart')?.getContext('2d');
    if (cpuCtx) cpuChart = new Chart(cpuCtx, chartOptions('CPU 使用率', '#0078d4'));

    const memCtx = document.getElementById('mem-trend-chart')?.getContext('2d');
    if (memCtx) memChart = new Chart(memCtx, chartOptions('内存使用率', '#107c10'));
}

function updateCharts(history) {
    if (!cpuChart || !memChart) return;

    const labels = history.map(h => h.time_label);
    const cpuData = history.map(h => h.cpu);
    const memData = history.map(h => h.mem);

    cpuChart.data.labels = labels;
    cpuChart.data.datasets[0].data = cpuData;
    cpuChart.update();

    memChart.data.labels = labels;
    memChart.data.datasets[0].data = memData;
    memChart.update();
}

function updateStatsUI(stats, serverId) {
    const safeSet = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.innerText = val;
    };

    safeSet('info-hostname', stats.hostname || '-');
    safeSet('info-os', stats.os || '-');
    safeSet('info-uptime', stats.uptime || '-');
    safeSet('info-ip', stats.ip || '-');
    safeSet('info-load', stats.load || '-');

    const cpuBar = document.getElementById('info-cpu-bar');
    if (cpuBar) cpuBar.style.width = `${stats.cpu || 0}%`;
    safeSet('info-cpu-val', `${stats.cpu || 0}%`);

    const memBar = document.getElementById('info-mem-bar');
    if (memBar) memBar.style.width = `${stats.mem_p || 0}%`;
    safeSet('info-mem-val', stats.mem || '-');

    const diskBar = document.getElementById('info-disk-bar');
    if (diskBar) diskBar.style.width = `${stats.disk_p || 0}%`;
    safeSet('info-disk-val', stats.disk || '-');

    // 进程列表：增加一键杀死功能
    const procList = document.getElementById('proc-list');
    if (procList && stats.procs) {
        procList.innerHTML = stats.procs.map(p => {
            const pid = p.pid || '';
            const cmd = p.cmd || '';
            const escapedCmd = cmd.replaceAll("'", String.raw`\'`);
            return `
                <tr>
                    <td>${p.mem}</td>
                    <td>${p.cpu}</td>
                    <td title="${cmd}">${cmd}</td>
                    <td>
                        <button class="btn-kill" onclick="killProcess(${serverId}, ${pid}, '${escapedCmd}')" title="结束进程">
                            <i class="fas fa-times"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
    }
}

// 暴露给全局调用 (用于 onclick)
globalThis.killProcess = async (serverId, pid, cmdInfo) => {
    if (!pid) return notify("无法获取进程 PID", "error");

    if (!confirm(`确定要结束进程 PID: ${pid} (${cmdInfo.substring(0, 20)}...) 吗？`)) return;

    try {
        await api.killProcess(serverId, pid);
        notify(`已对进程 ${pid} 发送结束信号`, "success");
    } catch (err) {
        notify("结束进程失败: " + err.message, "error");
    }
};

function clearStatsUI() {
    const safeSet = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.innerText = val;
    };
    const fields = ['hostname', 'os', 'uptime', 'ip', 'load', 'cpu-val', 'mem-val', 'disk-val'];
    fields.forEach(f => safeSet('info-' + f, '-'));
    
    ['cpu', 'mem', 'disk'].forEach(f => {
        const el = document.getElementById(`info-${f}-bar`);
        if (el) el.style.width = '0%';
    });

    const procList = document.getElementById('proc-list');
    if (procList) procList.innerHTML = '';

    if (cpuChart) { cpuChart.data.labels = []; cpuChart.data.datasets[0].data = []; cpuChart.update(); }
    if (memChart) { memChart.data.labels = []; memChart.data.datasets[0].data = []; memChart.update(); }
}
