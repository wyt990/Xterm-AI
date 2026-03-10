/**
 * 文本编辑器模块 - 支持目录树 + 多标签
 */
import { api } from './api.js';
import { store } from './store.js';
import { notify, storage, showModal, closeModal } from './utils.js';

// ===================== 状态 =====================
let aceEditor = null;
let editorModal = null;
let statusPath = null;
let statusInfo = null;
let modeDisplay = null;

// 多标签：每个 tab 有自己的 Ace EditSession
const openTabs = [];
let activeEditorTabId = null;

// 目录树状态
let treeServerId = null;
let treeRootPath = null;
let treeExpanded = new Set();   // 已展开的目录路径集合
let treeCache = {};             // dirPath -> items[]
let ctxTarget = null;           // 右键菜单目标节点 { path, name, type }

// ===================== 初始化 =====================
export function initEditorModule() {
    editorModal  = document.getElementById('editor-modal');
    statusPath   = document.getElementById('editor-status-path');
    statusInfo   = document.getElementById('editor-status-info');
    modeDisplay  = document.getElementById('editor-mode-display');

    ace.config.set('basePath', '/static/lib/ace');
    aceEditor = ace.edit('ace-editor');

    const savedTheme = storage.get('editor_theme', 'monokai');
    aceEditor.setTheme(`ace/theme/${savedTheme}`);
    document.getElementById('editor-theme-select').value = savedTheme;

    aceEditor.setOptions({
        enableBasicAutocompletion: true,
        enableLiveAutocompletion: true,
        showPrintMargin: false,
        useSoftTabs: true,
        tabSize: 4,
        wrap: storage.get('editor_wrap', false)
    });

    aceEditor.selection.on('changeCursor', () => {
        const pos = aceEditor.getCursorPosition();
        if (statusInfo) statusInfo.innerText = `Ln: ${pos.row + 1}, Col: ${pos.column + 1}`;
    });

    aceEditor.commands.addCommand({
        name: 'save',
        bindKey: { win: 'Ctrl-S', mac: 'Command-S' },
        exec: saveEditorContent
    });

    document.getElementById('editor-jump-form').onsubmit = (e) => {
        e.preventDefault();
        const line = parseInt(document.getElementById('editor-jump-line').value);
        if (line > 0) { aceEditor.gotoLine(line); closeModal('editor-jump-modal'); }
    };

    // 点击空白处关闭右键菜单
    document.addEventListener('click', () => {
        const m = document.getElementById('editor-context-menu');
        if (m) m.style.display = 'none';
    });

    initTreeResizer();

    // 暴露全局函数
    globalThis.openFileInEditor      = openFileInEditor;
    globalThis.saveEditorContent     = saveEditorContent;
    globalThis.saveAllEditorTabs     = saveAllEditorTabs;
    globalThis.refreshEditorContent  = refreshEditorContent;
    globalThis.closeEditorModal      = closeEditorModal;
    globalThis.changeEditorTheme     = changeEditorTheme;
    globalThis.changeEditorFontSize  = changeEditorFontSize;
    globalThis.changeEditorTabSize   = changeEditorTabSize;
    globalThis.toggleEditorSettingsMenu = toggleEditorSettingsMenu;
    globalThis.toggleEditorOption    = toggleEditorOption;
    globalThis.showJumpLine          = () => showModal('editor-jump-modal');
    globalThis.showShortcuts         = () => showModal('editor-shortcuts-modal');
    globalThis.toggleEditorSearch    = (m) => aceEditor.execCommand(m === 'find' ? 'find' : 'replace');
    globalThis.switchEditorTab       = switchEditorTab;
    globalThis.closeEditorTab        = closeEditorTab;
    globalThis.toggleTreeDir         = toggleTreeDir;
    globalThis.editorNavigateTo      = editorNavigateTo;
    globalThis.editorNewFile         = editorNewFile;
    globalThis.editorNewDir          = editorNewDir;
    globalThis.refreshFiletree       = refreshFiletree;
    globalThis.showEditorContextMenu = showEditorContextMenu;
    globalThis.editorContextAction   = editorContextAction;
}

