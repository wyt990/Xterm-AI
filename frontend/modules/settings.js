/**
 * 设置、角色、端点与日志管理模块
 */
import { api } from './api.js';
import { storage, notify, showModal, closeModal, setBtnLoading } from './utils.js';

export function initSettingsModule() {
    // 绑定表单提交事件
    initForms();
    
    // 暴露全局函数 (兼容 index.html)
    window.showAddServerModal = () => {
        document.getElementById('server-modal-title').innerText = '添加服务器';
        document.getElementById('server-form').reset();
        document.getElementById('server-id').value = '';
        showModal('server-modal');
    };
    window.showEditServerModal = async (id) => {
        try {
            const servers = await api.getServers();
            const server = servers.find(s => s.id === id);
            if (!server) return;
            document.getElementById('server-modal-title').innerText = '编辑服务器';
            document.getElementById('server-id').value = server.id;
            const form = document.getElementById('server-form');
            form.name.value = server.name;
            form.host.value = server.host;
            form.port.value = server.port;
            form.username.value = server.username;
            form.password.value = server.password;
            form.group_name.value = server.group_name;
            form.device_type_id.value = server.device_type_id || "";
            form.description.value = server.description;
            showModal('server-modal');
        } catch (err) { notify("获取服务器信息失败", "error"); }
    };
    window.showAddAIModal = () => {
        document.getElementById('ai-modal-title').innerText = '添加 AI 端点';
        document.getElementById('ai-form').reset();
        document.getElementById('ai-id').value = '';
        showModal('ai-modal');
    };
    window.showAddRoleModal = async () => {
        document.getElementById('role-modal-title').innerText = '创建 AI 角色';
        document.getElementById('role-form').reset();
        document.getElementById('role-id').value = '';
        loadAISelectOptions();
        await loadRoleDeviceTypeCheckboxes(null);
        showModal('role-modal');
    };
    window.testAIFromModal = testAIFromModal;
    window.testSSHFromModal = testSSHFromModal;
    window.showLogsModal = showLogsModal;
    window.loadLogContent = loadLogContent;
    window.handleClearLogs = handleClearLogs;
    window.handleClearAllStatsHistory = handleClearAllStatsHistory;

    // 命令管理
    window.showAddCommandGroupModal = () => {
        document.getElementById('command-group-modal-title').innerText = '添加命令分类';
        document.getElementById('command-group-form').reset();
        document.getElementById('command-group-id').value = '';
        showModal('command-group-modal');
    };
    window.showAddCommandModal = () => {
        const groupId = document.querySelector('.commands-sidebar-item.active')?.getAttribute('data-id');
        if (!groupId) return notify("请先选择一个命令分类", "warning");
        document.getElementById('command-modal-title').innerText = '添加快捷命令';
        document.getElementById('command-form').reset();
        document.getElementById('command-id').value = '';
        document.getElementById('command-group-id-hidden').value = groupId;
        showModal('command-modal');
    };
    
    // 技能管理
    window.showAddSkillModal = showAddSkillModal;
    window.showSkillStoreModal = showSkillStoreModal;
    window.translateSkillDescription = translateSkillDescription;
    window.loadSkills = loadSkills;

    // 角色与 AI 列表中的操作按钮需要全局访问
    window.editAI = (id) => { /* 逻辑已在 loadAIEndpoints 中动态绑定 */ };
    window.setActiveAI = async (id) => { await api.setActiveAI(id); loadAIEndpoints(); };
    window.deleteAI = async (id) => { if(confirm('确定删除?')){ await api.deleteAIEndpoint(id); loadAIEndpoints(); }};
    window.editRole = (id) => { /* 逻辑已在 loadRoles 中动态绑定 */ };
    window.setActiveRole = async (id) => { await api.setActiveRole(id); loadRoles(); };
    window.deleteRole = async (id) => { if(confirm('确定删除?')){ await api.deleteRole(id); loadRoles(); }};
}

// --- 技能管理 ---
async function showAddSkillModal() {
    document.getElementById('skill-modal-title').innerText = '创建技能';
    document.getElementById('skill-form').reset();
    document.getElementById('skill-id').value = '';
    const nameInput = document.querySelector('#skill-form input[name="name"]');
    if (nameInput) nameInput.readOnly = false;
    const descZhInput = document.querySelector('#skill-form input[name="description_zh"]');
    if (descZhInput) descZhInput.value = '';
    await loadSkillDeviceTypeCheckboxes(null);
    showModal('skill-modal');
}

