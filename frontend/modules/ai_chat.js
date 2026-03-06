/**
 * AI 助手与对话管理模块 (增强版)
 */
import { activeTabId } from './terminal.js';
import { api } from './api.js';
import { notify } from './utils.js';

let aiSocket = null;
let isAiProcessing = false;
const aiMessages = document.getElementById('ai-messages');
const aiInput = document.getElementById('ai-input');
const sendBtn = document.getElementById('send-btn');

export function initAIModule() {
    // 绑定发送按钮
    sendBtn.onclick = handleAISend;
    aiInput.onkeydown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleAISend();
        }
    };

    // 监听终端输出捕获完成事件，自动发给 AI 分析
    window.addEventListener('captureReady', (e) => {
        const { tabId, output } = e.detail;
        sendCaptureToAI(tabId, output);
    });

    // 所有连接关闭后，清空 AI 对话面板并停止正在进行的流
    window.addEventListener('allTabsClosed', () => {
        stopAI();
        aiMessages.innerHTML = '<div class="message system" style="text-align:center;color:#666;padding:20px;">暂无活跃连接，请先连接服务器</div>';
    });

    // 初始化时监听标签切换，刷新消息列表
    window.addEventListener('tabSwitched', (e) => {
        const tab = e.detail.tab;
        aiMessages.innerHTML = '';
        tab.chatHistory.forEach(msg => {
            const div = createMessageDiv(msg.role);
            if (msg.role === 'assistant') {
                processAIResponseForCommands(msg.content, div, tab);
            } else {
                div.textContent = msg.content;
            }
        });
        aiMessages.scrollTop = aiMessages.scrollHeight;
    });

    // 暴露清空对话给全局
    window.clearChat = clearChat;
}

// 核心功能：处理发送消息
async function handleAISend() {
    if (isAiProcessing || !aiInput.value.trim() || !activeTabId) return;

    const prompt = aiInput.value.trim();
    aiInput.value = '';
    
    const tab = window.getTab(activeTabId); 
    if (!tab) return;

    appendMessage('user', prompt, tab);
    startAIStream(prompt, tab);
}

// 核心：建立 AI WebSocket 流，messages 为完整消息数组（已包含本次用户消息）
function openAISocket(messages, tab) {
    if (aiSocket) {
        aiSocket.onclose = null;  // 防止旧 socket 的 onclose 干扰新一轮的状态
        aiSocket.close();
    }

    isAiProcessing = true;
    sendBtn.classList.add('processing');
    sendBtn.innerHTML = '<i class="fas fa-stop"></i>';
    sendBtn.onclick = stopAI;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const mode = document.getElementById('ai-mode-select').value;
    const wsUrl = `${protocol}//${window.location.host}/ws/ai?mode=${mode}`;

    aiSocket = new WebSocket(wsUrl);
    const currentAiMsgDiv = createMessageDiv('ai');
    let fullResponse = '';

    aiSocket.onopen = () => {
        aiSocket.send(JSON.stringify({ mode, messages }));
    };

    // 后端协议：发送原始文本片段，以 [DONE] 结束，错误以 [AI Error: ...] 开头
    aiSocket.onmessage = (event) => {
        const raw = event.data;
        if (raw === '[DONE]') {
            finishAIProcessing(fullResponse, currentAiMsgDiv, tab);
            return;
        }
        if (raw.startsWith('[AI Error:')) {
            fullResponse += `\n${raw}`;
            currentAiMsgDiv.innerText = fullResponse;
            finishAIProcessing(fullResponse, currentAiMsgDiv, tab);
            return;
        }
        fullResponse += raw;
        currentAiMsgDiv.innerText = fullResponse;
        aiMessages.scrollTop = aiMessages.scrollHeight;
    };

    aiSocket.onclose = () => {
        if (isAiProcessing) finishAIProcessing(fullResponse, currentAiMsgDiv, tab);
    };
}

function startAIStream(prompt, tab) {
    // appendMessage 已将用户消息推入 chatHistory，直接发送完整历史
    openAISocket(tab.chatHistory, tab);
}

// 捕获终端输出后自动提交给 AI 分析
function sendCaptureToAI(tabId, output) {
    const tab = window.getTab(tabId);
    if (!tab || isAiProcessing) return;

    const feedback = `命令执行结果如下：\n\`\`\`\n${output}\n\`\`\`\n请分析结果并给出下一步建议。`;

    // 在聊天区显示"结果已同步"提示
    const sysDiv = createMessageDiv('system');
    sysDiv.textContent = '结果已同步，AI 正在分析...';
    aiMessages.scrollTop = aiMessages.scrollHeight;

    // 将 feedback 加入历史（作为 user 消息），然后发送给 AI
    tab.chatHistory.push({ role: 'user', content: feedback });
    openAISocket(tab.chatHistory, tab);
}

function stopAI() {
    if (aiSocket) {
        aiSocket.onclose = null;  // 避免触发兜底处理
        aiSocket.close();
    }
    isAiProcessing = false;
    sendBtn.classList.remove('processing');
    sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i>';
    sendBtn.onclick = handleAISend;
}

function finishAIProcessing(fullResponse, msgDiv, tab) {
    if (!isAiProcessing) return;   // 防止 [DONE] 和 onclose 双重触发
    isAiProcessing = false;
    sendBtn.classList.remove('processing');
    sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i>';
    sendBtn.onclick = handleAISend;

    if (fullResponse) {
        // 渲染（Markdown + 命令卡片）
        processAIResponseForCommands(fullResponse, msgDiv, tab);
        // 保存到历史记录
        if (tab) {
            tab.chatHistory.push({ role: 'assistant', content: fullResponse });
        }
    }
}

// 消息 DOM 创建
function createMessageDiv(role) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    aiMessages.appendChild(div);
    return div;
}

