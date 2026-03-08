/**
 * SFTP 文件管理器模块 (增强版)
 */
import { api } from './api.js';
import { activeTabId } from './terminal.js';
import { formatSize, formatTime, notify, showModal, closeModal } from './utils.js';

let currentPath = '/';
let sftpSelectedFile = null;        // 右键菜单的单个目标文件
let sftpSelectedFiles = new Map();  // Ctrl 多选：name -> file 对象
let dragEnterCounter = 0;           // dragenter/dragleave 计数器，防止子元素误触发

let filesContainer = null;
let pathInput = null;
let fileListElement = null;
let contextMenu = null;

export function initSFTPModule() {
    // 延迟获取 DOM，确保组件已加载
    filesContainer = document.getElementById('sftp-files-container');
    pathInput = document.getElementById('sftp-current-path');
    fileListElement = document.getElementById('sftp-file-list');
    contextMenu = document.getElementById('sftp-context-menu');

    if (!fileListElement) return;

    // 暴露全局函数给 index.html
    window.sftpGoBack = () => {
        if (!currentPath || currentPath === '/') return;
        const parts = currentPath.split('/').filter(p => p);
        parts.pop();
        loadFiles('/' + parts.join('/') || '/');
    };
    window.sftpRefresh = () => loadFiles(currentPath);
    window.showUploadModal = () => document.getElementById('sftp-upload-input').click();
    window.handleSftpUpload = (e) => {
        // 先将 FileList 转成普通数组，再重置 input value
        // 避免部分浏览器在 value='' 后 FileList 引用被清空导致只上传第一个文件
        const files = Array.from(e.target.files);
        e.target.value = '';
        if (files.length > 0) uploadFiles(files);
    };
    window.loadSftpFiles = (path) => loadFiles(path);

    // 绑定文件列表容器右键（空白处）：在 initSFTPModule 中一次性绑定，不依赖 renderFileList
    fileListElement.addEventListener('contextmenu', (e) => {
        // 仅当点击容器本身或 filesContainer 时（点到文件项会被 item.oncontextmenu 的 stopPropagation 拦截）
        if (e.target === fileListElement || e.target === filesContainer || e.target.closest('#sftp-files-container') === null) {
            e.preventDefault();
            sftpSelectedFile = null;
            showContextMenu(e, false);
        }
    });
    window.addEventListener('tabSwitched', (e) => {
        const tab = e.detail.tab;
        // 网络设备不支持 SFTP
        const dtype = (tab.config.device_type_value || tab.config.device_type || '').toLowerCase();
        const isNetworkDevice = ['h3c', 'huawei', 'cisco', 'ruijie', 'network'].includes(dtype);
        
        if (isNetworkDevice) {
            if (filesContainer) filesContainer.innerHTML = '<div class="sftp-empty" style="padding: 20px; text-align: center; color: #888;">当前设备不支持 SFTP</div>';
            if (pathInput) pathInput.value = '';
            currentPath = '/';
            return;
        }

        // 空字符串也合法（后端解析为家目录），不能用 || '/' 覆盖掉
        currentPath = tab.sftpCurrentPath ?? '';
        loadFiles(currentPath);
    });

    // 2. 初始化右键菜单全局点击关闭
    window.addEventListener('click', () => {
        if (contextMenu) contextMenu.style.display = 'none';
    });

    // 3. 初始化拖拽上传
    initSftpDragAndDrop();

    // 4. 绑定表单提交事件
    initSftpForms();

    // 5. 暴露全局函数 (兼容 HTML 调用)
    window.sftpAction = sftpAction;
    window.updateChmodValue = updateChmodValue;
    window.loadSftpFiles = loadFiles;
}

