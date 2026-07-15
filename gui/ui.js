// Variables are declared in globals.js

// Character management removed.

window.addEventListener('load', async () => {
    try {
        const events = await eel.get_storage_recovery_events(true)();
        if (Array.isArray(events) && events.length && typeof addSystemMessage === 'function') {
            const names = events.map(item => item.path.split(/[\\/]/).pop()).join(', ');
            addSystemMessage(`저장 파일 손상을 감지해 정상 백업으로 자동 복구했습니다: ${names}`);
        }
    } catch (error) {
        console.warn('저장 복구 알림을 확인하지 못했습니다.', error);
    }
});

function scrollToBottom() {
    if (chatArea) chatArea.scrollTop = chatArea.scrollHeight;
}

// ===================================================
// UNIFIED DICTIONARY MODAL LOGIC
// ===================================================
window.switchUnifiedTab = function(tabName) {
    document.querySelectorAll('.unified-tab-pane').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.unified-tab-btn').forEach(btn => btn.classList.remove('active'));
    
    document.getElementById('tab-' + tabName).style.display = 'block';
    
    // Find the button that calls this tab and make it active
    document.querySelectorAll('.unified-tab-btn').forEach(btn => {
        if (btn.getAttribute('onclick').includes(tabName)) {
            btn.classList.add('active');
        }
    });

    if (tabName === 'macro') {
        if (typeof updateMacroList === 'function') updateMacroList();
        if (typeof updateLearnedMacroList === 'function') {
            updateLearnedMacroList(learnedMacroSearch?.value || '');
        }
    }
};
const dictSearchInput = document.getElementById('dict-search-input');
const dictAppSelect = document.getElementById('dict-app-select');
const dictSynonymInput = document.getElementById('dict-synonym-input');
const btnAddSynonym = document.getElementById('btn-add-synonym');
const dictRenameInput = document.getElementById('dict-rename-input');
const btnRenameNoun = document.getElementById('btn-rename-noun');

let currentNouns = {};
let favoritesList = [];
let currentTab = 'all';

// Mock value property for the div-based list
if (dictAppSelect && dictAppSelect.tagName !== 'SELECT') {
    Object.defineProperty(dictAppSelect, 'value', {
        get: function() { return this.getAttribute('data-selected') || ''; },
        set: function(v) { this.setAttribute('data-selected', v); }
    });
}

const btnToggleFav = document.getElementById('btn-toggle-fav');
const btnTabAll = document.getElementById('btn-tab-all');
const btnTabFav = document.getElementById('btn-tab-fav');

if (btnTabAll) btnTabAll.addEventListener('click', () => { currentTab = 'all'; updateAppSelect(dictSearchInput ? dictSearchInput.value.toLowerCase() : ''); });
if (btnTabFav) btnTabFav.addEventListener('click', () => { currentTab = 'fav'; updateAppSelect(dictSearchInput ? dictSearchInput.value.toLowerCase() : ''); });

async function updateAppSelect(filter = '') {
    const listDiv = document.getElementById('dict-app-select');
    if (!listDiv) return;

    let entries = Object.entries(currentNouns);
    if (currentTab === 'fav') entries = entries.filter(([n]) => favoritesList.includes(n));
    if (filter) entries = entries.filter(([n, p]) => n.toLowerCase().includes(filter) || (p && p.toLowerCase().includes(filter)));

    // Dedup by path
    const uniqueApps = {};
    for (const [noun, path] of entries) {
        if (!uniqueApps[path]) {
            uniqueApps[path] = noun;
        } else {
            const existing = uniqueApps[path];
            const existingKor = /[\uAC00-\uD7A3]/.test(existing);
            const newKor = /[\uAC00-\uD7A3]/.test(noun);
            const existingFav = favoritesList.includes(existing);
            const newFav = favoritesList.includes(noun);
            if (newFav && !existingFav) { uniqueApps[path] = noun; continue; }
            if (existingFav && !newFav) continue;
            if (newKor && !existingKor) uniqueApps[path] = noun;
        }
    }

    const isKorean = (str) => /[\uAC00-\uD7A3]/.test(str);
    const sortedApps = Object.entries(uniqueApps).sort((a, b) => {
        const aFav = favoritesList.includes(a[1]);
        const bFav = favoritesList.includes(b[1]);
        if (aFav && !bFav) return -1;
        if (!aFav && bFav) return 1;
        const aKor = isKorean(a[1]);
        const bKor = isKorean(b[1]);
        if (aKor && !bKor) return -1;
        if (!aKor && bKor) return 1;
        return a[1].localeCompare(b[1]);
    });

    if (listDiv.tagName === 'SELECT') {
        listDiv.innerHTML = '';
        sortedApps.forEach(([path, noun]) => {
            const opt = document.createElement('option');
            opt.value = noun;
            opt.textContent = noun;
            listDiv.appendChild(opt);
        });
    } else {
        listDiv.innerHTML = '';
        const frag = document.createDocumentFragment();
        // Cap results to 200 items to avoid lag
        const displayApps = sortedApps.slice(0, 200);
        displayApps.forEach(([path, noun]) => {
            const isWeb = path.startsWith('http://') || path.startsWith('https://');
            const displayPath = isWeb ? path : path.split('/').pop().split('\\').pop();
            const icon = isWeb ? '🌐' : '📄';
            const favStar = favoritesList.includes(noun) ? '⭐ ' : '';

            const row = document.createElement('div');
            row.className = 'list-item';
            row.dataset.val = noun;
            const name = document.createElement('span');
            name.style.flex = '1';
            name.textContent = `${favStar}${icon} ${noun}`;
            const location = document.createElement('small');
            location.style.cssText = 'color:#888;font-size:0.75em;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
            location.title = path;
            location.textContent = displayPath;
            row.append(name, location);
            row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;border-radius:6px;transition:background 0.15s;';
            row.addEventListener('mouseover', () => row.style.background = 'rgba(255,255,255,0.07)');
            row.addEventListener('mouseout', () => row.style.background = listDiv.getAttribute('data-selected') === noun ? 'rgba(108,92,231,0.25)' : '');
            row.addEventListener('click', () => {
                listDiv.setAttribute('data-selected', noun);
                listDiv.querySelectorAll('.list-item').forEach(r => r.style.background = '');
                row.style.background = 'rgba(108,92,231,0.25)';
                if (dictAppSelect) dictAppSelect.value = noun;
                updateSynonymList();
                updateFavButtonState();
            });
            if (listDiv.getAttribute('data-selected') === noun) {
                row.style.background = 'rgba(108,92,231,0.25)';
            }
            frag.appendChild(row);
        });
        if (sortedApps.length > 200) {
            const moreDiv = document.createElement('div');
            moreDiv.style.cssText = 'padding:10px;text-align:center;color:#888;font-size:0.9em;';
            moreDiv.textContent = `... 그 외 ${sortedApps.length - 200}개 앱 생략 (검색어를 입력하세요)`;
            frag.appendChild(moreDiv);
        }
        listDiv.appendChild(frag);
    }
}