async function translateSkillDescription() {
    const form = document.getElementById('skill-form');
    const descInput = form?.querySelector('input[name="description"]');
    const descZhInput = form?.querySelector('input[name="description_zh"]');
    const btn = document.getElementById('skill-translate-btn');
    if (!descInput || !descZhInput) return;
    const text = descInput.value.trim();
    if (!text) return notify('请先填写英文描述', 'warning');
    setBtnLoading(btn, true);
    try {
        const res = await api.translate(text);
        if (res.translation) {
            descZhInput.value = res.translation;
            notify('翻译完成', 'success');
        } else {
            notify(res.message || '翻译失败', 'info');
        }
    } catch (err) {
        notify('翻译服务暂不可用（离线/内网），请手动填写', 'info');
    } finally {
        setBtnLoading(btn, false);
    }
}

export async function loadSkills() {
    const filterDevice = document.getElementById('skill-filter-device')?.value || '';
    const filterEnabled = document.getElementById('skill-filter-enabled')?.value || '';
    const params = {};
    if (filterDevice) params.device_type_id = parseInt(filterDevice);
    if (filterEnabled !== '') params.enabled = parseInt(filterEnabled);

    try {
        const skills = await api.getSkills(params);
        const container = document.getElementById('skill-list-container');
        if (!container) return;

        if (skills.length === 0) {
            container.innerHTML = `
                <div class="empty-tip" style="grid-column: 1/-1; padding: 40px; text-align: center; color: #888;">
                    <i class="fas fa-puzzle-piece" style="font-size: 2rem; margin-bottom: 12px; display: block;"></i>
                    <p>暂无技能，点击「从商店安装」或「创建技能」添加</p>
                </div>
            `;
            return;
        }

        container.innerHTML = skills.map(s => {
            const desc = (s.description_zh || s.description || '').substring(0, 80);
            const typeLabels = (s.bound_device_type_ids || []).map(id => {
                const t = window.allDeviceTypes?.find(d => d.id === id);
                return t ? t.name : '';
            }).filter(Boolean).join('、') || '未绑定';
            const isRemote = (s.source || 'local') !== 'local';
            return `
            <div class="role-card ${s.is_enabled ? 'active' : ''}" data-skill-id="${s.id}">
                <div class="role-card-header">
                    <h3>${s.display_name || s.name}</h3>
                    ${s.is_enabled ? '<span class="badge">启用</span>' : '<span class="badge" style="background:#555">禁用</span>'}
                </div>
                <div class="role-card-body">
                    <small style="color:#888;">${s.name}</small>
                    <p style="margin: 8px 0 0;">${desc || '(无描述)'}${desc && desc.length >= 80 ? '...' : ''}</p>
                    <p style="margin: 8px 0 0; font-size: 11px; color: #666;">适用: ${typeLabels}</p>
                </div>
                <div class="card-actions">
                    <button class="btn btn-sm btn-secondary" onclick="editSkill(${s.id})">编辑</button>
                    <button class="btn btn-sm ${s.is_enabled ? 'btn-secondary' : 'btn-primary'}" onclick="toggleSkill(${s.id})">${s.is_enabled ? '禁用' : '启用'}</button>
                    ${isRemote ? `<button class="btn btn-sm btn-secondary" onclick="refreshSkill(${s.id})" title="从源刷新">刷新</button>` : ''}
                    <button class="btn btn-sm btn-danger" onclick="deleteSkill(${s.id}, '${(s.display_name || s.name).replace(/'/g, "\\'")}')">删除</button>
                </div>
            </div>
            `;
        }).join('');

        window.editSkill = async (id) => {
            const skill = skills.find(s => s.id === id);
            if (!skill) return;
            document.getElementById('skill-modal-title').innerText = '编辑技能';
            document.getElementById('skill-id').value = skill.id;
            const form = document.getElementById('skill-form');
            form.name.value = skill.name;
            const nameInput = form.querySelector('input[name="name"]');
            if (nameInput) nameInput.readOnly = true;
            form.display_name.value = skill.display_name || '';
            form.description.value = skill.description || '';
            form.description_zh.value = skill.description_zh || '';
            form.content.value = skill.content || '';
            form.is_enabled.checked = !!skill.is_enabled;
            await loadSkillDeviceTypeCheckboxes(skill.bound_device_type_ids || []);
            showModal('skill-modal');
        };
        window.toggleSkill = async (id) => {
            try {
                const res = await api.toggleSkill(id);
                notify(res.is_enabled ? '已启用' : '已禁用', 'success');
                loadSkills();
            } catch (err) { notify('操作失败: ' + err.message, 'error'); }
        };
        window.refreshSkill = async (id) => {
            try {
                await api.refreshSkill(id);
                notify('已从远程更新技能内容', 'success');
                loadSkills();
            } catch (err) { notify('刷新失败: ' + err.message, 'error'); }
        };
        window.deleteSkill = async (id, name) => {
            if (!confirm(`确定要删除技能「${name}」吗？`)) return;
            try {
                await api.deleteSkill(id);
                notify('已删除', 'success');
                loadSkills();
            } catch (err) { notify('删除失败: ' + err.message, 'error'); }
        };
    } catch (err) {
        notify('加载技能失败: ' + err.message, 'error');
    }
}