// ===================== 打开文件（从 SFTP 面板调用）=====================
export async function openFileInEditor(file) {
    if (!store.activeTabId) return;
    const sshTab = globalThis.getTab(store.activeTabId);
    const serverId = sshTab.config.id;
    const base = sshTab.sftpCurrentPath || '/';
    const filePath = base === '/' ? `/${file.name}` : `${base}/${file.name}`;

    // 已打开则直接激活
    const exist = openTabs.find(t => t.path === filePath);
    if (exist) {
        showEditorModal();
        switchEditorTab(exist.id);
        return;
    }

    try {
        notify('正在读取文件...');
        const data = await api.sftpRead(serverId, filePath);

        // 先初始化/切换目录树（必须在 _addTab 之前，否则 _addTab 会提前写 treeServerId）
        if (!treeServerId || treeServerId !== serverId) {
            treeServerId = serverId;
            treeCache = {};
            treeExpanded.clear();
            treeRootPath = null;
        }
        // treeRootPath 为空，或者 SFTP 当前路径和树根路径完全不同时，重新加载
        if (!treeRootPath || (!treeCache[treeRootPath] && treeRootPath !== base)) {
            loadFiletree(base); // 异步加载，不阻塞编辑器打开
        }

        _addTab(filePath, file.name, data.content, data.readonly);
        showEditorModal();
    } catch (err) {
        notify(`读取文件失败: ${err.message}`, 'error');
    }
}

// ===================== 多标签 =====================
function _addTab(filePath, name, content, readOnly) {

    const tabId = 'etab-' + Date.now();
    const session = new ace.EditSession(content);
    const modelist = ace.require('ace/ext/modelist');
    session.setMode(modelist.getModeForPath(name).mode);
    session.on('change', () => _markModified(tabId));

    openTabs.push({ id: tabId, path: filePath, name, session, modified: false, readOnly: !!readOnly });
    renderTabBar();
    switchEditorTab(tabId);
}

function _markModified(tabId) {
    const t = openTabs.find(t => t.id === tabId);
    if (t && !t.modified && !t.readOnly) { t.modified = true; renderTabBar(); }
}

function renderTabBar() {
    const bar = document.getElementById('editor-tab-bar');
    if (!bar) return;
    bar.innerHTML = openTabs.map(t => `
        <div class="editor-tab${t.id === activeEditorTabId ? ' active' : ''}"
             onclick="switchEditorTab('${t.id}')" title="${t.path}">
            <i class="${getFileIconClass(t.name)}"></i>
            <span class="tab-name">${t.name}${t.modified ? ' ●' : ''}</span>
            <span class="tab-close" onclick="event.stopPropagation();closeEditorTab('${t.id}')">×</span>
        </div>
    `).join('');
}

function switchEditorTab(tabId) {
    const t = openTabs.find(t => t.id === tabId);
    if (!t) return;
    activeEditorTabId = tabId;
    aceEditor.setSession(t.session);
    aceEditor.setReadOnly(t.readOnly);
    if (statusPath) statusPath.innerText = t.path;
    const mode = t.session.getMode().$id || '';
    if (modeDisplay) modeDisplay.innerText = `语言: ${mode.split('/').pop().toUpperCase()}`;
    renderTabBar();
    aceEditor.focus();
    // 高亮目录树中对应文件
    _highlightTreeItem(t.path);
}

