/**
 * 文本编辑器模块 (Ace Editor 封装)
 */
import { api } from './api.js';
import { activeTabId } from './terminal.js';
import { notify, storage, showModal, closeModal } from './utils.js';

let editor = null;
let currentEditingFile = null;

// DOM 引用延迟到 init 时获取（此时 loadComponents 已完成）
let editorModal = null;
let statusPath = null;
let statusInfo = null;
let modeDisplay = null;

export function initEditorModule() {
    editorModal = document.getElementById('editor-modal');
    statusPath  = document.getElementById('editor-status-path');
    statusInfo  = document.getElementById('editor-status-info');
    modeDisplay = document.getElementById('editor-mode-display');

    // 1. 初始化 Ace Editor
    ace.config.set('basePath', '/static/lib/ace');
    editor = ace.edit("ace-editor");
    
    // 加载用户保存的主题
    const savedTheme = storage.get('editor_theme', 'monokai');
    editor.setTheme(`ace/theme/${savedTheme}`);
    document.getElementById('editor-theme-select').value = savedTheme;

    // 默认配置
    editor.setOptions({
        enableBasicAutocompletion: true,
        enableLiveAutocompletion: true,
        showPrintMargin: false,
        useSoftTabs: true,
        tabSize: 4,
        wrap: storage.get('editor_wrap', false)
    });

    // 监听光标移动更新底部状态栏
    editor.selection.on('changeCursor', () => {
        const pos = editor.getCursorPosition();
        statusInfo.innerText = `Ln: ${pos.row + 1}, Col: ${pos.column + 1}`;
    });

    // 绑定快捷键：Ctrl+S 保存
    editor.commands.addCommand({
        name: 'save',
        bindKey: { win: 'Ctrl-S', mac: 'Command-S' },
        exec: saveEditorContent
    });

    // 绑定表单提交：跳转行
    document.getElementById('editor-jump-form').onsubmit = (e) => {
        e.preventDefault();
        const line = parseInt(document.getElementById('editor-jump-line').value);
        if (line > 0) {
            editor.gotoLine(line);
            closeModal('editor-jump-modal');
        }
    };

    // 暴露全局函数 (兼容 HTML 调用)
    window.openFileInEditor = openFileInEditor;
    window.saveEditorContent = saveEditorContent;
    window.refreshEditorContent = refreshEditorContent;
    window.closeEditorModal = closeEditorModal;
    window.changeEditorTheme = changeEditorTheme;
    window.changeEditorFontSize = changeEditorFontSize;
    window.changeEditorTabSize = changeEditorTabSize;
    window.toggleEditorSettingsMenu = toggleEditorSettingsMenu;
    window.toggleEditorOption = toggleEditorOption;
    window.showJumpLine = () => showModal('editor-jump-modal');
    window.showShortcuts = () => showModal('editor-shortcuts-modal');
    window.toggleEditorSearch = (mode) => {
        // Ace 自带搜索框逻辑
        editor.execCommand(mode === 'find' ? 'find' : 'replace');
    };
}

// 核心：打开文件
export async function openFileInEditor(file) {
    if (!activeTabId) return;
    const tab = window.getTab(activeTabId);
    const serverId = tab.config.id;
    const base = tab.sftpCurrentPath || '/';
    const filePath = base === '/' ? `/${file.name}` : `${base}/${file.name}`;

    try {
        notify("正在读取文件...");
        const data = await api.sftpRead(serverId, filePath);
        
        currentEditingFile = { ...file, path: filePath };
        editor.setValue(data.content, -1);
        editor.setReadOnly(data.readonly);
        
        // 自动识别语言
        const modelist = ace.require("ace/ext/modelist");
        const mode = modelist.getModeForPath(file.name).mode;
        editor.session.setMode(mode);
        
        // 更新 UI
        document.getElementById('editor-filename').innerText = file.name;
        if (statusPath) statusPath.innerText = filePath;
        if (modeDisplay) modeDisplay.innerText = `语言: ${mode.split('/').pop().toUpperCase()}`;
        
        if (data.readonly) notify("文件较大，已进入只读模式", 'warning');

        editorModal.style.display = 'flex';
        requestAnimationFrame(() => editor.resize());
    } catch (err) {
        notify(`读取文件失败: ${err.message}`, 'error');
    }
}

// 保存内容
async function saveEditorContent() {
    if (!currentEditingFile || editor.getReadOnly()) return;
    
    const tab = window.getTab(activeTabId);
    try {
        await api.sftpSave({
            server_id: tab.config.id,
            path: currentEditingFile.path,
            content: editor.getValue()
        });
        notify("保存成功", 'info');
    } catch (err) {
        notify(`保存失败: ${err.message}`, 'error');
    }
}

// 刷新内容
function refreshEditorContent() {
    if (confirm("刷新将丢失未保存的修改，确定吗？")) {
        openFileInEditor(currentEditingFile);
    }
}

// 关闭窗口
function closeEditorModal() {
    editorModal.style.display = 'none';
}

// --- 编辑器设置 ---
function changeEditorTheme(theme) {
    editor.setTheme(`ace/theme/${theme}`);
    storage.set('editor_theme', theme);
}

function changeEditorFontSize(size) {
    editor.setFontSize(size);
}

function changeEditorTabSize(size) {
    editor.session.setTabSize(parseInt(size));
}

function toggleEditorSettingsMenu() {
    const menu = document.getElementById('editor-settings-menu');
    menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

function toggleEditorOption(option, checked) {
    switch (option) {
        case 'wrap':
            editor.setOption('wrap', checked);
            storage.set('editor_wrap', checked);
            break;
        case 'autocomplete':
            editor.setOption('enableLiveAutocompletion', checked);
            break;
        case 'linenumbers':
            editor.setOption('showGutter', checked);
            break;
        case 'invisible':
            editor.setOption('showInvisibles', checked);
            break;
    }
}
