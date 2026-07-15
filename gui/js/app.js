function showRestartOverlay() {
    const overlay = document.createElement('div');
    overlay.className = 'loading-overlay';

    const spinner = document.createElement('div');
    spinner.className = 'spinner';

    const message = document.createElement('div');
    message.textContent = '🔄 재부팅 중입니다... 잠시만 기다려주세요!';

    overlay.append(spinner, message);
    document.body.replaceChildren(overlay);
}

const restartButton = document.getElementById('btn_refresh');
if (restartButton) {
    restartButton.addEventListener('click', () => {
        showRestartOverlay();
        eel.restart_jarvis()();
        setTimeout(() => window.close(), 500);
    });
}

document.querySelectorAll('.sidebar-tab[data-target]').forEach(button => {
    button.addEventListener('click', () => switchSidebarTab(button.dataset.target));
});

document.querySelectorAll('.memory-subtab[data-memory-target]').forEach(button => {
    button.addEventListener('click', () => {
        switchMemoryTab(button.dataset.memoryTarget, button);
    });
});

document.querySelectorAll('.unified-tab-btn[data-unified-tab]').forEach(button => {
    button.addEventListener('click', () => switchUnifiedTab(button.dataset.unifiedTab));
});

const sidebarToggle = document.getElementById('btn-toggle-sidebar');
if (sidebarToggle) {
    sidebarToggle.addEventListener('click', () => {
        const sidebar = document.querySelector('.sidebar');
        if (!sidebar) return;
        sidebar.classList.toggle('collapsed');
        sidebarToggle.textContent = sidebar.classList.contains('collapsed') ? '›' : '‹';
    });
}

const terminalPaneToggle = document.getElementById('btn-toggle-terminal-pane');
const hiddenTerminalToggle = document.getElementById('btn_toggle_term');
if (terminalPaneToggle && hiddenTerminalToggle) {
    terminalPaneToggle.addEventListener('click', () => hiddenTerminalToggle.click());
}