export async function loadFiles(path) {
    if (!activeTabId) {
        notify('请先连接到一台服务器', 'warning');
        return;
    }
    const tab = window.getTab(activeTabId);
    if (!tab) return;

    // 记录发起请求时的 tabId
    const requestId = activeTabId;

    try {
        const res = await api.sftpList(tab.config.id, path);
        
        // 如果请求回来时，标签已经切换了，则忽略结果
        if (activeTabId !== requestId) {
            console.log("SFTP: 标签已切换，忽略上个标签的列表结果");
            return;
        }

        const files = res.files || [];
        
        if (!Array.isArray(files)) {
            console.error("SFTP 列表返回异常:", res);
            return;
        }
        // 使用后端返回的真实路径（后端会将空路径解析为用户家目录）
        const resolvedPath = res.path || path || '/';
        currentPath = resolvedPath;
        tab.sftpCurrentPath = resolvedPath;
        if (pathInput) pathInput.value = resolvedPath;
        // 进入新目录时清空多选状态
        sftpSelectedFiles.clear();
        sftpSelectedFile = null;
        renderFileList(files);
    } catch (err) {
        // 如果请求失败时，标签已经切换了，静默失败（避免前一个标签的报错干扰当前标签）
        if (activeTabId !== requestId) return;
        console.error("加载文件失败:", err);
    }
}

function renderFileList(files) {
    if (!filesContainer) return;
    filesContainer.innerHTML = '';
    sftpSelectedFiles.clear();

    files.forEach(file => {
        const item = document.createElement('div');
        item.className = 'sftp-file-item';
        item.dataset.name = file.name;

        // 单击：Ctrl 多选 / 普通单选
        item.onclick = (e) => {
            if (e.ctrlKey || e.metaKey) {
                // Ctrl+Click：切换该文件的选中状态
                if (sftpSelectedFiles.has(file.name)) {
                    sftpSelectedFiles.delete(file.name);
                    item.classList.remove('selected');
                } else {
                    sftpSelectedFiles.set(file.name, file);
                    item.classList.add('selected');
                }
                // 多选时 sftpSelectedFile 指向最后操作的文件
                sftpSelectedFile = sftpSelectedFiles.size === 1
                    ? [...sftpSelectedFiles.values()][0] : null;
            } else {
                // 普通单击：清空多选，只选当前
                clearSelection();
                sftpSelectedFiles.set(file.name, file);
                sftpSelectedFile = file;
                item.classList.add('selected');
            }
        };

        // 双击：进入目录 / 打开编辑器
        item.ondblclick = () => {
            clearSelection();
            if (file.is_dir) {
                loadFiles(currentPath === '/' ? `/${file.name}` : `${currentPath}/${file.name}`);
            } else {
                if (window.openFileInEditor) window.openFileInEditor(file);
            }
        };

        // 右键菜单
        item.oncontextmenu = (e) => {
            e.preventDefault();
            e.stopPropagation();
            // 右键点击未选中的文件：先切为单选该文件
            if (!sftpSelectedFiles.has(file.name)) {
                clearSelection();
                sftpSelectedFiles.set(file.name, file);
                item.classList.add('selected');
            }
            sftpSelectedFile = file;
            showContextMenu(e, true);
        };

        const icon = getFileIcon(file);
        item.innerHTML = `
            <span class="file-icon"><i class="fas ${icon}"></i></span>
            <span class="file-name">${file.name}</span>
            <span class="file-size">${file.is_dir ? '-' : formatSize(file.size)}</span>
            <span class="file-perms">${file.perms || file.mode || ''}</span>
            <span class="file-time">${formatTime(file.mtime)}</span>
        `;
        filesContainer.appendChild(item);
    });

    // 点击空白区域清除选中
    filesContainer.onclick = (e) => {
        if (e.target === filesContainer) clearSelection();
    };
}

function clearSelection() {
    sftpSelectedFiles.clear();
    sftpSelectedFile = null;
    document.querySelectorAll('.sftp-file-item.selected').forEach(el => el.classList.remove('selected'));
}

