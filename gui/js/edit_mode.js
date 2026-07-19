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
const editSelectionOverlay = document.getElementById('edit-selection-overlay');
const editDropZone = document.getElementById('edit-file-drop-zone');
const editTargetBar = document.getElementById('edit-target-bar');
const editTargetState = document.getElementById('edit-target-state');
const editTargetAddress = document.getElementById('edit-target-address');
const editTargetDetails = document.getElementById('edit-target-details');
const editTargetPreview = document.getElementById('edit-target-preview');

window.currentEditSession = null;
window.currentEditContext = null;
window.lastConfirmedEditContext = null;
window.lastDirectEditFeedbackId = null;
let editContextRefreshPromise = null;
let editOverlaySyncPromise = null;
let editContextMonitorTimer = null;
// 의도된 설계: 편집 모드에서 문서가 연결된 동안 문맥 표시·선택 overlay 추적·
// 직접 수정 관찰(5분 유예)을 위해 주기적으로 문맥을 다시 읽는다. rc.6에서
// 제거한 폴링과 달리 편집 모드 밖·문서 미연결에서는 돌지 않고, 페이지가
// 숨겨지면 조회를 건너뛰며, 진행 중 조회는 공유 Promise로 병합된다.
// 조회는 읽기 전용이라 창 포커스를 바꾸지 않는다. (KNOWN_LIMITATIONS 참조)
const EDIT_CONTEXT_MONITOR_MS = 700;

const editAppLabels = {
    excel: 'Excel',
    hwp: '한글',
    word: 'Word',
    powerpoint: 'PowerPoint'
};

function renderDirectEditFeedback(feedback = null) {
    const feedbackId = String(feedback?.feedback_id || '');
    if (!feedbackId || feedbackId === window.lastDirectEditFeedbackId) return;
    window.lastDirectEditFeedbackId = feedbackId;
    const message = String(
        feedback?.message
        || '직접 고친 방식을 선호 증거로 기록했지만 아직 기본값으로 확정하지 않았습니다.'
    );
    if (typeof addSystemMessage === 'function') addSystemMessage(message);
}

function renderEditTargetBar(context = null, error = null) {
    const session = window.currentEditSession;
    const mode = document.querySelector('input[name="chat-mode"]:checked')?.value;
    const visible = mode === 'edit' && Boolean(session);
    editTargetBar?.classList.toggle('hidden-input', !visible);
    if (!editTargetBar || !visible) return;

    const confirmed = context || window.lastConfirmedEditContext;
    const busy = Boolean(error) && (
        error?.status === 'busy' || error?.retryable === true
    );
    editTargetBar.classList.toggle('target-busy', busy);
    editTargetBar.classList.toggle('target-error', Boolean(error) && !busy);

    const appLabel = editAppLabels[session.app_type] || session.app_type || '문서';
    const address = confirmed?.selection_reference || '선택 없음';
    const details = [
        appLabel,
        session.document_name,
        confirmed?.active_container
    ].filter(Boolean).join(' · ');

    editTargetAddress.textContent = address;
    editTargetDetails.textContent = details || '편집 대상을 확인해주세요.';
    if (error) {
        editTargetState.textContent = busy ? '입력 완료 대기' : '대상 확인 필요';
        editTargetPreview.textContent = error.message || '현재 선택 영역을 확인하지 못했습니다.';
        editTargetBar.title = editTargetPreview.textContent;
    } else if (context) {
        editTargetState.textContent = '대상 고정';
        editTargetPreview.textContent = '';
        editTargetBar.title = `${details} · ${address}`;
    } else {
        editTargetState.textContent = '문맥 대기';
        editTargetPreview.textContent = '셀이나 범위를 선택한 뒤 새로고침하세요.';
        editTargetBar.title = editTargetPreview.textContent;
    }
}

function setEditControlsBusy(busy) {
    editChooseButton.disabled = busy;
    editConnectActiveButton.disabled = busy;
    editActiveApp.disabled = busy;
    editDisconnectButton.disabled = busy || !window.currentEditSession;
}

function renderEditContext(context, error = null) {
    window.currentEditContext = context || null;
    if (context) window.lastConfirmedEditContext = context;
    editContextSummary.classList.toggle('context-unavailable', Boolean(error));
    if (error) {
        editContextTarget.textContent = '문맥 확인 필요';
        editContextPreview.textContent = error.message || '연결된 문서를 다시 선택해주세요.';
        renderEditTargetBar(null, error);
        return;
    }
    if (!context) {
        editContextTarget.textContent = '문서를 연결하면 선택 영역을 표시합니다.';
        editContextPreview.textContent = '';
        renderEditTargetBar();
        return;
    }
    const location = [context.active_container, context.selection_reference]
        .filter(Boolean)
        .join(' · ');
    editContextTarget.textContent = location || '선택 영역 없음';
    editContextPreview.textContent = '';
    renderEditTargetBar(context);
}

function renderEditSession(session, context = null, contextError = null) {
    const previousSessionId = window.currentEditSession?.session_id || null;
    window.currentEditSession = session || null;
    if (!session) {
        window.lastConfirmedEditContext = null;
        editDocumentName.textContent = '연결된 문서 없음';
        editDocumentLocation.textContent = '파일을 선택하거나 열린 문서를 연결해주세요.';
        editDisconnectButton.disabled = true;
        renderEditContext(null);
        updateEditPanelVisibility();
        return;
    }
    if (previousSessionId && previousSessionId !== session.session_id) {
        window.lastConfirmedEditContext = null;
    }
    const appLabel = editAppLabels[session.app_type] || session.app_type || '문서';
    const location = [session.active_container, session.selection_reference]
        .filter(Boolean)
        .join(' · ');
    editDocumentName.textContent = session.document_name || '이름 없는 문서';
    const identityLabel = session.identity_kind === 'runtime' ? '임시 연결 · 저장 전' : '';
    editDocumentLocation.textContent = [appLabel, identityLabel, location]
        .filter(Boolean)
        .join(' · ');
    editDisconnectButton.disabled = false;
    renderEditContext(context || session.context || null, contextError || session.context_error || null);
    updateEditPanelVisibility();
}

