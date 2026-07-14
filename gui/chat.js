window.conversationSummary = window.conversationSummary || "";

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatMessageContent(text, isIncoming, image_data = null) {
    let contentHtml = '';
    if (image_data) {
        contentHtml += `<img src="${image_data}" class="chat-image-attachment" onclick="document.getElementById('image-modal').style.display='block'; document.getElementById('modal-img').src=this.src;"><br>`;
    }
    if (!text) return contentHtml;

    let processed = text.trim();
    let parsedText = (typeof marked !== 'undefined') ? marked.parse(processed) : processed;
    if (parsedText.includes('[응/아니오]')) {
        parsedText = parsedText.replace('[응/아니오]', `<div style="margin-top: 10px; display: flex; gap: 10px;">
            <button class="premium-btn primary" onclick="openLearningReview()">학습 내용 검토</button>
            <button class="premium-btn" onclick="discardPendingLearning('run_once')">이번만 실행</button>
            <button class="premium-btn" style="background:#ff5555; color:white; border:none;" onclick="discardPendingLearning('discard')">폐기</button>
        </div>`);
    }
    return contentHtml + parsedText;
}

function finalizeStreamMessage(msgDiv, rawText) {
    if (!msgDiv || !rawText) return;
    msgDiv.dataset.rawContent = rawText;
    const bubble = msgDiv.querySelector('.bubble');
    if (bubble) {
        bubble.style.opacity = '1';
        bubble.innerHTML = formatMessageContent(rawText, true);
    }
    scrollToBottom();
}

function sendDirectMessage(text) {
    chatInput.value = text;
    sendMessage();
}

function getConfirmationFromResponse(response) {
    if (!response || typeof response !== 'object') return null;
    if (response.status !== 'confirmation_required') return null;
    const confirmation = response.data?.confirmation;
    return confirmation && confirmation.confirmation_id ? confirmation : null;
}