function getFileIcon(file) {
    if (file.is_dir) return 'fa-folder';
    const ext = file.name.split('.').pop().toLowerCase();
    const icons = {
        // 压缩包
        'zip': 'fa-file-archive', 'rar': 'fa-file-archive', 'tar': 'fa-file-archive',
        'gz': 'fa-file-archive', '7z': 'fa-file-archive', 'bz2': 'fa-file-archive', 'xz': 'fa-file-archive',
        // 代码
        'py': 'fa-file-code', 'js': 'fa-file-code', 'ts': 'fa-file-code',
        'html': 'fa-file-code', 'htm': 'fa-file-code', 'css': 'fa-file-code',
        'json': 'fa-file-code', 'xml': 'fa-file-code', 'yaml': 'fa-file-code', 'yml': 'fa-file-code',
        'sh': 'fa-file-code', 'bash': 'fa-file-code', 'php': 'fa-file-code',
        'c': 'fa-file-code', 'cpp': 'fa-file-code', 'h': 'fa-file-code',
        'java': 'fa-file-code', 'go': 'fa-file-code', 'rs': 'fa-file-code', 'rb': 'fa-file-code',
        'sql': 'fa-file-code', 'toml': 'fa-file-code', 'ini': 'fa-file-code', 'conf': 'fa-file-code',
        // 图片
        'jpg': 'fa-file-image', 'jpeg': 'fa-file-image', 'png': 'fa-file-image',
        'gif': 'fa-file-image', 'svg': 'fa-file-image', 'webp': 'fa-file-image',
        'bmp': 'fa-file-image', 'ico': 'fa-file-image',
        // 文档
        'log': 'fa-file-alt', 'txt': 'fa-file-alt', 'md': 'fa-file-alt', 'rst': 'fa-file-alt',
        // PDF
        'pdf': 'fa-file-pdf',
        // Office
        'xls': 'fa-file-excel', 'xlsx': 'fa-file-excel', 'csv': 'fa-file-excel',
        'doc': 'fa-file-word', 'docx': 'fa-file-word',
        'ppt': 'fa-file-powerpoint', 'pptx': 'fa-file-powerpoint',
        // 视频
        'mp4': 'fa-file-video', 'avi': 'fa-file-video', 'mkv': 'fa-file-video',
        'mov': 'fa-file-video', 'wmv': 'fa-file-video', 'flv': 'fa-file-video',
        // 音频
        'mp3': 'fa-file-audio', 'wav': 'fa-file-audio', 'flac': 'fa-file-audio',
        'ogg': 'fa-file-audio', 'm4a': 'fa-file-audio',
    };
    return icons[ext] || 'fa-file';
}

function showContextMenu(e, isFile) {
    if (!contextMenu) return;
    contextMenu.style.display = 'block';
    
    const fileOps = document.getElementById('sftp-file-ops');
    const deleteOp = document.getElementById('sftp-delete-op');
    const multiCount = sftpSelectedFiles.size;

    // 多选时隐藏单文件操作（重命名、权限等），只保留删除
    const isSingleFile = isFile && multiCount <= 1;
    if (fileOps) fileOps.style.display = isSingleFile ? 'block' : 'none';
    if (deleteOp) {
        deleteOp.style.display = isFile ? 'flex' : 'none';
        if (multiCount > 1) {
            deleteOp.querySelector('i').className = 'fas fa-trash-alt';
            deleteOp.childNodes[deleteOp.childNodes.length - 1].textContent = ` 删除 ${multiCount} 项`;
        } else {
            deleteOp.childNodes[deleteOp.childNodes.length - 1].textContent = ' 删除';
        }
    }

    let x = e.clientX;
    let y = e.clientY;
    const menuWidth = 160;
    const menuHeight = isFile ? 220 : 80;

    if (x + menuWidth > window.innerWidth) x -= menuWidth;
    if (y + menuHeight > window.innerHeight) y -= menuHeight;

    contextMenu.style.left = `${x}px`;
    contextMenu.style.top = `${y}px`;
}

