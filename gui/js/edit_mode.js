const editPanel = document.getElementById('edit-session-panel');
const editDocumentName = document.getElementById('edit-document-name');
const editDocumentLocation = document.getElementById('edit-document-location');
const editContextSummary = document.getElementById('edit-context-summary');
const editContextTarget = document.getElementById('edit-context-target');
const editContextPreview = document.getElementById('edit-context-preview');
const editContextRefreshButton = document.getElementById('btn-edit-refresh-context');
const editChooseButton = document.getElementById('btn-edit-choose-file');
const editConnectActiveButton = document.getElementById('btn-edit-connect-active');
const editDisconnectButton = document.getElementById('btn-edit-disconnect');
const editActiveApp = document.getElementById('edit-active-app');
const editAutoLayout = document.getElementById('edit-auto-layout');
const editDropZone = document.getElementById('edit-file-drop-zone');

window.currentEditSession = null;
window.currentEditContext = null;
let editContextRefreshPromise = null;

const editAppLabels = {
    excel: 'Excel',
    hwp: '한글',
    word: 'Word',
    powerpoint: 'PowerPoint'
};

function setEditControlsBusy(busy) {
    editChooseButton.disabled = busy;
    editConnectActiveButton.disabled = busy;
    editActiveApp.disabled = busy;
    editDisconnectButton.disabled = busy || !window.currentEditSession;
}

function renderEditContext(context, error = null) {
    window.currentEditContext = context || null;
    editContextSummary.classList.toggle('context-unavailable', Boolean(error));
    if (error) {
        editContextTarget.textContent = '문맥 확인 필요';
        editContextPreview.textContent = error.message || '연결된 문서를 다시 선택해주세요.';
        return;
    }
    if (!context) {
        editContextTarget.textContent = '문서를 연결하면 선택 영역을 표시합니다.';
        editContextPreview.textContent = '';
        return;
    }
    const location = [context.active_container, context.selection_reference]
        .filter(Boolean)
        .join(' · ');
    editContextTarget.textContent = location || '선택 영역 없음';
    editContextPreview.textContent = context.selected_text_preview
        ? `“${context.selected_text_preview}”`
        : '';
}

function renderEditSession(session, context = null, contextError = null) {
    window.currentEditSession = session || null;
    if (!session) {
        editDocumentName.textContent = '연결된 문서 없음';
        editDocumentLocation.textContent = '파일을 선택하거나 열린 문서를 연결해주세요.';
        editDisconnectButton.disabled = true;
        renderEditContext(null);
        updateEditPanelVisibility();
        return;
    }
    const appLabel = editAppLabels[session.app_type] || session.app_type || '문서';
    const location = [session.active_container, session.selection_reference]
        .filter(Boolean)
        .join(' · ');
    editDocumentName.textContent = session.document_name || '이름 없는 문서';
    editDocumentLocation.textContent = location ? `${appLabel} · ${location}` : appLabel;
    editDisconnectButton.disabled = false;
    renderEditContext(context || session.context || null, contextError || session.context_error || null);
    updateEditPanelVisibility();
}

function showEditResult(result) {
    const session = result?.data?.session || null;
    if (result?.success && session) {
        renderEditSession(
            session,
            result?.data?.context || null,
            result?.data?.context_error || null
        );
        if (typeof addSystemMessage === 'function') addSystemMessage(result.message);
        const layout = result?.data?.layout;
        if (layout && !layout.success && typeof addSystemMessage === 'function') {
            addSystemMessage(`문서는 연결했지만 창 자동 배치는 건너뛰었습니다: ${layout.message}`);
        }
        return true;
    }
    if (result?.status !== 'cancelled' && typeof addSystemError === 'function') {
        addSystemError(result?.message || '문서를 연결하지 못했습니다.');
    }
    return false;
}

async function chooseEditDocument() {
    setEditControlsBusy(true);
    try {
        const result = await eel.choose_and_connect_edit_document()();
        showEditResult(result);
    } catch (error) {
        addSystemError(error.message || String(error));
    } finally {
        setEditControlsBusy(false);
    }
}

async function connectActiveEditDocument() {
    setEditControlsBusy(true);
    try {
        const result = await eel.connect_active_edit_document(editActiveApp.value || null)();
        showEditResult(result);
    } catch (error) {
        addSystemError(error.message || String(error));
    } finally {
        setEditControlsBusy(false);
    }
}

async function disconnectEditDocument() {
    const sessionId = window.currentEditSession?.session_id || null;
    if (!sessionId) return;
    setEditControlsBusy(true);
    try {
        const result = await eel.disconnect_edit_document(sessionId)();
        if (!result?.success) {
            addSystemError(result?.message || '문서 연결을 해제하지 못했습니다.');
            return;
        }
        renderEditSession(null);
        addSystemMessage(result.message);
    } catch (error) {
        addSystemError(error.message || String(error));
    } finally {
        setEditControlsBusy(false);
    }
}