function updateFavButtonState() {
    const selectedNoun = dictAppSelect ? dictAppSelect.value : '';
    if (!selectedNoun || !btnToggleFav) return;
    btnToggleFav.style.background = favoritesList.includes(selectedNoun) ? "#fee500" : "#fff";
}

if (btnToggleFav) {
    btnToggleFav.addEventListener('click', async () => {
        const selectedNoun = dictAppSelect ? dictAppSelect.value : '';
        if (!selectedNoun) { alert("즐겨찾기할 앱을 먼저 선택해주세요."); return; }
        await eel.toggle_favorite(selectedNoun)();
        favoritesList = await eel.get_favorites()();
        updateFavButtonState();
        updateAppSelect(dictSearchInput ? dictSearchInput.value.toLowerCase() : '');
        if (dictAppSelect) dictAppSelect.value = selectedNoun;
    });
}

async function updateSynonymList() {
    const selectedNoun = dictAppSelect ? dictAppSelect.value : '';
    if (!selectedNoun) return;
    const path = currentNouns[selectedNoun];
    const syns = await eel.get_synonyms_for_path(path, selectedNoun)();

    const listDiv = document.getElementById('dict-synonyms-list');
    if (!listDiv) return;
    listDiv.innerHTML = '';

    if (syns.length === 0) {
        listDiv.innerHTML = `<span style="color:#888;font-size:0.9em;padding-left:5px;">현재 등록된 별명이 없습니다. 추가해 보세요!</span>`;
        return;
    }
    syns.forEach(syn => {
        const badge = document.createElement('span');
        badge.className = "synonym-badge";
        badge.appendChild(document.createTextNode(`${syn} `));
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'del-syn';
        remove.dataset.syn = syn;
        remove.textContent = '×';
        badge.appendChild(remove);
        listDiv.appendChild(badge);
    });
    listDiv.querySelectorAll('.del-syn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const s = e.target.getAttribute('data-syn');
            if (confirm("동의어 '" + s + "'를 정말 삭제할까요?")) {
                await eel.remove_noun_synonym(s)();
                currentNouns = await eel.get_nouns()();
                updateSynonymList();
            }
        });
    });
}

if (btnUnifiedDict && unifiedDictModal) {
    btnUnifiedDict.addEventListener('click', async () => {
        const pm = document.getElementById('plus-menu');
        if (pm) pm.classList.remove('show');
        unifiedDictModal.style.display = 'flex'; // show the modal
        
        // Load all data
        currentNouns = await eel.get_nouns()();
        favoritesList = await eel.get_favorites()();
        updateAppSelect();
        
        if (typeof renderActionDictionary === 'function') renderActionDictionary();
        if (typeof updateMacroList === 'function') updateMacroList();
        if (typeof updateLearnedMacroList === 'function') updateLearnedMacroList();
    });
}

if (dictSearchInput) {
    let debounceTimer;
    dictSearchInput.addEventListener('input', (e) => { 
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            updateAppSelect(e.target.value.toLowerCase()); 
        }, 300);
    });
}

