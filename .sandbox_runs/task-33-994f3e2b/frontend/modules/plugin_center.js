import { api } from './api.js';
import { notify } from './utils.js';

let state = {
    selectedPluginId: null,
};

function formatStatusBadge(status) {
    const normalized = String(status || 'unknown').toLowerCase();
    return `<span class="evo-status-badge status-${normalized}">${normalized}</span>`;
}

function renderPlugins(plugins) {
    const tbody = document.getElementById('plugin-list-body');
    if (!tbody) return;
    if (!plugins?.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="color:#8a8a8a;">暂无插件</td></tr>';
        return;
    }
    tbody.innerHTML = plugins.map((p) => `
        <tr data-plugin-id="${p.plugin_id}">
            <td><code>${p.plugin_id}</code></td>
            <td>${p.name || '-'}</td>
            <td>${p.version || '-'}</td>
            <td>${p.runtime || '-'}</td>
            <td>${formatStatusBadge(p.status)}</td>
            <td>
                <button class="btn-xs" data-action="detail" data-plugin-id="${p.plugin_id}">详情</button>
                <button class="btn-xs" data-action="enable" data-plugin-id="${p.plugin_id}">启用</button>
                <button class="btn-xs" data-action="disable" data-plugin-id="${p.plugin_id}">禁用</button>
                <button class="btn-xs btn-danger" data-action="uninstall" data-plugin-id="${p.plugin_id}">卸载</button>
            </td>
        </tr>
    `).join('');
}

function renderPluginDetail(plugin) {
    const panel = document.getElementById('plugin-detail-panel');
    if (!panel) return;
    if (!plugin) {
        panel.innerHTML = '<div style="color:#8a8a8a;">选择左侧插件查看详情</div>';
        return;
    }
    const schedules = plugin.schedules || [];
    const templates = (plugin.manifest?.task_templates) || [];
    panel.innerHTML = `
        <div class="kv-row"><span>插件 ID</span><code>${plugin.plugin_id}</code></div>
        <div class="kv-row"><span>状态</span>${formatStatusBadge(plugin.status)}</div>
        <div class="kv-row"><span>安装路径</span><code>${plugin.install_path || '-'}</code></div>
        <h4>定时规则 (${schedules.length})</h4>
        <pre>${JSON.stringify(schedules, null, 2)}</pre>
        <h4>任务模板 (${templates.length})</h4>
        <pre>${JSON.stringify(templates, null, 2)}</pre>
    `;
}

async function loadPluginDetail(pluginId) {
    try {
        const plugin = await api.getEvolutionPlugin(pluginId);
        state.selectedPluginId = pluginId;
        renderPluginDetail(plugin);
    } catch (err) {
        notify(`加载插件详情失败: ${err.message}`, 'error');
    }
}

export async function loadPlugins() {
    try {
        const plugins = await api.getEvolutionPlugins();
        renderPlugins(plugins);
        if (state.selectedPluginId) {
            await loadPluginDetail(state.selectedPluginId);
        }
    } catch (err) {
        notify(`加载插件列表失败: ${err.message}`, 'error');
    }
}

async function installFromForm() {
    const manifestEl = document.getElementById('plugin-manifest-input');
    const filesEl = document.getElementById('plugin-files-input');
    if (!manifestEl) return;
    let manifest;
    let files = {};
    try {
        manifest = JSON.parse(manifestEl.value || '{}');
    } catch (err) {
        notify(`manifest JSON 解析失败: ${err.message}`, 'error');
        return;
    }
    try {
        files = JSON.parse(filesEl?.value || '{}');
    } catch (err) {
        notify(`files JSON 解析失败: ${err.message}`, 'error');
        return;
    }
    try {
        const ret = await api.installEvolutionPlugin({ manifest, files });
        notify(`插件安装成功: ${ret.plugin_id}`, 'success');
        await loadPlugins();
    } catch (err) {
        notify(`插件安装失败: ${err.message}`, 'error');
    }
}

async function handleAction(action, pluginId) {
    try {
        if (action === 'detail') {
            await loadPluginDetail(pluginId);
            return;
        }
        if (action === 'enable') {
            await api.enableEvolutionPlugin(pluginId);
            notify(`已启用插件: ${pluginId}`, 'success');
        } else if (action === 'disable') {
            await api.disableEvolutionPlugin(pluginId);
            notify(`已禁用插件: ${pluginId}`, 'success');
        } else if (action === 'uninstall') {
            if (!confirm(`确定卸载插件 ${pluginId} 吗？`)) return;
            await api.uninstallEvolutionPlugin(pluginId);
            notify(`已卸载插件: ${pluginId}`, 'success');
        }
        await loadPlugins();
    } catch (err) {
        notify(`插件操作失败: ${err.message}`, 'error');
    }
}

function bindEvents() {
    const installBtn = document.getElementById('install-plugin-btn');
    if (installBtn) {
        installBtn.addEventListener('click', installFromForm);
    }
    const table = document.getElementById('plugin-list-body');
    if (table) {
        table.addEventListener('click', async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            const action = target.dataset.action;
            const pluginId = target.dataset.pluginId;
            if (!action || !pluginId) return;
            await handleAction(action, pluginId);
        });
    }
}

export function initPluginCenterModule() {
    bindEvents();
    globalThis.loadPlugins = loadPlugins;
}
