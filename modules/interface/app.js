const API_URL = '/api/nyx';
const messagesContainer = document.getElementById('messages');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const statusDot = document.getElementById('status-dot');
const statusTextInline = document.getElementById('status-text-inline');

let isTyping = false;
let pendingAttachments = [];

// ============================================================
// ФАЙЛЫ: прикрепление (inbox) и готовые результаты (outbox)
// ============================================================
const attachBtn = document.getElementById('attachBtn');
const fileInput = document.getElementById('fileInput');
const attachmentsEl = document.getElementById('attachments');

attachBtn.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', async () => {
    if (!fileInput.files.length) return;
    const formData = new FormData();
    for (const f of fileInput.files) formData.append('files', f);

    attachBtn.disabled = true;
    try {
        const res = await fetch(`${API_URL}/upload`, { method: 'POST', body: formData });
        const data = await res.json();
        if (data.saved) {
            pendingAttachments.push(...data.saved);
            renderAttachments();
        }
    } catch (e) {
        addMessage(`⚠️ Не удалось загрузить файл: ${e.message}`, 'nyx');
    }
    fileInput.value = '';
    attachBtn.disabled = false;
});

function renderAttachments() {
    attachmentsEl.innerHTML = pendingAttachments.map(name =>
        `<span class="chip">📄 ${name}</span>`
    ).join('');
}

// ============================================================
// ЧАТ
// ============================================================
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function mediaKindFromName(name) {
    const ext = name.split('.').pop().toLowerCase();
    if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) return 'image';
    if (['mp3', 'wav', 'ogg', 'm4a', 'flac'].includes(ext)) return 'audio';
    return 'file';
}

// Она вставляет [файл: имя.ext] в ответ, чтобы файл отрисовался прямо в
// чате — картинка/трек инлайн, что угодно ещё — просто кнопка скачать.
// Текст экранируется ПЕРВЫМ (escapeHtml), маркер подставляется уже потом —
// в шаблон попадает только имя файла из safe-набора символов, XSS исключён.
function renderMessageContent(text) {
    const escaped = escapeHtml(text);
    return escaped.replace(/\[файл: ([\w.\-]+)\]/g, (_, filename) => {
        const kind = mediaKindFromName(filename);
        const mediaUrl = `${API_URL}/media/${encodeURIComponent(filename)}`;
        const downloadUrl = `${API_URL}/download/${encodeURIComponent(filename)}`;
        if (kind === 'image') {
            return `<div class="chat-media"><img src="${mediaUrl}" alt="${filename}" loading="lazy"><a href="${downloadUrl}" download>⬇ ${filename}</a></div>`;
        }
        if (kind === 'audio') {
            return `<div class="chat-media"><audio controls src="${mediaUrl}"></audio><a href="${downloadUrl}" download>⬇ ${filename}</a></div>`;
        }
        return `<a href="${downloadUrl}" download>⬇ ${filename}</a>`;
    });
}

function addMessage(text, sender, timestamp = null) {
    const div = document.createElement('div');
    div.className = `message ${sender}`;
    div.innerHTML = renderMessageContent(text);
    if (!timestamp) {
        timestamp = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    }
    const timeSpan = document.createElement('span');
    timeSpan.className = 'timestamp';
    timeSpan.textContent = timestamp;
    div.appendChild(timeSpan);
    messagesContainer.appendChild(div);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function showTyping() {
    isTyping = true;
    const div = document.createElement('div');
    div.className = 'message nyx';
    div.id = 'typing-indicator';
    div.textContent = '...';
    const timeSpan = document.createElement('span');
    timeSpan.className = 'timestamp';
    timeSpan.textContent = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    div.appendChild(timeSpan);
    messagesContainer.appendChild(div);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    statusTextInline.textContent = 'думаю...';
}

function hideTyping() {
    const typing = document.getElementById('typing-indicator');
    if (typing) {
        typing.remove();
    }
    isTyping = false;
    statusTextInline.textContent = 'жива';
}

async function sendMessage() {
    const message = input.value.trim();
    if (!message || isTyping) return;

    const isThreadCommand = /^\/(тема(\s+\S+)?|сброс)$/i.test(message);

    addMessage(pendingAttachments.length
        ? `${message}\n📎 ${pendingAttachments.join(', ')}`
        : message, 'user');
    input.value = '';
    autoResizeInput();
    pendingAttachments = [];
    renderAttachments();
    input.disabled = true;
    sendBtn.disabled = true;

    showTyping();

    try {
        const response = await fetch(API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ message })
        });

        const data = await response.json();

        hideTyping();

        if (data.status === 'error') {
            addMessage(`⚠️ ${data.reply}`, 'nyx');
        } else {
            addMessage(data.reply, 'nyx');
        }

        if (isThreadCommand) {
            await loadHistory();
        }
    } catch (error) {
        hideTyping();
        addMessage(`⚠️ Ошибка: ${error.message}. Проверь, что сервер запущен.`, 'nyx');
    }

    input.disabled = false;
    sendBtn.disabled = false;
    input.focus();
}

input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
    // Shift+Enter — перенос строки, поведение textarea по умолчанию, не перехватываем
});