async function loadSkillDeviceTypeCheckboxes(checkedIds = []) {
    const container = document.getElementById('skill-device-type-list');
    if (!container) return;
    try {
        const types = await api.getDeviceTypes();
        const checkedSet = new Set(Array.isArray(checkedIds) && checkedIds.length ? checkedIds : []);
        container.innerHTML = types.map(t => `
            <label class="checkbox-label" style="display: flex; align-items: center; gap: 4px; padding: 4px 8px; background: #2d2d2d; border-radius: 3px; cursor: pointer;">
                <input type="checkbox" name="bound_device_types" value="${t.id}" ${checkedSet.has(t.id) ? 'checked' : ''} style="margin: 0;">
                <span style="font-size: 12px;">${t.name}</span>
            </label>
        `).join('');
    } catch (err) { console.error('加载设备类型失败', err); }
}

// --- 技能商店 ---
let skillStorePendingInstall = null;

async function showSkillStoreModal() {
    skillStorePendingInstall = null;
    document.getElementById('skill-store-install-panel').style.display = 'none';
    document.querySelectorAll('.skill-store-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === 'recommended');
        t.style.color = t.dataset.tab === 'recommended' ? '#ccc' : '#888';
        t.style.borderBottomColor = t.dataset.tab === 'recommended' ? '#0078d4' : 'transparent';
    });
    document.getElementById('skill-store-recommended').style.display = 'block';
    document.getElementById('skill-store-github').style.display = 'none';
    document.getElementById('skill-store-query').value = '';
    document.getElementById('skill-store-repo').value = '';
    document.getElementById('skill-store-token').value = '';
    document.getElementById('skill-store-github-list').innerHTML = '';
    await loadRecommendedSkills('');
    document.getElementById('skill-store-query').oninput = () => loadRecommendedSkills(document.getElementById('skill-store-query').value);
    document.querySelectorAll('.skill-store-tab').forEach(t => {
        t.onclick = () => {
            document.querySelectorAll('.skill-store-tab').forEach(x => {
                x.classList.toggle('active', x.dataset.tab === t.dataset.tab);
                x.style.color = x.dataset.tab === t.dataset.tab ? '#ccc' : '#888';
                x.style.borderBottomColor = x.dataset.tab === t.dataset.tab ? '#0078d4' : 'transparent';
            });
            document.getElementById('skill-store-recommended').style.display = t.dataset.tab === 'recommended' ? 'block' : 'none';
            document.getElementById('skill-store-github').style.display = t.dataset.tab === 'github' ? 'block' : 'none';
        };
    });
    showModal('skill-store-modal');
}

const DEVICE_TYPE_LABELS = { linux: 'Linux', windows: 'Windows', h3c: 'H3C', huawei: '华为', cisco: '思科', ruijie: '锐捷', other: '其它' };
function formatDeviceTypeLabels(values) {
    if (!values || !Array.isArray(values)) return '';
    return values.map(v => DEVICE_TYPE_LABELS[v] || v).filter(Boolean).join('、');
}