function closeEditorTab(tabId) {
    const t = openTabs.find(t => t.id === tabId);
    if (!t) return;
    if (t.modified && !confirm(`"${t.name}" 有未保存的修改，确定关闭吗？`)) return;
    const idx = openTabs.findIndex(t => t.id === tabId);
    openTabs.splice(idx, 1);
    if (openTabs.length === 0) {
        // 最后一个标签关闭，直接退出编辑器（不再二次确认，已在上面确认过）
        treeServerId = null;
        treeRootPath = null;
        treeExpanded.clear();
        treeCache = {};
        activeEditorTabId = null;
        const bar = document.getElementById('editor-tab-bar');
        if (bar) bar.innerHTML = '';
        const body = document.getElementById('filetree-body');
        if (body) body.innerHTML = '<div class="filetree-empty"><i class="fas fa-folder-open"></i><br>打开文件后显示目录树</div>';
        const inp = document.getElementById('filetree-path-input');
        if (inp) inp.value = '';
        aceEditor.setValue('', -1);
        editorModal.style.display = 'none';
        return;
    }
    if (activeEditorTabId === tabId) {
        switchEditorTab(openTabs[Math.min(idx, openTabs.length - 1)].id);
    } else {
        renderTabBar();
    }
}

// ===================== 目录树 =====================
async function loadFiletree(path) {
    if (!treeServerId) return;
    try {
        const res = await api.sftpList(treeServerId, path || '');
        const resolved = res.path || path || '/';
        treeRootPath = resolved;
        treeCache[resolved] = res.files || [];
        treeExpanded.add(resolved);
        const inp = document.getElementById('filetree-path-input');
        if (inp) inp.value = resolved;
        renderTreeDOM();
    } catch (err) {
        notify('加载目录失败: ' + err.message, 'error');
    }
}

async function toggleTreeDir(path) {
    if (treeExpanded.has(path)) {
        treeExpanded.delete(path);
        renderTreeDOM();
    } else {
        treeExpanded.add(path);
        if (!treeCache[path]) {
            try {
                const res = await api.sftpList(treeServerId, path);
                treeCache[path] = res.files || [];
            } catch (err) {
                treeExpanded.delete(path);
                notify('展开目录失败: ' + err.message, 'error');
                return;
            }
        }
        renderTreeDOM();
    }
}

function renderTreeDOM() {
    const body = document.getElementById('filetree-body');
    if (!body) return;
    body.innerHTML = '';
    if (!treeRootPath) return;

    function renderLevel(dirPath, depth) {
        const items = treeCache[dirPath] || [];
        const sorted = [...items].sort((a, b) => {
            if (a.type === b.type) return a.name.localeCompare(b.name);
            return a.type === 'dir' ? -1 : 1;
        });
        sorted.forEach(item => {
            const ipath = dirPath === '/' ? `/${item.name}` : `${dirPath}/${item.name}`;
            const isDir = item.type === 'dir';
            const isExp = isDir && treeExpanded.has(ipath);
            const isActive = openTabs.some(t => t.id === activeEditorTabId && t.path === ipath);

            const row = document.createElement('div');
            row.className = `tree-item${isDir ? ' tree-dir' : ' tree-file'}${isActive ? ' active' : ''}`;
            row.style.paddingLeft = `${depth * 14 + 8}px`;
            row.dataset.path = ipath;
            row.dataset.type = item.type;
            row.dataset.name = item.name;

            row.innerHTML = `
                <span class="tree-toggle">${isDir ? `<i class="fas fa-chevron-${isExp ? 'down' : 'right'}"></i>` : ''}</span>
                <i class="${isDir ? `fas fa-folder${isExp ? '-open' : ''} icon-dir` : `${getFileIconClass(item.name)} icon-file`} tree-item-icon"></i>
                <span class="tree-item-name" title="${item.name}">${item.name}</span>
            `;

            row.onclick = (e) => {
                e.stopPropagation();
                isDir ? toggleTreeDir(ipath) : openFileFromTree(ipath, item.name);
            };
            row.oncontextmenu = (e) => {
                e.preventDefault();
                e.stopPropagation();
                showEditorContextMenu(e, { path: ipath, name: item.name, type: item.type });
            };

            body.appendChild(row);
            if (isDir && isExp) renderLevel(ipath, depth + 1);
        });
    }
    renderLevel(treeRootPath, 0);
}

