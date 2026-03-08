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
    
    // 角色与 AI 列表中的操作按钮需要全局访问
    window.editAI = (id) => { /* 逻辑已在 loadAIEndpoints 中动态绑定，但为了安全这里也可以定义 */ };
    window.setActiveAI = async (id) => { await api.setActiveAI(id); loadAIEndpoints(); };
    window.deleteAI = async (id) => { if(confirm('确定删除?')){ await api.deleteAIEndpoint(id); loadAIEndpoints(); }};
    window.editRole = (id) => { /* 逻辑已在 loadRoles 中动态绑定 */ };
    window.setActiveRole = async (id) => { await api.setActiveRole(id); loadRoles(); };
    window.deleteRole = async (id) => { if(confirm('确定删除?')){ await api.deleteRole(id); loadRoles(); }};
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
