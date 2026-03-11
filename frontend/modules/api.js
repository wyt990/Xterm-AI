/**
 * 后端 API 通讯模块
 */

async function request(url, options = {}) {
    const token = localStorage.getItem('xterm_token');
    
    // 基础头信息
    const headers = { ...options.headers };
    
    // 如果没有显式设置 Content-Type，且 body 不是 FormData，则默认为 JSON
    if (!headers['Content-Type'] && !(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }
    
    // 如果显式将 Content-Type 设为 undefined，则删除该头（用于让 fetch 自动处理 FormData 边界）
    if (headers['Content-Type'] === undefined) {
        delete headers['Content-Type'];
    }

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    try {
        const response = await fetch(url, {
            ...options,
            headers
        });
        
        if (response.status === 401 && url !== '/api/login') {
            // 上报 401 来源到后端，便于排查登录循环
            try {
                fetch('/api/debug/auth_failure', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url }),
                    keepalive: true
                }).catch(() => {});
            } catch (e) {
                console.warn('上报鉴权失败来源失败:', e);
            }
            // Token 失效，跳转到登录或显示登录弹窗
            globalThis.dispatchEvent(new CustomEvent('authError'));
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
    getRecentConnections: (limit = 20) => request(`/api/servers/recent?limit=${limit}`),
    addServer: (data) => request('/api/servers', { method: 'POST', body: JSON.stringify(data) }),
    updateServer: (id, data) => request(`/api/servers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteServer: (id) => request(`/api/servers/${id}`, { method: 'DELETE' }),
    markServerConnected: (id) => request(`/api/servers/${id}/mark_connected`, { method: 'POST' }),
    clearRecentConnections: () => request('/api/servers/recent', { method: 'DELETE' }),
    testServer: (data) => request('/api/servers/test', { method: 'POST', body: JSON.stringify(data) }),
    getServerDoc: (id) => request(`/api/servers/${id}/doc`),
    updateServerDoc: (id, content) => request(`/api/servers/${id}/doc`, { method: 'PUT', body: JSON.stringify({ content }) }),

    // AI 配置
    getAIEndpoints: () => request('/api/ai_endpoints'),
    addAIEndpoint: (data) => request('/api/ai_endpoints', { method: 'POST', body: JSON.stringify(data) }),
    updateAIEndpoint: (id, data) => request(`/api/ai_endpoints/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteAIEndpoint: (id) => request(`/api/ai_endpoints/${id}`, { method: 'DELETE' }),
    setActiveAI: (id) => request(`/api/ai_endpoints/${id}/activate`, { method: 'POST' }),
    testAI: (data) => request('/api/ai_endpoints/test', { method: 'POST', body: JSON.stringify(data) }),

    // AI 角色
    getRoles: (params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/roles${qs ? '?' + qs : ''}`);
    },
    addRole: (data, params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/roles${qs ? '?' + qs : ''}`, { method: 'POST', body: JSON.stringify(data) });
    },
    updateRole: (id, data, params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/roles/${id}${qs ? '?' + qs : ''}`, { method: 'PUT', body: JSON.stringify(data) });
    },
    deleteRole: (id, params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/roles/${id}${qs ? '?' + qs : ''}`, { method: 'DELETE' });
    },
    setActiveRole: (id, params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/roles/${id}/activate${qs ? '?' + qs : ''}`, { method: 'POST' });
    },
    exportRoles: (params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/roles/export${qs ? '?' + qs : ''}`);
    },
    importRoles: (data, params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/roles/import${qs ? '?' + qs : ''}`, { method: 'POST', body: JSON.stringify(data) });
    },

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
    getServerTreeCollapsed: () => request('/api/server_tree_collapsed'),
    putServerTreeCollapsed: (data) => request('/api/server_tree_collapsed', { method: 'PUT', body: JSON.stringify(data) }),

    updateSettings: (data) => request('/api/system_settings', { method: 'POST', body: JSON.stringify(data) }),
    getLogFiles: () => request('/api/logs'),
    getLogContent: (file, lines) => request(`/api/logs/content?filename=${file}&lines=${lines}`),
    clearLogs: () => request('/api/logs', { method: 'DELETE' }),
    backupDatabase: () => request('/api/database/backup', { method: 'POST' }),

    // 技能管理
    getSkills: (params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/skills${qs ? '?' + qs : ''}`);
    },
    getSkill: (id) => request(`/api/skills/${id}`),
    addSkill: (data) => request('/api/skills', { method: 'POST', body: JSON.stringify(data) }),
    updateSkill: (id, data) => request(`/api/skills/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteSkill: (id) => request(`/api/skills/${id}`, { method: 'DELETE' }),
    toggleSkill: (id) => request(`/api/skills/${id}/toggle`, { method: 'POST' }),
    refreshSkill: (id) => request(`/api/skills/${id}/refresh`, { method: 'POST' }),
    translate: (text) => request('/api/translate', { method: 'POST', body: JSON.stringify({ text }) }),

    // 技能商店
    getRecommendedSkills: (query) => request(`/api/skill_store/recommended?q=${encodeURIComponent(query || '')}`),
    listSkillsFromRepo: (repo, token) => request(`/api/skill_store/list?repo=${encodeURIComponent(repo)}${token ? '&token=' + encodeURIComponent(token) : ''}`),
    installSkillFromStore: (data) => request('/api/skill_store/install', { method: 'POST', body: JSON.stringify(data) }),

    // 代理管理
    getProxies: () => request('/api/proxies'),
    getProxy: (id) => request(`/api/proxies/${id}`),
    addProxy: (data) => request('/api/proxies', { method: 'POST', body: JSON.stringify(data) }),
    updateProxy: (id, data) => request(`/api/proxies/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteProxy: (id) => request(`/api/proxies/${id}`, { method: 'DELETE' }),
    getProxyBindings: () => request('/api/proxy_bindings'),
    updateProxyBindings: (data) => request('/api/proxy_bindings', { method: 'POST', body: JSON.stringify(data) }),
    clearAiProxyBinding: () => request('/api/proxy_bindings/clear_ai', { method: 'POST' }),

    // 设备类型与绑定
    getDeviceTypes: () => request('/api/device_types'),
    addDeviceType: (data) => request('/api/device_types', { method: 'POST', body: JSON.stringify(data) }),
    updateDeviceType: (id, data) => request(`/api/device_types/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteDeviceType: (id) => request(`/api/device_types/${id}`, { method: 'DELETE' }),

    // SFTP 核心操作
    sftpList: (serverId, path) => request(`/api/sftp/list?server_id=${serverId}&path=${encodeURIComponent(path)}`),
    sftpRename: (data) => request('/api/sftp/rename', { method: 'POST', body: JSON.stringify(data) }),
    sftpChmod: (data) => request('/api/sftp/chmod', { method: 'POST', body: JSON.stringify(data) }),
    sftpDelete: (data) => request('/api/sftp/delete', { method: 'DELETE', body: JSON.stringify(data) }),
    sftpCreate: (data) => request('/api/sftp/create', { method: 'POST', body: JSON.stringify(data) }),
    sftpRead: (serverId, path) => request(`/api/sftp/read?server_id=${serverId}&path=${encodeURIComponent(path)}`),
    sftpSave: (data) => request('/api/sftp/save', { method: 'POST', body: JSON.stringify(data) }),

    // 系统指标与监控
    getStatsHistory: (serverId, minutes = 30) => request(`/api/servers/${serverId}/stats/history?minutes=${minutes}`),
    clearServerStats: (serverId) => request(`/api/servers/${serverId}/stats/history`, { method: 'DELETE' }),
    clearAllStatsHistory: () => request('/api/servers/stats/history/all', { method: 'DELETE' }),
    killProcess: (serverId, pid) => {
        const fd = new FormData();
        fd.append('pid', pid);
        return request(`/api/servers/${serverId}/process/kill`, { method: 'POST', body: fd, headers: { 'Content-Type': undefined } });
    },

    // 自进化任务中心（Phase A）
    getEvolutionTasks: (params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/evolution/tasks${qs ? '?' + qs : ''}`);
    },
    getEvolutionTask: (id) => request(`/api/evolution/tasks/${id}`),
    createEvolutionTask: (data) => request('/api/evolution/tasks', { method: 'POST', body: JSON.stringify(data) }),
    updateEvolutionTask: (id, data) => request(`/api/evolution/tasks/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    cloneEvolutionTask: (id, data = {}) => request(`/api/evolution/tasks/${id}/clone`, { method: 'POST', body: JSON.stringify(data) }),
    cancelEvolutionTask: (id) => request(`/api/evolution/tasks/${id}/cancel`, { method: 'POST' }),
    enableEvolutionTask: (id) => request(`/api/evolution/tasks/${id}/enable`, { method: 'POST' }),
    deleteEvolutionTask: (id) => request(`/api/evolution/tasks/${id}`, { method: 'DELETE' }),
    approveEvolutionTask: (id) => request(`/api/evolution/tasks/${id}/approve`, { method: 'POST' }),
    rejectEvolutionTask: (id, data = {}) => request(`/api/evolution/tasks/${id}/reject`, { method: 'POST', body: JSON.stringify(data) }),
    runEvolutionTask: (id, data) => request(`/api/evolution/tasks/${id}/run`, { method: 'POST', body: JSON.stringify(data || {}) }),
    runEvolutionTaskAsync: (id) => request(`/api/evolution/tasks/${id}/run_async`, { method: 'POST' }),
    enqueueEvolutionTask: (id) => request(`/api/evolution/tasks/${id}/enqueue`, { method: 'POST' }),
    getEvolutionTaskRuns: (id, limit = 20, order = 'desc') => request(`/api/evolution/tasks/${id}/runs?limit=${limit}&order=${order}`),
    executeEvolutionIntent: (data) => request('/api/evolution/intent/execute', { method: 'POST', body: JSON.stringify(data) }),
    getEvolutionQueueStatus: () => request('/api/evolution/queue/status'),
    getEvolutionSchedulerStatus: () => request('/api/evolution/scheduler/status'),
    updateEvolutionSchedulerConfig: (data) => request('/api/evolution/scheduler/config', { method: 'POST', body: JSON.stringify(data) }),
    runEvolutionSchedulerOnce: () => request('/api/evolution/scheduler/run_once', { method: 'POST' }),
    getEvolutionExperiences: (limit = 50) => request(`/api/evolution/experiences?limit=${limit}`),
    getEvolutionFailureReports: (limit = 50) => request(`/api/evolution/failure_reports?limit=${limit}`),
    getEvolutionSchemaMigrations: (limit = 50) => request(`/api/evolution/schema_migrations?limit=${limit}`),
    applyEvolutionSchemaMigration: (data) => request('/api/evolution/schema_migrations/apply', { method: 'POST', body: JSON.stringify(data) }),
    getEvolutionOpsTemplates: () => request('/api/evolution/templates/ops'),
    getEvolutionPlugins: (params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request(`/api/evolution/plugins${qs ? '?' + qs : ''}`);
    },
    getEvolutionPlugin: (pluginId) => request(`/api/evolution/plugins/${encodeURIComponent(pluginId)}`),
    installEvolutionPlugin: (data) => request('/api/evolution/plugins/install', { method: 'POST', body: JSON.stringify(data) }),
    enableEvolutionPlugin: (pluginId) => request(`/api/evolution/plugins/${encodeURIComponent(pluginId)}/enable`, { method: 'POST' }),
    disableEvolutionPlugin: (pluginId) => request(`/api/evolution/plugins/${encodeURIComponent(pluginId)}/disable`, { method: 'POST' }),
    uninstallEvolutionPlugin: (pluginId) => request(`/api/evolution/plugins/${encodeURIComponent(pluginId)}`, { method: 'DELETE' }),
    submitEvolutionPluginTask: (pluginId, data) => request(`/api/evolution/plugins/${encodeURIComponent(pluginId)}/submit_task`, { method: 'POST', body: JSON.stringify(data) }),
    initAIDialogUpload: (data) => request('/api/evolution/ai_uploads/init', { method: 'POST', body: JSON.stringify(data) }),
    uploadAIDialogChunk: (uploadId, chunkIndex, blob, fileName = 'chunk.bin') => {
        const fd = new FormData();
        fd.append('chunk_index', String(chunkIndex));
        fd.append('file', blob, fileName);
        return request(`/api/evolution/ai_uploads/${encodeURIComponent(uploadId)}/chunk`, {
            method: 'POST',
            body: fd,
            headers: { 'Content-Type': undefined }
        });
    },
    getAIDialogUploadProgress: (uploadId) => request(`/api/evolution/ai_uploads/${encodeURIComponent(uploadId)}/progress`),
    getAIDialogUploadChunks: (uploadId) => request(`/api/evolution/ai_uploads/${encodeURIComponent(uploadId)}/chunks`),
    completeAIDialogUpload: (uploadId, sessionId) => {
        const fd = new FormData();
        fd.append('session_id', String(sessionId));
        return request(`/api/evolution/ai_uploads/${encodeURIComponent(uploadId)}/complete`, {
            method: 'POST',
            body: fd,
            headers: { 'Content-Type': undefined }
        });
    },
    seedEvolutionDemo: (data = {}) => request('/api/evolution/demo/seed', { method: 'POST', body: JSON.stringify(data) }),
    resetEvolutionDemo: (data = {}) => request('/api/evolution/demo/reset', { method: 'POST', body: JSON.stringify(data) }),
    
    // 暴露通用请求方法
    request
};
