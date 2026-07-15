// Button interactions
if (btnScanMenu) {
    btnScanMenu.addEventListener('click', (e) => {
        e.stopPropagation(); // Prevents clicking the menu from closing the plus-menu immediately
        if (scanSubOptions.style.display === 'none') {
            scanSubOptions.style.display = 'flex';
            btnScanMenu.innerText = '🔍 스캔 및 북마크 동기화 ▲';
        } else {
            scanSubOptions.style.display = 'none';
            btnScanMenu.innerText = '🔍 스캔 및 북마크 동기화 ▼';
        }
    });
}

if (btnAppScan) {
    btnAppScan.addEventListener('click', async () => {
        addMessage("시스템에 있는 앱들을 스캔 중입니다... ⏳", true);
        const plusMenu = document.getElementById('plus-menu');
        if(plusMenu) plusMenu.classList.remove('show');
        const count = await eel.scan_apps(false)();
        if (count === -1) {
            setTimeout(async () => {
                if (confirm("이미 초기 정밀 스캔을 마쳤습니다.\n다시 전체 정밀 스캔을 강제로 진행할까요?\n(새로운 한글화 이름들이 갱신됩니다!)")) {
                    addMessage("강제 정밀 스캔을 진행합니다... ⏳", true);
                    const forcedCount = await eel.scan_apps(true)();
                    addMessage(`강제 스캔 완료! 총 ${forcedCount}개의 앱을 갱신/추가했습니다.`, true);
                } else {
                    addMessage(`이미 초기 정밀 스캔을 마쳤습니다. 새로 설치한 앱은 <strong>[최근 앱 스캔]</strong> 버튼을 사용해주세요.`, true);
                }
            }, 500);
        } else {
            setTimeout(() => {
                addMessage(`초기 스캔 완료! 총 ${count}개의 앱을 등록했습니다.`, true);
            }, 1000);
        }
    });
}

if (btnRecentScan) {
    btnRecentScan.addEventListener('click', async () => {
        addMessage("최근 24시간 내에 새로 설치된 앱을 스캔합니다... ⏳", true);
        const plusMenu = document.getElementById('plus-menu');
        if(plusMenu) plusMenu.classList.remove('show');
        const count = await eel.scan_recent_apps()();
        setTimeout(() => {
            if (count > 0) {
                addMessage(`스캔 완료! 최근 설치된 앱 ${count}개를 추가 등록했습니다.`, true);
            } else {
                addMessage(`최근 24시간 동안 새로 설치된 앱이 없습니다.`, true);
            }
        }, 1000);
    });
}

if (btnWebScan) {
    btnWebScan.addEventListener('click', async () => {
        addMessage("Chrome 웹 북마크를 시스템에 동기화합니다... 🌐", true);
        const plusMenu = document.getElementById('plus-menu');
        if(plusMenu) plusMenu.classList.remove('show');
        const count = await eel.scan_web_bookmarks()();
        setTimeout(() => {
            if (count > 0) {
                addMessage(`동기화 완료! 총 ${count}개의 웹사이트 북마크를 추가했습니다.`, true);
            } else if (count === -1) {
                addMessage(`Chrome 북마크 파일을 찾을 수 없습니다.`, true);
            } else {
                addMessage(`새로 추가할 북마크가 없습니다.`, true);
            }
        }, 1000);
    });
}


