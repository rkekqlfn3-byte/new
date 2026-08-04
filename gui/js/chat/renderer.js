window.conversationSummary = window.conversationSummary || "";

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    // Browser serialization does not necessarily escape quotes in text-node
    // context.  Escape them too because this helper is retained for legacy
    // static templates that place a value in a data attribute.
    return div.innerHTML
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// 이 창은 eel을 통해 Python 기능과 연결되므로, AI 응답·저장 대화에서 온
// HTML이 그대로 실행되면 일반 채팅창보다 피해가 크다. 마크다운 렌더 결과를
// 허용 목록 기반으로 정화해 스크립트·이벤트 속성·위험 링크를 제거한다.
const SANITIZE_ALLOWED_TAGS = new Set([
    'P', 'BR', 'HR', 'STRONG', 'EM', 'B', 'I', 'U', 'S', 'DEL',
    'CODE', 'PRE', 'BLOCKQUOTE', 'UL', 'OL', 'LI',
    'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'A',
    'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD',
]);
const SANITIZE_SAFE_HREF = /^(https?:|mailto:)/i;

function sanitizeRenderedHtml(html) {
    const template = document.createElement('template');
    template.innerHTML = html;
    const elements = Array.from(template.content.querySelectorAll('*'));
    for (const el of elements) {
        if (!template.content.contains(el)) continue;
        if (!SANITIZE_ALLOWED_TAGS.has(el.tagName)) {
            el.replaceWith(document.createTextNode(el.textContent || ''));
            continue;
        }
        for (const attr of Array.from(el.attributes)) {
            const name = attr.name.toLowerCase();
            if (el.tagName === 'A' && name === 'href'
                && SANITIZE_SAFE_HREF.test(attr.value.trim())) continue;
            if (el.tagName === 'CODE' && name === 'class'
                && /^language-[A-Za-z0-9_+-]*$/.test(attr.value)) continue;
            el.removeAttribute(attr.name);
        }
        if (el.tagName === 'A' && el.hasAttribute('href')) {
            el.setAttribute('target', '_blank');
            el.setAttribute('rel', 'noopener noreferrer');
        }
    }
    return template.innerHTML;
}

function normalizeKoreanMarkdownBoundaries(text) {
    // Marked treats a closing ** immediately followed by a Hangul syllable as
    // an in-word delimiter. An invisible boundary keeps the Korean suffix
    // visually attached while allowing the intended strong span to render.
    return String(text || '').replace(
        /\*\*([^*\r\n]+?)\*\*(?=[가-힣])/g,
        (_match, content) => `**${content}\u200B**\u200B`
    );
}

function formatMessageContent(text, isIncoming, image_data = null) {
    if (!text) return '';

    let processed = normalizeKoreanMarkdownBoundaries(
        text.trim().replace('[응/아니오]', '')
    );
    let parsedText = (typeof marked !== 'undefined')
        ? sanitizeRenderedHtml(marked.parse(processed))
        : escapeHtml(processed).replace(/\n/g, '<br>');
    return parsedText;
}

function appendLearningPromptActions(bubble) {
    const actions = document.createElement('div');
    actions.style.cssText = 'margin-top:10px;display:flex;gap:10px;';
    const review = window.createTextElement('button', '학습 내용 검토', 'premium-btn primary');
    review.type = 'button';
    review.addEventListener('click', openLearningReview);
    const runOnce = window.createTextElement('button', '이번만 실행', 'premium-btn');
    runOnce.type = 'button';
    runOnce.addEventListener('click', () => discardPendingLearning('run_once'));
    const discard = window.createTextElement('button', '폐기', 'premium-btn');
    discard.type = 'button';
    discard.style.cssText = 'background:#ff5555;color:white;border:none;';
    discard.addEventListener('click', () => discardPendingLearning('discard'));
    actions.append(review, runOnce, discard);
    bubble.appendChild(actions);
}

function isSafeImageData(imageData) {
    return typeof imageData === 'string'
        && /^data:image\/(?:png|jpeg|gif|webp);base64,/i.test(imageData);
}