if (btnAddSynonym) {
    btnAddSynonym.addEventListener('click', async () => {
        const original = dictAppSelect ? dictAppSelect.value : '';
        const synonym = dictSynonymInput ? dictSynonymInput.value.trim() : '';
        if (!synonym) return;
        const success = await eel.add_noun_synonym(original, synonym)();
        if (success) {
            alert("성공! 이제 '" + original + "' 대신 '" + synonym + "'(으)로 부를 수 있어요!");
            if (dictSynonymInput) dictSynonymInput.value = '';
            currentNouns = await eel.get_nouns()();
            updateSynonymList();
            addMessage("이제 '" + synonym + " 열어줘'라고 편하게 명령해도 다 알아들을 수 있어! ✨", true);
        } else {
            alert("저장에 실패했습니다.");
        }
    });
}

if (btnRenameNoun) {
    btnRenameNoun.addEventListener('click', async () => {
        const selectedNoun = dictAppSelect ? dictAppSelect.value : '';
        const newName = dictRenameInput ? dictRenameInput.value.trim() : '';
        if (!selectedNoun) return alert('단어를 먼저 선택해주세요.');
        if (!newName) return alert('변경할 새 이름을 입력해주세요.');
        if (selectedNoun === newName) return alert('기존 이름과 동일합니다!');
        const success = await eel.rename_noun(selectedNoun, newName)();
        if (success) {
            alert("새 이름 '" + newName + "'(으)로 변경되었습니다!");
            if (dictRenameInput) dictRenameInput.value = '';
            currentNouns = await eel.get_nouns()();
            updateAppSelect();
        } else {
            alert("변경에 실패했습니다.");
        }
    });
}

// Custom Noun Add
const btnAddCustom = document.getElementById('btn-add-custom');
if (btnAddCustom) {
    btnAddCustom.addEventListener('click', async () => {
        const noun = document.getElementById('custom-noun-input').value.trim();
        const path = document.getElementById('custom-path-input').value.trim();
        if (noun && path) {
            await eel.add_custom_noun(noun, path)();
            alert("'" + noun + "'이(가) 등록되었습니다!");
            document.getElementById('custom-noun-input').value = '';
            document.getElementById('custom-path-input').value = '';
            currentNouns = await eel.get_nouns()();
            updateAppSelect();
        }
    });
}

// AI Finder
const btnAiSearch = document.getElementById('btn-ai-search');
if (btnAiSearch) {
    btnAiSearch.addEventListener('click', async () => {
        const query = document.getElementById('custom-noun-input').value.trim();
        if (!query) { alert("검색할 앱 이름을 먼저 입력해주세요!"); return; }
        btnAiSearch.textContent = "\uD83D\uDD0D 검색 중...";
        const results = await eel.ai_find_exe(query)();
        const listDiv = document.getElementById('ai-results-list');
        if (listDiv) listDiv.innerHTML = '';
        btnAiSearch.textContent = "✨ 컴퓨터에서 직접 찾기";
        if (results.length === 0) {
            if (listDiv) listDiv.innerHTML = "<div class='ai-result-item' style='color:#ed8796;'>발견된 앱이 없습니다. 직접 경로를 입력해주세요. 😭</div>";
            return;
        }
        results.forEach(res => {
            const div = document.createElement('div');
            div.className = "ai-result-item";
            const name = document.createElement('strong');
            name.textContent = res.name || '';
            const path = document.createElement('small');
            path.textContent = res.path || '';
            div.append(name, path);
            div.addEventListener('click', () => {
                document.getElementById('custom-path-input').value = res.path;
                div.style.borderColor = "#a6e3a1";
                setTimeout(() => div.style.borderColor = "transparent", 500);
            });
            if (listDiv) listDiv.appendChild(div);
        });
    });
}

// ===================================================
// IMAGE MODAL
// ===================================================
const modal = document.getElementById('image-modal');
const span = document.querySelector('#image-modal .close-modal');

if (span) span.onclick = function() { if (modal) modal.style.display = 'none'; };
if (modal) modal.onclick = function(e) { if (e.target === modal) modal.style.display = 'none'; };

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

const nativeCandidateModal = document.getElementById('native-action-candidates-modal');
const nativeCandidateList = document.getElementById('native-action-candidates-list');
const nativeCandidateShowDismissed = document.getElementById('native-candidate-show-dismissed');

function nativeCandidateStatus(record) {
    if (record.status === 'ready_for_review') {
        return record.strong_candidate ? '검토 준비 · 강한 후보' : '검토 준비';
    }
    if (record.status === 'accepted') return '승인됨 · 구현 명세 생성';
    if (record.status === 'rejected') {
        return record.review_reason === 'macro_sufficient' ? '거절 · 매크로로 충분' : '거절됨';
    }
    if (record.status === 'implemented') return '구현 완료';
    if (record.status === 'merged') return '기존 네이티브 기능과 병합';
    return `관찰 중 · 정상 종료 ${record.process_success_count || 0}/${record.threshold || 3}`;
}

function nativeCandidateRiskLabel(value) {
    return ({low: '낮음', medium: '중간', high: '높음'})[value] || '미분류';
}

function nativeCandidateReversibilityLabel(value) {
    return ({easy: '쉬움', limited: '제한적', hard: '어려움'})[value] || '미분류';
}

