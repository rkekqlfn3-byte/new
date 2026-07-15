const actionDictList = document.getElementById('action-dict-list');

window.addEventListener('click', (e) => {
    if (e.target === aiSettingsModal) aiSettingsModal.style.display = 'none';
});

// ===================================================
// ACTION DICTIONARY RENDER
// ===================================================
async function renderActionDictionary(filter = '') {
    if (!actionDictList) return;
    try {
        actionDictList.innerHTML = '';
        const macros = await eel.get_macros()();
        for (const [macro, data] of Object.entries(macros)) {
            // Filter out custom macros
            if (data.type && data.type !== 'default') continue;

            // Search filter
            if (filter) {
                const searchLower = filter.toLowerCase();
                const macroMatch = macro.toLowerCase().includes(searchLower);
                const descMatch = (data.description || '').toLowerCase().includes(searchLower);
                const synMatch = (data.synonyms || []).some(s => s.toLowerCase().includes(searchLower));
                if (!macroMatch && !descMatch && !synMatch) continue;
            }

            const itemDiv = document.createElement('div');
            itemDiv.className = 'list-item';
            itemDiv.style.cssText = 'padding:10px;border-bottom:1px solid rgba(255,255,255,0.1);display:flex;flex-direction:column;gap:6px;';

            let codeHtml = '';
            if (data.description) {
                const codeBox = document.createElement('div');
                codeBox.style.cssText = 'color:#666;font-size:0.9em;margin-bottom:5px;';
                codeBox.textContent = data.description;
                codeHtml = codeBox.outerHTML;
            } else if (data.code) {
                const codeBox = document.createElement('pre');
                codeBox.style.cssText = 'background:rgba(0,0,0,0.05);padding:6px 8px;border-radius:5px;font-size:0.8em;max-height:80px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;width:100%;';
                codeBox.textContent = data.code;
                codeHtml = codeBox.outerHTML;
            }


        itemDiv.innerHTML = `
            <div><strong>${escapeDictionaryHtml(macro)}</strong></div>
            ${codeHtml}
        `;

        const synContainer = document.createElement('div');
        synContainer.style.cssText = 'display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px;';
        (data.synonyms || []).forEach(syn => {
            const badge = document.createElement('span');
            badge.className = 'synonym-badge';
            badge.textContent = `${syn} ×`;
            badge.style.cursor = 'pointer';
            badge.addEventListener('click', async () => {
                if (confirm("'" + syn + "' 동의어를 삭제하시겠습니까?")) {
                    await eel.remove_macro_synonym(macro, syn)();
                    renderActionDictionary();
                }
            });
            synContainer.appendChild(badge);
        });
        itemDiv.appendChild(synContainer);

        const addDiv = document.createElement('div');
        addDiv.style.cssText = 'display:flex;gap:5px;width:100%;';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'premium-input';
        input.placeholder = '새 행동/말투 추가 (예: 꺄)';
        input.style.flex = '1';
        const btnAdd = document.createElement('button');
        btnAdd.className = 'premium-btn primary';
        btnAdd.textContent = '추가';
        btnAdd.addEventListener('click', async () => {
            const newSyn = input.value.trim();
            if (newSyn) { await eel.add_macro_synonym(macro, newSyn)(); renderActionDictionary(); }
        });
        input.addEventListener('keypress', (e) => { if (e.key === 'Enter') btnAdd.click(); });
        addDiv.appendChild(input);
        addDiv.appendChild(btnAdd);
        itemDiv.appendChild(addDiv);
        if (actionDictList) actionDictList.appendChild(itemDiv);
    }
    } catch (e) {
        if (actionDictList) actionDictList.innerHTML = `<div style="color:red;padding:10px;">Error: ${e.message}</div>`;
        console.error(e);
    }
}

const actionDictSearch = document.getElementById('action-dict-search');
if (actionDictSearch) {
    actionDictSearch.addEventListener('input', (e) => {
        renderActionDictionary(e.target.value);
    });
}
