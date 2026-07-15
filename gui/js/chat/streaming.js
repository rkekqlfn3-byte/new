let currentStreamId = null;
let currentStreamRawContent = "";
let streamScrollTimer = null;

eel.expose(receive_stream_chunk);
function receive_stream_chunk(chunk) {
    if (!currentStreamId) return;
    const msgDiv = document.getElementById(currentStreamId);
    if (!msgDiv) return;
    const bubble = msgDiv.querySelector('.bubble');
    if (!bubble) return;

    if (currentStreamRawContent === "") {
        bubble.style.opacity = "1";
        bubble.replaceChildren();
    }

    currentStreamRawContent += chunk;
    msgDiv.dataset.rawContent = currentStreamRawContent;

    bubble.replaceChildren();
    window.appendTextLineBreaks(bubble, currentStreamRawContent);

    if (!streamScrollTimer) {
        streamScrollTimer = setTimeout(() => {
            scrollToBottom();
            streamScrollTimer = null;
        }, 100);
    }
}

let _summaryVersion = 0;
function runBackgroundSummarization(oldElements, oldMessages) {
    if (!oldMessages.length) return;
    const myVersion = ++_summaryVersion;
    const sessionIdAtStart = currentSessionId;

    eel.summarize_memory(window.conversationSummary, oldMessages)().then(summaryResult => {
        if (myVersion !== _summaryVersion || sessionIdAtStart !== currentSessionId) return;
        const summarySucceeded = typeof summaryResult === 'string' || summaryResult?.success;
        let updatedSummary = typeof summaryResult === 'string' ? summaryResult : summaryResult?.summary;
        if (summarySucceeded && updatedSummary) {
            window.conversationSummary = updatedSummary;
            oldElements.forEach(el => el.classList.add('summarized'));
            if (typeof saveCurrentSession === 'function') {
                saveCurrentSession().catch(saveError => console.warn('요약 저장 실패', saveError));
            }
        }
    }).catch(summaryError => console.warn('요약 실패', summaryError));
}