function nativeCandidateRecommendationLabel(value) {
    return ({
        strongly_recommended: '네이티브화 강력 권장',
        recommended: '네이티브화 권장',
        observe: '추가 관찰',
        macro_sufficient: '기존 매크로로 충분',
        already_native: '이미 네이티브 기능 존재',
        not_recommended: '네이티브화 비권장',
        split_scope: '작은 작업 단위로 분리 필요'
    })[value] || '추가 관찰';
}

function nativeCandidateActionButtons(record) {
    const id = escapeDictionaryHtml(record.candidate_id);
    const button = (status, label, reason = '', extraClass = '') =>
        `<button class="premium-btn ${extraClass} btn-native-candidate-state" data-id="${id}" data-status="${status}" data-reason="${reason}">${label}</button>`;
    if (record.status === 'ready_for_review') {
        return [
            button('accepted', '승인하고 명세 만들기', 'user_approved'),
            button('observing', '보류', 'review_deferred'),
            button('rejected', '매크로로 충분', 'macro_sufficient'),
            button('rejected', '거절', 'user_rejected', 'warning')
        ].join('');
    }
    if (record.status === 'accepted') {
        return [
            button('implemented', '구현 완료', 'implementation_completed'),
            button('merged', '기존 기능과 병합', 'manual_native_merge'),
            button('rejected', '승인 철회', 'approval_withdrawn', 'warning')
        ].join('');
    }
    if (['rejected', 'implemented', 'merged'].includes(record.status)) {
        return button('observing', '다시 관찰', 'review_reopened');
    }
    return [
        button('rejected', '매크로로 충분', 'macro_sufficient'),
        button('rejected', '후보 거절', 'user_rejected', 'warning')
    ].join('');
}

async function loadNativeActionCandidates() {
    if (!nativeCandidateList) return;
    nativeCandidateList.innerHTML = '<div class="learned-library-empty">불러오는 중...</div>';
    const result = await eel.get_native_action_candidates(
        true,
        Boolean(nativeCandidateShowDismissed?.checked)
    )();
    if (!result?.success) {
        nativeCandidateList.innerHTML = `<div class="learning-review-error">${escapeDictionaryHtml(result?.message || '후보를 불러오지 못했습니다.')}</div>`;
        return;
    }
    const records = result.candidates || [];
    if (!records.length) {
        nativeCandidateList.innerHTML = '<div class="learned-library-empty">아직 관찰된 확장 후보가 없습니다.</div>';
        return;
    }
    nativeCandidateList.innerHTML = '';
    records.forEach(record => {
        const card = document.createElement('section');
        card.className = `native-candidate-card ${['ready_for_review', 'accepted'].includes(record.status) ? 'ready' : ''}`;
        const slots = (record.required_slots || [])
            .map(item => `${item.name}:${item.type}`)
            .join(', ') || '필수 슬롯 없음';
        const templates = (record.utterance_templates || []).join(' · ') || '개인 값이 없는 템플릿 없음';
        const processProgress = Math.min(
            100,
            ((record.process_success_count || 0) / (record.threshold || 3)) * 100
        );
        const failureRate = Math.round((record.failure_rate || 0) * 1000) / 10;
        const testObservations = (record.test_process_success_count || 0) + (record.test_failure_count || 0);
        const spec = record.implementation_spec || {};
        const specHtml = Object.keys(spec).length ? `
            <div class="native-candidate-spec">
                <strong>구현 명세</strong>
                <span><b>작업명</b> ${escapeDictionaryHtml(spec.task_name || '')}</span>
                <span><b>변경 유형</b> ${escapeDictionaryHtml(spec.change_type || '')}</span>
                <span><b>검증 방법</b> ${escapeDictionaryHtml(spec.verification_method || '')}</span>
                <span><b>권장 구현 위치</b> ${escapeDictionaryHtml(spec.recommended_implementation_location || '')}</span>
                <span><b>롤백 필요</b> ${spec.rollback_required ? '예' : '아니요'} · <b>코드 자동 생성</b> 안 함</span>
            </div>` : '';
        card.innerHTML = `
            <div class="native-candidate-card-heading">
                <div>
                    <strong>${escapeDictionaryHtml(record.description || record.suggested_native_action_name)}</strong>
                    <small>${escapeDictionaryHtml(record.target_app)} · ${escapeDictionaryHtml(record.normalized_intent)} · 후보 ID ${escapeDictionaryHtml(record.candidate_id)}</small>
                </div>
                <span class="learning-status">${escapeDictionaryHtml(nativeCandidateStatus(record))}</span>
            </div>
            <div class="native-candidate-progress"><span style="width:${processProgress}%"></span></div>
            <div class="native-candidate-meta">
                <span><b>정상 종료</b> ${record.process_success_count || 0}회 · <b>자동 검증 성공</b> ${record.verified_success_count || 0}회 · <b>사용자 확인</b> ${record.user_confirmed_count || 0}회</span>
                <span><b>실패</b> ${record.failure_count || 0}회 · 실패율 ${failureRate}% · <b>서로 다른 문서</b> ${record.distinct_document_count || 0}개</span>
                ${testObservations ? `<span><b>테스트 관찰</b> ${testObservations}회 — 검토 준비 횟수에는 포함하지 않음</span>` : ''}
                <span><b>실제 실행 경로</b> ${escapeDictionaryHtml(record.selected_route || 'python')} · <b>필수 슬롯</b> ${escapeDictionaryHtml(slots)}</span>
                <span><b>위험도</b> ${nativeCandidateRiskLabel(record.risk_level)} · <b>복구 용이성</b> ${nativeCandidateReversibilityLabel(record.reversibility)} · <b>롤백 필요</b> ${record.rollback_required ? '예' : '아니요'}</span>
                <span><b>검증 가능 여부</b> ${record.verification_available ? '가능' : '미정'} · ${escapeDictionaryHtml(record.verification_hint || '')}</span>
                <span><b>매크로 충분 여부</b> ${record.macro_sufficiency === 'sufficient' ? '충분' : record.macro_sufficiency === 'insufficient' ? '불충분' : '미정'} · <b>권장 판단</b> ${nativeCandidateRecommendationLabel(record.native_recommendation)}</span>
                ${record.scope_review_required ? '<span><b>범위 재검토</b> 후보가 너무 넓어 작은 작업 단위로 나눠야 합니다.</span>' : ''}
                <span><b>명령 템플릿</b> ${escapeDictionaryHtml(templates)}</span>
                <span><b>최초 관찰</b> ${escapeDictionaryHtml(record.first_seen_at || '')} · <b>최근 관찰</b> ${escapeDictionaryHtml(record.last_seen_at || '')}</span>
            </div>
            ${specHtml}
            <div class="native-candidate-notice">개발 검토와 구현 명세만 관리합니다. 코드 작성·설치·자동 승격은 수행하지 않습니다.</div>
            <div class="learned-library-actions">
                ${nativeCandidateActionButtons(record)}
            </div>`;
        nativeCandidateList.appendChild(card);
    });
    nativeCandidateList.querySelectorAll('.btn-native-candidate-state').forEach(button => {
        button.addEventListener('click', async () => {
            button.disabled = true;
            const changed = await eel.set_native_action_candidate_status(
                button.dataset.id,
                button.dataset.status,
                button.dataset.reason || ''
            )();
            if (!changed?.success) {
                alert(changed?.message || '후보 상태를 변경하지 못했습니다.');
            }
            await loadNativeActionCandidates();
        });
    });
}