async function sftpAction(type) {
    if (!activeTabId) return;
    const tab = window.getTab(activeTabId);
    const serverId = tab.config.id;
    const filePath = sftpSelectedFile ? (currentPath === '/' ? `/${sftpSelectedFile.name}` : `${currentPath}/${sftpSelectedFile.name}`) : currentPath;

    try {
        switch (type) {
            case 'download':
                window.location.href = `/api/sftp/download?server_id=${serverId}&path=${encodeURIComponent(filePath)}`;
                break;
            case 'rename':
                document.getElementById('sftp-rename-old-path').value = filePath;
                document.getElementById('sftp-new-name').value = sftpSelectedFile.name;
                showModal('sftp-rename-modal');
                break;
            case 'chmod':
                document.getElementById('sftp-chmod-path').value = filePath;
                document.getElementById('sftp-chmod-filename').innerText = sftpSelectedFile.name;
                document.getElementById('sftp-new-mode').value = sftpSelectedFile.perms_octal || '0644';
                setChmodCheckboxes(sftpSelectedFile.perms_octal || '0644');
                showModal('sftp-chmod-modal');
                break;
            case 'delete': {
                const targets = sftpSelectedFiles.size > 0
                    ? [...sftpSelectedFiles.values()]
                    : (sftpSelectedFile ? [sftpSelectedFile] : []);
                if (targets.length === 0) break;

                const confirmMsg = targets.length === 1
                    ? `确定要删除 ${targets[0].is_dir ? '目录' : '文件'} "${targets[0].name}" 吗？`
                    : `确定要删除以下 ${targets.length} 个项目吗？\n${targets.map(f => f.name).join('\n')}`;

                if (confirm(confirmMsg)) {
                    for (const f of targets) {
                        const fp = currentPath === '/' ? `/${f.name}` : `${currentPath}/${f.name}`;
                        await api.sftpDelete({ server_id: serverId, path: fp, is_dir: f.is_dir });
                    }
                    notify(`已删除 ${targets.length} 个项目`, 'success');
                    sftpSelectedFiles.clear();
                    sftpSelectedFile = null;
                    refresh();
                }
                break;
            }
            case 'newfile':
            case 'newdir':
                document.getElementById('sftp-create-type').value = type === 'newfile' ? 'file' : 'dir';
                document.getElementById('sftp-create-title').innerText = type === 'newfile' ? '新建文件' : '新建目录';
                document.getElementById('sftp-create-name').value = '';
                showModal('sftp-create-modal');
                break;
            case 'edit':
                if (window.openFileInEditor) window.openFileInEditor(sftpSelectedFile);
                break;
        }
    } catch (err) {
        notify(`操作失败: ${err.message}`, 'error');
    }
}

function initSftpDragAndDrop() {
    if (!fileListElement) return;

    const sftpContainer = fileListElement.closest('.sftp-container');

    const setDragActive = (active) => {
        fileListElement.classList.toggle('drag-over', active);
        if (sftpContainer) sftpContainer.classList.toggle('drag-over-active', active);
    };

    fileListElement.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragEnterCounter++;
        setDragActive(true);
    });

    fileListElement.addEventListener('dragleave', () => {
        dragEnterCounter--;
        if (dragEnterCounter <= 0) {
            dragEnterCounter = 0;
            setDragActive(false);
        }
    });

    fileListElement.addEventListener('dragover', (e) => { e.preventDefault(); });

    fileListElement.addEventListener('drop', (e) => {
        e.preventDefault();
        dragEnterCounter = 0;
        setDragActive(false);
        const files = e.dataTransfer.files;
        if (files.length > 0) uploadFiles(Array.from(files));
    });
}

