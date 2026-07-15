const btnAiSettings = document.getElementById('btn_ai_settings');
const aiSettingsModal = document.getElementById('ai-settings-modal');
const closeAiSettings = document.querySelector('.close-ai-settings');
const btnSaveAiSettings = document.getElementById('btn-save-ai-settings');
const btnClearAiApiKey = document.getElementById('btn-clear-ai-api-key');
const aiProvider = document.getElementById('ai-provider');
const aiApiKey = document.getElementById('ai-api-key');
const aiApiKeyStatus = document.getElementById('ai-api-key-status');

function renderAiApiKeyState(config) {
    const hasApiKey = Boolean(config?.has_api_key);
    if (aiApiKey) {
        aiApiKey.value = '';
        aiApiKey.placeholder = hasApiKey
            ? '새 키 입력 시 기존 키 교체'
            : 'API 키 입력';
    }
    if (aiApiKeyStatus) {
        aiApiKeyStatus.textContent = hasApiKey
            ? 'API 키가 저장되어 있습니다. 빈칸으로 저장하면 기존 키를 유지합니다.'
            : '저장된 API 키가 없습니다.';
    }
    if (btnClearAiApiKey) btnClearAiApiKey.disabled = !hasApiKey;
}

if (btnAiSettings) {
    btnAiSettings.addEventListener('click', async () => {
        const config = await eel.get_ai_config()();
        if (aiProvider) aiProvider.value = config.provider || "openai";
        renderAiApiKeyState(config);
        const routingEl = document.getElementById('ai-routing-mode');
        if (routingEl) routingEl.value = config.routing_mode || "auto";
        if (aiSettingsModal) aiSettingsModal.style.display = 'block';
    });
}
if (closeAiSettings) closeAiSettings.addEventListener('click', () => { if (aiSettingsModal) aiSettingsModal.style.display = 'none'; });

if (btnSaveAiSettings) {
    btnSaveAiSettings.addEventListener('click', async () => {
        const provider = aiProvider ? aiProvider.value : "openai";
        const apiKey = aiApiKey ? aiApiKey.value.trim() : "";
        const routingMode = document.getElementById('ai-routing-mode') ? document.getElementById('ai-routing-mode').value : "auto";

        const success = await eel.save_ai_config(provider, apiKey, routingMode)();
        if (success) {
            alert("AI 설정이 저장되었습니다!");
            window._cachedAiConfig = await eel.get_ai_config()();
            renderAiApiKeyState(window._cachedAiConfig);
            if (aiSettingsModal) aiSettingsModal.style.display = 'none';
        } else {
            alert("설정 저장에 실패했습니다.");
        }
    });
}

if (btnClearAiApiKey) {
    btnClearAiApiKey.addEventListener('click', async () => {
        if (!confirm('저장된 API 키를 삭제할까요? 이 작업은 되돌릴 수 없습니다.')) return;
        const success = await eel.clear_ai_api_key(true)();
        if (!success) {
            alert('API 키 삭제에 실패했습니다.');
            return;
        }
        window._cachedAiConfig = await eel.get_ai_config()();
        renderAiApiKeyState(window._cachedAiConfig);
        alert('저장된 API 키를 삭제했습니다.');
    });
}

// ===================================================
// ACTION DICTIONARY MODAL
// ===================================================