document.getElementById('btn-native-action-candidates')?.addEventListener('click', async () => {
    document.getElementById('plus-menu')?.classList.remove('show');
    if (nativeCandidateModal) nativeCandidateModal.style.display = 'flex';
    await loadNativeActionCandidates();
});
document.getElementById('btn-refresh-native-candidates')?.addEventListener('click', loadNativeActionCandidates);
nativeCandidateShowDismissed?.addEventListener('change', loadNativeActionCandidates);

async function updateLearnedMacroList(filter = '') {
    if (!learnedMacroListDiv) return;
    learnedMacroListDiv.innerHTML = '<div class="learned-library-empty">불러오는 중...</div>';
    let records;
    try {
        const loader = () => eel.get_learned_macros()();
        records = typeof window.runUiLoadWithRetry === 'function'
            ? await window.runUiLoadWithRetry(loader)
            : await loader();
    } catch (error) {
        console.warn('학습 행동 초기 로딩 실패', error);
        learnedMacroListDiv.innerHTML = '<div class="learned-library-empty">학습 행동을 불러오지 못했습니다. 관리 창을 다시 열어주세요.</div>';
        return;
    }
    const query = String(filter || '').trim().toLowerCase();
    const visible = (records || []).filter(record => {
        if (!query) return true;
        return [
            record.name, record.app, record.description, record.intent,
            record.state, record.state_reason, record.run_policy,
            ...(record.verbs || []), ...(record.utterances || [])
        ].some(value => String(value || '').toLowerCase().includes(query));
    });
    learnedMacroListDiv.innerHTML = '';
    if (!visible.length) {
        learnedMacroListDiv.innerHTML = '<div class="learned-library-empty">조건에 맞는 학습 행동이 없습니다.</div>';
        return;
    }

    visible.forEach(record => {
        const card = document.createElement('section');
        card.className = 'learned-library-card';
        card.dataset.app = record.app;
        card.dataset.name = record.name;
        const lastUsed = record.last_used_at ? record.last_used_at.replace('T', ' ') : '기록 없음';
        const nouns = (record.nouns || []).map(item => item.text).filter(Boolean).join(', ') || '없음';
        const slots = (record.slots || []).map(item => `${item.name}=${item.value}`).join(', ') || '없음';
        card.innerHTML = `
            <div class="learned-library-heading">
                <div>
                    <strong>${escapeDictionaryHtml(record.description || record.name)}</strong>
                    <small>${escapeDictionaryHtml(record.app)} · ${escapeDictionaryHtml(record.intent)} · ${escapeDictionaryHtml(learnedKindLabel(record.kind))}</small>
                </div>
                <span class="learning-status">${escapeDictionaryHtml(learnedStateLabel(record.state))} · ${escapeDictionaryHtml(learnedVerificationLabel(record.verification_status))} · ${escapeDictionaryHtml(learnedRunPolicyLabel(record.run_policy))}</span>
            </div>
            <div class="learned-library-stats">${escapeDictionaryHtml(learnedStatusText(record))}<br>자동 검증 성공 ${record.verified_success_count || 0}회 · 연속 ${record.consecutive_verified_success || 0}회 · 마지막 사용: ${escapeDictionaryHtml(lastUsed)}</div>
            <label>매크로 이름<input class="premium-input learned-library-name" value="${escapeDictionaryHtml(record.name)}"></label>
            <label>발동 문장 — 한 줄에 하나<textarea class="premium-input learned-library-utterances" rows="4">${escapeDictionaryHtml((record.utterances || []).join('\n'))}</textarea></label>
            <label>핵심 동사 — 쉼표로 구분<input class="premium-input learned-library-verbs" value="${escapeDictionaryHtml((record.verbs || []).join(', '))}"></label>
            <label>실행 상태<select class="premium-input learned-library-state">
                <option value="active" ${record.state === 'active' ? 'selected' : ''}>사용 중</option>
                <option value="needs_review" ${record.state === 'needs_review' ? 'selected' : ''}>검토 필요</option>
                <option value="broken" ${record.state === 'broken' ? 'selected' : ''}>고장</option>
                <option value="disabled" ${record.state === 'disabled' ? 'selected' : ''}>일시 정지</option>
            </select></label>
            <label>실행 정책<select class="premium-input learned-library-run-policy">
                <option value="confirm" ${record.run_policy !== 'auto' ? 'selected' : ''}>실행 전 확인</option>
                <option value="auto" ${record.run_policy === 'auto' ? 'selected' : ''}>사용자 승인 자동 실행</option>
            </select></label>
            ${record.state_reason ? `<div class="learned-state-reason">${escapeDictionaryHtml(record.state_reason)}</div>` : ''}
            <div class="learned-library-meta"><span><b>명사</b> ${escapeDictionaryHtml(nouns)}</span><span><b>슬롯</b> ${escapeDictionaryHtml(slots)}</span></div>
            <div class="learning-review-error" aria-live="polite"></div>
            <div class="learned-library-actions">
                <button class="premium-btn primary btn-save-learned">수정 저장</button>
                ${record.step_count ? `<input class="premium-input learned-retry-step" type="number" min="1" max="${record.step_count}" value="1" title="재시작 단계"><button class="premium-btn btn-retry-learned">단계부터 재시도</button>` : ''}
                <button class="premium-btn warning btn-delete-learned">완전 삭제</button>
            </div>`;
        learnedMacroListDiv.appendChild(card);

        card.querySelector('.btn-save-learned').addEventListener('click', async () => {
            const error = card.querySelector('.learning-review-error');
            const edits = {
                name: card.querySelector('.learned-library-name').value.trim(),
                utterances: card.querySelector('.learned-library-utterances').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
                verbs: card.querySelector('.learned-library-verbs').value.split(/[,\n]/).map(value => value.trim()).filter(Boolean),
                state: card.querySelector('.learned-library-state').value,
                run_policy: card.querySelector('.learned-library-run-policy').value
            };
            const result = await eel.update_learned_macro(record.app, record.name, edits)();
            if (!result?.success) {
                error.textContent = result?.message || '수정 저장에 실패했습니다.';
                return;
            }
            await updateLearnedMacroList(learnedMacroSearch?.value || '');
        });
        card.querySelector('.btn-delete-learned').addEventListener('click', async () => {
            if (!confirm(`학습 행동 '${record.name}'을 완전히 삭제할까요?`)) return;
            await eel.delete_learned_macro(record.app, record.name)();
            await updateLearnedMacroList(learnedMacroSearch?.value || '');
        });
        card.querySelector('.btn-retry-learned')?.addEventListener('click', async () => {
            const step = Number(card.querySelector('.learned-retry-step')?.value || 1);
            const result = await eel.retry_learned_macro_step(record.app, record.name, step)();
            if (!result?.success) {
                card.querySelector('.learning-review-error').textContent = result?.message || '단계 재시도에 실패했습니다.';
                return;
            }
            alert(`${step}단계부터 재실행을 완료했습니다.`);
            await updateLearnedMacroList(learnedMacroSearch?.value || '');
        });
    });
}