async function uploadFiles(files) {
    if (!activeTabId) return;
    const tab = window.getTab(activeTabId);
    if (!tab) return;

    notify(`正在上传 ${files.length} 个文件...`);
    let successCount = 0;
    let failCount = 0;

    for (let i = 0; i < files.length; i++) {
        const formData = new FormData();
        formData.append('server_id', tab.config.id);
        // 后端字段名是 path（非 remote_path），file（非 files，且单次单个）
        formData.append('path', currentPath);
        formData.append('file', files[i]);

        try {
            const response = await fetch('/api/sftp/upload', { method: 'POST', body: formData });
            if (response.ok) {
                successCount++;
            } else {
                const data = await response.json();
                console.error(`上传 ${files[i].name} 失败:`, data.detail);
                failCount++;
            }
        } catch (err) {
            console.error(`上传 ${files[i].name} 异常:`, err);
            failCount++;
        }
    }

    if (failCount === 0) {
        notify(`上传成功，共 ${successCount} 个文件`, 'info');
    } else {
        notify(`上传完成：成功 ${successCount}，失败 ${failCount}`, 'error');
    }
    refresh();
}

function setChmodCheckboxes(octal) {
    const mode = parseInt(octal, 8);
    const roles = ['owner', 'group', 'others'];
    const types = ['read', 'write', 'execute'];
    const masks = [4, 2, 1];

    roles.slice().reverse().forEach((role, i) => {
        const val = (mode >> (i * 3)) & 7;
        types.forEach((type, j) => {
            const cb = document.querySelector(`input[data-role="${role}"][data-type="${type}"]`);
            if (cb) cb.checked = !!(val & masks[j]);
        });
    });
}

function updateChmodValue() {
    let owner = 0, group = 0, others = 0;
    const getVal = (role) => {
        let v = 0;
        if (document.querySelector(`input[data-role="${role}"][data-type="read"]`).checked) v += 4;
        if (document.querySelector(`input[data-role="${role}"][data-type="write"]`).checked) v += 2;
        if (document.querySelector(`input[data-role="${role}"][data-type="execute"]`).checked) v += 1;
        return v;
    };
    owner = getVal('owner');
    group = getVal('group');
    others = getVal('others');
    document.getElementById('sftp-new-mode').value = `0${owner}${group}${others}`;
}

function initSftpForms() {
    const renameForm = document.getElementById('sftp-rename-form');
    if (renameForm) {
        renameForm.onsubmit = async (e) => {
            e.preventDefault();
            const tab = window.getTab(activeTabId);
            const oldPath = document.getElementById('sftp-rename-old-path').value;
            const newName = document.getElementById('sftp-new-name').value;
            const newPath = oldPath.substring(0, oldPath.lastIndexOf('/') + 1) + newName;
            await api.sftpRename({ server_id: tab.config.id, old_path: oldPath, new_path: newPath });
            closeModal('sftp-rename-modal');
            refresh();
        };
    }

    const chmodForm = document.getElementById('sftp-chmod-form');
    if (chmodForm) {
        chmodForm.onsubmit = async (e) => {
            e.preventDefault();
            const tab = window.getTab(activeTabId);
            const path = document.getElementById('sftp-chmod-path').value;
            const mode = document.getElementById('sftp-new-mode').value;
            await api.sftpChmod({ server_id: tab.config.id, path, mode });
            closeModal('sftp-chmod-modal');
            refresh();
        };
    }

    const createForm = document.getElementById('sftp-create-form');
    if (createForm) {
        createForm.onsubmit = async (e) => {
            e.preventDefault();
            const tab = window.getTab(activeTabId);
            const type = document.getElementById('sftp-create-type').value;
            const name = document.getElementById('sftp-create-name').value;
            const path = currentPath === '/' ? `/${name}` : `${currentPath}/${name}`;
            await api.sftpCreate({ server_id: tab.config.id, path, type });
            closeModal('sftp-create-modal');
            refresh();
        };
    }
}

export function goBack() {
    if (currentPath === '/') return;
    const parts = currentPath.split('/').filter(p => p);
    parts.pop();
    loadFiles('/' + parts.join('/'));
}

export function refresh() {
    loadFiles(currentPath);
}
