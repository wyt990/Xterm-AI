/**
 * 通用工具类与组件加载器
 */

// 1. 动态加载 HTML 组件
export async function loadComponents() {
    const components = [
        'auth_modals.html',
        'server_modals.html',
        'ai_modals.html',
        'command_modals.html',
        'sftp_modals.html',
        'editor_modals.html',
        'system_modals.html',
        'device_type_modals.html'
    ];
    
    const placeholder = document.getElementById('component-placeholder');
    if (!placeholder) return;

    for (const file of components) {
        try {
            const response = await fetch(`/frontend/components/${file}`);
            if (response.ok) {
                const html = await response.text();
                const div = document.createElement('div');
                div.innerHTML = html;
                // 将子元素移入占位符
                while (div.firstChild) {
                    placeholder.appendChild(div.firstChild);
                }
            }
        } catch (error) {
            console.error(`加载组件失败: ${file}`, error);
        }
    }
}

// 2. 弹窗控制
export function showModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.style.display = 'flex';
}

export function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.style.display = 'none';
}

// 3. 格式化工具
export function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function formatTime(timestamp) {
    if (!timestamp) return '-';
    const date = new Date(timestamp * 1000);
    return date.toLocaleString();
}

// 4. 通知提示 (Toast 版)
export function notify(message, type = 'info') {
    console.log(`[${type.toUpperCase()}] ${message}`);
    
    // 创建 toast 元素
    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed; bottom: 20px; right: 20px; z-index: 9999;
        padding: 10px 18px; border-radius: 6px; font-size: 13px;
        color: #fff; max-width: 320px; word-break: break-all;
        box-shadow: 0 4px 12px rgba(0,0,0,0.4);
        animation: fadeInUp 0.2s ease;
    `;
    const colors = { info: '#0078d4', success: '#107c10', error: '#c42b1c', warning: '#ca5010' };
    toast.style.background = colors[type] || colors.info;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), type === 'error' ? 4000 : 2500);
}

// 5. 按钮 Loading 状态切换
export function setBtnLoading(btn, isLoading) {
    if (!btn) return;
    if (isLoading) {
        btn.classList.add('loading');
        btn.disabled = true;
    } else {
        btn.classList.remove('loading');
        btn.disabled = false;
    }
}

// 6. 本地存储包装
export const storage = {
    get: (key, defaultValue = null) => {
        const val = localStorage.getItem(key);
        try {
            return val ? JSON.parse(val) : defaultValue;
        } catch {
            return val || defaultValue;
        }
    },
    set: (key, value) => {
        localStorage.setItem(key, typeof value === 'object' ? JSON.stringify(value) : value);
    }
};
