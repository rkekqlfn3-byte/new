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


window.lastFailedMessagePayload = null;
window.commandConversationState = window.commandConversationState || {
    version: 1,
    recent_turns: [],
    pending_confirmation: false,
};

function boundedReferenceText(value, limit) {
    return String(value ?? '').replace(/[\u0000-\u001f\u007f]+/g, ' ')
        .replace(/\s+/g, ' ').trim().slice(0, limit);
}

function compactCommandResult(response, responseText) {
    const value = response && typeof response === 'object' ? response : {};
    const data = value.data && typeof value.data === 'object' ? value.data : {};
    return {
        action: boundedReferenceText(value.action, 60),
        target: boundedReferenceText(value.target, 200),
        app_name: boundedReferenceText(value.app_name || data.app, 100),
        macro_name: boundedReferenceText(value.macro_name || data.macro_name, 100),
        status: boundedReferenceText(value.status, 60),
        verified: value.verified === true,
        message: boundedReferenceText(responseText, 300),
    };
}

function rememberCommandTurn(userRequest, response, responseText) {
    const previous = window.commandConversationState;
    const turns = Array.isArray(previous?.recent_turns)
        ? previous.recent_turns.slice(-3)
        : [];
    const result = compactCommandResult(response, responseText);
    const pendingBefore = previous?.pending_confirmation === true;
    if (pendingBefore && turns.length) {
        // A confirmation answer completes the original request; do not replace
        // its referential subject with a bare "예" or option label.
        turns[turns.length - 1] = { ...turns[turns.length - 1], result };
    } else {
        turns.push({
            user_request: boundedReferenceText(userRequest, 300),
            result,
        });
    }
    window.commandConversationState = {
        version: 1,
        recent_turns: turns.slice(-3),
        pending_confirmation: result.status === 'confirmation_required',
    };
}

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

    const useApi = true;

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

        const activeEditSession = mode === "edit" ? window.currentEditSession : null;
        const latestEditContext = mode === "edit"
            ? await window.refreshEditContext({ required: true })
            : null;
        const editContext = activeEditSession ? {
            edit_session_id: activeEditSession.session_id,
            document_fingerprint: activeEditSession.document_fingerprint,
            context_fingerprint: latestEditContext.context_fingerprint
        } : null;

        const response = await eel.parse_command(
            historyPayload, imgDataToSend, mode, useApi, summaryPayload,
            mode === 'command' ? window.commandConversationState : null,
            currentSessionId, editContext
        )();

        const streamDiv = document.getElementById(loadingId);
        let responseText = null;
        if (response) {
            responseText = (typeof response === 'object' && response !== null) ? (response.display_message || response.message || response.response || JSON.stringify(response)) : response;
        }
        if (mode === 'command' && response) {
            rememberCommandTurn(text, response, responseText || '');
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
        if (mode === "edit") await window.refreshEditContext({ required: false });
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
    window.commandConversationState = {
        version: 1, recent_turns: [], pending_confirmation: false,
    };
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