async function connectDroppedEditDocument(file) {
    setEditControlsBusy(true);
    try {
        const pathHint = typeof file.path === 'string' ? file.path : null;
        const result = await eel.connect_dropped_edit_document(
            file.name, file.size, pathHint
        )();
        showEditResult(result);
    } catch (error) {
        addSystemError(error.message || String(error));
    } finally {
        setEditControlsBusy(false);
    }
}

async function refreshEditContext(options = {}) {
    const required = Boolean(options?.required);
    const sessionId = window.currentEditSession?.session_id || null;
    const mode = document.querySelector('input[name="chat-mode"]:checked')?.value;
    if (!sessionId) {
        if (required) throw new Error('먼저 편집할 문서를 연결해주세요.');
        return null;
    }
    if (mode !== 'edit') {
        if (required) throw new Error('편집 모드에서만 문맥을 확인할 수 있습니다.');
        return null;
    }
    if (document.hidden && !required) return null;
    if (editContextRefreshPromise) {
        const context = await editContextRefreshPromise;
        if (required && !context) {
            throw new Error('현재 선택 영역을 확인하지 못했습니다. 다시 시도해주세요.');
        }
        return context;
    }

    const pending = (async () => {
        if (editContextRefreshButton) editContextRefreshButton.disabled = true;
        try {
            const result = await eel.get_edit_context(sessionId)();
            if (window.currentEditSession?.session_id !== sessionId) {
                throw new Error('문맥 확인 중 편집 문서가 바뀌었습니다. 다시 시도해주세요.');
            }
            if (result?.success && result?.data?.context?.context_fingerprint) {
                renderEditContext(result.data.context);
                return result.data.context;
            }
            const message = result?.message || '현재 선택 영역을 읽지 못했습니다.';
            renderEditContext(null, { message });
            if (required) throw new Error(message);
            return null;
        } catch (error) {
            if (window.currentEditSession?.session_id === sessionId) {
                renderEditContext(null, { message: error.message || String(error) });
            }
            if (required) throw error;
            return null;
        } finally {
            if (editContextRefreshButton) editContextRefreshButton.disabled = false;
        }
    })();
    editContextRefreshPromise = pending;
    try {
        return await pending;
    } finally {
        if (editContextRefreshPromise === pending) editContextRefreshPromise = null;
    }
}

window.refreshEditContext = refreshEditContext;

function updateEditPanelVisibility() {
    const mode = document.querySelector('input[name="chat-mode"]:checked')?.value;
    const editing = mode === 'edit';
    editPanel.classList.toggle('hidden-input', !editing);
    if (editing) {
        chatInput.placeholder = window.currentEditSession
            ? '연결된 문서에 적용할 편집 내용을 입력하세요…'
            : '먼저 편집할 문서를 연결해주세요…';
        refreshEditContext();
    } else {
        chatInput.placeholder = '메시지를 입력하세요…';
    }
}

document.querySelectorAll('input[name="chat-mode"]').forEach(input => {
    input.addEventListener('change', updateEditPanelVisibility);
});

editChooseButton.addEventListener('click', chooseEditDocument);
editConnectActiveButton.addEventListener('click', connectActiveEditDocument);
editDisconnectButton.addEventListener('click', disconnectEditDocument);
editContextRefreshButton?.addEventListener('click', () => refreshEditContext());
editAutoLayout.addEventListener('change', async () => {
    const result = await eel.set_edit_auto_layout(editAutoLayout.checked)();
    if (!result?.success) {
        editAutoLayout.checked = !editAutoLayout.checked;
        addSystemError(result?.message || '자동 배치 설정을 변경하지 못했습니다.');
    }
});

['dragenter', 'dragover'].forEach(eventName => {
    editDropZone.addEventListener(eventName, event => {
        event.preventDefault();
        editDropZone.classList.add('drag-active');
    });
});

['dragleave', 'drop'].forEach(eventName => {
    editDropZone.addEventListener(eventName, event => {
        event.preventDefault();
        editDropZone.classList.remove('drag-active');
    });
});

editDropZone.addEventListener('drop', event => {
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length !== 1) {
        addSystemError('한 번에 문서 파일 하나만 연결할 수 있습니다.');
        return;
    }
    connectDroppedEditDocument(files[0]);
});

window.addEventListener('DOMContentLoaded', async () => {
    updateEditPanelVisibility();
    try {
        const result = await runUiLoadWithRetry(() => eel.get_edit_session_status()());
        if (result?.success) {
            renderEditSession(
                result.data?.session || null,
                result.data?.context || null,
                result.data?.context_error || null
            );
            editAutoLayout.checked = result.data?.auto_layout !== false;
        }
    } catch (error) {
        console.warn('편집 세션 상태를 불러오지 못했습니다.', error);
    }
});

window.addEventListener('focus', refreshEditContext);
