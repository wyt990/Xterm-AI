/**
 * 实时系统指标监控模块 (修复版)
 */
import { activeTabId } from './terminal.js';

let statsSocket = null;
let currentServerId = null;

export function initStatsModule() {
    window.addEventListener('tabSwitched', (e) => {
        const tab = e.detail.tab;
        if (tab.config.id !== currentServerId) {
            startStatsMonitoring(tab);
        }
    });

    window.addEventListener('allTabsClosed', () => {
        // 关闭 stats WebSocket
        if (statsSocket) {
            statsSocket.close();
            statsSocket = null;
        }
        currentServerId = null;
        clearStatsUI();
    });
}

function startStatsMonitoring(tab) {
    if (statsSocket) statsSocket.close();
    
    if (!tab.config || !tab.config.id) return;
    currentServerId = tab.config.id;
    
    // 延迟发起，给 SSH 连接留出带宽
    setTimeout(() => {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // 后端路由是 /ws/stats/{server_id}，使用 server_id 作为路径参数
        const wsUrl = `${protocol}//${window.location.host}/ws/stats/${tab.config.id}`;

        statsSocket = new WebSocket(wsUrl);

        statsSocket.onmessage = (event) => {
            try {
                const stats = JSON.parse(event.data);
                tab.stats = stats;
                if (activeTabId === tab.id) {
                    updateStatsUI(stats);
                }
            } catch (e) {}
        };

        statsSocket.onclose = () => {
            console.log("指标监控连接已断开");
        };
    }, 500); 
}

function updateStatsUI(stats) {
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

    // 后端返回 mem 为字符串（如 "512M / 2048M"）和 mem_p 为百分比数字
    const memBar = document.getElementById('info-mem-bar');
    if (memBar) memBar.style.width = `${stats.mem_p || 0}%`;
    safeSet('info-mem-val', stats.mem || '-');

    const diskBar = document.getElementById('info-disk-bar');
    if (diskBar) diskBar.style.width = `${stats.disk_p || 0}%`;
    safeSet('info-disk-val', stats.disk || '-');

    const procList = document.getElementById('proc-list');
    if (procList && stats.procs) {
        procList.innerHTML = stats.procs.map(p => `
            <tr>
                <td>${p.mem}</td>
                <td>${p.cpu}</td>
                <td title="${p.cmd}">${p.cmd}</td>
            </tr>
        `).join('');
    }
}

function clearStatsUI() {
    const safeSet = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.innerText = val;
    };
    safeSet('info-hostname', '-');
    safeSet('info-os', '-');
    safeSet('info-uptime', '-');
    safeSet('info-ip', '-');
    safeSet('info-load', '-');
    safeSet('info-cpu-val', '0%');
    safeSet('info-mem-val', '-');
    safeSet('info-disk-val', '-');

    const cpuBar = document.getElementById('info-cpu-bar');
    if (cpuBar) cpuBar.style.width = '0%';
    const memBar = document.getElementById('info-mem-bar');
    if (memBar) memBar.style.width = '0%';
    const diskBar = document.getElementById('info-disk-bar');
    if (diskBar) diskBar.style.width = '0%';

    const procList = document.getElementById('proc-list');
    if (procList) procList.innerHTML = '';
}
