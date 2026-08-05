const dictSearchInput = document.getElementById('dict-search-input');
const dictAppSelect = document.getElementById('dict-app-select');
const dictSynonymInput = document.getElementById('dict-synonym-input');
const btnAddSynonym = document.getElementById('btn-add-synonym');
const dictRenameInput = document.getElementById('dict-rename-input');
const btnRenameNoun = document.getElementById('btn-rename-noun');

let currentNouns = {};
let favoritesList = [];
let currentTab = 'all';

// Mock value property for the div-based list
if (dictAppSelect && dictAppSelect.tagName !== 'SELECT') {
    Object.defineProperty(dictAppSelect, 'value', {
        get: function() { return this.getAttribute('data-selected') || ''; },
        set: function(v) { this.setAttribute('data-selected', v); }
    });
}

const btnToggleFav = document.getElementById('btn-toggle-fav');
const btnTabAll = document.getElementById('btn-tab-all');
const btnTabFav = document.getElementById('btn-tab-fav');

if (btnTabAll) btnTabAll.addEventListener('click', () => { currentTab = 'all'; updateAppSelect(dictSearchInput ? dictSearchInput.value.toLowerCase() : ''); });
if (btnTabFav) btnTabFav.addEventListener('click', () => { currentTab = 'fav'; updateAppSelect(dictSearchInput ? dictSearchInput.value.toLowerCase() : ''); });

async function updateAppSelect(filter = '') {
    const listDiv = document.getElementById('dict-app-select');
    if (!listDiv) return;

    let entries = Object.entries(currentNouns);
    if (currentTab === 'fav') entries = entries.filter(([n]) => favoritesList.includes(n));
    if (filter) entries = entries.filter(([n, p]) => n.toLowerCase().includes(filter) || (p && p.toLowerCase().includes(filter)));

    // Dedup by path
    const uniqueApps = {};
    for (const [noun, path] of entries) {
        if (!uniqueApps[path]) {
            uniqueApps[path] = noun;
        } else {
            const existing = uniqueApps[path];
            const existingKor = /[\uAC00-\uD7A3]/.test(existing);
            const newKor = /[\uAC00-\uD7A3]/.test(noun);
            const existingFav = favoritesList.includes(existing);
            const newFav = favoritesList.includes(noun);
            if (newFav && !existingFav) { uniqueApps[path] = noun; continue; }
            if (existingFav && !newFav) continue;
            if (newKor && !existingKor) uniqueApps[path] = noun;
        }
    }

    const isKorean = (str) => /[\uAC00-\uD7A3]/.test(str);
    const sortedApps = Object.entries(uniqueApps).sort((a, b) => {
        const aFav = favoritesList.includes(a[1]);
        const bFav = favoritesList.includes(b[1]);
        if (aFav && !bFav) return -1;
        if (!aFav && bFav) return 1;
        const aKor = isKorean(a[1]);
        const bKor = isKorean(b[1]);
        if (aKor && !bKor) return -1;
        if (!aKor && bKor) return 1;
        return a[1].localeCompare(b[1]);
    });

    if (listDiv.tagName === 'SELECT') {
        listDiv.innerHTML = '';
        sortedApps.forEach(([path, noun]) => {
            const opt = document.createElement('option');
            opt.value = noun;
            opt.textContent = noun;
            listDiv.appendChild(opt);
        });
    } else {
        listDiv.innerHTML = '';
        const frag = document.createDocumentFragment();
        // Cap results to 200 items to avoid lag
        const displayApps = sortedApps.slice(0, 200);
        displayApps.forEach(([path, noun]) => {
            const isWeb = path.startsWith('http://') || path.startsWith('https://');
            const displayPath = isWeb ? path : path.split('/').pop().split('\\').pop();
            const icon = isWeb ? '🌐' : '📄';
            const favStar = favoritesList.includes(noun) ? '⭐ ' : '';

            const row = document.createElement('div');
            row.className = 'list-item';
            row.dataset.val = noun;
            const name = document.createElement('span');
            name.style.flex = '1';
            name.textContent = `${favStar}${icon} ${noun}`;
            const location = document.createElement('small');
            location.style.cssText = 'color:#888;font-size:0.75em;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
            location.title = path;
            location.textContent = displayPath;
            const edit = document.createElement('button');
            edit.type = 'button';
            edit.className = 'row-edit';
            edit.textContent = '✏️';
            edit.title = `'${noun}' 이름 바꾸기`;
            edit.setAttribute('aria-label', `${noun} 이름 바꾸기`);
            edit.style.cssText = 'background:none;border:none;cursor:pointer;padding:2px 4px;opacity:0.55;';

            const del = document.createElement('button');
            del.type = 'button';
            del.className = 'row-delete';
            del.textContent = '×';
            del.title = `'${noun}' 목록에서 지우기`;
            del.setAttribute('aria-label', `${noun} 목록에서 지우기`);
            del.style.cssText = 'background:none;border:none;color:#e06c75;font-size:1.2em;line-height:1;cursor:pointer;padding:2px 6px;opacity:0.7;';

            edit.addEventListener('click', (e) => {
                e.stopPropagation();
                row.click();
                if (dictRenameInput) {
                    dictRenameInput.value = noun;
                    dictRenameInput.focus();
                    dictRenameInput.select();
                }
            });
            del.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm(`'${noun}'을(를) 목록에서 지울까요?\n\n다시 검색해도 돌아오지 않습니다. 나중에 필요하면 아래 '직접 가르치기'로 다시 등록할 수 있어요.`)) return;
                const removed = await eel.remove_noun(noun)();
                if (!removed) { alert('삭제에 실패했습니다.'); return; }
                if (listDiv.getAttribute('data-selected') === noun) {
                    listDiv.setAttribute('data-selected', '');
                    const synList = document.getElementById('dict-synonyms-list');
                    if (synList) synList.innerHTML = '';
                }
                currentNouns = await eel.get_nouns()();
                favoritesList = await eel.get_favorites()();
                updateAppSelect(dictSearchInput ? dictSearchInput.value.toLowerCase() : '');
            });

            row.append(name, location, edit, del);
            row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;border-radius:6px;transition:background 0.15s;';
            row.addEventListener('mouseover', () => row.style.background = 'rgba(255,255,255,0.07)');
            row.addEventListener('mouseout', () => row.style.background = listDiv.getAttribute('data-selected') === noun ? 'rgba(108,92,231,0.25)' : '');
            row.addEventListener('click', () => {
                listDiv.setAttribute('data-selected', noun);
                listDiv.querySelectorAll('.list-item').forEach(r => r.style.background = '');
                row.style.background = 'rgba(108,92,231,0.25)';
                if (dictAppSelect) dictAppSelect.value = noun;
                updateSynonymList();
                updateFavButtonState();
            });
            if (listDiv.getAttribute('data-selected') === noun) {
                row.style.background = 'rgba(108,92,231,0.25)';
            }
            frag.appendChild(row);
        });
        if (sortedApps.length > 200) {
            const moreDiv = document.createElement('div');
            moreDiv.style.cssText = 'padding:10px;text-align:center;color:#888;font-size:0.9em;';
            moreDiv.textContent = `... 그 외 ${sortedApps.length - 200}개 앱 생략 (검색어를 입력하세요)`;
            frag.appendChild(moreDiv);
        }
        listDiv.appendChild(frag);
    }
}