async function loadRecommendedSkills(query) {
    const container = document.getElementById('skill-store-recommended-list');
    if (!container) return;
    try {
        const [skills, installedList] = await Promise.all([
            api.getRecommendedSkills(query),
            api.getSkills({})
        ]);
        const installedNames = new Set((installedList || []).map(s => (s.name || '').toLowerCase()));
        if (skills.length === 0) {
            container.innerHTML = '<div class="empty-tip" style="grid-column: 1/-1; padding: 24px; color: #888; text-align: center;">暂无推荐技能</div>';
            return;
        }
        container.innerHTML = skills.map(s => {
            const deviceLabels = formatDeviceTypeLabels(s.device_type_values);
            const isInstalled = installedNames.has((s.name || '').toLowerCase());
            return `
            <div class="role-card" style="cursor: pointer;${isInstalled ? ' opacity: 0.85;' : ''}"
                data-skill-source="${(s.source || '').replace(/"/g, '&quot;')}"
                data-skill-name="${(s.name || '').replace(/"/g, '&quot;')}"
                data-skill-path="${(s.skill_path || '.agent-skills').replace(/"/g, '&quot;')}"
                data-skill-description="${(s.description || '').replace(/"/g, '&quot;')}"
                data-skill-desc-zh="${(s.description_zh || '').replace(/"/g, '&quot;')}"
                data-skill-device-values="${(s.device_type_values || []).join(',')}">
                <div class="role-card-header"><h3>${(s.display_name || s.name)}</h3>${isInstalled ? '<span class="badge" style="background:#28a745; margin-left:6px;">已安装</span>' : ''}</div>
                <div class="role-card-body">
                    <small style="color:#888;">${s.name}</small>
                    <p style="margin: 8px 0 0; font-size: 12px;">${(s.description_zh || s.description || '(无描述)').substring(0, 100)}...</p>
                    ${deviceLabels ? `<p style="margin: 6px 0 0; font-size: 11px; color: #0078d4;"><i class="fas fa-server"></i> 适用: ${deviceLabels}</p>` : ''}
                </div>
                <div class="card-actions">
                    ${isInstalled ? '<button class="btn btn-sm btn-secondary" disabled><i class="fas fa-check"></i> 已安装</button>' : '<button class="btn btn-sm btn-primary" onclick="skillStoreSelectInstall(event, this)">安装</button>'}
                </div>
            </div>
        `}).join('');
    } catch (err) {
        container.innerHTML = '<div class="empty-tip" style="grid-column: 1/-1; padding: 24px; color: #e74c3c;">加载失败: ' + err.message + '</div>';
    }
}

async function loadSkillsFromRepo() {
    const repo = document.getElementById('skill-store-repo').value.trim();
    const token = document.getElementById('skill-store-token').value.trim() || undefined;
    const container = document.getElementById('skill-store-github-list');
    if (!repo) return notify('请输入仓库地址', 'warning');
    container.innerHTML = '<div style="padding: 24px; color: #888;">正在列出技能...</div>';
    try {
        const skills = await api.listSkillsFromRepo(repo, token);
        if (skills.length === 0) {
            container.innerHTML = '<div class="empty-tip" style="grid-column: 1/-1; padding: 24px; color: #888;">未找到技能目录（.agent-skills、skills 等）</div>';
            return;
        }
        container.innerHTML = skills.map(s => `
            <div class="role-card"
                data-skill-source="${(s.source || '').replace(/"/g, '&quot;')}"
                data-skill-name="${(s.name || '').replace(/"/g, '&quot;')}"
                data-skill-path="${(s.skill_path || '.agent-skills').replace(/"/g, '&quot;')}"
                data-skill-description="${(s.description || '').replace(/"/g, '&quot;')}"
                data-skill-desc-zh="${(s.description_zh || '').replace(/"/g, '&quot;')}"
                data-skill-device-values="">
                <div class="role-card-header"><h3>${s.name}</h3></div>
                <div class="role-card-body">
                    <small style="color:#888;">${s.source} / ${s.skill_path || '.agent-skills'}</small>
                </div>
                <div class="card-actions">
                    <button class="btn btn-sm btn-primary" onclick="skillStoreSelectInstall(event, this)">安装</button>
                </div>
            </div>
        `).join('');
    } catch (err) {
        container.innerHTML = '<div class="empty-tip" style="grid-column: 1/-1; padding: 24px; color: #e74c3c;">加载失败: ' + err.message + '</div>';
    }
}

window.loadSkillsFromRepo = loadSkillsFromRepo;
window.skillStoreSelectInstall = skillStoreSelectInstall;