if (learnedMacroSearch) {
    let learnedSearchTimer;
    learnedMacroSearch.addEventListener('input', event => {
        clearTimeout(learnedSearchTimer);
        learnedSearchTimer = setTimeout(
            () => updateLearnedMacroList(event.target.value), 200
        );
    });
}

const macroTypeSelect = document.getElementById('macro-type-select');
const macroDataInput = document.getElementById('macro-data-input');
if (macroTypeSelect && macroDataInput) {
    macroTypeSelect.addEventListener('change', () => {
        const t = macroTypeSelect.value;
        if (t === 'hotkey') macroDataInput.placeholder = "어떤 단축키를 누를까요? (예: win+shift+s)";
        else if (t === 'cmd') macroDataInput.placeholder = "실행할 프로그램과 인수 (예: calc 또는 notepad 메모.txt) — 실행 전 확인";
        else macroDataInput.placeholder = "어떤 주문들을 연속으로 할까요? 쉼표로 구분 (예: 볼륨 줄여, 유튜브 열어)";
    });
}

const macroTemplateSelect = document.getElementById('macro-template-select');
if (macroTemplateSelect) {
    const templates = {
        "hotkey_screenshot": { id: "MY_SCREENSHOT", type: "hotkey", data: "win+shift+s", synonyms: "화면 캡처, 스크린샷, 캡처해" },
        "hotkey_desktop": { id: "MY_DESKTOP", type: "hotkey", data: "win+d", synonyms: "바탕화면, 바탕화면 보여줘" },
        "hotkey_copy": { id: "MY_COPY", type: "hotkey", data: "ctrl+c", synonyms: "복사, 이거 복사해" },
        "cmd_calc": { id: "MY_CALC", type: "cmd", data: "calc", synonyms: "계산기, 계산기 켜" },
        "cmd_taskmgr": { id: "MY_TASKMGR", type: "cmd", data: "taskmgr", synonyms: "작업 관리자, 작업관리자" },
        "compound_example": { id: "MY_COMPOUND", type: "compound", data: "볼륨 줄여, 유튜브 찾아줘", synonyms: "조용히 유튜브" }
    };
    macroTemplateSelect.addEventListener('change', (e) => {
        const val = e.target.value;
        if (val && templates[val]) {
            const t = templates[val];
            const n = document.getElementById('macro-name-input'); if(n) n.value = t.id;
            const ty = document.getElementById('macro-type-select'); if(ty) ty.value = t.type;
            const d = document.getElementById('macro-data-input'); if(d) d.value = t.data;
            const s = document.getElementById('macro-synonyms-input'); if(s) s.value = t.synonyms;
        } else {
            ['macro-name-input','macro-data-input','macro-synonyms-input'].forEach(id => { const el = document.getElementById(id); if(el) el.value = ''; });
        }
    });
}

