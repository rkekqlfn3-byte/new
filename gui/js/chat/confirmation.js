function getConfirmationFromResponse(response) {
    if (!response || typeof response !== 'object') return null;
    if (
        response.status !== 'confirmation_required'
        && response.data?.confirmation_pending !== true
    ) return null;
    const confirmation = response.data?.confirmation;
    return confirmation && confirmation.confirmation_id ? confirmation : null;
}

function createConfirmationCard(confirmation) {
    const card = document.createElement('div');
    card.className = 'confirmation-card';
    card.dataset.confirmationId = String(confirmation?.confirmation_id || '');
    card.appendChild(window.createTextElement(
        'div', '선택이 필요합니다', 'confirmation-card-heading'
    ));

    const message = document.createElement('div');
    message.className = 'confirmation-card-message';
    window.appendTextLineBreaks(message, confirmation?.message || '계속할까요?');
    card.appendChild(message);

    if (['destructive_action', 'external_program'].includes(confirmation?.reason)) {
        card.appendChild(window.createTextElement(
            'div', '⚠️ 기존 데이터가 변경될 수 있습니다.', 'confirmation-warning'
        ));
    }

    const options = document.createElement('div');
    options.className = 'confirmation-options';
    (Array.isArray(confirmation?.options) ? confirmation.options : []).forEach(option => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = [
            'confirmation-option', option?.recommended ? 'recommended' : '',
            option?.danger ? 'danger' : '', option?.cancel ? 'cancel' : '',
        ].filter(Boolean).join(' ');
        button.dataset.optionId = String(option?.id || '');
        const title = window.createTextElement(
            'span', option?.label || '선택', 'confirmation-option-title'
        );
        button.appendChild(title);
        if (option?.recommended) {
            button.appendChild(window.createTextElement(
                'span', '추천', 'confirmation-badge'
            ));
        }
        if (option?.description) {
            button.appendChild(window.createTextElement(
                'span', option.description, 'confirmation-option-description'
            ));
        }
        options.appendChild(button);
    });
    card.appendChild(options);

    if (confirmation?.rememberable) {
        const remember = document.createElement('label');
        remember.className = 'confirmation-remember';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.className = 'confirmation-remember-input';
        remember.append(input, document.createTextNode('앞으로 같은 요청에는 이 방식 사용'));
        card.appendChild(remember);
    }
    const status = document.createElement('div');
    status.className = 'confirmation-choice-status';
    status.setAttribute('aria-live', 'polite');
    card.appendChild(status);
    return card;
}

function bindConfirmationCard(card) {
    if (!card || card.dataset.bound === 'true') return;
    card.dataset.bound = 'true';
    card.querySelectorAll('.confirmation-option').forEach(button => {
        button.addEventListener('click', async () => {
            if (card.dataset.busy === 'true' || card.dataset.consumed === 'true') return;
            const confirmationId = card.dataset.confirmationId;
            const optionId = button.dataset.optionId;
            const label = button.querySelector('.confirmation-option-title')?.childNodes[0]?.textContent?.trim()
                || button.innerText.trim();
            const buttons = Array.from(card.querySelectorAll('.confirmation-option'));
            const status = card.querySelector('.confirmation-choice-status');
            const rememberPreference = Boolean(
                card.querySelector('.confirmation-remember-input')?.checked
            );
            card.dataset.busy = 'true';
            buttons.forEach(item => { item.disabled = true; });
            if (status) status.textContent = '선택을 처리하고 있습니다...';
            addMessage(label, false);
            try {
                const result = await eel.resolve_confirmation(
                    confirmationId, optionId, currentSessionId, rememberPreference
                )();
                const nextConfirmation = getConfirmationFromResponse(result);
                const currentStillPending = Boolean(
                    result?.data?.confirmation_pending
                    && nextConfirmation?.confirmation_id === confirmationId
                );
                if (currentStillPending) {
                    card.dataset.busy = 'false';
                    buttons.forEach(item => { item.disabled = false; });
                    if (status) status.textContent = '처리되지 않았습니다. 다시 선택할 수 있습니다.';
                    addSystemError(
                        result?.message || '확인 응답을 처리하지 못했습니다. 잠시 후 다시 시도해주세요.'
                    );
                    return;
                }
                card.dataset.consumed = 'true';
                card.classList.add('consumed');
                if (status) status.textContent = `선택 완료: ${label}`;
                if (nextConfirmation) {
                    addConfirmationCard(result);
                } else {
                    addMessage(result?.message || result?.response || '확인 응답을 처리했습니다.', true);
                }
                if (typeof saveCurrentSession === 'function') await saveCurrentSession();
            } catch (error) {
                card.dataset.busy = 'false';
                buttons.forEach(item => { item.disabled = false; });
                if (status) status.textContent = '';
                addSystemError(error.message || String(error));
            }
        });
    });
}

function markActiveConfirmationResolved(label) {
    const card = chatArea.querySelector('.confirmation-card:not(.consumed)');
    if (!card) return;
    card.dataset.consumed = 'true';
    card.classList.add('consumed');
    card.querySelectorAll('.confirmation-option').forEach(button => {
        button.disabled = true;
    });
    const status = card.querySelector('.confirmation-choice-status');
    if (status) status.textContent = `응답 처리 완료: ${label}`;
}

function addConfirmationCard(response, existingMessage=null) {
    const confirmation = getConfirmationFromResponse(response);
    if (!confirmation) return false;
    const alreadyRendered = Array.from(
        chatArea.querySelectorAll('[data-confirmation-id]')
    ).some(element => element.dataset.confirmationId === confirmation.confirmation_id);
    if (alreadyRendered) {
        if (existingMessage) existingMessage.remove();
        return true;
    }
    const msgDiv = existingMessage || document.createElement('div');
    msgDiv.removeAttribute('id');
    msgDiv.className = 'message incoming confirmation-message';
    msgDiv.dataset.rawContent = confirmation.message || response.message || '';
    msgDiv.dataset.confirmationId = confirmation.confirmation_id;
    const content = document.createElement('div');
    content.className = 'message-content';
    content.appendChild(window.createTextElement(
        'div', 'Jarvis ⚡', 'sender-name current-name'
    ));
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    const card = createConfirmationCard(confirmation);
    bubble.appendChild(card);
    content.appendChild(bubble);
    msgDiv.replaceChildren(content);
    if (!existingMessage) chatArea.appendChild(msgDiv);
    bindConfirmationCard(card);
    scrollToBottom();
    return true;
}

async function restorePendingConfirmationCard(sessionId=currentSessionId) {
    try {
        const result = await eel.get_pending_confirmation(sessionId)();
        if (!result?.pending || !result.confirmation) return;
        addConfirmationCard({
            status: 'confirmation_required',
            message: result.confirmation.message,
            data: { confirmation: result.confirmation }
        });
    } catch (error) {
        console.warn('확인 요청 복원 실패', error);
    }
}