function skillStoreSelectInstall(ev, btn, skillData) {
    let data = skillData;
    if (!data) {
        const card = btn.closest('.role-card');
        if (!card) return;
        if (card.dataset.skillSource !== undefined) {
            const dv = card.dataset.skillDeviceValues || '';
            data = {
                source: card.dataset.skillSource || '',
                name: card.dataset.skillName || '',
                skill_path: card.dataset.skillPath || '.agent-skills',
                description: card.dataset.skillDescription || '',
                description_zh: card.dataset.skillDescZh || '',
                device_type_values: dv ? dv.split(',').filter(Boolean) : []
            };
        }
    }
    if (!data || !data.name) return;
    skillStorePendingInstall = {
        source: data.source || data.repo || '',
        skill_name: data.name,
        skill_path: data.skill_path || '.agent-skills',
        description_zh: data.description_zh || data.description || '',
        description: data.description || '',
        device_type_values: data.device_type_values || []
    };
    document.getElementById('skill-store-install-panel').style.display = 'block';
    const descZhGroup = document.getElementById('skill-store-desc-zh-group');
    const descZhInput = document.getElementById('skill-store-desc-zh');
    if (descZhGroup && descZhInput) {
        descZhInput.value = skillStorePendingInstall.description_zh || '';
        descZhGroup.style.display = 'block';
    }
    loadSkillStoreDeviceTypes(skillStorePendingInstall.device_type_values);
}

async function loadSkillStoreDeviceTypes(recommendedValues = []) {
    const container = document.getElementById('skill-store-device-types');
    if (!container) return;
    try {
        const types = await api.getDeviceTypes();
        const recommendSet = new Set((recommendedValues || []).map(v => String(v).toLowerCase()));
        container.innerHTML = types.map(t => {
            const isRecommended = recommendSet.has((t.value || '').toLowerCase());
            return `
            <label class="checkbox-label" style="display: flex; align-items: center; gap: 4px; padding: 4px 8px; background: #2d2d2d; border-radius: 3px; cursor: pointer;">
                <input type="checkbox" class="skill-store-dt" value="${t.id}" ${isRecommended ? 'checked' : ''} style="margin: 0;">
                <span style="font-size: 12px;">${t.name}</span>
            </label>
        `}).join('');
    } catch (err) { console.error(err); }
}

window.skillStoreCancelInstall = function() {
    skillStorePendingInstall = null;
    document.getElementById('skill-store-install-panel').style.display = 'none';
};

window.translateStoreSkillDescription = async function() {
    if (!skillStorePendingInstall?.description) return notify('该技能无英文描述可翻译', 'info');
    const descZhInput = document.getElementById('skill-store-desc-zh');
    if (!descZhInput) return;
    try {
        const res = await api.translate(skillStorePendingInstall.description);
        if (res.translation) {
            descZhInput.value = res.translation;
            skillStorePendingInstall.description_zh = res.translation;
            notify('翻译完成', 'success');
        } else {
            notify(res.message || '翻译失败', 'info');
        }
    } catch (err) {
        notify('翻译服务暂不可用（离线/内网），可安装后手动编辑', 'info');
    }
};

window.skillStoreDoInstall = async function() {
    if (!skillStorePendingInstall) return;
    const descZhInput = document.getElementById('skill-store-desc-zh');
    const descZh = descZhInput?.value?.trim() || skillStorePendingInstall.description_zh || null;
    const checked = Array.from(document.querySelectorAll('.skill-store-dt:checked')).map(c => parseInt(c.value));
    const btn = document.getElementById('skill-store-install-btn');
    setBtnLoading(btn, true);
    try {
        await api.installSkillFromStore({
            source: skillStorePendingInstall.source,
            skill_name: skillStorePendingInstall.skill_name,
            skill_path: skillStorePendingInstall.skill_path,
            description_zh: descZh,
            bound_device_type_ids: checked
        });
        notify('安装成功', 'success');
        skillStoreCancelInstall();
        closeModal('skill-store-modal');
        loadSkills();
    } catch (err) {
        notify('安装失败: ' + err.message, 'error');
    } finally {
        setBtnLoading(btn, false);
    }
};

export async function initSkillFilters() {
    const deviceSelect = document.getElementById('skill-filter-device');
    const enabledSelect = document.getElementById('skill-filter-enabled');
    if (!deviceSelect) return;
    try {
        const types = await api.getDeviceTypes();
        window.allDeviceTypes = types;
        const currentVal = deviceSelect.value;
        deviceSelect.innerHTML = '<option value="">全部设备类型</option>' + types.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
        if (currentVal) deviceSelect.value = currentVal;
        deviceSelect.onchange = () => loadSkills();
        if (enabledSelect) enabledSelect.onchange = () => loadSkills();
    } catch (err) { console.error('加载设备类型失败', err); }
}