function applyEditSessionHandoff(handoff = null) {
    if (!handoff?.session_id) return false;
    renderEditSession(
        handoff,
        handoff.context || null,
        handoff.context_error || null
    );
    syncEditContextMonitor(true);
    return true;
}

window.applyEditSessionHandoff = applyEditSessionHandoff;

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
                renderDirectEditFeedback(result?.data?.direct_edit_feedback || null);
                const refreshedSession = result?.data?.session;
                if (refreshedSession?.session_id === sessionId) {
                    window.currentEditSession = refreshedSession;
                    const appLabel = editAppLabels[refreshedSession.app_type]
                        || refreshedSession.app_type
                        || '문서';
                    const location = [
                        refreshedSession.active_container,
                        refreshedSession.selection_reference
                    ].filter(Boolean).join(' · ');
                    const identityLabel = refreshedSession.identity_kind === 'runtime'
                        ? '임시 연결 · 저장 전'
                        : '';
                    editDocumentName.textContent = refreshedSession.document_name
                        || '이름 없는 문서';
                    editDocumentLocation.textContent = [
                        appLabel,
                        identityLabel,
                        location
                    ].filter(Boolean).join(' · ');
                }
                renderEditContext(result.data.context);
                return result.data.context;
            }
            const message = result?.message || '현재 선택 영역을 읽지 못했습니다.';
            renderEditContext(null, {
                message,
                status: result?.status,
                retryable: result?.retryable === true
            });
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

function syncEditContextMonitor(editing) {
    const shouldRun = Boolean(editing && window.currentEditSession);
    if (shouldRun && editContextMonitorTimer === null) {
        editContextMonitorTimer = window.setInterval(() => {
            if (!editContextRefreshPromise) void refreshEditContext();
        }, EDIT_CONTEXT_MONITOR_MS);
        return;
    }
    if (!shouldRun && editContextMonitorTimer !== null) {
        window.clearInterval(editContextMonitorTimer);
        editContextMonitorTimer = null;
    }
}

function updateEditPanelVisibility() {
    const mode = document.querySelector('input[name="chat-mode"]:checked')?.value;
    const editing = mode === 'edit';
    editPanel.classList.toggle('hidden-input', !editing);
    renderEditTargetBar(window.currentEditContext);
    if (editing) {
        chatInput.placeholder = window.currentEditSession
            ? '연결된 문서에 적용할 편집 내용을 입력하세요…'
            : '먼저 편집할 문서를 연결해주세요…';
        refreshEditContext();
    } else {
        chatInput.placeholder = '메시지를 입력하세요…';
    }
    syncEditSelectionOverlay(editing && editSelectionOverlay?.checked);
    syncEditContextMonitor(editing);
}

function syncEditSelectionOverlay(enabled) {
    if (typeof eel?.set_edit_selection_overlay !== 'function') return;
    const pending = (async () => {
        try {
            const result = await eel.set_edit_selection_overlay(Boolean(enabled))();
            if (!result?.success) {
                console.warn('선택 영역 표시를 변경하지 못했습니다.', result?.message);
            }
        } catch (error) {
            console.warn('선택 영역 표시를 변경하지 못했습니다.', error);
        }
    })();
    editOverlaySyncPromise = pending;
    pending.finally(() => {
        if (editOverlaySyncPromise === pending) editOverlaySyncPromise = null;
    });
}

const chatModeInputs = Array.from(document.querySelectorAll('input[name="chat-mode"]'));

chatModeInputs.forEach((input, index) => {
    input.addEventListener('change', updateEditPanelVisibility);
    input.addEventListener('keydown', event => {
        const keyDirections = {
            ArrowRight: 1,
            ArrowDown: 1,
            ArrowLeft: -1,
            ArrowUp: -1,
        };
        let targetIndex = index;
        if (event.key === 'Home') targetIndex = 0;
        else if (event.key === 'End') targetIndex = chatModeInputs.length - 1;
        else if (keyDirections[event.key]) {
            targetIndex = (
                index + keyDirections[event.key] + chatModeInputs.length
            ) % chatModeInputs.length;
        } else {
            return;
        }
        event.preventDefault();
        const target = chatModeInputs[targetIndex];
        target.checked = true;
        target.dispatchEvent(new Event('change', { bubbles: true }));
        target.focus();
    });
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
editSelectionOverlay?.addEventListener('change', () => {
    const mode = document.querySelector('input[name="chat-mode"]:checked')?.value;
    syncEditSelectionOverlay(mode === 'edit' && editSelectionOverlay.checked);
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

editDropZone.addEventListener('keydown', event => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    chooseEditDocument();
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
            renderDirectEditFeedback(result.data?.direct_edit_feedback || null);
            editAutoLayout.checked = result.data?.auto_layout !== false;
        }
    } catch (error) {
        console.warn('편집 세션 상태를 불러오지 못했습니다.', error);
    }
});

window.addEventListener('focus', refreshEditContext);
window.addEventListener('beforeunload', () => syncEditContextMonitor(false));