if (btnAddMacro) {
    btnAddMacro.addEventListener('click', async () => {
        const macroNameInput = document.getElementById('macro-name-input');
        const mType = document.getElementById('macro-type-select').value;
        const mData = document.getElementById('macro-data-input').value.trim();
        const mSynonyms = document.getElementById('macro-synonyms-input').value.trim();
        if (!mData || !mSynonyms) {
            alert("실행 내용과 호출할 명령어를 모두 입력해주세요!");
            return;
        }
        const synonyms = mSynonyms.split(',').map(s => s.trim()).filter(Boolean);
        const mId = macroNameInput && macroNameInput.value.trim()
            ? macroNameInput.value.trim()
            : `CUSTOM_${Date.now()}`;
        const result = await eel.add_custom_macro(mId, synonyms[0], synonyms, mType, mData)();
        if (result?.success === false || result !== true) {
            alert(result?.message || '매크로를 저장하지 못했습니다.');
            return;
        }
        alert("매크로 '" + mId + "'(이)가 추가되었습니다!");
        ['macro-name-input','macro-data-input','macro-synonyms-input'].forEach(id => { const el = document.getElementById(id); if(el) el.value = ''; });
        updateMacroList();
    });
}



// ===================================================
// AI SETTINGS MODAL
// ===================================================
const btnAiSettings = document.getElementById('btn_ai_settings');
const aiSettingsModal = document.getElementById('ai-settings-modal');
const closeAiSettings = document.querySelector('.close-ai-settings');
const btnSaveAiSettings = document.getElementById('btn-save-ai-settings');
const btnClearAiApiKey = document.getElementById('btn-clear-ai-api-key');
const aiProvider = document.getElementById('ai-provider');
const aiApiKey = document.getElementById('ai-api-key');
const aiApiKeyStatus = document.getElementById('ai-api-key-status');

function renderAiApiKeyState(config) {
    const hasApiKey = Boolean(config?.has_api_key);
    if (aiApiKey) {
        aiApiKey.value = '';
        aiApiKey.placeholder = hasApiKey
            ? '새 키 입력 시 기존 키 교체'
            : 'API 키 입력';
    }
    if (aiApiKeyStatus) {
        aiApiKeyStatus.textContent = hasApiKey
            ? 'API 키가 저장되어 있습니다. 빈칸으로 저장하면 기존 키를 유지합니다.'
            : '저장된 API 키가 없습니다.';
    }
    if (btnClearAiApiKey) btnClearAiApiKey.disabled = !hasApiKey;
}

if (btnAiSettings) {
    btnAiSettings.addEventListener('click', async () => {
        const config = await eel.get_ai_config()();
        if (aiProvider) aiProvider.value = config.provider || "openai";
        renderAiApiKeyState(config);
        const ollamaEl = document.getElementById('ai-ollama-model');
        if (ollamaEl) ollamaEl.value = config.ollama_model || "llama3";
        const routingEl = document.getElementById('ai-routing-mode');
        if (routingEl) routingEl.value = config.routing_mode || "auto";
        if (aiSettingsModal) aiSettingsModal.style.display = 'block';
    });
}
if (closeAiSettings) closeAiSettings.addEventListener('click', () => { if (aiSettingsModal) aiSettingsModal.style.display = 'none'; });

