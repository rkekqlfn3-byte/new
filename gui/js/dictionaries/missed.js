// 자비스가 못 알아들은 문장 목록.
// 이 화면이 없으면 실패는 기록만 되고 아무도 보지 못한다. 오늘 찾은 규칙
// 버그는 전부 사람이 "이렇게 말했는데 안 되네"라고 말해줘서 나왔다.
const missedList = document.getElementById('missed-list');
const btnMissedRefresh = document.getElementById('btn-missed-refresh');
const btnMissedClear = document.getElementById('btn-missed-clear');

function missedRow(item) {
    const row = document.createElement('div');
    row.className = 'list-item';
    row.style.cssText =
        'display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:6px;';

    const text = document.createElement('span');
    text.style.flex = '1';
    text.textContent = item.command;

    const meta = document.createElement('small');
    meta.style.cssText = 'color:#888;font-size:0.75em;white-space:nowrap;';
    meta.textContent = `${item.app_type || '?'} · ${item.count}회`;
    meta.title = item.reason || '';

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.title = '이 문장 지우기';
    remove.setAttribute('aria-label', `${item.command} 지우기`);
    remove.style.cssText =
        'background:none;border:none;color:#e06c75;font-size:1.2em;line-height:1;cursor:pointer;padding:2px 6px;';
    remove.addEventListener('click', async () => {
        await eel.forget_unrecognised_command(item.command, item.app_type || '')();
        refreshMissed();
    });

    row.append(text, meta, remove);
    return row;
}

async function refreshMissed() {
    if (!missedList) return;
    let items = [];
    try {
        items = await eel.get_unrecognised_commands()();
    } catch (error) {
        items = [];
    }
    missedList.innerHTML = '';
    if (!items.length) {
        missedList.innerHTML =
            '<span style="color:#888;font-size:0.9em;padding-left:5px;">' +
            '아직 없습니다. 자비스가 못 알아들은 문장이 생기면 여기 쌓입니다.</span>';
        return;
    }
    const frag = document.createDocumentFragment();
    items.forEach((item) => frag.appendChild(missedRow(item)));
    missedList.appendChild(frag);
}

if (btnMissedRefresh) btnMissedRefresh.addEventListener('click', refreshMissed);
if (btnMissedClear) {
    btnMissedClear.addEventListener('click', async () => {
        if (!confirm('못 알아들은 문장 기록을 모두 지울까요?')) return;
        await eel.clear_unrecognised_commands()();
        refreshMissed();
    });
}