function updateFavButtonState() {
    const selectedNoun = dictAppSelect ? dictAppSelect.value : '';
    if (!selectedNoun || !btnToggleFav) return;
    btnToggleFav.style.background = favoritesList.includes(selectedNoun) ? "#fee500" : "#fff";
}

if (btnToggleFav) {
    btnToggleFav.addEventListener('click', async () => {
        const selectedNoun = dictAppSelect ? dictAppSelect.value : '';
        if (!selectedNoun) { alert("즐겨찾기할 앱을 먼저 선택해주세요."); return; }
        await eel.toggle_favorite(selectedNoun)();
        favoritesList = await eel.get_favorites()();
        updateFavButtonState();
        updateAppSelect(dictSearchInput ? dictSearchInput.value.toLowerCase() : '');
        if (dictAppSelect) dictAppSelect.value = selectedNoun;
    });
}

async function updateSynonymList() {
    const selectedNoun = dictAppSelect ? dictAppSelect.value : '';
    if (!selectedNoun) return;
    const path = currentNouns[selectedNoun];
    const syns = await eel.get_synonyms_for_path(path, selectedNoun)();

    const listDiv = document.getElementById('dict-synonyms-list');
    if (!listDiv) return;
    listDiv.innerHTML = '';

    if (syns.length === 0) {
        listDiv.innerHTML = `<span style="color:#888;font-size:0.9em;padding-left:5px;">현재 등록된 별명이 없습니다. 추가해 보세요!</span>`;
        return;
    }
    syns.forEach(syn => {
        const badge = document.createElement('span');
        badge.className = "synonym-badge";
        badge.appendChild(document.createTextNode(`${syn} `));
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'del-syn';
        remove.dataset.syn = syn;
        remove.textContent = '×';
        badge.appendChild(remove);
        listDiv.appendChild(badge);
    });
    listDiv.querySelectorAll('.del-syn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const s = e.target.getAttribute('data-syn');
            if (confirm("동의어 '" + s + "'를 정말 삭제할까요?")) {
                await eel.remove_noun_synonym(s)();
                currentNouns = await eel.get_nouns()();
                updateSynonymList();
            }
        });
    });
}