export function appendMessage(role, content, tab) {
    const div = createMessageDiv(role);
    div.textContent = content;
    if (tab) tab.chatHistory.push({ role, content });
    aiMessages.scrollTop = aiMessages.scrollHeight;
    return div;
}

// --- 核心：JSON 指令解析渲染（移植自原始版，正则 + 字符串感知大括号计数）---
function processAIResponseForCommands(text, msgDiv, tab) {
    try {
        msgDiv.classList.add('rendered');
        msgDiv.innerHTML = '';

        let lastIndex = 0;
        // 用正则定位含 "type":"command_request" 的 JSON 块起点
        const startRegex = /(?:```(?:json)?\s*)?\{[\s\S]*?"type"\s*:\s*"command_request"/g;
        let match;

        while ((match = startRegex.exec(text)) !== null) {
            // 1. 渲染 JSON 之前的文本
            const plainText = text.substring(lastIndex, match.index);
            if (plainText.trim()) {
                const textNode = document.createElement('div');
                textNode.className = 'message-text-content';
                textNode.innerHTML = typeof marked !== 'undefined' ? marked.parse(plainText) : plainText;
                msgDiv.appendChild(textNode);
            }

            // 2. 用字符串感知的大括号计数器找到 JSON 的真正结束位置
            const startPos = text.indexOf('{', match.index);
            let braceCount = 0;
            let inString = false;
            let escaped = false;
            let endPos = -1;

            for (let i = startPos; i < text.length; i++) {
                const char = text[i];
                if (escaped) { escaped = false; continue; }
                if (char === '\\') { escaped = true; continue; }
                if (char === '"') { inString = !inString; continue; }
                if (!inString) {
                    if (char === '{') braceCount++;
                    if (char === '}') {
                        braceCount--;
                        if (braceCount === 0) { endPos = i + 1; break; }
                    }
                }
            }

            if (endPos !== -1) {
                const jsonStr = text.substring(startPos, endPos);
                // 跳过可能的 Markdown 代码块闭合标记
                let fullEndPos = endPos;
                if (text.substring(endPos).trimStart().startsWith('```')) {
                    fullEndPos = text.indexOf('```', endPos) + 3;
                }

                try {
                    const cmdData = JSON.parse(jsonStr);
                    const command = cmdData.command.trim();
                    renderCommandCard(command, msgDiv, tab);
                } catch (e) {
                    // JSON 解析失败，降级为文本
                    const errNode = document.createElement('div');
                    errNode.className = 'message-text-content';
                    errNode.innerText = text.substring(match.index, fullEndPos);
                    msgDiv.appendChild(errNode);
                }

                lastIndex = fullEndPos;
                startRegex.lastIndex = fullEndPos;
            } else {
                lastIndex = match.index + 1;
            }
        }

        // 3. 渲染剩余文本
        const remainingText = text.substring(lastIndex);
        if (remainingText.trim()) {
            const textNode = document.createElement('div');
            textNode.className = 'message-text-content';
            textNode.innerHTML = typeof marked !== 'undefined' ? marked.parse(remainingText) : remainingText;
            msgDiv.appendChild(textNode);
        }
    } catch (e) {
        console.error('处理 AI 响应命令失败:', e);
        msgDiv.innerText = text;
    }
}

// 渲染指令卡片
function renderCommandCard(command, container, tab) {
    const card = document.createElement('div');
    card.className = 'command-card';
    card.innerHTML = `
        <div class="command-card-header"><i class="fas fa-terminal"></i> <span>SSH 命令执行</span></div>
        <div class="command-card-body"><code>${command}</code></div>
        <div class="command-card-footer">
            <div class="command-card-tip"><i class="fas fa-shield-alt"></i> 需要确认</div>
            <div class="command-actions">
                <button class="btn btn-sm btn-primary btn-confirm"><i class="fas fa-check"></i> 同意</button>
                <button class="btn btn-sm btn-secondary btn-reject"><i class="fas fa-times"></i> 拒绝</button>
            </div>
        </div>
    `;

    const confirmBtn = card.querySelector('.btn-confirm');
    const rejectBtn = card.querySelector('.btn-reject');

    confirmBtn.onclick = () => {
        card.innerHTML = `<div class="command-card-header" style="color:#4caf50"><i class="fas fa-check-circle"></i> 命令已发送执行</div><div class="command-card-body"><code>${command}</code></div>`;
        executeAICommand(command, tab);
    };

    rejectBtn.onclick = () => {
        card.innerHTML = `<div class="command-card-header" style="color:#f1707b"><i class="fas fa-times-circle"></i> 您拒绝了此命令执行</div><div class="command-card-body"><code>${command}</code></div>`;
    };

    container.appendChild(card);
}

// 执行命令：发送到终端并启动输出捕获
function executeAICommand(command, tab) {
    if (!tab || !tab.socket || tab.socket.readyState !== WebSocket.OPEN) {
        notify('SSH 未就绪，无法执行命令', 'error');
        return;
    }

    // 启动捕获模式：terminal.js 的 onmessage 会累积输出
    tab.isCapturing = true;
    tab.captureBuffer = '';
    if (tab.captureTimer) {
        clearTimeout(tab.captureTimer);
        tab.captureTimer = null;
    }

    // 发送命令到终端（带回车换行）
    tab.socket.send(JSON.stringify({ type: 'data', data: command.trim() + '\n' }));
    notify('命令已发送，等待执行结果...');
}

// 清空对话
export function clearChat() {
    if (!activeTabId) return;
    const tab = window.getTab(activeTabId);
    if (tab) {
        tab.chatHistory = [];
        aiMessages.innerHTML = '';
        appendMessage('system', '会话历史已清空。');
    }
}