function _highlightTreeItem(filePath) {
    document.querySelectorAll('#filetree-body .tree-item').forEach(el => {
        el.classList.toggle('active', el.dataset.path === filePath);
    });
}

async function openFileFromTree(filePath, name) {
    if (!treeServerId) return;
    const exist = openTabs.find(t => t.path === filePath);
    if (exist) { switchEditorTab(exist.id); return; }
    try {
        notify('正在读取文件...');
        const data = await api.sftpRead(treeServerId, filePath);
        _addTab(filePath, name, data.content, data.readonly);
        if (data.readonly) notify('文件较大，已进入只读模式', 'warning');
    } catch (err) {
        notify(`读取文件失败: ${err.message}`, 'error');
    }
}

function editorNavigateTo() {
    if (!treeServerId) return;
    const val = (document.getElementById('filetree-path-input')?.value || '').trim();
    treeCache = {};
    treeExpanded.clear();
    loadFiletree(val || '/');
}

async function refreshFiletree() {
    if (!treeServerId || !treeRootPath) return;
    // 只清理已缓存路径，保留展开状态
    Object.keys(treeCache).forEach(k => { delete treeCache[k]; });
    await loadFiletree(treeRootPath);
    // 重新加载展开目录的子项
    for (const expPath of [...treeExpanded]) {
        if (expPath !== treeRootPath) {
            try {
                const r = await api.sftpList(treeServerId, expPath);
                treeCache[expPath] = r.files || [];
            } catch (_) {}
        }
    }
    renderTreeDOM();
}

// ===================== 右键菜单 =====================
function showEditorContextMenu(e, node) {
    ctxTarget = node;
    const menu = document.getElementById('editor-context-menu');
    if (!menu) return;
    // 仅文件显示"打开"
    const openItem = menu.querySelector('.ctx-open');
    if (openItem) openItem.style.display = node.type === 'file' ? '' : 'none';
    menu.style.display = 'block';
    // 防止超出视窗
    const x = Math.min(e.clientX, globalThis.innerWidth - 180);
    const y = Math.min(e.clientY, globalThis.innerHeight - 180);
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
}

async function editorContextAction(action) {
    const menu = document.getElementById('editor-context-menu');
    if (menu) menu.style.display = 'none';
    if (!ctxTarget) return;
    const { path, name, type } = ctxTarget;
    const parentPath = path.lastIndexOf('/') > 0 ? path.substring(0, path.lastIndexOf('/')) : '/';
    const inDir = type === 'dir' ? path : parentPath;  // 新建项目放在哪里

    switch (action) {
        case 'open':
            if (type === 'file') openFileFromTree(path, name);
            break;

        case 'rename': {
            const newName = prompt('请输入新名称:', name);
            if (!newName || newName === name) return;
            const newPath = parentPath === '/' ? `/${newName}` : `${parentPath}/${newName}`;
            try {
                await api.sftpRename({ server_id: treeServerId, old_path: path, new_path: newPath });
                openTabs.forEach(t => { if (t.path === path) { t.path = newPath; t.name = newName; } });
                renderTabBar();
                await _invalidateDir(parentPath);
                notify('重命名成功', 'success');
            } catch (err) { notify('重命名失败: ' + err.message, 'error'); }
            break;
        }

        case 'delete': {
            if (!confirm(`确定要删除 "${name}" 吗？此操作不可恢复！`)) return;
            try {
                await api.sftpDelete({ server_id: treeServerId, path, is_dir: type === 'dir' });
                // 关闭属于该路径的 tab
                for (let i = openTabs.length - 1; i >= 0; i--) {
                    if (openTabs[i].path === path || openTabs[i].path.startsWith(path + '/')) {
                        openTabs[i].modified = false;
                        closeEditorTab(openTabs[i].id);
                    }
                }
                await _invalidateDir(parentPath);
                notify('删除成功', 'success');
            } catch (err) { notify('删除失败: ' + err.message, 'error'); }
            break;
        }

        case 'new-file': {
            const fname = prompt('请输入新文件名:');
            if (!fname) return;
            const np = inDir === '/' ? `/${fname}` : `${inDir}/${fname}`;
            try {
                await api.sftpCreate({ server_id: treeServerId, path: np, is_dir: false });
                await _invalidateDir(inDir);
                openFileFromTree(np, fname);
                notify('文件创建成功', 'success');
            } catch (err) { notify('创建文件失败: ' + err.message, 'error'); }
            break;
        }

        case 'new-dir': {
            const dname = prompt('请输入新目录名:');
            if (!dname) return;
            const np = inDir === '/' ? `/${dname}` : `${inDir}/${dname}`;
            try {
                await api.sftpCreate({ server_id: treeServerId, path: np, is_dir: true });
                await _invalidateDir(inDir);
                notify('目录创建成功', 'success');
            } catch (err) { notify('创建目录失败: ' + err.message, 'error'); }
            break;
        }
    }
}

