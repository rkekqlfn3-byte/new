// --- sessions.js ---

let saveSessionTimer = null;
let lastSavedSessionSignature = null;

function createSessionDateMessage() {
    const message = document.createElement('div');
    message.className = 'message system-date';
    message.textContent = '오늘';
    return message;
}

function resetChatArea() {
    if (chatArea) chatArea.replaceChildren(createSessionDateMessage());
}

function createSessionListItem(session) {
    const id = String(session?.id || '');
    const li = document.createElement('li');
    li.className = 'session-item' + (id === currentSessionId ? ' active' : '');
    li.dataset.sessionId = id;

    const info = document.createElement('div');
    info.className = 'session-info';
    const title = document.createElement('span');
    title.className = 'session-title';
    title.textContent = session?.title || '새로운 대화';
    const date = document.createElement('span');
    date.className = 'session-date';
    const timestamp = Number(session?.timestamp || 0);
    date.textContent = Number.isFinite(timestamp)
        ? new Date(timestamp).toLocaleString()
        : '';
    info.append(title, date);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'session-delete';
    remove.textContent = '🗑️';
    remove.addEventListener('click', event => deleteSession(id, event));
    li.addEventListener('click', () => switchSession(id));
    li.append(info, remove);
    return li;
}

async function loadSessions() {
    if (!sessionList) return;
    try {
        const loader = () => eel.get_chat_sessions()();
        const sessions = typeof window.runUiLoadWithRetry === 'function'
            ? await window.runUiLoadWithRetry(loader)
            : await loader();
        sessionList.replaceChildren();
        (Array.isArray(sessions) ? sessions : []).forEach(session => {
            sessionList.appendChild(createSessionListItem(session));
        });
    } catch (error) {
        console.warn('대화 기록 초기 로딩 실패', error);
        const item = document.createElement('li');
        item.className = 'session-load-error';
        item.textContent = '대화 기록을 불러오지 못했습니다. 창을 다시 열어주세요.';
        sessionList.replaceChildren(item);
    }
}

function updateActiveSessionHighlight() {
    if (!sessionList) return;
    sessionList.querySelectorAll('.session-item').forEach(li => {
        li.classList.toggle('active', li.dataset.sessionId === currentSessionId);
    });
}

async function switchSession(id) {
    if (currentSessionId === id) return;
    if (typeof saveCurrentSession === 'function') {
        try { await saveCurrentSession(true); } catch (saveError) {}
    }
    if (typeof _summaryVersion !== 'undefined') _summaryVersion += 1;
    currentSessionId = id;
    lastSavedSessionSignature = null;
    resetChatArea();

    const sessionData = await eel.load_chat_session(id)();
    const messages = Array.isArray(sessionData?.messages) ? sessionData.messages : [];
    window.conversationSummary = sessionData?.summary || '';

    const fragment = document.createDocumentFragment();
    messages.forEach(message => {
        const content = String(message?.content || '');
        const incoming = message?.role === 'assistant';
        if (typeof window.createChatMessage === 'function') {
            fragment.appendChild(window.createChatMessage(content, incoming));
        } else {
            const item = document.createElement('div');
            item.className = `message ${incoming ? 'incoming' : 'outgoing'}`;
            item.dataset.rawContent = content;
            item.textContent = content;
            fragment.appendChild(item);
        }
    });
    chatArea.appendChild(fragment);

    if (window.conversationSummary) {
        const restored = Array.from(chatArea.querySelectorAll('.message'))
            .filter(element => !element.classList.contains('system-date'));
        restored.slice(0, Math.max(0, restored.length - 10))
            .forEach(element => element.classList.add('summarized'));
    }

    updateActiveSessionHighlight();
    if (typeof restorePendingConfirmationCard === 'function') {
        await restorePendingConfirmationCard(id);
    }
    scrollToBottom();
}

async function deleteSession(id, event) {
    event?.stopPropagation();
    await eel.delete_chat_session(id)();
    if (id === currentSessionId) {
        startNewChat(false);
    } else {
        loadSessions();
    }
}
window.deleteSession = deleteSession;

async function _doSaveSession() {
    const history = [];
    document.querySelectorAll('.message').forEach(element => {
        if (element.classList.contains('system-date')) return;
        const bubble = element.querySelector('.bubble');
        if (!bubble) return;
        history.push({
            role: element.classList.contains('outgoing') ? 'user' : 'assistant',
            content: element.dataset.rawContent || bubble.innerText.trim(),
        });
    });
    if (!history.length) return;

    const title = (history.find(message => message.role === 'user')?.content || '새로운 대화')
        .substring(0, 20);
    const sessionIdToSave = currentSessionId;
    const saveSignature = JSON.stringify([
        sessionIdToSave, history, window.conversationSummary || '',
    ]);
    if (saveSignature === lastSavedSessionSignature) return;

    const saved = await eel.save_chat_session(
        sessionIdToSave, title, history, window.conversationSummary || '', null
    )();
    if (!saved) throw new Error('대화 기록을 저장하지 못했습니다.');
    if (currentSessionId === sessionIdToSave) {
        lastSavedSessionSignature = saveSignature;
    }
    await loadSessions();
    updateActiveSessionHighlight();
}

async function saveCurrentSession(immediate = false) {
    if (saveSessionTimer) {
        clearTimeout(saveSessionTimer);
        saveSessionTimer = null;
    }
    if (immediate) return _doSaveSession();
    saveSessionTimer = setTimeout(() => {
        saveSessionTimer = null;
        _doSaveSession().catch(error => console.warn('세션 저장 실패', error));
    }, 1500);
    return Promise.resolve();
}
