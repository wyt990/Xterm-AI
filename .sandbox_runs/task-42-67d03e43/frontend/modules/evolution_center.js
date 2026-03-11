import { api } from './api.js';
import { notify, showModal } from './utils.js';

let evolutionTaskCache = [];
let activeStatusChip = '';
let currentDetailTaskId = null;
let queueStatusTimer = null;
let opsTemplateCache = [];
let editingTaskId = null;

function escapeHtml(text) {
    return String(text ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function statusBadge(status) {
    return `<span class="evo-status-badge status-${status || 'unknown'}">${escapeHtml(status || '-')}</span>`;
}

function taskRowActions(task) {
    const actions = [];
    if (task.approval_status === 'pending') {
        actions.push(
            `<button class="btn btn-sm btn-primary" onclick="approveEvolutionTask(${task.id})">审批通过</button>`,
            `<button class="btn btn-sm btn-secondary" onclick="rejectEvolutionTask(${task.id})">审批拒绝</button>`
        );
    }
    if (task.status === 'pending_approval') {
        actions.push(
            `<button class="btn btn-sm btn-secondary" onclick="cancelEvolutionTask(${task.id})">停用</button>`,
            `<button class="btn btn-sm btn-secondary" onclick="editEvolutionTask(${task.id})">编辑</button>`,
            `<button class="btn btn-sm btn-danger" onclick="deleteEvolutionTask(${task.id})">删除</button>`
        );
    }
    if (task.status === 'approved') {
        actions.push(
            `<button class="btn btn-sm btn-secondary" onclick="cancelEvolutionTask(${task.id})">停用</button>`,
            `<button class="btn btn-sm btn-danger" onclick="deleteEvolutionTask(${task.id})">删除</button>`
        );
    }
    if (task.status === 'cancelled') {
        actions.push(
            `<button class="btn btn-sm btn-secondary" onclick="enableEvolutionTask(${task.id})">启用</button>`,
            `<button class="btn btn-sm btn-secondary" onclick="editEvolutionTask(${task.id})">编辑</button>`,
            `<button class="btn btn-sm btn-danger" onclick="deleteEvolutionTask(${task.id})">删除</button>`
        );
    }
    if (['failed', 'blocked_manual'].includes(task.status)) {
        actions.push(`<button class="btn btn-sm btn-secondary" onclick="cancelEvolutionTask(${task.id})">停用</button>`);
    }
    if (['approved', 'failed'].includes(task.status)) {
        actions.push(
            `<button class="btn btn-sm btn-secondary" onclick="enqueueEvolutionTask(${task.id})">加入队列</button>`,
            `<button class="btn btn-sm btn-primary" onclick="runEvolutionTaskAsync(${task.id})">异步执行</button>`,
            `<button class="btn btn-sm btn-primary" onclick="runEvolutionTask(${task.id}, 'success')">执行成功</button>`,
            `<button class="btn btn-sm btn-secondary" onclick="runEvolutionTask(${task.id}, 'failed')">执行失败</button>`
        );
    }
    if (task.status === 'blocked_manual') {
        actions.push('<span style="color:#c586c0;">需人工介入</span>');
    }
    actions.push(`<button class="btn btn-sm btn-secondary" onclick="showEvolutionTaskDetail(${task.id})">详情</button>`);
    return actions.join(' ');
}

function renderTasks(tasks) {
    const body = document.getElementById('evolution-task-table-body');
    if (!body) return;
    if (!tasks.length) {
        body.innerHTML = '<tr><td colspan="8" style="color:#888;">暂无任务</td></tr>';
        return;
    }
    body.innerHTML = tasks.map(task => `
        <tr>
            <td>#${task.id}</td>
            <td title="${task.title || ''}">${task.title || '-'}</td>
            <td>${statusBadge(task.risk_level)}</td>
            <td>${statusBadge(task.status)}</td>
            <td>${statusBadge(task.approval_status)}</td>
            <td>${task.retry_count}/${task.max_retries}</td>
            <td>${task.source || '-'}</td>
            <td style="display:flex;gap:6px;flex-wrap:wrap;">${taskRowActions(task)}</td>
        </tr>
    `).join('');
}

export async function loadEvolutionTasks() {
    try {
        const tasks = await api.getEvolutionTasks({});
        evolutionTaskCache = tasks;
        applyTaskFiltersAndRender();
        await loadEvolutionQueueStatus();
        await loadEvolutionExperiences();
        await loadEvolutionFailureReports();
        await loadEvolutionSchemaMigrations();
    } catch (e) {
        notify(`加载任务失败: ${e.message}`, 'error');
    }
}

function renderSimpleList(el, rows, renderer) {
    if (!el) return;
    if (!rows?.length) {
        el.innerHTML = '<div style="color:#777;">暂无数据</div>';
        return;
    }
    el.innerHTML = rows.map(renderer).join('');
}

export async function loadEvolutionOpsTemplates() {
    const el = document.getElementById('evolution-ops-templates');
    if (!el) return;
    try {
        const list = await api.getEvolutionOpsTemplates();
        opsTemplateCache = list || [];
        renderSimpleList(
            el,
            opsTemplateCache,
            (item) => `<div style="border-bottom:1px dashed #3a3a3a;padding:6px 0;">
                <div><b>${escapeHtml(item.name || '-')}</b></div>
                <div style="color:#8f8f8f;">${escapeHtml(item.template_id || '-')}</div>
                <div style="margin-top:4px;">
                    <button class="btn btn-sm btn-secondary" onclick="createEvolutionTaskFromTemplate('${escapeHtml(item.template_id || '')}')">按模板建任务</button>
                </div>
            </div>`
        );
    } catch (e) {
        el.innerHTML = `<div style="color:#e05252;">模板加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

export async function loadEvolutionExperiences() {
    const el = document.getElementById('evolution-experience-list');
    if (!el) return;
    try {
        const rows = await api.getEvolutionExperiences(20);
        renderSimpleList(
            el,
            rows,
            (x) => `<div style="border-bottom:1px dashed #3a3a3a;padding:6px 0;">
                <div><b>${escapeHtml(x.error_category || 'unknown')}</b> / ${escapeHtml(x.error_signature || '-')}</div>
                <div style="color:#9aa0a6;">任务#${x.task_id || '-'} ${escapeHtml(x.summary || '')}</div>
            </div>`
        );
    } catch (e) {
        el.innerHTML = `<div style="color:#e05252;">经验库加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

export async function loadEvolutionFailureReports() {
    const el = document.getElementById('evolution-failure-reports');
    if (!el) return;
    try {
        const rows = await api.getEvolutionFailureReports(10);
        renderSimpleList(
            el,
            rows,
            (x) => `<div style="border-bottom:1px dashed #3a3a3a;padding:6px 0;">
                <div><b>#${x.id}</b> 任务#${x.task_id} ${escapeHtml(x.report_title || '-')}</div>
                <div style="color:#9aa0a6;">通知: ${escapeHtml(x.notify_status || '-')}</div>
            </div>`
        );
    } catch (e) {
        el.innerHTML = `<div style="color:#e05252;">失败报告加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

export async function loadEvolutionSchemaMigrations() {
    const el = document.getElementById('evolution-schema-migrations');
    if (!el) return;
    try {
        const rows = await api.getEvolutionSchemaMigrations(10);
        renderSimpleList(
            el,
            rows,
            (x) => `<div style="border-bottom:1px dashed #3a3a3a;padding:6px 0;">
                <div><b>${escapeHtml(x.migration_name || '-')}</b> / ${escapeHtml(x.status || '-')}</div>
                <div style="color:#9aa0a6;">checksum: ${escapeHtml((x.checksum || '').slice(0, 12))}</div>
            </div>`
        );
    } catch (e) {
        el.innerHTML = `<div style="color:#e05252;">迁移审计加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

export async function createEvolutionTaskFromTemplate(templateId) {
    try {
        const selected = opsTemplateCache.find((x) => (x.template_id || '') === (templateId || ''));
        const task = selected?.task || {};
        await api.createEvolutionTask(task || {});
        notify('已按模板创建任务', 'success');
        await loadEvolutionTasks();
    } catch (e) {
        notify(`模板建任务失败: ${e.message}`, 'error');
    }
}

async function loadEvolutionQueueStatus() {
    const box = document.getElementById('evolution-queue-status');
    if (!box) return;
    try {
        const stat = await api.getEvolutionQueueStatus();
        box.innerHTML = `
            <span>队列: ${stat.queued}</span>
            <span>运行中: ${stat.running}</span>
            <span>Worker: ${stat.workers}</span>
            <span>已处理: ${stat.processed}</span>
            <span>失败: ${stat.failed}</span>
        `;
    } catch (e) {
        box.innerHTML = `<span style="color:#e05252;">队列状态加载失败: ${e.message}</span>`;
    }
}

async function loadEvolutionSchedulerStatus() {
    const statusEl = document.getElementById('evolution-scheduler-status');
    const enabledEl = document.getElementById('evolution-scheduler-enabled');
    const intervalEl = document.getElementById('evolution-scheduler-interval');
    const maxTasksEl = document.getElementById('evolution-scheduler-max-tasks');
    const retryDelayEl = document.getElementById('evolution-retry-delay');
    if (!statusEl || !enabledEl || !intervalEl || !maxTasksEl || !retryDelayEl) return;
    try {
        const data = await api.getEvolutionSchedulerStatus();
        const cfg = data.config || {};
        enabledEl.checked = !!cfg.enabled;
        intervalEl.value = String(cfg.interval_sec ?? 30);
        maxTasksEl.value = String(cfg.max_tasks_per_tick ?? 3);
        retryDelayEl.value = String(cfg.retry_delay_sec ?? 60);
        const last = data.last_result || {};
        statusEl.textContent = `最近: picked=${last.picked ?? 0}, enqueued=${last.enqueued ?? 0}, skipped=${last.skipped ?? 0}`;
    } catch (e) {
        statusEl.textContent = `调度状态加载失败: ${e.message}`;
    }
}

function applyTaskFiltersAndRender() {
    const statusSelect = document.getElementById('evolution-status-filter')?.value || '';
    const chipStatus = activeStatusChip || '';
    const targetStatus = chipStatus || statusSelect;
    const filtered = targetStatus
        ? evolutionTaskCache.filter(t => (t.status || '') === targetStatus)
        : evolutionTaskCache.slice();
    renderTasks(filtered);
}

function bindStatusChips() {
    const chips = document.querySelectorAll('.evo-chip');
    chips.forEach(chip => {
        if (chip.dataset.bound === '1') return;
        chip.addEventListener('click', () => {
            const status = chip.dataset.status || '';
            const isAll = status === '';
            const alreadyActive = chip.classList.contains('active') && !isAll;
            chips.forEach(c => c.classList.remove('active'));
            if (alreadyActive) {
                activeStatusChip = '';
                const all = document.querySelector('.evo-chip[data-status=""]');
                if (all) all.classList.add('active');
            } else {
                chip.classList.add('active');
                activeStatusChip = status;
            }
            applyTaskFiltersAndRender();
        });
        chip.dataset.bound = '1';
    });
}

async function createTaskFromForm(e) {
    e.preventDefault();
    const form = document.getElementById('evolution-task-create-form');
    if (!form) return;
    const payloadRaw = (form.payload_json.value || '').trim();
    let payload = {};
    if (payloadRaw) {
        try {
            payload = JSON.parse(payloadRaw);
        } catch (parseError) {
            console.warn('payload_json 解析失败:', parseError);
            notify('payload_json 不是合法 JSON', 'error');
            return;
        }
    }
    const maxRetries = Number.parseInt(form.max_retries.value, 10) || 100;
    const data = {
        title: form.title.value.trim(),
        description: form.description.value.trim(),
        source: form.source.value,
        task_type: form.task_type.value,
        scope: form.scope.value,
        risk_level: form.risk_level.value,
        max_retries: maxRetries,
        acceptance_criteria: form.acceptance_criteria.value.trim(),
        rollback_plan: form.rollback_plan.value.trim(),
        payload
    };
    if (!data.title) {
        notify('任务标题不能为空', 'warning');
        return;
    }
    if (editingTaskId) {
        await api.updateEvolutionTask(editingTaskId, {
            title: data.title,
            description: data.description,
            source: data.source,
            task_type: data.task_type,
            scope: data.scope,
            risk_level: data.risk_level,
            max_retries: data.max_retries,
            acceptance_criteria: data.acceptance_criteria,
            rollback_plan: data.rollback_plan,
            payload: data.payload
        });
        notify(`任务 #${editingTaskId} 已保存`, 'success');
        resetEvolutionTaskForm();
    } else {
        await api.createEvolutionTask(data);
        notify('任务创建成功', 'success');
        resetEvolutionTaskForm();
    }
    await loadEvolutionTasks();
}

export async function approveEvolutionTask(taskId) {
    try {
        await api.approveEvolutionTask(taskId);
        notify(`任务 #${taskId} 已审批通过`, 'success');
        await loadEvolutionTasks();
    } catch (e) {
        notify(`审批失败: ${e.message}`, 'error');
    }
}

export async function rejectEvolutionTask(taskId) {
    try {
        const reason = prompt('请输入拒绝原因（可选）：', '') || '';
        await api.rejectEvolutionTask(taskId, { reason });
        notify(`任务 #${taskId} 已拒绝`, 'success');
        await loadEvolutionTasks();
    } catch (e) {
        notify(`操作失败: ${e.message}`, 'error');
    }
}

export async function editEvolutionTask(taskId) {
    try {
        const task = await api.getEvolutionTask(taskId);
        const form = document.getElementById('evolution-task-create-form');
        if (!form) return;
        form.title.value = task.title || '';
        form.description.value = task.description || '';
        form.source.value = task.source || 'user';
        form.task_type.value = task.task_type || 'fix';
        form.scope.value = task.scope || 'backend';
        form.risk_level.value = task.risk_level || 'low';
        form.acceptance_criteria.value = task.acceptance_criteria || '';
        form.rollback_plan.value = task.rollback_plan || '';
        form.max_retries.value = String(task.max_retries || 100);
        form.payload_json.value = JSON.stringify(task.payload || {}, null, 2);
        editingTaskId = taskId;
        updateEvolutionTaskFormMode();
        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
        notify(`已进入编辑模式：任务 #${taskId}`, 'info');
    } catch (e) {
        notify(`编辑失败: ${e.message}`, 'error');
    }
}

function resetEvolutionTaskForm() {
    const form = document.getElementById('evolution-task-create-form');
    if (!form) return;
    form.reset();
    form.max_retries.value = '100';
    editingTaskId = null;
    updateEvolutionTaskFormMode();
}

function updateEvolutionTaskFormMode() {
    const submitBtn = document.getElementById('evolution-task-submit-btn');
    const cancelBtn = document.getElementById('evolution-task-cancel-edit-btn');
    if (!submitBtn || !cancelBtn) return;
    if (editingTaskId) {
        submitBtn.textContent = `保存编辑 #${editingTaskId}`;
        cancelBtn.style.display = 'inline-flex';
    } else {
        submitBtn.textContent = '创建任务';
        cancelBtn.style.display = 'none';
    }
}

export async function cancelEvolutionTask(taskId) {
    try {
        const ok = confirm(`确认停用任务 #${taskId} 吗？`);
        if (!ok) return;
        const ret = await api.cancelEvolutionTask(taskId);
        notify(`任务 #${taskId} 已停用: ${ret.task_status || 'cancelled'}`, 'success');
        await loadEvolutionTasks();
    } catch (e) {
        notify(`停用失败: ${e.message}`, 'error');
    }
}

export async function enableEvolutionTask(taskId) {
    try {
        const ok = confirm(`确认启用任务 #${taskId} 吗？`);
        if (!ok) return;
        const ret = await api.enableEvolutionTask(taskId);
        notify(`任务 #${taskId} 已启用: ${ret.task_status || 'approved'}`, 'success');
        await loadEvolutionTasks();
    } catch (e) {
        notify(`启用失败: ${e.message}`, 'error');
    }
}

export async function deleteEvolutionTask(taskId) {
    try {
        const ok = confirm(`确认删除任务 #${taskId} 吗？该操作不可恢复。`);
        if (!ok) return;
        await api.deleteEvolutionTask(taskId);
        notify(`任务 #${taskId} 已删除`, 'success');
        await loadEvolutionTasks();
    } catch (e) {
        notify(`删除失败: ${e.message}`, 'error');
    }
}

function renderRunRows(runs) {
    if (!runs?.length) {
        return '<div style="color:#888;">暂无运行记录</div>';
    }
    return runs.map(run => `
        <div style="border:1px solid #333;border-radius:6px;padding:8px;margin-bottom:8px;">
            <div style="display:flex;gap:10px;flex-wrap:wrap;color:#ddd;">
                <span><b>#${run.id}</b></span>
                <span>状态: <b>${run.run_status}</b></span>
                <span>触发: ${run.trigger_type || '-'}</span>
                <span>执行人: ${run.operator || '-'}</span>
                <span>时间: ${run.started_at || '-'}</span>
            </div>
            <div style="margin-top:6px;color:#bbb;">详情: ${run.detail || '-'}</div>
            <pre style="margin:6px 0 0 0;padding:8px;background:#1e1e1e;border:1px solid #2f2f2f;border-radius:4px;white-space:pre-wrap;color:#a9d1ff;">${JSON.stringify(run.result || {}, null, 2)}</pre>
        </div>
    `).join('');
}

function renderStageSummary(runs) {
    const el = document.getElementById('evolution-run-stage-summary');
    if (!el) return;
    const latest = runs?.[0];
    const logs = latest?.result?.logs;
    if (!Array.isArray(logs) || !logs.length) {
        el.textContent = '阶段流水：无结构化阶段日志';
        return;
    }
    const stat = {};
    logs.forEach(item => {
        const stage = item.stage || 'unknown';
        if (!stat[stage]) stat[stage] = { total: 0, failed: 0 };
        stat[stage].total += 1;
        if (!item.ok) stat[stage].failed += 1;
    });
    const text = Object.entries(stat)
        .map(([stage, v]) => `${stage}: ${v.total} 条, 失败 ${v.failed}`)
        .join(' | ');
    el.textContent = `阶段流水：${text}`;
}

function humanSuggestion(task) {
    if (!task.needs_human_action) {
        return '当前无需人工接管，系统可继续自动执行。';
    }
    if (task.status === 'blocked_manual') {
        return '任务已进入人工接管状态：请先检查错误签名、执行日志与回滚方案，再决定是否调整任务目标或拆分子任务。';
    }
    return '建议人工复核当前失败原因，必要时补充约束条件后再继续自动执行。';
}

async function refreshTaskRuns(taskId) {
    const runsEl = document.getElementById('evolution-task-runs-panel');
    const order = document.getElementById('evolution-run-order-select')?.value || 'desc';
    if (!runsEl) return;
    const runs = await api.getEvolutionTaskRuns(taskId, 30, order);
    runsEl.innerHTML = renderRunRows(runs);
    renderStageSummary(runs);
}

export async function showEvolutionTaskDetail(taskId) {
    try {
        currentDetailTaskId = taskId;
        const task = await api.getEvolutionTask(taskId);
        const basicEl = document.getElementById('evolution-task-detail-basic');
        const approvalEl = document.getElementById('evolution-task-detail-approval');
        const failureEl = document.getElementById('evolution-task-detail-failure');
        const humanEl = document.getElementById('evolution-task-detail-human');
        const runsEl = document.getElementById('evolution-task-runs-panel');
        const titleEl = document.getElementById('evolution-task-detail-title');
        if (!basicEl || !approvalEl || !runsEl || !titleEl || !failureEl || !humanEl) return;
        titleEl.innerText = `任务详情 #${task.id}`;
        basicEl.innerHTML = `
            <div>标题：${task.title || '-'}</div>
            <div>类型：${task.task_type || '-'}</div>
            <div>范围：${task.scope || '-'}</div>
            <div>风险：${task.risk_level || '-'}</div>
            <div>状态：${task.status || '-'}</div>
            <div>重试：${task.retry_count}/${task.max_retries}</div>
            <div>创建人：${task.created_by || '-'}</div>
            <div>创建时间：${task.created_at || '-'}</div>
            <div>验收标准：${task.acceptance_criteria || '-'}</div>
            <div>回滚方案：${task.rollback_plan || '-'}</div>
        `;
        approvalEl.innerHTML = `
            <div>审批状态：${task.approval_status || '-'}</div>
            <div>审批人：${task.approved_by || '-'}</div>
            <div>审批时间：${task.approved_at || '-'}</div>
            <div>拒绝人：${task.rejected_by || '-'}</div>
            <div>拒绝时间：${task.rejected_at || '-'}</div>
            <div>拒绝原因：${task.rejection_reason || '-'}</div>
        `;
        failureEl.innerHTML = `
            <div>错误签名：${task.error_signature || '-'}</div>
            <div>重复次数：${task.error_repeat_count ?? 0}</div>
            <div>最终报告：${task.final_report || '-'}</div>
        `;
        humanEl.textContent = humanSuggestion(task);
        await refreshTaskRuns(taskId);
        showModal('evolution-task-detail-modal');
    } catch (e) {
        notify(`加载任务详情失败: ${e.message}`, 'error');
    }
}

export async function runEvolutionTask(taskId, result) {
    try {
        const body = {
            result,
            detail: result === 'success' ? '手动标记成功' : '手动标记失败',
            error_signature: result === 'failed' ? 'manual_failure' : null
        };
        const ret = await api.runEvolutionTask(taskId, body);
        notify(`任务 #${taskId} 执行完成: ${ret.task_status}`, 'success');
        await loadEvolutionTasks();
    } catch (e) {
        notify(`执行失败: ${e.message}`, 'error');
    }
}

export async function runEvolutionTaskAsync(taskId) {
    try {
        const ret = await api.runEvolutionTaskAsync(taskId);
        notify(`任务 #${taskId} 异步执行完成: ${ret.task_status}`, ret.task_status === 'success' ? 'success' : 'warning');
        if (ret.log_file) {
            notify(`执行日志: ${ret.log_file}`, 'info');
        }
        if (ret.report_id) {
            notify(`已生成无法修复报告 #${ret.report_id}`, 'warning');
        }
        await loadEvolutionTasks();
    } catch (e) {
        notify(`异步执行失败: ${e.message}`, 'error');
    }
}

export async function enqueueEvolutionTask(taskId) {
    try {
        const ret = await api.enqueueEvolutionTask(taskId);
        notify(`任务 #${taskId} 已加入队列: ${ret.task_status}`, 'success');
        await loadEvolutionTasks();
    } catch (e) {
        notify(`加入队列失败: ${e.message}`, 'error');
    }
}

export function initEvolutionCenterModule() {
    const form = document.getElementById('evolution-task-create-form');
    if (form && !form.dataset.bound) {
        form.addEventListener('submit', createTaskFromForm);
        form.dataset.bound = '1';
    }
    const cancelEditBtn = document.getElementById('evolution-task-cancel-edit-btn');
    if (cancelEditBtn && !cancelEditBtn.dataset.bound) {
        cancelEditBtn.addEventListener('click', () => {
            resetEvolutionTaskForm();
            notify('已取消编辑模式', 'info');
        });
        cancelEditBtn.dataset.bound = '1';
    }
    updateEvolutionTaskFormMode();
    const filter = document.getElementById('evolution-status-filter');
    if (filter && !filter.dataset.bound) {
        filter.addEventListener('change', () => {
            activeStatusChip = '';
            const all = document.querySelector('.evo-chip[data-status=""]');
            document.querySelectorAll('.evo-chip').forEach(c => c.classList.remove('active'));
            if (all) all.classList.add('active');
            applyTaskFiltersAndRender();
        });
        filter.dataset.bound = '1';
    }
    const orderSelect = document.getElementById('evolution-run-order-select');
    if (orderSelect && !orderSelect.dataset.bound) {
        orderSelect.addEventListener('change', async () => {
            if (currentDetailTaskId) await refreshTaskRuns(currentDetailTaskId);
        });
        orderSelect.dataset.bound = '1';
    }
    bindStatusChips();
    if (!queueStatusTimer) {
        queueStatusTimer = setInterval(() => {
            loadEvolutionQueueStatus().catch(() => {});
            loadEvolutionSchedulerStatus().catch(() => {});
        }, 3000);
    }

    globalThis.loadEvolutionTasks = loadEvolutionTasks;
    globalThis.approveEvolutionTask = approveEvolutionTask;
    globalThis.rejectEvolutionTask = rejectEvolutionTask;
    globalThis.editEvolutionTask = editEvolutionTask;
    globalThis.cancelEvolutionTask = cancelEvolutionTask;
    globalThis.enableEvolutionTask = enableEvolutionTask;
    globalThis.deleteEvolutionTask = deleteEvolutionTask;
    globalThis.runEvolutionTask = runEvolutionTask;
    globalThis.runEvolutionTaskAsync = runEvolutionTaskAsync;
    globalThis.enqueueEvolutionTask = enqueueEvolutionTask;
    globalThis.showEvolutionTaskDetail = showEvolutionTaskDetail;
    globalThis.loadEvolutionOpsTemplates = loadEvolutionOpsTemplates;
    globalThis.loadEvolutionExperiences = loadEvolutionExperiences;
    globalThis.loadEvolutionFailureReports = loadEvolutionFailureReports;
    globalThis.loadEvolutionSchemaMigrations = loadEvolutionSchemaMigrations;
    globalThis.createEvolutionTaskFromTemplate = createEvolutionTaskFromTemplate;
    globalThis.seedEvolutionDemo = async () => {
        try {
            const clear = confirm('是否先清空现有任务中心演示数据再生成？\n点击“确定”会先清空后生成，点击“取消”会直接追加。');
            const ret = await api.seedEvolutionDemo({ clear_existing: clear });
            notify(`演示数据已生成：${ret.created_tasks} 条任务，${ret.created_failed_runs} 条失败日志`, 'success');
            await loadEvolutionTasks();
        } catch (e) {
            notify(`生成演示数据失败: ${e.message}`, 'error');
        }
    };
    globalThis.resetEvolutionDemo = async () => {
        try {
            const ok = confirm('确认将任务中心数据重置到干净状态？此操作会清空当前任务与运行日志。');
            if (!ok) return;
            await api.resetEvolutionDemo();
            notify('任务中心已重置到干净状态', 'success');
            evolutionTaskCache = [];
            await loadEvolutionTasks();
        } catch (e) {
            notify(`重置失败: ${e.message}`, 'error');
        }
    };
    globalThis.saveEvolutionSchedulerConfig = async () => {
        try {
            const enabled = !!document.getElementById('evolution-scheduler-enabled')?.checked;
            const interval_sec = Number.parseInt(document.getElementById('evolution-scheduler-interval')?.value || '30', 10) || 30;
            const max_tasks_per_tick = Number.parseInt(document.getElementById('evolution-scheduler-max-tasks')?.value || '3', 10) || 3;
            const retry_delay_sec = Number.parseInt(document.getElementById('evolution-retry-delay')?.value || '60', 10) || 60;
            await api.updateEvolutionSchedulerConfig({ enabled, interval_sec, max_tasks_per_tick, retry_delay_sec });
            notify('调度配置已保存', 'success');
            await loadEvolutionSchedulerStatus();
        } catch (e) {
            notify(`保存调度配置失败: ${e.message}`, 'error');
        }
    };
    globalThis.runEvolutionSchedulerOnce = async () => {
        try {
            const ret = await api.runEvolutionSchedulerOnce();
            notify(`调度执行一次完成: picked=${ret.picked}, enqueued=${ret.enqueued}, skipped=${ret.skipped}`, 'success');
            await loadEvolutionTasks();
        } catch (e) {
            notify(`调度执行失败: ${e.message}`, 'error');
        }
    };
    loadEvolutionSchedulerStatus().catch(() => {});
    loadEvolutionOpsTemplates().catch(() => {});
}