// --- 服务器操作 ---
export async function testSSHFromModal(e) {
    e.preventDefault();
    const btn = e.target;
    const form = document.getElementById('server-form');
    const formData = new FormData(form);
    const data = Object.fromEntries(formData.entries());
    
    setBtnLoading(btn, true);
    notify("正在测试 SSH 连接...", "info");
    try {
        const res = await api.testServer(data);
        notify("连接测试成功！", "success");
    } catch (err) {
        notify(`连接失败: ${err.message}`, "error");
    } finally {
        setBtnLoading(btn, false);
    }
}

// --- AI 端点操作 ---
export async function loadAIEndpoints() {
    try {
        const endpoints = await api.getAIEndpoints();
        const container = document.getElementById('ai-list-container');
        if (!container) return;
        
        container.innerHTML = endpoints.map(ai => `
            <div class="ai-card ${ai.is_active ? 'active' : ''}">
                <div class="ai-card-header">
                    <h3>${ai.name}</h3>
                    <div class="status-dot ${ai.is_active ? 'online' : ''}"></div>
                </div>
                <div class="ai-card-body">
                    <p><strong>模型:</strong> ${ai.model}</p>
                    <p><strong>地址:</strong> ${ai.base_url}</p>
                </div>
                <div class="card-actions">
                    <button class="btn btn-sm btn-secondary" onclick="editAI(${ai.id})">编辑</button>
                    <button class="btn btn-sm ${ai.is_active ? 'btn-primary' : 'btn-secondary'}" onclick="setActiveAI(${ai.id})">${ai.is_active ? '已激活' : '激活'}</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteAI(${ai.id})">删除</button>
                </div>
            </div>
        `).join('');
        
        window.editAI = async (id) => {
            const ai = endpoints.find(a => a.id === id);
            document.getElementById('ai-modal-title').innerText = '编辑 AI 端点';
            document.getElementById('ai-id').value = ai.id;
            document.getElementById('ai-form').name.value = ai.name;
            document.getElementById('ai-form').base_url.value = ai.base_url;
            document.getElementById('ai-form').api_key.value = ai.api_key;
            document.getElementById('ai-form').model.value = ai.model;
            showModal('ai-modal');
        };
        window.setActiveAI = async (id) => { await api.setActiveAI(id); loadAIEndpoints(); };
        window.deleteAI = async (id) => { if(confirm('确定删除?')){ await api.deleteAIEndpoint(id); loadAIEndpoints(); }};
    } catch (err) { notify("加载 AI 端点失败", "error"); }
}

async function testAIFromModal(e) {
    e.preventDefault();
    const btn = e.target;
    const form = document.getElementById('ai-form');
    const data = {
        api_key: form.api_key.value,
        base_url: form.base_url.value,
        model: form.model.value
    };
    setBtnLoading(btn, true);
    notify("正在测试 AI 连接...", "info");
    try {
        await api.testAI(data);
        notify("AI 连接测试成功！", "success");
    } catch (err) {
        notify(`连接失败: ${err.message}`, "error");
    } finally {
        setBtnLoading(btn, false);
    }
}

// --- AI 角色操作 ---
export async function loadRoles() {
    try {
        const roles = await api.getRoles();
        const container = document.getElementById('role-list-container');
        if (!container) return;
        
        container.innerHTML = roles.map(role => `
            <div class="role-card ${role.is_active ? 'active' : ''}">
                <div class="role-card-header">
                    <h3>${role.name}</h3>
                    ${role.is_active ? '<span class="badge">激活</span>' : ''}
                </div>
                <div class="role-card-body">${role.system_prompt}</div>
                <div class="card-actions">
                    <button class="btn btn-sm btn-secondary" onclick="editRole(${role.id})">编辑</button>
                    <button class="btn btn-sm ${role.is_active ? 'btn-primary' : 'btn-secondary'}" onclick="setActiveRole(${role.id})">使用</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteRole(${role.id})">删除</button>
                </div>
            </div>
        `).join('');
        
        window.editRole = async (id) => {
            const role = roles.find(r => r.id === id);
            document.getElementById('role-modal-title').innerText = '编辑 AI 角色';
            document.getElementById('role-id').value = role.id;
            const form = document.getElementById('role-form');
            form.name.value = role.name;
            form.system_prompt.value = role.system_prompt;
            loadAISelectOptions(role.ai_endpoint_id);
            // 加载绑定的系统类型
            await loadRoleDeviceTypeCheckboxes(role.id);
            showModal('role-modal');
        };
        window.setActiveRole = async (id) => { await api.setActiveRole(id); loadRoles(); };
        window.deleteRole = async (id) => { if(confirm('确定删除?')){ await api.deleteRole(id); loadRoles(); }};
    } catch (err) { notify("加载角色失败", "error"); }
}