if (btnUnifiedDict && unifiedDictModal) {
    btnUnifiedDict.addEventListener('click', async () => {
        const pm = document.getElementById('plus-menu');
        if (pm) window.setPlusMenuOpen(false);
        window.openAccessibleModal(unifiedDictModal, 'flex', btnUnifiedDict);

        // Load all data
        currentNouns = await eel.get_nouns()();
        favoritesList = await eel.get_favorites()();
        updateAppSelect();

        if (typeof renderActionDictionary === 'function') renderActionDictionary();
        if (typeof updateMacroList === 'function') updateMacroList();
        if (typeof updateLearnedMacroList === 'function') updateLearnedMacroList();
    });
}

if (dictSearchInput) {
    let debounceTimer;
    dictSearchInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            updateAppSelect(e.target.value.toLowerCase());
        }, 300);
    });
}

if (btnAddSynonym) {
    btnAddSynonym.addEventListener('click', async () => {
        const original = dictAppSelect ? dictAppSelect.value : '';
        const synonym = dictSynonymInput ? dictSynonymInput.value.trim() : '';
        if (!synonym) return;
        const success = await eel.add_noun_synonym(original, synonym)();
        if (success) {
            alert("성공! 이제 '" + original + "' 대신 '" + synonym + "'(으)로 부를 수 있어요!");
            if (dictSynonymInput) dictSynonymInput.value = '';
            currentNouns = await eel.get_nouns()();
            updateSynonymList();
            addMessage("이제 '" + synonym + " 열어줘'라고 편하게 명령해도 다 알아들을 수 있어! ✨", true);
        } else {
            alert("저장에 실패했습니다.");
        }
    });
}

if (btnRenameNoun) {
    btnRenameNoun.addEventListener('click', async () => {
        const selectedNoun = dictAppSelect ? dictAppSelect.value : '';
        const newName = dictRenameInput ? dictRenameInput.value.trim() : '';
        if (!selectedNoun) return alert('단어를 먼저 선택해주세요.');
        if (!newName) return alert('변경할 새 이름을 입력해주세요.');
        if (selectedNoun === newName) return alert('기존 이름과 동일합니다!');
        const success = await eel.rename_noun(selectedNoun, newName)();
        if (success) {
            alert("새 이름 '" + newName + "'(으)로 변경되었습니다!");
            if (dictRenameInput) dictRenameInput.value = '';
            currentNouns = await eel.get_nouns()();
            updateAppSelect();
        } else {
            alert("변경에 실패했습니다.");
        }
    });
}

// Custom Noun Add
const btnAddCustom = document.getElementById('btn-add-custom');
if (btnAddCustom) {
    btnAddCustom.addEventListener('click', async () => {
        const noun = document.getElementById('custom-noun-input').value.trim();
        const path = document.getElementById('custom-path-input').value.trim();
        if (noun && path) {
            await eel.add_custom_noun(noun, path)();
            alert("'" + noun + "'이(가) 등록되었습니다!");
            document.getElementById('custom-noun-input').value = '';
            document.getElementById('custom-path-input').value = '';
            currentNouns = await eel.get_nouns()();
            updateAppSelect();
        }
    });
}

// AI Finder
const btnAiSearch = document.getElementById('btn-ai-search');
if (btnAiSearch) {
    btnAiSearch.addEventListener('click', async () => {
        const query = document.getElementById('custom-noun-input').value.trim();
        if (!query) { alert("검색할 앱 이름을 먼저 입력해주세요!"); return; }
        btnAiSearch.textContent = "\uD83D\uDD0D 검색 중...";
        const results = await eel.ai_find_exe(query)();
        const listDiv = document.getElementById('ai-results-list');
        if (listDiv) listDiv.innerHTML = '';
        btnAiSearch.textContent = "✨ 컴퓨터에서 직접 찾기";
        if (results.length === 0) {
            if (listDiv) listDiv.innerHTML = "<div class='ai-result-item' style='color:#ed8796;'>발견된 앱이 없습니다. 직접 경로를 입력해주세요. 😭</div>";
            return;
        }
        results.forEach(res => {
            const div = document.createElement('div');
            div.className = "ai-result-item";
            const name = document.createElement('strong');
            name.textContent = res.name || '';
            const path = document.createElement('small');
            path.textContent = res.path || '';
            div.append(name, path);
            div.addEventListener('click', () => {
                document.getElementById('custom-path-input').value = res.path;
                div.style.borderColor = "#a6e3a1";
                setTimeout(() => div.style.borderColor = "transparent", 500);
            });
            if (listDiv) listDiv.appendChild(div);
        });
    });
}

// ===================================================
// IMAGE MODAL
// ===================================================