// 在根目录工具栏新建文件
async function editorNewFile() {
    if (!treeServerId) return;
    const fname = prompt('请输入新文件名:');
    if (!fname) return;
    const np = treeRootPath === '/' ? `/${fname}` : `${treeRootPath}/${fname}`;
    try {
        await api.sftpCreate({ server_id: treeServerId, path: np, is_dir: false });
        await _invalidateDir(treeRootPath);
        openFileFromTree(np, fname);
        notify('文件创建成功', 'success');
    } catch (err) { notify('创建文件失败: ' + err.message, 'error'); }
}

async function editorNewDir() {
    if (!treeServerId) return;
    const dname = prompt('请输入新目录名:');
    if (!dname) return;
    const np = treeRootPath === '/' ? `/${dname}` : `${treeRootPath}/${dname}`;
    try {
        await api.sftpCreate({ server_id: treeServerId, path: np, is_dir: true });
        await _invalidateDir(treeRootPath);
        notify('目录创建成功', 'success');
    } catch (err) { notify('创建目录失败: ' + err.message, 'error'); }
}

// 使指定目录缓存失效并重新加载
async function _invalidateDir(dirPath) {
    delete treeCache[dirPath];
    if (treeExpanded.has(dirPath) || dirPath === treeRootPath) {
        try {
            const r = await api.sftpList(treeServerId, dirPath);
            treeCache[dirPath] = r.files || [];
        } catch (_) {}
    }
    renderTreeDOM();
}

// ===================== 保存 =====================
async function saveEditorContent() {
    const t = openTabs.find(t => t.id === activeEditorTabId);
    if (!t || t.readOnly) return;
    try {
        await api.sftpSave({ server_id: treeServerId, path: t.path, content: t.session.getValue() });
        t.modified = false;
        renderTabBar();
        notify('保存成功', 'info');
    } catch (err) { notify(`保存失败: ${err.message}`, 'error'); }
}

async function saveAllEditorTabs() {
    const dirty = openTabs.filter(t => t.modified && !t.readOnly);
    if (!dirty.length) { notify('没有需要保存的文件', 'info'); return; }
    let ok = 0;
    for (const t of dirty) {
        try {
            await api.sftpSave({ server_id: treeServerId, path: t.path, content: t.session.getValue() });
            t.modified = false; ok++;
        } catch (err) { notify(`保存 ${t.name} 失败`, 'error'); }
    }
    renderTabBar();
    if (ok > 0) notify(`已保存 ${ok} 个文件`, 'success');
}

function refreshEditorContent() {
    const t = openTabs.find(t => t.id === activeEditorTabId);
    if (!t) return;
    if (t.modified && !confirm('刷新将丢失未保存的修改，确定吗？')) return;
    openFileFromTree(t.path, t.name);
}

// ===================== 打开/关闭模态框 =====================
function showEditorModal() {
    editorModal.style.display = 'flex';
    requestAnimationFrame(() => aceEditor.resize());
}

