const macroTypeSelect = document.getElementById('macro-type-select');
const macroDataInput = document.getElementById('macro-data-input');
if (macroTypeSelect && macroDataInput) {
    macroTypeSelect.addEventListener('change', () => {
        const t = macroTypeSelect.value;
        if (t === 'hotkey') macroDataInput.placeholder = "어떤 단축키를 누를까요? (예: win+shift+s)";
        else if (t === 'cmd') macroDataInput.placeholder = "실행할 프로그램과 인수 (예: calc 또는 notepad 메모.txt) — 실행 전 확인";
        else macroDataInput.placeholder = "어떤 주문들을 연속으로 할까요? 쉼표로 구분 (예: 볼륨 줄여, 유튜브 열어)";
    });
}

const macroTemplateSelect = document.getElementById('macro-template-select');
if (macroTemplateSelect) {
    const templates = {
        "hotkey_screenshot": { id: "MY_SCREENSHOT", type: "hotkey", data: "win+shift+s", synonyms: "화면 캡처, 스크린샷, 캡처해" },
        "hotkey_desktop": { id: "MY_DESKTOP", type: "hotkey", data: "win+d", synonyms: "바탕화면, 바탕화면 보여줘" },
        "hotkey_copy": { id: "MY_COPY", type: "hotkey", data: "ctrl+c", synonyms: "복사, 이거 복사해" },
        "cmd_calc": { id: "MY_CALC", type: "cmd", data: "calc", synonyms: "계산기, 계산기 켜" },
        "cmd_taskmgr": { id: "MY_TASKMGR", type: "cmd", data: "taskmgr", synonyms: "작업 관리자, 작업관리자" },
        "compound_example": { id: "MY_COMPOUND", type: "compound", data: "볼륨 줄여, 유튜브 찾아줘", synonyms: "조용히 유튜브" }
    };
    macroTemplateSelect.addEventListener('change', (e) => {
        const val = e.target.value;
        if (val && templates[val]) {
            const t = templates[val];
            const n = document.getElementById('macro-name-input'); if(n) n.value = t.id;
            const ty = document.getElementById('macro-type-select'); if(ty) ty.value = t.type;
            const d = document.getElementById('macro-data-input'); if(d) d.value = t.data;
            const s = document.getElementById('macro-synonyms-input'); if(s) s.value = t.synonyms;
        } else {
            ['macro-name-input','macro-data-input','macro-synonyms-input'].forEach(id => { const el = document.getElementById(id); if(el) el.value = ''; });
        }
    });
}

if (btnAddMacro) {
    btnAddMacro.addEventListener('click', async () => {
        const macroNameInput = document.getElementById('macro-name-input');
        const mType = document.getElementById('macro-type-select').value;
        const mData = document.getElementById('macro-data-input').value.trim();
        const mSynonyms = document.getElementById('macro-synonyms-input').value.trim();
        if (!mData || !mSynonyms) {
            alert("실행 내용과 호출할 명령어를 모두 입력해주세요!");
            return;
        }
        const synonyms = mSynonyms.split(',').map(s => s.trim()).filter(Boolean);
        const mId = macroNameInput && macroNameInput.value.trim()
            ? macroNameInput.value.trim()
            : `CUSTOM_${Date.now()}`;
        const result = await eel.add_custom_macro(mId, synonyms[0], synonyms, mType, mData)();
        if (result?.success === false || result !== true) {
            alert(result?.message || '매크로를 저장하지 못했습니다.');
            return;
        }
        alert("매크로 '" + mId + "'(이)가 추가되었습니다!");
        ['macro-name-input','macro-data-input','macro-synonyms-input'].forEach(id => { const el = document.getElementById(id); if(el) el.value = ''; });
        updateMacroList();
    });
}



// ===================================================
// AI SETTINGS MODAL
// ===================================================