async function loadAISelectOptions(selectedId = null) {
    const endpoints = await api.getAIEndpoints();
    const select = document.getElementById('role-ai-select');
    select.innerHTML = '<option value="">使用系统默认激活端点</option>' + 
        endpoints.map(ai => `<option value="${ai.id}" ${ai.id == selectedId ? 'selected' : ''}>${ai.name}</option>`).join('');
}

async function loadRoleDeviceTypeCheckboxes(roleId = null) {
    const container = document.getElementById('role-device-type-list');
    if (!container) return;
    try {
        const types = await api.getDeviceTypes();
        container.innerHTML = types.map(t => `
            <label class="checkbox-label" style="display: flex; align-items: center; gap: 4px; padding: 4px 8px; background: ${t.role_id === roleId && roleId !== null ? '#0078d4' : '#2d2d2d'}; border-radius: 3px; cursor: pointer;">
                <input type="checkbox" name="bound_device_types" value="${t.id}" ${t.role_id === roleId && roleId !== null ? 'checked' : ''} style="margin: 0;">
                <span style="font-size: 12px;">${t.name}</span>
            </label>
        `).join('');
    } catch (err) { console.error("加载系统类型失败", err); }
}

// --- 系统日志逻辑 ---
async function showLogsModal() {
    showModal('logs-modal');
    const files = await api.getLogFiles();
    const select = document.getElementById('log-file-select');
    // 后端返回 [{name, size, mtime}, ...]，取 name 字段
    select.innerHTML = files.map(f => {
        const name = typeof f === 'string' ? f : f.name;
        const size = f.size ? ` (${f.size} KB)` : '';
        return `<option value="${name}">${name}${size}</option>`;
    }).join('');
    if (files.length > 0) loadLogContent();
}

async function loadLogContent() {
    const file = document.getElementById('log-file-select').value;
    const lines = document.getElementById('log-lines-select').value;
    const viewer = document.getElementById('log-content-viewer');
    if (!file) return;
    
    viewer.innerText = "正在加载日志...";
    try {
        const res = await api.getLogContent(file, lines);
        viewer.innerText = res.content || "日志文件为空";
        viewer.scrollTop = viewer.scrollHeight;
    } catch (err) {
        viewer.innerText = `读取日志失败: ${err.message}`;
    }
}

async function handleClearLogs() {
    if (confirm("确定要清空所有日志文件吗？此操作不可恢复。")) {
        await api.clearLogs();
        notify("日志已清空");
        if (document.getElementById('logs-modal').style.display === 'flex') loadLogContent();
    }
}

async function handleClearAllStatsHistory() {
    if (!confirm("确定要清除所有服务器的状态记录吗？（CPU、内存、磁盘趋势图数据）")) return;
    try {
        await api.clearAllStatsHistory();
        notify("已清除所有服务器的状态记录", "success");
        window.dispatchEvent(new CustomEvent('statsCleared', { detail: { serverId: null, clearAll: true } }));
    } catch (err) { notify("清除失败: " + err.message, "error"); }
}