function confirmationCardHtml(confirmation) {
    const options = Array.isArray(confirmation.options) ? confirmation.options : [];
    const optionHtml = options.map(option => {
        const classes = [
            'confirmation-option',
            option.recommended ? 'recommended' : '',
            option.danger ? 'danger' : '',
            option.cancel ? 'cancel' : ''
        ].filter(Boolean).join(' ');
        const badge = option.recommended ? '<span class="confirmation-badge">추천</span>' : '';
        return `<button type="button" class="${classes}" data-option-id="${escapeHtml(option.id || '')}">
            <span class="confirmation-option-title">${escapeHtml(option.label || '선택')}${badge}</span>
            ${option.description ? `<span class="confirmation-option-description">${escapeHtml(option.description)}</span>` : ''}
        </button>`;
    }).join('');
    const warning = confirmation.reason === 'destructive_action'
        ? '<div class="confirmation-warning">⚠️ 기존 데이터가 변경될 수 있습니다.</div>'
        : '';
    const remember = confirmation.rememberable
        ? `<label class="confirmation-remember">
            <input type="checkbox" class="confirmation-remember-input">
            <span>앞으로 같은 요청에는 이 방식 사용</span>
        </label>`
        : '';
    return `<div class="confirmation-card" data-confirmation-id="${escapeHtml(confirmation.confirmation_id)}">
        <div class="confirmation-card-heading">선택이 필요합니다</div>
        <div class="confirmation-card-message">${escapeHtml(confirmation.message || '계속할까요?').replace(/\n/g, '<br>')}</div>
        ${warning}
        <div class="confirmation-options">${optionHtml}</div>
        ${remember}
        <div class="confirmation-choice-status" aria-live="polite"></div>
    </div>`;
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
                card.dataset.consumed = 'true';
                card.classList.add('consumed');
                if (status) status.textContent = `선택 완료: ${label}`;
                const nextConfirmation = getConfirmationFromResponse(result);
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
    if (chatArea.querySelector(`[data-confirmation-id="${confirmation.confirmation_id}"]`)) {
        if (existingMessage) existingMessage.remove();
        return true;
    }
    const msgDiv = existingMessage || document.createElement('div');
    msgDiv.removeAttribute('id');
    msgDiv.className = 'message incoming confirmation-message';
    msgDiv.dataset.rawContent = confirmation.message || response.message || '';
    msgDiv.dataset.confirmationId = confirmation.confirmation_id;
    msgDiv.innerHTML = `<div class="message-content">
        <div class="sender-name current-name">Jarvis ⚡</div>
        <div class="bubble">${confirmationCardHtml(confirmation)}</div>
    </div>`;
    if (!existingMessage) chatArea.appendChild(msgDiv);
    bindConfirmationCard(msgDiv.querySelector('.confirmation-card'));
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
        list.innerHTML = review.candidates.map(candidate => {
            const nouns = learningListText(candidate.nouns, item => {
                const canonical = item.canonical ? ` → ${item.canonical}` : '';
                return `${item.text || ''}${canonical} (${item.type || 'general'})`;
            });
            const slots = learningListText(candidate.slots, item => `${item.name}=${item.value} (${item.type})`);
            const verification = ['verified', 'passed'].includes(candidate.verification_status) ? '자동 확인 완료' : '사용자 확인 필요';
            return `<section class="learning-review-card" data-index="${candidate.index}">
                <div class="learning-review-heading">
                    <strong>${escapeHtml(candidate.description || '학습 행동')}</strong>
                    <span class="learning-status">${escapeHtml(verification)}</span>
                </div>
                <label>매크로 이름
                    <input class="premium-input learning-name" value="${escapeHtml(candidate.name || '')}">
                </label>
                <label>발동 문장 — 한 줄에 하나
                    <textarea class="premium-input learning-utterances" rows="4">${escapeHtml((candidate.utterances || []).join('\n'))}</textarea>
                </label>
                <label>핵심 동사 — 쉼표로 구분
                    <input class="premium-input learning-verbs" value="${escapeHtml((candidate.verbs || []).join(', '))}">
                </label>
                <div class="learning-detail-grid">
                    <div><b>의도</b><span>${escapeHtml(candidate.intent || '')}</span></div>
                    <div><b>대상</b><span>${escapeHtml(candidate.app || '')}</span></div>
                    <div><b>명사</b><span>${escapeHtml(nouns)}</span></div>
                    <div><b>슬롯</b><span>${escapeHtml(slots)}</span></div>
                </div>
                <label>실행 단계
                    <textarea class="premium-input learning-steps" rows="4" readonly>${escapeHtml(learningStepText(candidate))}</textarea>
                </label>
            </section>`;
        }).join('');
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

function addMessage(text, isIncoming, image_data=null) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${isIncoming ? 'incoming' : 'outgoing'}`;
    msgDiv.dataset.rawContent = text || '';

    const contentHtml = formatMessageContent(text, isIncoming, image_data);

    let html = '';
    if (isIncoming) {
        html += `<div class="message-content">`;
        html += `<div class="sender-name current-name">Jarvis ⚡</div>`;
        html += `<div class="bubble">${contentHtml}</div>`;
        html += `</div>`;
    } else {
        html += `<div class="message-content">`;
        html += `<div class="bubble">${contentHtml}</div>`;
        html += `</div>`;
    }

    msgDiv.innerHTML = html;
    chatArea.appendChild(msgDiv);
    scrollToBottom();
}

function addSystemMessage(msg) {
    const chatOutput = document.getElementById('chat-area');
    if (!chatOutput) return;
    const line = document.createElement('div');
    line.className = 'system-error-line';
    line.setAttribute('data-system-note', 'true');
    line.innerHTML = `<span>ℹ️ ${escapeHtml(msg)}</span>`;
    chatOutput.appendChild(line);
    scrollToBottom();
}

function addSystemError(errorMsg) {
    const line = document.createElement('div');
    line.className = 'system-error-line';
    line.setAttribute('data-system-note', 'true');
    line.innerHTML = `
        <span>⚠️ 오류</span>
        <span class="error-detail">${escapeHtml(errorMsg)}</span>
        <button onclick="this.parentElement.remove(); sendMessage(true)" style="margin-left:10px; padding:2px 8px; font-size:0.8em; cursor:pointer; border:1px solid #ff6b6b; border-radius:4px; background:transparent; color:#ff6b6b;">&#x21ba; 다시 시도</button>
    `;
    chatArea.appendChild(line);
    scrollToBottom();
}

// Image Attachment Logic
let currentAttachedImage = null;
const btnAttach = document.getElementById('btn-attach');
const chatFileUpload = document.getElementById('chat-file-upload');
const previewContainer = document.getElementById('chat-preview-container');
const previewImg = document.getElementById('chat-preview-img');
const btnCancelPreview = document.getElementById('btn-cancel-preview');

function setPreviewImage(base64Data) {
    currentAttachedImage = base64Data;
    previewImg.src = base64Data;
    previewContainer.style.display = 'flex';
    chatInput.focus();
}

function clearPreviewImage() {
    currentAttachedImage = null;
    previewImg.src = '';
    previewContainer.style.display = 'none';
    chatFileUpload.value = '';
}

btnAttach.addEventListener('click', () => { chatFileUpload.click(); });
chatFileUpload.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = function(event) { setPreviewImage(event.target.result); };
        reader.readAsDataURL(file);
    }
});
btnCancelPreview.addEventListener('click', clearPreviewImage);

document.addEventListener('paste', (e) => {
    const items = (e.clipboardData || e.originalEvent.clipboardData).items;
    for (const item of items) {
        if (item.type.indexOf('image') === 0) {
            const blob = item.getAsFile();
            const reader = new FileReader();
            reader.onload = function(event) { setPreviewImage(event.target.result); };
            reader.readAsDataURL(blob);
            break;
        }
    }
});

let currentStreamId = null;
let currentStreamRawContent = "";
let streamScrollTimer = null;

eel.expose(receive_stream_chunk);
function receive_stream_chunk(chunk) {
    if (!currentStreamId) return;
    const msgDiv = document.getElementById(currentStreamId);
    if (!msgDiv) return;
    const bubble = msgDiv.querySelector('.bubble');
    if (!bubble) return;

    if (currentStreamRawContent === "") {
        bubble.style.opacity = "1";
        bubble.innerHTML = "";
    }

    currentStreamRawContent += chunk;
    msgDiv.dataset.rawContent = currentStreamRawContent;

    bubble.innerHTML = currentStreamRawContent.replace(/\n/g, '<br>');

    if (!streamScrollTimer) {
        streamScrollTimer = setTimeout(() => {
            scrollToBottom();
            streamScrollTimer = null;
        }, 100);
    }
}

let _summaryVersion = 0;
function runBackgroundSummarization(oldElements, oldMessages) {
    if (!oldMessages.length) return;
    const myVersion = ++_summaryVersion;
    const sessionIdAtStart = currentSessionId;

    eel.summarize_memory(window.conversationSummary, oldMessages)().then(summaryResult => {
        if (myVersion !== _summaryVersion || sessionIdAtStart !== currentSessionId) return;
        const summarySucceeded = typeof summaryResult === 'string' || summaryResult?.success;
        let updatedSummary = typeof summaryResult === 'string' ? summaryResult : summaryResult?.summary;
        if (summarySucceeded && updatedSummary) {
            window.conversationSummary = updatedSummary;
            oldElements.forEach(el => el.classList.add('summarized'));
            if (typeof saveCurrentSession === 'function') {
                saveCurrentSession().catch(saveError => console.warn('요약 저장 실패', saveError));
            }
        }
    }).catch(summaryError => console.warn('요약 실패', summaryError));
}

window.lastFailedMessagePayload = null;
async function sendMessage(isRetry = false) {
    if (sendBtn.disabled) return;
    
    let text = "";
    let imgDataToSend = null;

    if (!isRetry) {
        text = chatInput.value.trim();
        if (!text && !currentAttachedImage) return;

        addMessage(text, false, currentAttachedImage);
        imgDataToSend = currentAttachedImage;
        chatInput.value = '';
        chatInput.style.height = 'auto';
        clearPreviewImage();
        chatInput.focus();
    } else {
        if (!window.lastFailedMessagePayload) return;
        text = window.lastFailedMessagePayload.text;
        imgDataToSend = window.lastFailedMessagePayload.img;
    }

    window.lastFailedMessagePayload = { text, img: imgDataToSend };

    const modeRadio = document.querySelector('input[name="chat-mode"]:checked');
    const mode = modeRadio ? modeRadio.value : "command";
    
    const apiToggle = document.getElementById('api-toggle');
    const useApi = apiToggle ? apiToggle.checked : false;

    const loadingId = 'stream-' + Date.now();
    currentStreamId = loadingId;
    currentStreamRawContent = "";
    
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'message incoming';
    loadingDiv.id = loadingId;
    loadingDiv.innerHTML = `
    <div class="message-content">
        <div class="sender-name current-name">Jarvis ⚡</div>
        <div class="bubble" style="opacity: 0.7; min-height: 24px;"><span style="letter-spacing: 2px; font-weight: bold; color: #888; animation: pulse 1.5s infinite;">. . .</span></div>
    </div>`;
    chatArea.appendChild(loadingDiv);
    scrollToBottom();
    sendBtn.disabled = true;
    if (cancelExecutionBtn) cancelExecutionBtn.style.display = 'inline-flex';

    try {
        let historyPayload = text;
        let summaryPayload = "";
        if (mode === "question" || mode === "conversation") {
            const allMsgs = Array.from(document.querySelectorAll('.message'))
                .filter(el => !el.classList.contains('system-date') && el.id !== loadingId);

            if (allMsgs.length > 10) {
                const oldElements = allMsgs.slice(0, allMsgs.length - 10).filter(el => !el.classList.contains('summarized'));
                const oldMessages = oldElements.map(el => ({
                    role: el.classList.contains('outgoing') ? 'user' : 'assistant',
                    content: el.dataset.rawContent || (el.querySelector('.bubble')?.innerText.trim() || '')
                })).filter(msg => msg.content);

                if (oldMessages.length > 0) {
                    runBackgroundSummarization(oldElements, oldMessages);
                }
            }

            summaryPayload = window.conversationSummary;
            historyPayload = allMsgs.slice(-10).map(el => ({
                role: el.classList.contains('outgoing') ? 'user' : 'assistant',
                content: el.dataset.rawContent || (el.querySelector('.bubble')?.innerText.trim() || '')
            })).filter(msg => msg.content);
        }

        const response = await eel.parse_command(
            historyPayload, imgDataToSend, mode, useApi, summaryPayload, null,
            currentSessionId
        )();

        const streamDiv = document.getElementById(loadingId);
        let responseText = null;
        if (response) {
            responseText = (typeof response === 'object' && response !== null) ? (response.message || response.response || JSON.stringify(response)) : response;
        }

        if (getConfirmationFromResponse(response)) {
            if (streamDiv) addConfirmationCard(response, streamDiv);
            else addConfirmationCard(response);
        } else if (streamDiv && (currentStreamRawContent || responseText)) {
            markActiveConfirmationResolved(text);
            finalizeStreamMessage(streamDiv, responseText || currentStreamRawContent);
        } else if (streamDiv) {
            markActiveConfirmationResolved(text);
            streamDiv.remove();
            addMessage("AI가 빈 응답을 반환했어요. 잠시 후 다시 시도해주세요.", true);
        } else if (responseText) {
            markActiveConfirmationResolved(text);
            addMessage(responseText, true);
        } else {
            addMessage("AI가 빈 응답을 반환했어요. 잠시 후 다시 시도해주세요.", true);
        }
        currentStreamId = null;
        currentStreamRawContent = "";
        window.lastFailedMessagePayload = null;
    } catch (error) {
        console.error('메시지 처리 실패:', error);
        const streamDiv = document.getElementById(loadingId);
        if (streamDiv) streamDiv.remove();
        currentStreamId = null;
        currentStreamRawContent = "";
        addSystemError(error.message || String(error));
    } finally {
        sendBtn.disabled = false;
        if (cancelExecutionBtn) cancelExecutionBtn.style.display = 'none';
        if (typeof saveCurrentSession === 'function') {
            try { await saveCurrentSession(); } catch (saveError) {}
        }
    }
}

sendBtn.addEventListener('click', () => sendMessage());
cancelExecutionBtn?.addEventListener('click', async () => {
    cancelExecutionBtn.disabled = true;
    try {
        const result = await eel.cancel_current_execution()();
        if (result?.success) addSystemMessage('현재 실행에 취소 요청을 보냈습니다.');
    } finally {
        cancelExecutionBtn.disabled = false;
    }
});
chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.isComposing) {
        if (!e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    }
});
chatInput.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 150) + 'px';
});
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') chatInput.focus();
});

// Plus Menu Logic
const btnPlus = document.getElementById('btn-plus');
const plusMenu = document.getElementById('plus-menu');
btnPlus.addEventListener('click', (e) => {
    plusMenu.classList.toggle('show');
    e.stopPropagation();
});

// Terminal Logic
const btnToggleTerm = document.getElementById('btn_toggle_term');
const terminalPane = document.getElementById('terminal-pane');
let termVisible = false;

btnToggleTerm.addEventListener('click', () => {
    termVisible = !termVisible;
    terminalPane.style.display = termVisible ? 'block' : 'none';
    if (termVisible) {
        window.resizeBy(320, 0); 
        terminalPane.scrollTop = terminalPane.scrollHeight;
    } else {
        window.resizeBy(-320, 0); 
    }
    const rightToggle = document.querySelector('.toggle-right');
    if (rightToggle) rightToggle.innerText = termVisible ? '›' : '‹';
});

eel.expose(log_terminal);
function log_terminal(msg) {
    const line = document.createElement('div');
    line.className = 'terminal-line';
    line.textContent = `> ${msg}`;
    terminalPane.appendChild(line);
    terminalPane.scrollTop = terminalPane.scrollHeight;
}

// Sidebar & Memory Logic
let currentSessionId = "session_" + Date.now();

const chatHistoryTab = document.querySelector('[data-target="chat-history-tab"]');
const userMemoryTab = document.querySelector('[data-target="user-memory-tab"]');
const paneHistory = document.getElementById('chat-history-tab');
const paneMemory = document.getElementById('user-memory-tab');
const sessionList = document.getElementById('chat-session-list');
const btnNewChat = document.getElementById('btn-new-chat');
const btnSaveMemory = document.getElementById('btn-save-memory');

window.switchSidebarTab = function(target) {
    if(!chatHistoryTab || !userMemoryTab) return;
    chatHistoryTab.classList.remove('active');
    userMemoryTab.classList.remove('active');
    paneHistory.style.display = 'none';
    paneMemory.style.display = 'none';
    
    if (target === 'chat-history-tab') {
        chatHistoryTab.classList.add('active');
        paneHistory.style.display = 'block';
    } else {
        userMemoryTab.classList.add('active');
        paneMemory.style.display = 'flex';
    }
};

window.switchMemoryTab = function(target, clickedTab) {
    const tabs = document.querySelectorAll('.memory-subtab');
    const boxes = document.querySelectorAll('.memory-box');

    tabs.forEach(t => t.classList.remove('active'));
    boxes.forEach(b => { b.style.display = 'none'; b.classList.remove('active'); });

    if (clickedTab) clickedTab.classList.add('active');
    const activeBox = document.getElementById(target);
    if (activeBox) {
        activeBox.style.display = 'block';
        activeBox.classList.add('active');
    }
};

async function loadUserMemory() {
    const memUser = document.getElementById('mem-user');
    const memRules = document.getElementById('mem-rules');
    const memOthers = document.getElementById('mem-others');
    if(!memUser || !memRules || !memOthers) return;
    
    try {
        const memory = await eel.load_user_memory()();
        if (memory) {
            memUser.value = memory.user_info || '';
            memRules.value = memory.rules || '';
            memOthers.value = memory.others || '';
        }
    } catch (e) {
        console.error("Failed to load user memory", e);
    }
}

if (btnSaveMemory) {
    btnSaveMemory.addEventListener('click', async () => {
        const memUser = document.getElementById('mem-user').value;
        const memRules = document.getElementById('mem-rules').value;
        const memOthers = document.getElementById('mem-others').value;

        try {
            const success = await eel.save_user_memory(memUser, memRules, memOthers)();
            if (!success) throw new Error("저장 결과가 실패로 반환되었습니다.");
            const originalText = btnSaveMemory.innerText;
            btnSaveMemory.innerText = "✅ 저장 완료!";
            setTimeout(() => { btnSaveMemory.innerText = originalText; }, 2000);
        } catch (e) {
            console.error("Failed to save user memory", e);
            addSystemError("영구 기억을 저장하지 못했습니다.");
        }
    });
}

async function startNewChat(savePrevious = true) {
    if (savePrevious && typeof saveCurrentSession === 'function') {
        try { await saveCurrentSession(true); } catch (saveError) {}
    }
    _summaryVersion += 1;
    currentSessionId = "session_" + Date.now();
    if (typeof lastSavedSessionSignature !== 'undefined') lastSavedSessionSignature = null;
    chatArea.innerHTML = '<div class="message system-date">오늘</div>';
    window.conversationSummary = "";
    addMessage("안녕하세요! 새로운 대화를 시작할게요 😊", true);
    if (typeof loadSessions === 'function') loadSessions();
}

if(btnNewChat) btnNewChat.addEventListener('click', () => startNewChat(true));

window.addEventListener('DOMContentLoaded', async () => {
    try { window._cachedAiConfig = await eel.get_ai_config()(); } catch(e) { window._cachedAiConfig = {}; }
    const loaders = [];
    if (typeof loadSessions === 'function') loaders.push(loadSessions());
    loaders.push(loadUserMemory());
    if (typeof updateLearnedMacroList === 'function') {
        loaders.push(updateLearnedMacroList());
    }
    await Promise.allSettled(loaders);
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modals = document.querySelectorAll('.modal, .modal-overlay, .plus-menu');
        modals.forEach(m => {
            if (m.style.display === 'block' || m.style.display === 'flex' || m.classList.contains('show') || !m.classList.contains('hidden')) {
                if (m.style.display !== 'none' && m.style.display !== '' && !m.classList.contains('plus-menu')) m.style.display = 'none';
                if (m.classList.contains('modal-overlay')) m.classList.add('hidden');
                if (m.classList.contains('show')) m.classList.remove('show');
                if (m.classList.contains('plus-menu')) {
                    m.style.opacity = '';
                    m.style.pointerEvents = '';
                }
            }
        });
    }
});

document.addEventListener('click', (e) => {
    // 1. Modal Overlay Click
    if (e.target.classList.contains('modal') || e.target.classList.contains('modal-overlay')) {
        e.target.style.display = 'none';
        if (e.target.classList.contains('modal-overlay')) e.target.classList.add('hidden');
    }
    
    // 2. Universal Close (X) Button Click
    const closeBtn = e.target.closest('.close-modal');
    if (closeBtn) {
        const modal = closeBtn.closest('.modal') || closeBtn.closest('.modal-overlay');
        if (modal) {
            modal.style.display = 'none';
        }
    }

    // 3. Plus Menu Click Outside
    const plusMenu = document.getElementById('plus-menu');
    const btnPlus = document.getElementById('btn-plus');
    if (plusMenu && btnPlus && !plusMenu.contains(e.target) && !btnPlus.contains(e.target)) {
        plusMenu.classList.remove('show');
        plusMenu.style.opacity = '';
        plusMenu.style.pointerEvents = '';
    }
});

eel.expose(notifySystemMsg);
function notifySystemMsg(msg) {
    if (typeof addSystemMessage === 'function') addSystemMessage(msg);
}
