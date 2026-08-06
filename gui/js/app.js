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

const sidebarTabs = Array.from(document.querySelectorAll('.sidebar-tab[data-target]'));
sidebarTabs.forEach((button, index) => {
    button.addEventListener('click', () => switchSidebarTab(button.dataset.target));
    button.addEventListener('keydown', event => {
        const keys = {
            ArrowRight: (index + 1) % sidebarTabs.length,
            ArrowLeft: (index - 1 + sidebarTabs.length) % sidebarTabs.length,
            Home: 0,
            End: sidebarTabs.length - 1,
        };
        if (!(event.key in keys)) return;
        event.preventDefault();
        const next = sidebarTabs[keys[event.key]];
        switchSidebarTab(next.dataset.target);
        next.focus({ preventScroll: true });
    });
});

document.querySelectorAll('.memory-subtab[data-memory-target]').forEach(button => {
    button.addEventListener('click', () => {
        switchMemoryTab(button.dataset.memoryTarget, button);
    });
});

document.querySelectorAll('.unified-tab-btn[data-unified-tab]').forEach(button => {
    button.addEventListener('click', () => {
        switchUnifiedTab(button.dataset.unifiedTab);
        // The refused-wording list is only worth reading when it is opened,
        // and it must be current then rather than as of the last refresh.
        if (button.dataset.unifiedTab === 'missed' && typeof refreshMissed === 'function') {
            refreshMissed();
        }
    });
});

const sidebarToggle = document.getElementById('btn-toggle-sidebar');
if (sidebarToggle) {
    sidebarToggle.addEventListener('click', () => {
        const sidebar = document.querySelector('.sidebar');
        if (!sidebar) return;
        sidebar.classList.toggle('collapsed');
        const expanded = !sidebar.classList.contains('collapsed');
        sidebarToggle.textContent = expanded ? '‹' : '›';
        sidebarToggle.setAttribute('aria-expanded', String(expanded));
        sidebarToggle.setAttribute('aria-label', expanded ? '사이드바 접기' : '사이드바 펼치기');
        sidebarToggle.title = expanded ? '사이드바 접기' : '사이드바 펼치기';
        sidebar.setAttribute('aria-hidden', String(!expanded));
        if (expanded) {
            sidebar.removeAttribute('inert');
        } else {
            sidebar.setAttribute('inert', '');
        }
    });
}

const terminalPaneToggle = document.getElementById('btn-toggle-terminal-pane');
const hiddenTerminalToggle = document.getElementById('btn_toggle_term');
if (terminalPaneToggle && hiddenTerminalToggle) {
    terminalPaneToggle.addEventListener('click', () => hiddenTerminalToggle.click());
}
