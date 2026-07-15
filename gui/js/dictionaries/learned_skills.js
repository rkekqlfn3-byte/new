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
        const lastUsed = record.last_used_at ? String(record.last_used_at).replace('T', ' ') : '기록 없음';
        const nouns = (record.nouns || []).map(item => item.text).filter(Boolean).join(', ') || '없음';
        const slots = (record.slots || []).map(item => `${item.name}=${item.value}`).join(', ') || '없음';
        const verifiedSuccessCount = safeDictionaryInteger(record.verified_success_count);
        const consecutiveSuccess = safeDictionaryInteger(record.consecutive_verified_success);
        const stepCount = safeDictionaryInteger(record.step_count, 0, 0, 10000);
        card.innerHTML = `
            <div class="learned-library-heading">
                <div>
                    <strong>${escapeDictionaryHtml(record.description || record.name)}</strong>
                    <small>${escapeDictionaryHtml(record.app)} · ${escapeDictionaryHtml(record.intent)} · ${escapeDictionaryHtml(learnedKindLabel(record.kind))}</small>
                </div>
                <span class="learning-status">${escapeDictionaryHtml(learnedStateLabel(record.state))} · ${escapeDictionaryHtml(learnedVerificationLabel(record.verification_status))} · ${escapeDictionaryHtml(learnedRunPolicyLabel(record.run_policy))}</span>
            </div>
            <div class="learned-library-stats">${escapeDictionaryHtml(learnedStatusText(record))}<br>자동 검증 성공 ${verifiedSuccessCount}회 · 연속 ${consecutiveSuccess}회 · 마지막 사용: ${escapeDictionaryHtml(lastUsed)}</div>
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
                ${stepCount ? `<input class="premium-input learned-retry-step" type="number" min="1" max="${stepCount}" value="1" title="재시작 단계"><button class="premium-btn btn-retry-learned">단계부터 재시도</button>` : ''}
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
