const pdfContextBar = document.getElementById('pdf-context-bar');
const pdfDocumentName = document.getElementById('pdf-document-name');
const pdfDocumentDetails = document.getElementById('pdf-document-details');
const pdfConnectButton = document.getElementById('btn-pdf-connect');
const pdfDisconnectButton = document.getElementById('btn-pdf-disconnect');
const pdfBarDisconnectButton = document.getElementById('btn-pdf-disconnect-bar');
const pdfDropZone = document.getElementById('pdf-file-drop-zone');

window.currentPdfConnection = null;
let pdfConnectionBusy = false;

function setPdfConnectionBusy(busy) {
    pdfConnectionBusy = Boolean(busy);
    if (pdfConnectButton) pdfConnectButton.disabled = pdfConnectionBusy;
    if (pdfDisconnectButton) pdfDisconnectButton.disabled = pdfConnectionBusy;
    if (pdfBarDisconnectButton) pdfBarDisconnectButton.disabled = pdfConnectionBusy;
    pdfDropZone?.setAttribute('aria-busy', String(pdfConnectionBusy));
}

function isValidPdfConnection(connection) {
    if (!connection || typeof connection !== 'object') return false;
    if (connection.schema_version !== 1 || connection.read_only !== true) return false;
    if (!/^pdf-connection-[a-f0-9]{32}$/.test(String(connection.connection_id || ''))) return false;
    if (!/^[a-f0-9]{64}$/.test(String(connection.document_fingerprint || ''))) return false;
    const name = String(connection.document_name || '');
    if (!name || name.length > 260 || /[\u0000-\u001f]/.test(name)) return false;
    const pages = Number(connection.page_count);
    if (!Number.isInteger(pages) || pages < 1 || pages > 2000) return false;
    return ['unknown', 'text', 'scanned', 'mixed'].includes(connection.document_kind);
}

function renderPdfConnection(connection = null) {
    const validConnection = isValidPdfConnection(connection) ? connection : null;
    window.currentPdfConnection = validConnection;
    const connected = Boolean(validConnection);

    pdfContextBar?.classList.toggle('hidden-input', !connected);
    pdfDisconnectButton?.classList.toggle('hidden-input', !connected);
    if (!connected) {
        if (pdfDocumentName) pdfDocumentName.textContent = '';
        if (pdfDocumentDetails) pdfDocumentDetails.textContent = '';
        return;
    }

    pdfDocumentName.textContent = validConnection.document_name;
    const kindLabel = {
        text: '텍스트 PDF',
        scanned: '스캔 PDF',
        mixed: '혼합 PDF',
        unknown: '형식 확인 전'
    }[validConnection.document_kind];
    pdfDocumentDetails.textContent = `${validConnection.page_count}쪽 · ${kindLabel}`;
    pdfContextBar.title = `${validConnection.document_name} · 읽기 전용 연결`;
}

function showPdfResult(result, successMessage = true) {
    const connection = result?.data?.connection || null;
    if (result?.success && connection) {
        renderPdfConnection(connection);
        if (successMessage && typeof addSystemMessage === 'function') {
            addSystemMessage(result.message || 'PDF를 읽기 전용으로 연결했습니다.');
        }
        return true;
    }
    if (result?.status !== 'cancelled' && typeof addSystemError === 'function') {
        addSystemError(result?.message || 'PDF 연결 상태를 확인하지 못했습니다.');
    }
    return false;
}

async function chooseAndConnectPdf() {
    if (!window.eel || pdfConnectionBusy) return;
    setPdfConnectionBusy(true);
    try {
        const result = await eel.choose_and_connect_pdf_document()();
        if (showPdfResult(result)) window.setPlusMenuOpen?.(false);
    } catch (error) {
        if (typeof addSystemError === 'function') {
            addSystemError(error?.message || 'PDF 선택창을 열지 못했습니다.');
        }
    } finally {
        setPdfConnectionBusy(false);
    }
}

async function connectDroppedPdf(file) {
    if (!window.eel || pdfConnectionBusy) return;
    if (!file || !String(file.name || '').toLowerCase().endsWith('.pdf')) {
        if (typeof addSystemError === 'function') {
            addSystemError('PDF 파일 하나만 놓아주세요.');
        }
        return;
    }
    setPdfConnectionBusy(true);
    try {
        const pathHint = typeof file.path === 'string' ? file.path : null;
        const result = await eel.connect_dropped_pdf_document(
            file.name,
            file.size,
            pathHint
        )();
        if (showPdfResult(result)) window.setPlusMenuOpen?.(false);
    } catch (error) {
        if (typeof addSystemError === 'function') {
            addSystemError(error?.message || '놓은 PDF를 연결하지 못했습니다.');
        }
    } finally {
        setPdfConnectionBusy(false);
    }
}

async function disconnectPdf() {
    if (!window.eel || !window.currentPdfConnection || pdfConnectionBusy) return;
    const connectionId = window.currentPdfConnection.connection_id;
    setPdfConnectionBusy(true);
    try {
        const result = await eel.disconnect_pdf_document(connectionId)();
        if (result?.success) {
            renderPdfConnection(null);
            if (typeof addSystemMessage === 'function') addSystemMessage(result.message);
            window.setPlusMenuOpen?.(false);
            return;
        }
        if (typeof addSystemError === 'function') {
            addSystemError(result?.message || 'PDF 연결을 해제하지 못했습니다.');
        }
    } catch (error) {
        if (typeof addSystemError === 'function') {
            addSystemError(error?.message || 'PDF 연결을 해제하지 못했습니다.');
        }
    } finally {
        setPdfConnectionBusy(false);
    }
}

async function refreshPdfConnectionStatus({ silent = true } = {}) {
    if (!window.eel) return null;
    try {
        const result = await eel.get_pdf_connection_status()();
        if (result?.success) {
            renderPdfConnection(result?.data?.connection || null);
            return window.currentPdfConnection;
        }
        renderPdfConnection(null);
        if (!silent && typeof addSystemError === 'function') addSystemError(result?.message);
    } catch (error) {
        renderPdfConnection(null);
        if (!silent && typeof addSystemError === 'function') {
            addSystemError(error?.message || 'PDF 연결 상태를 확인하지 못했습니다.');
        }
    }
    return null;
}

window.refreshPdfConnectionStatus = refreshPdfConnectionStatus;

pdfConnectButton?.addEventListener('click', chooseAndConnectPdf);
pdfDisconnectButton?.addEventListener('click', disconnectPdf);
pdfBarDisconnectButton?.addEventListener('click', disconnectPdf);
pdfDropZone?.addEventListener('click', chooseAndConnectPdf);

['dragenter', 'dragover'].forEach(eventName => {
    pdfDropZone?.addEventListener(eventName, event => {
        event.preventDefault();
        pdfDropZone.classList.add('drag-active');
    });
});

['dragleave', 'drop'].forEach(eventName => {
    pdfDropZone?.addEventListener(eventName, event => {
        event.preventDefault();
        pdfDropZone.classList.remove('drag-active');
    });
});

pdfDropZone?.addEventListener('drop', event => {
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length !== 1) {
        if (typeof addSystemError === 'function') {
            addSystemError('한 번에 PDF 파일 하나만 연결할 수 있습니다.');
        }
        return;
    }
    connectDroppedPdf(files[0]);
});

pdfDropZone?.addEventListener('keydown', event => {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    chooseAndConnectPdf();
});

document.addEventListener('DOMContentLoaded', () => {
    refreshPdfConnectionStatus();
});