// --- 初始化所有表单 ---
function initForms() {
    // 服务器表单
    const serverForm = document.getElementById('server-form');
    if (serverForm) {
        serverForm.onsubmit = async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const id = document.getElementById('server-id').value;
            const formData = new FormData(e.target);
            const data = Object.fromEntries(formData.entries());
            
            setBtnLoading(submitBtn, true);
            try {
                if (id) await api.updateServer(id, data);
                else await api.addServer(data);
                closeModal('server-modal');
                notify("保存服务器成功", "success");
                window.dispatchEvent(new CustomEvent('serversChanged'));
            } catch (err) { notify("保存失败: " + err.message, "error"); }
            finally { setBtnLoading(submitBtn, false); }
        };
    }

    // 角色表单
    const roleForm = document.getElementById('role-form');
    if (roleForm) {
        roleForm.onsubmit = async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const id = document.getElementById('role-id').value;
            const checkedTypes = Array.from(e.target.querySelectorAll('input[name="bound_device_types"]:checked')).map(cb => parseInt(cb.value));
            const data = {
                name: e.target.name.value,
                system_prompt: e.target.system_prompt.value,
                ai_endpoint_id: e.target.ai_endpoint_id.value || null,
                bound_device_types: checkedTypes
            };
            
            setBtnLoading(submitBtn, true);
            try {
                if (id) await api.updateRole(id, data);
                else await api.addRole(data);
                closeModal('role-modal');
                notify("保存角色成功", "success");
                loadRoles();
                if (window.loadDeviceTypes) window.loadDeviceTypes();
            } catch (err) { notify("保存角色失败: " + err.message, "error"); }
            finally { setBtnLoading(submitBtn, false); }
        };
    }

    // AI 端点表单
    const aiForm = document.getElementById('ai-form');
    if (aiForm) {
        aiForm.onsubmit = async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const id = document.getElementById('ai-id').value;
            const capabilities = Array.from(e.target.querySelectorAll('input[name="capabilities"]:checked')).map(cb => cb.value);
            const data = {
                name: e.target.name.value,
                base_url: e.target.base_url.value,
                api_key: e.target.api_key.value,
                model: e.target.model.value,
                capabilities: capabilities
            };
            
            setBtnLoading(submitBtn, true);
            try {
                if (id) await api.updateAIEndpoint(id, data);
                else await api.addAIEndpoint(data);
                closeModal('ai-modal');
                loadAIEndpoints();
            } catch (err) { notify("保存端点失败: " + err.message, "error"); }
            finally { setBtnLoading(submitBtn, false); }
        };
    }

    // 命令分组表单
    const commandGroupForm = document.getElementById('command-group-form');
    if (commandGroupForm) {
        commandGroupForm.onsubmit = async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const id = document.getElementById('command-group-id').value;
            const name = e.target.name.value;
            
            setBtnLoading(submitBtn, true);
            try {
                if (id) await api.updateCommandGroup(id, name);
                else await api.addCommandGroup(name);
                closeModal('command-group-modal');
                window.dispatchEvent(new CustomEvent('commandsChanged'));
            } catch (err) { notify("操作失败: " + err.message, "error"); }
            finally { setBtnLoading(submitBtn, false); }
        };
    }

    // 命令表单
    const commandForm = document.getElementById('command-form');
    if (commandForm) {
        commandForm.onsubmit = async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const id = document.getElementById('command-id').value;
            const data = {
                group_id: e.target.group_id.value,
                name: e.target.name.value,
                content: e.target.content.value,
                auto_cr: e.target.auto_cr.checked ? 1 : 0
            };
            
            setBtnLoading(submitBtn, true);
            try {
                if (id) await api.updateCommand(id, data);
                else await api.addCommand(data);
                closeModal('command-modal');
                window.dispatchEvent(new CustomEvent('commandsChanged'));
            } catch (err) { notify("操作失败: " + err.message, "error"); }
            finally { setBtnLoading(submitBtn, false); }
        };
    }

    // 技能表单
    const skillForm = document.getElementById('skill-form');
    if (skillForm) {
        skillForm.onsubmit = async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const id = document.getElementById('skill-id').value;
            const checkedTypes = Array.from(e.target.querySelectorAll('input[name="bound_device_types"]:checked')).map(cb => parseInt(cb.value));
            const data = {
                name: e.target.name.value,
                display_name: e.target.display_name.value || null,
                description: e.target.description.value || null,
                description_zh: e.target.description_zh?.value || null,
                content: e.target.content.value || null,
                is_enabled: e.target.is_enabled.checked ? 1 : 0,
                bound_device_types: checkedTypes
            };
            setBtnLoading(submitBtn, true);
            try {
                if (id) await api.updateSkill(id, data);
                else await api.addSkill(data);
                closeModal('skill-modal');
                notify('保存技能成功', 'success');
                loadSkills();
            } catch (err) { notify('保存失败: ' + err.message, 'error'); }
            finally { setBtnLoading(submitBtn, false); }
            };
    }

    // 系统设置表单
    const sysSettingsForm = document.getElementById('system-settings-form');
    if (sysSettingsForm) {
        sysSettingsForm.onsubmit = async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const formData = new FormData(e.target);
            
            setBtnLoading(submitBtn, true);
            try {
                await api.updateSettings(Object.fromEntries(formData.entries()));
                notify("设置已保存", "success");
            } catch (err) { notify("保存失败: " + err.message, "error"); }
            finally { setBtnLoading(submitBtn, false); }
        };
    }
}
