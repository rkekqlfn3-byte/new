
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