function appendFormattedMessageContent(bubble, text, isIncoming, imageData = null) {
    bubble.replaceChildren();
    if (isSafeImageData(imageData)) {
        const image = document.createElement('img');
        image.className = 'chat-image-attachment';
        image.src = imageData;
        image.alt = '첨부 이미지. Enter 키로 크게 보기';
        image.tabIndex = 0;
        image.setAttribute('role', 'button');
        const openImageModal = () => {
            const modal = document.getElementById('image-modal');
            const modalImage = document.getElementById('modal-img');
            if (modal && modalImage) {
                modalImage.src = image.src;
                window.openAccessibleModal(modal, 'block', image);
            }
        };
        image.addEventListener('click', openImageModal);
        image.addEventListener('keydown', event => {
            if (!['Enter', ' '].includes(event.key)) return;
            event.preventDefault();
            openImageModal();
        });
        bubble.append(image, document.createElement('br'));
    }
    const rendered = formatMessageContent(text, isIncoming);
    if (!rendered) return;
    // ``formatMessageContent`` accepts either escaped plain text or the
    // allowlist-sanitized Markdown result.  This is the only chat Markdown
    // insertion boundary.
    bubble.insertAdjacentHTML('beforeend', rendered);
    if (String(text || '').includes('[응/아니오]')) {
        appendLearningPromptActions(bubble);
    }
}

function createChatMessage(text, isIncoming, imageData = null) {
    const message = document.createElement('div');
    message.className = `message ${isIncoming ? 'incoming' : 'outgoing'}`;
    message.dataset.rawContent = String(text || '');
    const content = document.createElement('div');
    content.className = 'message-content';
    if (isIncoming) {
        const sender = document.createElement('div');
        sender.className = 'sender-name current-name';
        sender.textContent = 'Jarvis ⚡';
        content.appendChild(sender);
    }
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    appendFormattedMessageContent(bubble, text, isIncoming, imageData);
    content.appendChild(bubble);
    message.appendChild(content);
    return message;
}
window.createChatMessage = createChatMessage;

const SESSION_ONLY_MESSAGE_PLACEHOLDER = '[PDF 기반 응답은 현재 실행에서만 표시되었습니다.]';

function applyMessagePersistencePolicy(message, response) {
    if (!message || response?.data?.chat_persistence !== 'session_only') return message;
    message.dataset.sessionOnly = 'true';
    message.dataset.persistencePlaceholder = SESSION_ONLY_MESSAGE_PLACEHOLDER;
    return message;
}
window.applyMessagePersistencePolicy = applyMessagePersistencePolicy;

function finalizeStreamMessage(msgDiv, rawText) {
    if (!msgDiv || !rawText) return;
    msgDiv.dataset.rawContent = rawText;
    const bubble = msgDiv.querySelector('.bubble');
    if (bubble) {
        bubble.style.opacity = '1';
        appendFormattedMessageContent(bubble, rawText, true);
    }
    scrollToBottom();
}

function sendDirectMessage(text) {
    chatInput.value = text;
    sendMessage();
}


function addMessage(text, isIncoming, image_data=null) {
    const msgDiv = createChatMessage(text, isIncoming, image_data);
    chatArea.appendChild(msgDiv);
    scrollToBottom();
    return msgDiv;
}

function addSystemMessage(msg) {
    const chatOutput = document.getElementById('chat-area');
    if (!chatOutput) return;
    const line = document.createElement('div');
    line.className = 'system-error-line';
    line.setAttribute('data-system-note', 'true');
    const message = document.createElement('span');
    message.textContent = `ℹ️ ${msg ?? ''}`;
    line.appendChild(message);
    chatOutput.appendChild(line);
    scrollToBottom();
}

function addSystemError(errorMsg) {
    const line = document.createElement('div');
    line.className = 'system-error-line';
    line.setAttribute('data-system-note', 'true');
    const title = document.createElement('span');
    title.textContent = '⚠️ 오류';
    const detail = document.createElement('span');
    detail.className = 'error-detail';
    detail.textContent = errorMsg ?? '';
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = '↺ 다시 시도';
    retry.style.cssText = 'margin-left:10px; padding:2px 8px; font-size:0.8em; cursor:pointer; border:1px solid #ff6b6b; border-radius:4px; background:transparent; color:#ff6b6b;';
    retry.addEventListener('click', () => {
        line.remove();
        sendMessage(true);
    });
    line.append(title, detail, retry);
    chatArea.appendChild(line);
    scrollToBottom();
}
