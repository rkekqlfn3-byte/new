
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const cancelExecutionBtn = document.getElementById('cancel-execution-btn');
const chatArea = document.getElementById('chat-area');
const btnAppScan = document.getElementById('btn_app_scan');
const btnRecentScan = document.getElementById('btn_recent_scan');
const btnWebScan = document.getElementById('btn_web_scan');
const btnScanMenu = document.getElementById('btn_scan_menu');
const scanSubOptions = document.getElementById('scan-sub-options');
const btnUnifiedDict = document.getElementById('btn_open_unified_dict');
const unifiedDictModal = document.getElementById('unified-dict-modal');

// UI data from Eel, saved JSON, and user input is always assigned through DOM
// properties.  Keep these tiny helpers in the earliest-loaded GUI script so
// every renderer shares the same safe construction boundary.
window.createTextElement = function(tag, text = '', className = '') {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text ?? '';
    return element;
};

window.clearElement = function(element) {
    if (element) element.replaceChildren();
    return element;
};

window.appendTextLineBreaks = function(element, text) {
    const lines = String(text ?? '').split(/\r?\n/);
    lines.forEach((line, index) => {
        if (index) element.appendChild(document.createElement('br'));
        element.appendChild(document.createTextNode(line));
    });
    return element;
};

// The first Eel websocket call can briefly race with a newly opened window.
// Persisted UI loaders use this bounded retry instead of requiring a reload.
window.runUiLoadWithRetry = async function(task, attempts = 5) {
    let lastError;
    const delays = [120, 300, 700, 1200];
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        try {
            return await task();
        } catch (error) {
            lastError = error;
            if (attempt + 1 < attempts) {
                await new Promise(resolve => setTimeout(resolve, delays[attempt] || 700));
            }
        }
    }
    throw lastError;
};

window.setPlusMenuOpen = function(open) {
    const menu = document.getElementById('plus-menu');
    const button = document.getElementById('btn-plus');
    if (!menu || !button) return false;
    menu.classList.toggle('show', Boolean(open));
    if (!open) {
        menu.style.opacity = '';
        menu.style.pointerEvents = '';
    }
    button.setAttribute('aria-expanded', String(Boolean(open)));
    return true;
};

const modalFocusOrigins = new WeakMap();

function visibleModal() {
    return Array.from(document.querySelectorAll('.modal[role="dialog"]'))
        .reverse()
        .find(modal => modal.getAttribute('aria-hidden') === 'false') || null;
}

function modalFocusableElements(modal) {
    if (!modal) return [];
    return Array.from(modal.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), '
        + 'textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
    )).filter(element => element.getClientRects().length > 0);
}

window.openAccessibleModal = function(modal, display = 'block', trigger = document.activeElement) {
    if (!modal) return false;
    const alreadyOpen = modal.getAttribute('aria-hidden') === 'false';
    if (!alreadyOpen && trigger instanceof HTMLElement) {
        modalFocusOrigins.set(modal, trigger);
    }
    modal.style.display = display;
    modal.setAttribute('aria-hidden', 'false');
    window.requestAnimationFrame(() => {
        const focusTarget = modal.querySelector('[autofocus]')
            || modalFocusableElements(modal)[0]
            || modal.querySelector('.modal-content')
            || modal;
        if (!(focusTarget instanceof HTMLElement)) return;
        if (!focusTarget.hasAttribute('tabindex') && !focusTarget.matches(
            'button, input, select, textarea, a[href]'
        )) {
            focusTarget.tabIndex = -1;
        }
        focusTarget.focus({ preventScroll: true });
    });
    return true;
};

window.closeAccessibleModal = function(modal) {
    if (!modal) return false;
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    const origin = modalFocusOrigins.get(modal);
    modalFocusOrigins.delete(modal);
    window.requestAnimationFrame(() => {
        if (origin instanceof HTMLElement && origin.isConnected) {
            origin.focus({ preventScroll: true });
        } else if (chatInput) {
            chatInput.focus({ preventScroll: true });
        }
    });
    return true;
};

document.addEventListener('keydown', event => {
    const modal = visibleModal();
    if (!modal) return;
    if (event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        window.closeAccessibleModal(modal);
        return;
    }
    if (event.key !== 'Tab') return;
    const focusable = modalFocusableElements(modal);
    if (!focusable.length) {
        event.preventDefault();
        modal.focus({ preventScroll: true });
        return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
});

document.addEventListener('click', event => {
    const closeButton = event.target.closest?.('.close-modal');
    const backdrop = event.target.classList?.contains('modal') ? event.target : null;
    const modal = closeButton?.closest('.modal') || backdrop;
    if (modal?.getAttribute('role') === 'dialog') {
        window.closeAccessibleModal(modal);
    }
});
