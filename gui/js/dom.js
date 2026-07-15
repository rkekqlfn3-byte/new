// Variables are declared in globals.js

// Character management removed.

window.addEventListener('load', async () => {
    try {
        const events = await eel.get_storage_recovery_events(true)();
        if (Array.isArray(events) && events.length && typeof addSystemMessage === 'function') {
            const names = events.map(item => item.path.split(/[\\/]/).pop()).join(', ');
            addSystemMessage(`저장 파일 손상을 감지해 정상 백업으로 자동 복구했습니다: ${names}`);
        }
    } catch (error) {
        console.warn('저장 복구 알림을 확인하지 못했습니다.', error);
    }
});

function scrollToBottom() {
    if (chatArea) chatArea.scrollTop = chatArea.scrollHeight;
}

// ===================================================
// UNIFIED DICTIONARY MODAL LOGIC
// ===================================================
window.switchUnifiedTab = function(tabName) {
    document.querySelectorAll('.unified-tab-pane').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.unified-tab-btn').forEach(btn => btn.classList.remove('active'));

    document.getElementById('tab-' + tabName).style.display = 'block';

    // Find the button bound to this tab and make it active.
    document.querySelectorAll('.unified-tab-btn').forEach(btn => {
        if (btn.dataset.unifiedTab === tabName) {
            btn.classList.add('active');
        }
    });

    if (tabName === 'macro') {
        if (typeof updateMacroList === 'function') updateMacroList();
        if (typeof updateLearnedMacroList === 'function') {
            updateLearnedMacroList(learnedMacroSearch?.value || '');
        }
    }
};
