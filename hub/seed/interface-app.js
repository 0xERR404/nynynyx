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

async function loadOutputFiles() {
    try {
        const res = await fetch(`${API_URL}/files`);
        const data = await res.json();
        const filesTitle = document.getElementById('filesTitle');
        const filesList = document.getElementById('filesList');
        const outbox = data.outbox || [];
        if (outbox.length === 0) {
            filesTitle.style.display = 'none';
            filesList.innerHTML = '';
            return;
        }
        filesTitle.style.display = '';
        filesList.innerHTML = outbox.map(({ name, kind }) => {
            const mediaUrl = `${API_URL}/media/${encodeURIComponent(name)}`;
            const downloadUrl = `${API_URL}/download/${encodeURIComponent(name)}`;
            if (kind === 'image') {
                return `<div class="file-card media"><img src="${mediaUrl}" alt="${name}" loading="lazy">
                    <a href="${downloadUrl}" download>⬇ ${name}</a></div>`;
            }
            if (kind === 'audio') {
                return `<div class="file-card media"><audio controls src="${mediaUrl}"></audio>
                    <a href="${downloadUrl}" download>⬇ ${name}</a></div>`;
            }
            return `<a class="file-card" href="${downloadUrl}" download>⬇ ${name}</a>`;
        }).join('');
    } catch (e) {
        // не критично — панель файлов просто не обновится в этот раз
    }
}
loadOutputFiles();

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
        loadOutputFiles();
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
    modulesGrid.innerHTML = modules.map(mod => `
        <a class="card" href="/api/${mod.folder}" target="_blank" rel="noopener">
            <div class="top">
                <div class="name">${mod.name}</div>
                <div class="desc">${mod.description || mod.folder}</div>
            </div>
            <div class="bottom">
                <span class="version">v${mod.version || '0.0.0'}</span>
                <span class="enabled-dot ${mod.enabled ? 'on' : 'off'}" title="${mod.enabled ? 'включён' : 'выключен'}"></span>
            </div>
        </a>
    `).join('');
}

// ============================================================
// НИЗ СТРАНИЦЫ — modules/dashboard/, полностью её холст. Хаб только
// проверяет, что модуль есть и включён, и подставляет iframe на всю область.
// Что внутри — решает она сама, мы туда не лезем и не задаём разметку.
// ============================================================
const dashboardArea = document.getElementById('dashboardArea');

// Один запрос к /api/modules обслуживает и карточки, и проверку dashboard —
// не дублируем сетевой вызов дважды каждые 20 секунд.
async function loadModulesData() {
    let modules = [];
    try {
        const response = await fetch('/api/modules');
        const data = await response.json();
        modules = (data.modules || []).filter(m => !m.builtin);
    } catch (e) {
        modulesGrid.innerHTML = '<div class="empty-hint">Не удалось загрузить список модулей.</div>';
        return;
    }

    renderModuleCards(modules);

    const dash = modules.find(m => m.folder === 'dashboard' && m.enabled);
    if (!dash) {
        dashboardArea.innerHTML = '<div class="empty-hint">Дашборд модулей ещё не создан — попроси Никс собрать modules/dashboard/.</div>';
        return;
    }
    // Не пересоздаём iframe, если он уже стоит — иначе он бы перезагружался
    // каждые 20 секунд вместе с опросом.
    if (dashboardArea.querySelector('iframe.dashboard-frame')) return;
    dashboardArea.innerHTML = '<iframe class="dashboard-frame" src="/api/dashboard/" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>';
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
