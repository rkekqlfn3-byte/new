function learningListText(values, formatter) {
    if (!Array.isArray(values) || values.length === 0) return '없음';
    return values.map(formatter || (value => String(value))).join(', ');
}

function learningStepText(candidate) {
    const plan = Array.isArray(candidate.plan) ? candidate.plan : [];
    if (plan.length) {
        return plan.map((step, index) => {
            const fileActions = new Set(['copy_file', 'move_file', 'write_text_file']);
            const overwrite = fileActions.has(step.action)
                ? (step.overwrite === true ? '덮어쓰기 허용' : '기존 파일 보호')
                : '';
            const details = [step.target, step.direction, step.text, overwrite].filter(Boolean).join(' / ');
            return `${index + 1}. ${step.action}${details ? ` — ${details}` : ''}`;
        }).join('\n');
    }
    const steps = Array.isArray(candidate.explanation_steps) ? candidate.explanation_steps : [];
    return steps.length ? steps.map((step, index) => `${index + 1}. ${step.step || '실행'}`).join('\n') : '실행 단계 정보 없음';
}

function createLearningReviewCard(candidate, index) {
    const card = document.createElement('section');
    card.className = 'learning-review-card';
    card.dataset.index = String(candidate?.index ?? index);

    const heading = document.createElement('div');
    heading.className = 'learning-review-heading';
    heading.append(
        window.createTextElement('strong', candidate?.description || '학습 행동'),
        window.createTextElement(
            'span',
            ['verified', 'passed'].includes(candidate?.verification_status)
                ? '자동 확인 완료' : '사용자 확인 필요',
            'learning-status'
        )
    );
    card.appendChild(heading);

    const field = (labelText, className, value, multiline = false, readOnly = false) => {
        const label = document.createElement('label');
        label.appendChild(document.createTextNode(labelText));
        const control = document.createElement(multiline ? 'textarea' : 'input');
        control.className = `premium-input ${className}`;
        control.value = String(value ?? '');
        if (multiline) control.rows = 4;
        control.readOnly = readOnly;
        label.appendChild(control);
        return label;
    };
    card.append(
        field('매크로 이름', 'learning-name', candidate?.name),
        field('발동 문장 — 한 줄에 하나', 'learning-utterances',
            (candidate?.utterances || []).join('\n'), true),
        field('핵심 동사 — 쉼표로 구분', 'learning-verbs',
            (candidate?.verbs || []).join(', '))
    );

    const details = document.createElement('div');
    details.className = 'learning-detail-grid';
    const nouns = learningListText(candidate?.nouns, item => {
        const canonical = item?.canonical ? ` → ${item.canonical}` : '';
        return `${item?.text || ''}${canonical} (${item?.type || 'general'})`;
    });
    const slots = learningListText(candidate?.slots,
        item => `${item?.name || ''}=${item?.value || ''} (${item?.type || ''})`
    );
    [
        ['의도', candidate?.intent], ['대상', candidate?.app],
        ['명사', nouns], ['슬롯', slots],
    ].forEach(([label, value]) => {
        const item = document.createElement('div');
        item.append(
            window.createTextElement('b', label),
            window.createTextElement('span', value)
        );
        details.appendChild(item);
    });
    card.append(
        details,
        field('실행 단계', 'learning-steps', learningStepText(candidate || {}), true, true)
    );
    return card;
}

async function openLearningReview() {
    const modal = document.getElementById('learning-review-modal');
    const list = document.getElementById('learning-review-list');
    const error = document.getElementById('learning-review-error');
    if (!modal || !list) return;
    try {
        const review = await eel.get_pending_learning_review()();
        if (!review || !review.pending || !review.candidates.length) {
            addSystemMessage('검토할 학습 후보가 없습니다.');
            return;
        }
        if (error) error.textContent = '';
        list.replaceChildren();
        review.candidates.forEach((candidate, index) => {
            list.appendChild(createLearningReviewCard(candidate, index));
        });
        modal.style.display = 'block';
    } catch (reviewError) {
        addSystemError(reviewError.message || String(reviewError));
    }
}

async function approveLearningReview() {
    const modal = document.getElementById('learning-review-modal');
    const error = document.getElementById('learning-review-error');
    const cards = Array.from(document.querySelectorAll('.learning-review-card'));
    const edits = cards.map(card => ({
        index: Number(card.dataset.index),
        name: card.querySelector('.learning-name')?.value.trim() || '',
        utterances: (card.querySelector('.learning-utterances')?.value || '').split(/\r?\n/).map(value => value.trim()).filter(Boolean),
        verbs: (card.querySelector('.learning-verbs')?.value || '').split(/[,\n]/).map(value => value.trim()).filter(Boolean)
    }));
    try {
        const result = await eel.approve_pending_learning(edits)();
        if (!result?.success) {
            if (error) error.textContent = result?.message || '학습 저장에 실패했습니다.';
            return;
        }
        if (modal) modal.style.display = 'none';
        addMessage(result.message, true);
        if (typeof updateLearnedMacroList === 'function') {
            await updateLearnedMacroList();
        }
        if (typeof saveCurrentSession === 'function') await saveCurrentSession();
    } catch (approveError) {
        if (error) error.textContent = approveError.message || String(approveError);
    }
}

async function discardPendingLearning(reason = 'discard') {
    try {
        const result = await eel.reject_pending_learning(reason)();
        const modal = document.getElementById('learning-review-modal');
        if (modal) modal.style.display = 'none';
        addMessage(result?.message || '학습 후보를 저장하지 않았습니다.', true);
    } catch (discardError) {
        addSystemError(discardError.message || String(discardError));
    }
}

document.getElementById('btn-approve-learning')?.addEventListener('click', approveLearningReview);
document.getElementById('btn-run-once-learning')?.addEventListener('click', () => discardPendingLearning('run_once'));
document.getElementById('btn-discard-learning')?.addEventListener('click', () => discardPendingLearning('discard'));
