/**
 * components.js - Web Components 定义
 */

class ServerCard extends HTMLElement {
    constructor() {
        super();
        this._server = null;
    }

    set server(data) {
        this._server = data;
        this.render();
    }

    get server() {
        return this._server;
    }

    connectedCallback() {
        if (this._server) {
            this.render();
        }
    }

    render() {
        const s = this._server;
        if (!s) return;

        // 获取图标 (复用 app.js 的逻辑，通过全局获取)
        const icon = globalThis.getServerIcon ? globalThis.getServerIcon(s.device_type) : 'fas fa-server';
        const safeName = s.name.replace(/'/g, "\\'");

        this.className = 'server-card';
        const escapedName = (s.name || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        this.innerHTML = `
            <div class="server-card-icon"><i class="${icon}"></i></div>
            <div class="server-card-info">
                <h4 title="${escapedName}">${escapedName}</h4>
                <p>${s.host}:${s.port}</p>
            </div>
            <div class="server-card-actions">
                <button class="btn-icon" title="清除状态记录" onclick="event.stopPropagation(); globalThis.clearServerStats(${s.id}, '${safeName}')">
                    <i class="fas fa-eraser"></i>
                </button>
                <button class="btn-icon" title="编辑" onclick="event.stopPropagation(); globalThis.showEditServerModal(${s.id})">
                    <i class="fas fa-edit"></i>
                </button>
                <button class="btn-icon" title="删除" onclick="event.stopPropagation(); globalThis.deleteServer(${s.id}, '${safeName}')">
                    <i class="fas fa-trash-alt"></i>
                </button>
            </div>
        `;
        
        this.onclick = () => {
            if (globalThis.connectToServer) globalThis.connectToServer(s);
        };
    }
}

// 注册组件
customElements.define('server-card', ServerCard);
