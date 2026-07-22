(function () {
    'use strict';

    const EVENT_TYPES = new Set([
        'request_received', 'intent_resolved', 'action_preparing',
        'action_started', 'confirmation_required', 'clarification_required',
        'verification_passed', 'verification_failed', 'action_completed',
        'action_failed', 'rollback_started', 'rollback_completed',
        'rollback_failed', 'action_cancelled', 'action_busy',
        'recovery_started', 'recovery_completed', 'recovery_failed'
    ]);
    const TONES = new Set(['progress', 'success', 'attention', 'error', 'neutral']);
    const region = document.getElementById('user-feedback-region');
    let lastSignature = '';

    function boundedText(value, limit) {
        return String(value ?? '')
            .replace(/[\u0000-\u001f\u007f]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, limit);
    }

    function normalizeUserEvent(value) {
        if (!value || typeof value !== 'object') return null;
        const eventType = boundedText(value.event_type, 40);
        if (value.schema_version !== 1 || !EVENT_TYPES.has(eventType)) return null;
        return {
            event_type: eventType,
            execution_id: boundedText(value.execution_id, 80),
            headline: boundedText(value.headline, 160),
            detail: boundedText(value.detail, 260),
            next_action: boundedText(value.next_action, 220),
            tone: TONES.has(value.tone) ? value.tone : 'neutral'
        };
    }

    function appendText(parent, className, text, tagName) {
        if (!text) return;
        const node = document.createElement(tagName || 'p');
        node.className = className;
        node.textContent = text;
        parent.appendChild(node);
    }

    function renderUserEvent(value) {
        const event = normalizeUserEvent(value);
        if (!event || !region || !event.headline) return false;
        const signature = [
            event.execution_id, event.event_type, event.headline,
            event.detail, event.next_action, event.tone
        ].join('|');
        if (signature === lastSignature) return true;
        lastSignature = signature;

        const card = document.createElement('div');
        card.className = 'user-feedback-card';
        card.dataset.tone = event.tone;
        card.dataset.executionId = event.execution_id;

        const indicator = document.createElement('span');
        indicator.className = 'user-feedback-indicator';
        indicator.setAttribute('aria-hidden', 'true');
        card.appendChild(indicator);

        const copy = document.createElement('div');
        copy.className = 'user-feedback-copy';
        appendText(copy, 'user-feedback-headline', event.headline, 'strong');
        appendText(copy, 'user-feedback-detail', event.detail);
        appendText(copy, 'user-feedback-next-action', event.next_action);
        card.appendChild(copy);

        region.replaceChildren(card);
        region.classList.remove('hidden-input');
        return true;
    }

    window.renderUserEvent = renderUserEvent;
    window.receive_user_event = renderUserEvent;
    if (window.eel && typeof window.eel.expose === 'function') {
        window.eel.expose(window.receive_user_event, 'receive_user_event');
    }
})();
