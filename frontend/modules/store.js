/**
 * Store.js - 简单的状态管理 (Store 模式)
 * 统一管理全局状态，避免模块间直接修改 DOM 或相互持有引用
 */

class Store {
    constructor() {
        this.state = {
            tabs: [],
            activeTabId: null,
            servers: [],
            connectionHistory: [],
            aiEndpoints: [],
            roles: [],
            activeRoleId: null,
            activeAIEndpointId: null
        };
        this.listeners = new Map();
    }

    /**
     * 订阅状态变化
     * @param {string} key 状态键
     * @param {Function} callback 回调函数
     */
    subscribe(key, callback) {
        if (!this.listeners.has(key)) {
            this.listeners.set(key, new Set());
        }
        this.listeners.get(key).add(callback);
        // 返回取消订阅函数
        return () => this.listeners.get(key).delete(callback);
    }

    /**
     * 更新状态并通知订阅者
     * @param {string} key 状态键
     * @param {any} value 新值
     */
    setState(key, value) {
        const oldValue = this.state[key];
        if (oldValue === value) return;
        
        this.state[key] = value;
        if (this.listeners.has(key)) {
            this.listeners.get(key).forEach(callback => callback(value, oldValue));
        }
    }

    /**
     * 获取当前状态
     * @param {string} key 状态键
     * @returns {any}
     */
    getState(key) {
        return this.state[key];
    }

    // --- 快捷方法 ---

    get tabs() { return this.state.tabs; }
    set tabs(val) { this.setState('tabs', val); }

    get activeTabId() { return this.state.activeTabId; }
    set activeTabId(val) { this.setState('activeTabId', val); }

    get activeTab() {
        return this.state.tabs.find(t => t.id === this.state.activeTabId);
    }
}

export const store = new Store();
window.store = store; // 挂载到 window 方便调试