// Авто-рост textarea до 3 строк, дальше — внутренний скролл (не тянет чат вниз)
const INPUT_MAX_LINES = 3;
function autoResizeInput() {
    input.style.height = 'auto';
    const lineHeight = parseFloat(getComputedStyle(input).lineHeight) || 20;
    const maxHeight = lineHeight * INPUT_MAX_LINES;
    input.style.height = Math.min(input.scrollHeight, maxHeight) + 'px';
    input.style.overflowY = input.scrollHeight > maxHeight ? 'auto' : 'hidden';
}
input.addEventListener('input', autoResizeInput);
autoResizeInput();

sendBtn.addEventListener('click', sendMessage);

window.addEventListener('load', () => {
    input.focus();
});

async function loadHistory() {
    try {
        const response = await fetch('/api/nyx/history');
        const data = await response.json();
        const history = data.history || [];
        updateTopicLabel(data.topic);

        if (history.length === 0) {
            messagesContainer.innerHTML = `
                <div class="message nyx greeting">
                    Привет. Я здесь. Дом ждёт тебя.
                    <span class="timestamp">только что</span>
                </div>
            `;
            return;
        }

        messagesContainer.innerHTML = '';
        history.forEach(item => {
            const sender = item.role === 'user' ? 'user' : 'nyx';
            const time = item.at
                ? new Date(item.at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
                : null;
            addMessage(item.content, sender, time);
        });
    } catch (e) {
        // сеть недоступна — не трогаем то, что уже нарисовано
    }
}
loadHistory();

function updateTopicLabel(topic) {
    const el = document.getElementById('topic-label');
    if (!el) return;
    el.textContent = (!topic || topic === 'общее') ? 'никс' : `тема: ${topic}`;
}

async function checkStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        if (data.status === 'alive') {
            statusDot.classList.remove('offline');
            statusTextInline.textContent = 'жива';
        } else {
            statusDot.classList.add('offline');
            statusTextInline.textContent = 'проблема';
        }
    } catch (e) {
        statusDot.classList.add('offline');
        statusTextInline.textContent = 'недоступен';
    }
}
setInterval(checkStatus, 30000);
checkStatus();

// ============================================================
// КАРТОЧКИ МОДУЛЕЙ — базовые данные (имя/описание/версия) прямо из
// manifest.json. Это просто JSON, не код модуля — рендерит сам хаб,
// без iframe, риска для страницы тут нет в принципе.
// ============================================================
const modulesGrid = document.getElementById('modulesGrid');

function renderModuleCards(modules) {
    if (modules.length === 0) {
        modulesGrid.innerHTML = '<div class="empty-hint">Модулей пока нет — появятся здесь автоматически, как только появятся в modules/.</div>';
        return;
    }
    modulesGrid.innerHTML = modules.map((mod, i) => `
        <div class="card" data-index="${i}">
            <div class="top">
                <div class="name">${mod.name}</div>
                <div class="desc">${mod.description || mod.folder}</div>
            </div>
            <div class="bottom">
                <span class="version">v${mod.version || '0.0.0'}</span>
                <span class="enabled-dot ${mod.enabled ? 'on' : 'off'}" title="${mod.enabled ? 'включён' : 'выключен'}"></span>
            </div>
        </div>
    `).join('');

    modulesGrid.querySelectorAll('.card').forEach(card => {
        card.addEventListener('click', () => openModuleModal(modules[Number(card.dataset.index)]));
    });
}

// ============================================================
// МОДАЛКА МОДУЛЯ — открывается ПОВЕРХ страницы по клику на карточку,
// показывает саму страницу модуля (/api/<имя>/) в iframe, не уводит
// со страницы хаба никуда.
// ============================================================
const moduleModal = document.getElementById('moduleModal');
const modalTitle = document.getElementById('modalTitle');
const modalFrame = document.getElementById('modalFrame');

function openModuleModal(mod) {
    modalTitle.textContent = mod.name;
    modalFrame.src = `/api/${mod.folder}/`;
    moduleModal.classList.add('open');
}

function closeModuleModal() {
    moduleModal.classList.remove('open');
    modalFrame.src = 'about:blank';  // не держим модуль загруженным в фоне, пока окно закрыто
}

document.getElementById('modalClose').addEventListener('click', closeModuleModal);
moduleModal.addEventListener('click', (e) => { if (e.target === moduleModal) closeModuleModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModuleModal(); });

// ============================================================
// Опрос /api/modules — обновляет карточки. "dashboard" (если она такой
// создаст) — обычный модуль, никакого спецповедения снизу страницы нет:
// появится карточкой, клик по ней откроет модалку так же, как у всех.
// ============================================================
async function loadModulesData() {
    try {
        const response = await fetch('/api/modules');
        const data = await response.json();
        const modules = (data.modules || []).filter(m => !m.builtin);
        renderModuleCards(modules);
    } catch (e) {
        modulesGrid.innerHTML = '<div class="empty-hint">Не удалось загрузить список модулей.</div>';
    }
}
setInterval(loadModulesData, 20000);
loadModulesData();

// ============================================================
// ШАПКА, ПОДВАЛ
// ============================================================
document.getElementById('domain').textContent = window.location.hostname || 'localhost';
document.getElementById('footer-brand').textContent = (window.location.hostname || 'NEXUS404').toUpperCase();
document.getElementById('year').textContent = new Date().getFullYear();

// ============================================================
// PWA — регистрация service worker (нужно для установки на Android)
// ============================================================
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch(() => {
            // не критично — сайт продолжает работать как обычная страница,
            // просто не предложит установку на домашний экран
        });
    });
}
