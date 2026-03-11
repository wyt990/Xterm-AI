import { api } from './api.js';
import { notify } from './utils.js';

let state = {
    selectedPluginId: null,
    plugins: [],
    statusFilter: '',
    searchKeyword: '',
    manifestValid: true,
    filesValid: true,
};

function escapeHtml(text) {
    return String(text ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function formatStatusBadge(status) {
    const normalized = String(status || 'unknown').toLowerCase();
    return `<span class="evo-status-badge status-${normalized}">${escapeHtml(normalized)}</span>`;
}

function canEnable(status) {
    const s = String(status || '').toLowerCase();
    return s === 'installed' || s === 'disabled';
}

function canDisable(status) {
    return String(status || '').toLowerCase() === 'enabled';
}

function applyFilters(plugins) {
    const keyword = state.searchKeyword.trim().toLowerCase();
    const targetStatus = state.statusFilter;
    return (plugins || []).filter((p) => {
        const status = String(p.status || '').toLowerCase();
        if (targetStatus && status !== targetStatus) return false;
        if (!keyword) return true;
        const pluginId = String(p.plugin_id || '').toLowerCase();
        const name = String(p.name || '').toLowerCase();
        return pluginId.includes(keyword) || name.includes(keyword);
    });
}

function renderPlugins(plugins) {
    const tbody = document.getElementById('plugin-list-body');
    if (!tbody) return;
    if (!plugins?.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="color:#8a8a8a;">暂无插件</td></tr>';
        return;
    }
    tbody.innerHTML = plugins.map((p) => `
        <tr data-plugin-id="${escapeHtml(p.plugin_id)}" class="${state.selectedPluginId === p.plugin_id ? 'plugin-row-selected' : ''}">
            <td><code class="plugin-id-code">${escapeHtml(p.plugin_id)}</code></td>
            <td>${escapeHtml(p.name || '-')}</td>
            <td>${escapeHtml(p.version || '-')}</td>
            <td>${escapeHtml(p.runtime || '-')}</td>
            <td>${formatStatusBadge(p.status)}</td>
            <td>
                <div class="plugin-action-group">
                    <button class="btn btn-sm btn-secondary" data-action="detail" data-plugin-id="${escapeHtml(p.plugin_id)}">详情</button>
                    <button class="btn btn-sm btn-secondary" data-action="enable" data-plugin-id="${escapeHtml(p.plugin_id)}" ${canEnable(p.status) ? '' : 'disabled'}>启用</button>
                    <button class="btn btn-sm btn-secondary" data-action="disable" data-plugin-id="${escapeHtml(p.plugin_id)}" ${canDisable(p.status) ? '' : 'disabled'}>禁用</button>
                    <button class="btn btn-sm btn-danger" data-action="uninstall" data-plugin-id="${escapeHtml(p.plugin_id)}">卸载</button>
                </div>
            </td>
        </tr>
    `).join('');
}

function renderPluginDetail(plugin) {
    const panel = document.getElementById('plugin-detail-panel');
    if (!panel) return;
    if (!plugin) {
        panel.innerHTML = '<div class="plugin-empty-tip">选择左侧插件查看详情</div>';
        return;
    }
    const schedules = plugin.schedules || [];
    const templates = (plugin.manifest?.task_templates) || [];
    panel.innerHTML = `
        <div class="plugin-detail-kv"><span class="key">插件 ID</span><span class="val"><code class="plugin-id-code">${escapeHtml(plugin.plugin_id)}</code></span></div>
        <div class="plugin-detail-kv"><span class="key">状态</span><span class="val">${formatStatusBadge(plugin.status)}</span></div>
        <div class="plugin-detail-kv"><span class="key">安装路径</span><span class="val"><code>${escapeHtml(plugin.install_path || '-')}</code></span></div>
        <div class="plugin-detail-title">定时规则 (${schedules.length})</div>
        <pre class="plugin-detail-pre">${escapeHtml(JSON.stringify(schedules, null, 2))}</pre>
        <div class="plugin-detail-title">任务模板 (${templates.length})</div>
        <pre class="plugin-detail-pre">${escapeHtml(JSON.stringify(templates, null, 2))}</pre>
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
        state.plugins = plugins || [];
        if (state.selectedPluginId && !state.plugins.some((p) => p.plugin_id === state.selectedPluginId)) {
            state.selectedPluginId = null;
        }
        const filtered = applyFilters(state.plugins);
        if (state.selectedPluginId && !filtered.some((p) => p.plugin_id === state.selectedPluginId)) {
            state.selectedPluginId = null;
        }
        renderPlugins(filtered);
        if (state.selectedPluginId) {
            await loadPluginDetail(state.selectedPluginId);
        } else {
            renderPluginDetail(null);
        }
    } catch (err) {
        notify(`加载插件列表失败: ${err.message}`, 'error');
    }
}

function setJsonError(type, message = '') {
    const isManifest = type === 'manifest';
    const input = document.getElementById(isManifest ? 'plugin-manifest-input' : 'plugin-files-input');
    const errorEl = document.getElementById(isManifest ? 'plugin-manifest-error' : 'plugin-files-error');
    if (!input || !errorEl) return;
    if (message) {
        input.classList.add('invalid');
        errorEl.textContent = message;
        errorEl.style.display = 'block';
    } else {
        input.classList.remove('invalid');
        errorEl.textContent = '';
        errorEl.style.display = 'none';
    }
}

function validateJsonInput(type, text) {
    const raw = String(text || '').trim();
    if (!raw) {
        setJsonError(type, '');
        return { valid: true, parsed: {} };
    }
    try {
        const parsed = JSON.parse(raw);
        if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') {
            setJsonError(type, '必须是 JSON 对象，例如 {}');
            return { valid: false, parsed: null };
        }
        setJsonError(type, '');
        return { valid: true, parsed };
    } catch (err) {
        setJsonError(type, `JSON 格式错误: ${err.message}`);
        return { valid: false, parsed: null };
    }
}

function updateInstallButtonState() {
    const installBtn = document.getElementById('install-plugin-btn');
    if (!installBtn) return;
    installBtn.disabled = !(state.manifestValid && state.filesValid);
}

function bindInstallJsonValidation() {
    const manifestEl = document.getElementById('plugin-manifest-input');
    const filesEl = document.getElementById('plugin-files-input');
    if (manifestEl && !manifestEl.dataset.bound) {
        const onManifestInput = () => {
            const ret = validateJsonInput('manifest', manifestEl.value);
            state.manifestValid = ret.valid;
            updateInstallButtonState();
        };
        manifestEl.addEventListener('input', onManifestInput);
        manifestEl.dataset.bound = '1';
        onManifestInput();
    }
    if (filesEl && !filesEl.dataset.bound) {
        const onFilesInput = () => {
            const ret = validateJsonInput('files', filesEl.value);
            state.filesValid = ret.valid;
            updateInstallButtonState();
        };
        filesEl.addEventListener('input', onFilesInput);
        filesEl.dataset.bound = '1';
        onFilesInput();
    }
}

async function installFromForm() {
    const manifestEl = document.getElementById('plugin-manifest-input');
    const filesEl = document.getElementById('plugin-files-input');
    if (!manifestEl) return;
    const manifestRet = validateJsonInput('manifest', manifestEl.value);
    const filesRet = validateJsonInput('files', filesEl?.value || '{}');
    state.manifestValid = manifestRet.valid;
    state.filesValid = filesRet.valid;
    updateInstallButtonState();
    if (!manifestRet.valid || !filesRet.valid) {
        notify('请先修复 JSON 格式错误后再安装', 'warning');
        return;
    }
    try {
        const manifest = manifestRet.parsed || {};
        const files = filesRet.parsed || {};
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
    if (installBtn && !installBtn.dataset.bound) {
        installBtn.addEventListener('click', installFromForm);
        installBtn.dataset.bound = '1';
    }
    const statusFilter = document.getElementById('plugin-status-filter');
    if (statusFilter && !statusFilter.dataset.bound) {
        statusFilter.addEventListener('change', () => {
            state.statusFilter = statusFilter.value || '';
            void loadPlugins();
        });
        statusFilter.dataset.bound = '1';
    }
    const searchInput = document.getElementById('plugin-search-input');
    if (searchInput && !searchInput.dataset.bound) {
        let timer = null;
        searchInput.addEventListener('input', () => {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => {
                state.searchKeyword = searchInput.value || '';
                void loadPlugins();
            }, 180);
        });
        searchInput.dataset.bound = '1';
    }
    const table = document.getElementById('plugin-list-body');
    if (table && !table.dataset.bound) {
        table.addEventListener('click', async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            const action = target.dataset.action;
            const pluginId = target.dataset.pluginId;
            if (!action || !pluginId) return;
            await handleAction(action, pluginId);
        });
        table.dataset.bound = '1';
    }
}

export function initPluginCenterModule() {
    bindEvents();
    bindInstallJsonValidation();
    globalThis.loadPlugins = loadPlugins;
}
