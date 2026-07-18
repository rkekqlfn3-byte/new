// ===================================================
// CUSTOM MACRO MANAGEMENT
// ===================================================
const btnAddMacro = document.getElementById('btn-add-macro');
const macroListDiv = document.getElementById('custom-macro-list');
const learnedMacroListDiv = document.getElementById('learned-macro-list');
const learnedMacroSearch = document.getElementById('learned-macro-search');

function escapeDictionaryHtml(value) {
    const node = document.createElement('div');
    node.textContent = String(value ?? '');
    return node.innerHTML
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function safeDictionaryInteger(value, fallback = 0, min = 0, max = 1000000) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return fallback;
    return Math.min(max, Math.max(min, Math.trunc(numeric)));
}

async function updateMacroList() {
    if (!macroListDiv) return;
    macroListDiv.innerHTML = '';
    const macros = await eel.get_macros()();
    let hasCustom = false;
    for (const [mId, mData] of Object.entries(macros)) {
        if (mData.type === "default" || mData.type === "learned" || !mData.type) continue;
        hasCustom = true;
        const div = document.createElement('div');
        div.className = 'list-item';
        div.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px;border-bottom:1px solid #eee;';
        const typeStr = (mData.type || 'cmd').toUpperCase();
        const safeMacroId = escapeDictionaryHtml(mId);
        const safeType = escapeDictionaryHtml(typeStr);
        div.innerHTML = `
            <div>
                <strong>[${safeType}] ${safeMacroId}</strong>
                <div style="font-size:0.85em;color:#666;margin-top:2px;">명령어: ${(mData.synonyms || []).map(escapeDictionaryHtml).join(', ')}</div>
                <div style="font-size:0.85em;color:#888;margin-top:2px;">동작/값: ${escapeDictionaryHtml(mData.data || '')}</div>
            </div>
            <button class="btn-del-macro" data-id="${safeMacroId}" style="background:#ff5555;border:none;color:#fff;border-radius:5px;padding:4px 8px;cursor:pointer;">삭제</button>
        `;
        macroListDiv.appendChild(div);
    }
    if (!hasCustom) {
        macroListDiv.innerHTML = '<div style="padding:10px;color:#aaa;text-align:center;">등록된 커스텀 매크로가 없습니다.</div>';
    }
    document.querySelectorAll('.btn-del-macro').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const targetId = e.target.getAttribute('data-id');
            if (confirm("매크로 '" + targetId + "' 삭제할까요?")) {
                await eel.delete_custom_macro(targetId)();
                updateMacroList();
            }
        });
    });
}

function learnedStatusText(record) {
    const stateNames = {active: '사용 중', needs_review: '검토 필요', broken: '고장', disabled: '일시 정지'};
    const state = stateNames[record.state] || record.state || '검토 필요';
    if (!record.usage_count) return `${state} · 아직 재사용 안 함`;
    const status = record.last_status === 'success' ? '최근 성공' : `최근 ${record.last_status || '실패'}`;
    return `${state} · ${record.usage_count}회 사용 · 성공 ${record.success_count} · 실패 ${record.failure_count} · ${status}`;
}

function learnedStateLabel(state) {
    return ({active: '사용 중', needs_review: '검토 필요', broken: '고장', disabled: '일시 정지'})[state]
        || state || '검토 필요';
}

function learnedVerificationLabel(status) {
    return ({
        verified: '자동 검증 완료',
        user_confirmed: '사용자 확인 완료',
        confirmation_required: '사용자 확인 필요',
        unknown: '이전 검증 기록 없음'
    })[status] || status || '검증 기록 없음';
}

function learnedKindLabel(kind) {
    return ({action_plan: '행동 계획', dynamic_code: '동적 코드'})[kind] || kind || '학습 행동';
}

function learnedRunPolicyLabel(policy) {
    return policy === 'auto' ? '사용자 승인 자동 실행' : '실행 전 확인';
}

document.getElementById('btn-execution-diagnostics')?.addEventListener('click', async () => {
    const diagnostics = await eel.get_execution_diagnostics(10)();
    const records = diagnostics?.records || [];
    if (!records.length) {
        alert('아직 기록된 실행 진단이 없습니다.');
        return;
    }
    const lines = records.slice().reverse().map(record =>
        `${record.finished_at || ''} · ${record.status} · ${record.duration_ms || 0}ms · ${record.label || ''}`
    );
    alert(`최근 실행 진단\n\n${lines.join('\n')}`);
});
