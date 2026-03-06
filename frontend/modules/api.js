/**
 * 后端 API 通讯模块
 */

async function request(url, options = {}) {
    const token = localStorage.getItem('xterm_token');
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers,
    };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    try {
        const response = await fetch(url, {
            ...options,
            headers
        });
        
        if (response.status === 401 && url !== '/api/login') {
            // Token 失效，跳转到登录或显示登录弹窗
            window.dispatchEvent(new CustomEvent('authError'));
            throw new Error('未登录或登录过期');
        }

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '请求失败');
        return data;
    } catch (error) {
        console.error(`API Error (${url}):`, error);
        throw error;
    }
}

export const api = {
    // 登录
    login: (password) => request('/api/login', { method: 'POST', body: JSON.stringify({ password }) }),
    
    // 服务器管理
    getServers: () => request('/api/servers'),
    addServer: (data) => request('/api/servers', { method: 'POST', body: JSON.stringify(data) }),
    updateServer: (id, data) => request(`/api/servers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteServer: (id) => request(`/api/servers/${id}`, { method: 'DELETE' }),
    testServer: (data) => request('/api/servers/test', { method: 'POST', body: JSON.stringify(data) }),

    // AI 配置
    getAIEndpoints: () => request('/api/ai_endpoints'),
    addAIEndpoint: (data) => request('/api/ai_endpoints', { method: 'POST', body: JSON.stringify(data) }),
    updateAIEndpoint: (id, data) => request(`/api/ai_endpoints/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteAIEndpoint: (id) => request(`/api/ai_endpoints/${id}`, { method: 'DELETE' }),
    setActiveAI: (id) => request(`/api/ai_endpoints/${id}/activate`, { method: 'POST' }),
    testAI: (data) => request('/api/ai_endpoints/test', { method: 'POST', body: JSON.stringify(data) }),

    // AI 角色
    getRoles: () => request('/api/roles'),
    addRole: (data) => request('/api/roles', { method: 'POST', body: JSON.stringify(data) }),
    updateRole: (id, data) => request(`/api/roles/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteRole: (id) => request(`/api/roles/${id}`, { method: 'DELETE' }),
    setActiveRole: (id) => request(`/api/roles/${id}/activate`, { method: 'POST' }),

    // 命令片段
    getCommandGroups: () => request('/api/command_groups'),
    addCommandGroup: (name) => request('/api/command_groups', { method: 'POST', body: JSON.stringify({ name }) }),
    updateCommandGroup: (id, name) => request(`/api/command_groups/${id}`, { method: 'PUT', body: JSON.stringify({ name }) }),
    deleteCommandGroup: (id) => request(`/api/command_groups/${id}`, { method: 'DELETE' }),
    getCommands: (groupId) => request(`/api/commands/${groupId}`),
    addCommand: (data) => request('/api/commands', { method: 'POST', body: JSON.stringify(data) }),
    updateCommand: (id, data) => request(`/api/commands/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteCommand: (id) => request(`/api/commands/${id}`, { method: 'DELETE' }),

    // 系统设置
    getSettings: () => request('/api/system_settings'),
    updateSettings: (data) => request('/api/system_settings', { method: 'POST', body: JSON.stringify(data) }),
    getLogFiles: () => request('/api/logs'),
    getLogContent: (file, lines) => request(`/api/logs/content?filename=${file}&lines=${lines}`),
    clearLogs: () => request('/api/logs', { method: 'DELETE' }),

    // SFTP 核心操作
    sftpList: (serverId, path) => request(`/api/sftp/list?server_id=${serverId}&path=${encodeURIComponent(path)}`),
    sftpRename: (data) => request('/api/sftp/rename', { method: 'POST', body: JSON.stringify(data) }),
    sftpChmod: (data) => request('/api/sftp/chmod', { method: 'POST', body: JSON.stringify(data) }),
    sftpDelete: (data) => request('/api/sftp/delete', { method: 'DELETE', body: JSON.stringify(data) }),
    sftpCreate: (data) => request('/api/sftp/create', { method: 'POST', body: JSON.stringify(data) }),
    sftpRead: (serverId, path) => request(`/api/sftp/read?server_id=${serverId}&path=${encodeURIComponent(path)}`),
    sftpSave: (data) => request('/api/sftp/save', { method: 'POST', body: JSON.stringify(data) })
};