function closeEditorModal() {
    // 有未保存修改时二次确认
    const dirty = openTabs.filter(t => t.modified);
    if (dirty.length > 0) {
        const names = dirty.map(t => t.name).join('、');
        if (!confirm(`以下文件有未保存的修改：\n${names}\n\n确定关闭并放弃修改吗？`)) return;
    }

    // 清空所有标签
    openTabs.length = 0;
    activeEditorTabId = null;

    // 重置目录树
    treeServerId = null;
    treeRootPath = null;
    treeExpanded.clear();
    treeCache = {};

    // 清空 UI
    const bar = document.getElementById('editor-tab-bar');
    if (bar) bar.innerHTML = '';
    const body = document.getElementById('filetree-body');
    if (body) body.innerHTML = '<div class="filetree-empty"><i class="fas fa-folder-open"></i><br>打开文件后显示目录树</div>';
    const inp = document.getElementById('filetree-path-input');
    if (inp) inp.value = '';

    // 给 Ace 一个空内容，避免残留
    aceEditor.setValue('', -1);

    editorModal.style.display = 'none';
}

// ===================== 目录树调宽 =====================
function initTreeResizer() {
    const resizer = document.getElementById('editor-tree-resizer');
    const tree    = document.getElementById('editor-filetree');
    if (!resizer || !tree) return;
    let startX, startW;
    resizer.onmousedown = (e) => {
        startX = e.clientX; startW = tree.offsetWidth;
        document.onmousemove = (ev) => {
            const w = startW + (ev.clientX - startX);
            if (w >= 120 && w <= 600) { tree.style.width = `${w}px`; aceEditor.resize(); }
        };
        document.onmouseup = () => { document.onmousemove = null; document.onmouseup = null; };
        e.preventDefault();
    };
}

// ===================== 设置 =====================
function changeEditorTheme(theme) { aceEditor.setTheme(`ace/theme/${theme}`); storage.set('editor_theme', theme); }
function changeEditorFontSize(size) { aceEditor.setFontSize(size); }
function changeEditorTabSize(size) { aceEditor.session.setTabSize(parseInt(size)); }
function toggleEditorSettingsMenu() {
    const m = document.getElementById('editor-settings-menu');
    m.style.display = m.style.display === 'none' ? 'block' : 'none';
}
function toggleEditorOption(opt, val) {
    const map = { wrap: () => { aceEditor.setOption('wrap', val); storage.set('editor_wrap', val); },
                  autocomplete: () => aceEditor.setOption('enableLiveAutocompletion', val),
                  linenumbers:  () => aceEditor.setOption('showGutter', val),
                  invisible:    () => aceEditor.setOption('showInvisibles', val) };
    if (map[opt]) map[opt]();
}

// ===================== 工具函数 =====================
function getFileIconClass(filename) {
    const ext = (filename.split('.').pop() || '').toLowerCase();
    const m = {
        js:'fab fa-js', ts:'fab fa-js', jsx:'fab fa-js', tsx:'fab fa-js',
        py:'fab fa-python', php:'fab fa-php', java:'fab fa-java',
        html:'fab fa-html5', htm:'fab fa-html5', css:'fab fa-css3-alt',
        md:'fab fa-markdown',
        sh:'fas fa-terminal', bash:'fas fa-terminal',
        json:'fas fa-file-code', xml:'fas fa-file-code',
        yaml:'fas fa-file-code', yml:'fas fa-file-code',
        go:'fas fa-file-code', c:'fas fa-file-code', cpp:'fas fa-file-code', rs:'fas fa-file-code',
        zip:'fas fa-file-archive', tar:'fas fa-file-archive', gz:'fas fa-file-archive',
        jpg:'fas fa-file-image', jpeg:'fas fa-file-image', png:'fas fa-file-image',
        sql:'fas fa-database', conf:'fas fa-cog', ini:'fas fa-cog', env:'fas fa-cog',
        log:'fas fa-file-alt', txt:'fas fa-file-alt',
    };
    return m[ext] || 'fas fa-file';
}
