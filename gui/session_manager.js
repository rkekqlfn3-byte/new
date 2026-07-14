// --- session_manager.js ---

let saveSessionTimer = null;
let lastSavedSessionSignature = null;

async function loadSessions() {
    if(!sessionList) return;
    try {
        const loader = () => eel.get_chat_sessions()();
        const sessions = typeof window.runUiLoadWithRetry === 'function'
            ? await window.runUiLoadWithRetry(loader)
            : await loader();
        sessionList.innerHTML = '';
        sessions.forEach(session => {
        const li = document.createElement('li');
        li.className = 'session-item' + (session.id === currentSessionId ? ' active' : '');
        li.dataset.sessionId = session.id;
        li.onclick = () => switchSession(session.id);
        
        const dateStr = new Date(session.timestamp).toLocaleString();
        
        li.innerHTML = `
        <div class="session-info">
            <span class="session-title">${session.title || '새로운 대화'}</span>
            <span class="session-date">${dateStr}</span>
        </div>
        <button class="session-delete" onclick="deleteSession('${session.id}', event)">🗑️</button>`;
        
            sessionList.appendChild(li);
        });
    } catch (error) {
        console.warn('대화 기록 초기 로딩 실패', error);
        sessionList.innerHTML = '<li class="session-load-error">대화 기록을 불러오지 못했습니다. 창을 다시 열어주세요.</li>';
    }
}

function updateActiveSessionHighlight() {
    if (!sessionList) return;
    sessionList.querySelectorAll('.session-item').forEach(li => {
        li.classList.toggle('active', li.dataset.sessionId === currentSessionId);
    });
}

// Switch Session
async function switchSession(id) {
    if (currentSessionId === id) return;
    if (typeof saveCurrentSession === 'function') {
        try { await saveCurrentSession(true); } catch (saveError) {}
    }
    if (typeof _summaryVersion !== 'undefined') _summaryVersion += 1;
    currentSessionId = id;
    lastSavedSessionSignature = null;
    
    chatArea.innerHTML = '<div class="message system-date">오늘</div>';
    
    const sessionData = await eel.load_chat_session(id)();
    const messages = sessionData ? sessionData.messages : [];
    window.conversationSummary = sessionData ? (sessionData.summary || '') : '';
    
    const fragment = document.createDocumentFragment();
    messages.forEach(msg => {
        const isIncoming = msg.role === 'assistant';
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${isIncoming ? 'incoming' : 'outgoing'}`;
        msgDiv.dataset.rawContent = msg.content || '';
        const contentHtml = typeof formatMessageContent === 'function'
            ? formatMessageContent(msg.content, isIncoming)
            : (msg.content || '');
        if (isIncoming) {
            msgDiv.innerHTML = `<div class="message-content"><div class="sender-name">Jarvis ⚡</div><div class="bubble">${contentHtml}</div></div>`;
        } else {
            msgDiv.innerHTML = `<div class="message-content"><div class="bubble">${contentHtml}</div></div>`;
        }
        fragment.appendChild(msgDiv);
    });
    chatArea.appendChild(fragment);

    if (window.conversationSummary) {
        const restoredMessages = Array.from(chatArea.querySelectorAll('.message'))
            .filter(el => !el.classList.contains('system-date'));
        restoredMessages.slice(0, Math.max(0, restoredMessages.length - 10))
            .forEach(el => el.classList.add('summarized'));
    }
    
    updateActiveSessionHighlight();
    if (typeof restorePendingConfirmationCard === 'function') {
        await restorePendingConfirmationCard(id);
    }
    scrollToBottom();
}

// Delete Session
window.deleteSession = async function(id, event) {
    event.stopPropagation();
    await eel.delete_chat_session(id)();
    if (id === currentSessionId) {
        startNewChat(false);
    } else {
        loadSessions();
    }
}

async function _doSaveSession() {
    const messageElements = document.querySelectorAll('.message');
    let historyArray = [];
    for (let i = 0; i < messageElements.length; i++) {
        const el = messageElements[i];
        if (el.classList.contains('system-date')) continue;
        
        const isUser = el.classList.contains('outgoing');
        const contentEl = el.querySelector('.bubble');
        if (contentEl) {
            historyArray.push({
                "role": isUser ? "user" : "assistant",
                "content": el.dataset.rawContent || contentEl.innerText.trim()
            });
        }
    }
    
    if (historyArray.length > 0) {
        let title = "새로운 대화";
        for(let msg of historyArray) {
            if(msg.role === 'user') {
                title = msg.content.substring(0, 20);
                break;
            }
        }
        const sessionIdToSave = currentSessionId;
        const saveSignature = JSON.stringify([
            sessionIdToSave,
            historyArray,
            window.conversationSummary || ''
        ]);
        if (saveSignature === lastSavedSessionSignature) return;

        await eel.save_chat_session(
            sessionIdToSave,
            title,
            historyArray,
            window.conversationSummary || '',
            null
        )();
        if (currentSessionId === sessionIdToSave) {
            lastSavedSessionSignature = saveSignature;
        }
        // A newly created session has no sidebar element yet. Reload the list
        // immediately so it appears without a full browser refresh.
        await loadSessions();
        updateActiveSessionHighlight();
    }
}

async function saveCurrentSession(immediate = false) {
    if (saveSessionTimer) {
        clearTimeout(saveSessionTimer);
        saveSessionTimer = null;
    }
    if (immediate) {
        return _doSaveSession();
    }
    saveSessionTimer = setTimeout(() => {
        saveSessionTimer = null;
        _doSaveSession().catch(saveError => console.warn('세션 저장 실패', saveError));
    }, 1500);
    return Promise.resolve();
}