if (btnSaveAiSettings) {
    btnSaveAiSettings.addEventListener('click', async () => {
        const provider = aiProvider ? aiProvider.value : "openai";
        const apiKey = aiApiKey ? aiApiKey.value.trim() : "";
        const ollamaModel = document.getElementById('ai-ollama-model') ? document.getElementById('ai-ollama-model').value.trim() : "llama3";
        const routingMode = document.getElementById('ai-routing-mode') ? document.getElementById('ai-routing-mode').value : "auto";

        const success = await eel.save_ai_config(provider, apiKey, ollamaModel, routingMode)();
        if (success) {
            alert("AI 설정이 저장되었습니다!");
            window._cachedAiConfig = await eel.get_ai_config()();
            renderAiApiKeyState(window._cachedAiConfig);
            if (aiSettingsModal) aiSettingsModal.style.display = 'none';
        } else {
            alert("설정 저장에 실패했습니다.");
        }
    });
}

if (btnClearAiApiKey) {
    btnClearAiApiKey.addEventListener('click', async () => {
        if (!confirm('저장된 API 키를 삭제할까요? 이 작업은 되돌릴 수 없습니다.')) return;
        const success = await eel.clear_ai_api_key(true)();
        if (!success) {
            alert('API 키 삭제에 실패했습니다.');
            return;
        }
        window._cachedAiConfig = await eel.get_ai_config()();
        renderAiApiKeyState(window._cachedAiConfig);
        alert('저장된 API 키를 삭제했습니다.');
    });
}

// ===================================================
// ACTION DICTIONARY MODAL
// ===================================================
const actionDictList = document.getElementById('action-dict-list');

window.addEventListener('click', (e) => {
    if (e.target === aiSettingsModal) aiSettingsModal.style.display = 'none';
});

// ===================================================
// ACTION DICTIONARY RENDER
// ===================================================
async function renderActionDictionary(filter = '') {
    if (!actionDictList) return;
    try {
        actionDictList.innerHTML = '';
        const macros = await eel.get_macros()();
        for (const [macro, data] of Object.entries(macros)) {
            // Filter out custom macros
            if (data.type && data.type !== 'default') continue;
            
            // Search filter
            if (filter) {
                const searchLower = filter.toLowerCase();
                const macroMatch = macro.toLowerCase().includes(searchLower);
                const descMatch = (data.description || '').toLowerCase().includes(searchLower);
                const synMatch = (data.synonyms || []).some(s => s.toLowerCase().includes(searchLower));
                if (!macroMatch && !descMatch && !synMatch) continue;
            }

            const itemDiv = document.createElement('div');
            itemDiv.className = 'list-item';
            itemDiv.style.cssText = 'padding:10px;border-bottom:1px solid rgba(255,255,255,0.1);display:flex;flex-direction:column;gap:6px;';

            let codeHtml = '';
            if (data.description) {
                const codeBox = document.createElement('div');
                codeBox.style.cssText = 'color:#666;font-size:0.9em;margin-bottom:5px;';
                codeBox.textContent = data.description;
                codeHtml = codeBox.outerHTML;
            } else if (data.code) {
                const codeBox = document.createElement('pre');
                codeBox.style.cssText = 'background:rgba(0,0,0,0.05);padding:6px 8px;border-radius:5px;font-size:0.8em;max-height:80px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;width:100%;';
                codeBox.textContent = data.code;
                codeHtml = codeBox.outerHTML;
            }


        itemDiv.innerHTML = `
            <div><strong>${escapeDictionaryHtml(macro)}</strong></div>
            ${codeHtml}
        `;

        const synContainer = document.createElement('div');
        synContainer.style.cssText = 'display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px;';
        (data.synonyms || []).forEach(syn => {
            const badge = document.createElement('span');
            badge.className = 'synonym-badge';
            badge.textContent = `${syn} ×`;
            badge.style.cursor = 'pointer';
            badge.addEventListener('click', async () => {
                if (confirm("'" + syn + "' 동의어를 삭제하시겠습니까?")) {
                    await eel.remove_macro_synonym(macro, syn)();
                    renderActionDictionary();
                }
            });
            synContainer.appendChild(badge);
        });
        itemDiv.appendChild(synContainer);

        const addDiv = document.createElement('div');
        addDiv.style.cssText = 'display:flex;gap:5px;width:100%;';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'premium-input';
        input.placeholder = '새 행동/말투 추가 (예: 꺄)';
        input.style.flex = '1';
        const btnAdd = document.createElement('button');
        btnAdd.className = 'premium-btn primary';
        btnAdd.textContent = '추가';
        btnAdd.addEventListener('click', async () => {
            const newSyn = input.value.trim();
            if (newSyn) { await eel.add_macro_synonym(macro, newSyn)(); renderActionDictionary(); }
        });
        input.addEventListener('keypress', (e) => { if (e.key === 'Enter') btnAdd.click(); });
        addDiv.appendChild(input);
        addDiv.appendChild(btnAdd);
        itemDiv.appendChild(addDiv);
        if (actionDictList) actionDictList.appendChild(itemDiv);
    }
    } catch (e) {
        if (actionDictList) actionDictList.innerHTML = `<div style="color:red;padding:10px;">Error: ${e.message}</div>`;
        console.error(e);
    }
}

const actionDictSearch = document.getElementById('action-dict-search');
if (actionDictSearch) {
    actionDictSearch.addEventListener('input', (e) => {
        renderActionDictionary(e.target.value);
    });
}

