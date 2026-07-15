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
