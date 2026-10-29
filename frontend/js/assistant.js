import { postJSON } from './api.js';

const suggestions = [
  'What is the current disaster risk in this area?',
  'How should residents prepare for flooding?',
  'What should I do if roads become flooded?',
  'How can I find nearby emergency shelters?',
  'Summarize the latest available disaster information.',
  'Explain the risk indicators displayed on this dashboard.',
];
let sessionId;
let pending = false;

function addMessage(role, message, { loading = false, error = false, sources = [], notice } = {}) {
  const log = document.getElementById('assistant-messages');
  const bubble = document.createElement('article');
  bubble.className = `assistant-message assistant-message-${role}${error ? ' assistant-message-error' : ''}${loading ? ' assistant-message-loading' : ''}`;
  const label = document.createElement('b');
  label.textContent = role === 'user' ? 'You' : 'Response assistant';
  bubble.append(label);
  const text = document.createElement('p');
  text.textContent = message;
  bubble.append(text);
  if (notice) {
    const safety = document.createElement('p');
    safety.className = 'assistant-message-notice';
    safety.textContent = notice;
    bubble.append(safety);
  }
  if (sources.length) {
    const list = document.createElement('ul');
    list.className = 'assistant-sources';
    for (const source of sources) {
      let url;
      try { url = new URL(source.url); } catch { continue; }
      if (!['https:', 'http:'].includes(url.protocol)) continue;
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = source.title || url.hostname;
      item.append(link);
      list.append(item);
    }
    if (list.childElementCount) bubble.append(list);
  }
  log.append(bubble);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

function setPending(value) {
  pending = value;
  document.getElementById('assistant-send').disabled = value;
  document.getElementById('assistant-input').disabled = value;
  document.getElementById('assistant-status').textContent = value ? 'Checking dashboard data and preparing an answer…' : 'Answers use available dashboard data and general preparedness knowledge.';
}

async function sendMessage(value) {
  const message = value.trim();
  if (!message || pending) return;
  document.getElementById('assistant-input').value = '';
  addMessage('user', message);
  setPending(true);
  const loadingBubble = addMessage('bot', 'Thinking…', { loading: true });
  try {
    const result = await postJSON('/api/assistant/chat', { message, ...(sessionId ? { session_id: sessionId } : {}) });
    sessionId = result.session_id;
    loadingBubble.remove();
    addMessage('bot', result.answer, { sources: result.sources || [], notice: result.safety_notice });
    document.getElementById('assistant-status').textContent = 'Answer based on the current simulation snapshot and general guidance. No live alerts are connected.';
  } catch (error) {
    loadingBubble.remove();
    const messageText = error instanceof Error ? error.message : 'The assistant could not answer. Please try again.';
    addMessage('bot', messageText, { error: true });
    document.getElementById('assistant-status').textContent = 'The assistant is unavailable. Your dashboard and simulation remain available.';
  } finally {
    setPending(false);
    document.getElementById('assistant-input').focus();
  }
}

export function initAssistant() {
  const form = document.getElementById('assistant-form');
  const input = document.getElementById('assistant-input');
  form.addEventListener('submit', event => {
    event.preventDefault();
    sendMessage(input.value);
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.querySelectorAll('.assistant-suggestions button').forEach((button, index) => {
    button.addEventListener('click', () => sendMessage(suggestions[index] || button.textContent));
  });
  document.getElementById('assistant-clear').addEventListener('click', () => {
    sessionId = undefined;
    const log = document.getElementById('assistant-messages');
    log.replaceChildren();
    addMessage('bot', 'Conversation cleared. I can explain the simulation and share general preparedness guidance. I cannot verify live alerts, road conditions, or shelter availability.');
    document.getElementById('assistant-status').textContent = 'A new conversation will start with the next question.';
    input.focus();
  });
}
