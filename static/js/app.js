/**
 * OCR Live Stream - Frontend Logic
 * Canvas region selection, WebSocket real-time data, stream management
 */

// ============================================================
// State
// ============================================================
const ROOM_ID = window.location.pathname.split('/').pop();
const API = '';

let ws = null;
let currentTool = null; // 'lote', 'nome', 'valor', or custom type
let regions = []; // { type, label, value, x, y, width, height } (relative 0-1)
let isDrawing = false;
let drawStart = { x: 0, y: 0 };
let drawCurrent = { x: 0, y: 0 };
let frameLoaded = false;
let isRunning = false;
let frameRefreshInterval = null;
let lastLote = null;
let currentPayload = {};
let currentLotBids = [];    // lances do lote atual
let lotStartTime = null;    // quando o lote atual começou
let lastValue = null;       // último valor de lance (para dedup)
let pendingLote = null;     // lote candidato aguardando estabilização
let pendingLoteCount = 0;   // quantas leituras consecutivas do candidato
const LOT_STABLE_READS = 2; // leituras necessárias para confirmar troca

// Canvas and image refs
const canvas = document.getElementById('region-canvas');
const ctx = canvas.getContext('2d');
const previewImg = document.getElementById('preview-img');
const previewContainer = document.getElementById('preview-container');

// Colors per region type
const REGION_COLORS = {
    lote:  { fill: 'rgba(0, 214, 143, 0.15)', stroke: '#00d68f', text: '#00d68f' },
    nome:  { fill: 'rgba(77, 166, 255, 0.15)', stroke: '#4da6ff', text: '#4da6ff' },
    valor: { fill: 'rgba(255, 217, 61, 0.15)', stroke: '#ffd93d', text: '#ffd93d' },
};

// Paleta extra para campos customizados
const CUSTOM_COLORS = [
    { fill: 'rgba(168, 85, 247, 0.15)', stroke: '#a855f7', text: '#a855f7' },
    { fill: 'rgba(251, 146, 60, 0.15)', stroke: '#fb923c', text: '#fb923c' },
    { fill: 'rgba(236, 72, 153, 0.15)', stroke: '#ec4899', text: '#ec4899' },
    { fill: 'rgba(34, 211, 238, 0.15)', stroke: '#22d3ee', text: '#22d3ee' },
    { fill: 'rgba(163, 230, 53, 0.15)', stroke: '#a3e635', text: '#a3e635' },
];
let customColorIndex = 0;

const REGION_LABELS = {
    lote: 'Lote',
    nome: 'Pagamento',
    valor: 'Valor',
};

const REGION_DEFAULT_VALUES = {
    lote: 'lotNumber',
    nome: 'conditions',
    valor: 'value',
};

// ============================================================
// Init
// ============================================================
async function init() {
    await loadRoom();
    setupCanvas();
    connectWebSocket();
    loadTemplates();
}

async function loadRoom() {
    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}`);
        if (!res.ok) {
            showToast('Sala não encontrada', 'error');
            return;
        }
        const room = await res.json();
        document.getElementById('room-name').textContent = room.name;
        document.title = `${room.name} - OCR Live Stream`;

        if (room.stream_url) {
            document.getElementById('stream-url').value = room.stream_url;
        }

        if (room.auction_id) {
            document.getElementById('auction-id').value = room.auction_id;
        }

        if (room.ocr_interval) {
            document.getElementById('interval-slider').value = room.ocr_interval;
            updateIntervalLabel();
        }

        // Load regions
        if (room.regions && room.regions.length > 0) {
            regions = room.regions.map(r => ({
                type: r.type,
                label: r.label || REGION_LABELS[r.type] || r.type,
                value: r.value || REGION_DEFAULT_VALUES[r.type] || r.type,
                x: r.x,
                y: r.y,
                width: r.width,
                height: r.height,
            }));

            // Restore custom field types (non-default)
            const DEFAULT_TYPES = ['lote', 'nome', 'valor'];
            const seenCustom = new Set();
            for (const r of regions) {
                if (!DEFAULT_TYPES.includes(r.type) && !seenCustom.has(r.type)) {
                    seenCustom.add(r.type);
                    restoreCustomType(r.type, r.label);
                }
            }

            updateRegionsList();
        }

        // If stream is active, load frame
        if (room.stream_status && room.stream_status.has_frame) {
            loadFrame();
        }

        // If extraction is running
        if (room.is_active) {
            setRunningState(true);
            startFrameRefresh();
            startDebugRefresh();
            startReportRefresh();
        }

        // Carregar relatório de lotes e log de POST
        loadLotReport();
        loadPostLog();

    } catch (err) {
        console.error('Error loading room:', err);
    }
}

// ============================================================
// Stream Connection
// ============================================================
async function connectStream() {
    // Se já está rodando, parar
    if (isRunning) {
        await stopExtraction();
        updateConnectButton();
        return;
    }

    const url = document.getElementById('stream-url').value.trim();
    if (!url) {
        showToast('Cole uma URL de stream', 'error');
        return;
    }

    const btn = document.getElementById('btn-connect');
    btn.textContent = 'Conectando...';
    btn.disabled = true;

    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stream_url: url })
        });
        const data = await res.json();

        if (data.status === 'ok') {
            showToast(`Stream conectado! (${data.stream_type})`, 'success');
            loadFrame();
            // Auto-start OCR se houver regiões definidas
            if (regions.length > 0) {
                setTimeout(() => startExtraction(true), 500);
            }
        } else {
            showToast(`Erro: ${data.error}`, 'error');
        }
    } catch (err) {
        showToast('Erro ao conectar stream', 'error');
    } finally {
        updateConnectButton();
    }
}

function updateConnectButton() {
    const btn = document.getElementById('btn-connect');
    const restartBtn = document.getElementById('btn-restart');
    btn.disabled = false;
    if (isRunning) {
        btn.innerHTML = '<i data-lucide="square" style="width:14px;height:14px;"></i> Parar';
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-danger');
        restartBtn.style.display = '';
        // Atualizar status no header
        const wsStatus = document.getElementById('ws-status');
        wsStatus.className = 'connection-status connected';
        wsStatus.innerHTML = '<span class="status-dot active"></span> Extraindo';
    } else {
        btn.innerHTML = '<i data-lucide="play" style="width:14px;height:14px;"></i> Conectar';
        btn.classList.remove('btn-danger');
        btn.classList.add('btn-primary');
        restartBtn.style.display = 'none';
    }
    if (window.lucide) lucide.createIcons();
}

async function restartStream() {
    const btn = document.getElementById('btn-restart');
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader" style="width:14px;height:14px;" class="spin"></i>';
    if (window.lucide) lucide.createIcons();

    try {
        // Parar
        await stopExtraction();
        // Aguardar um momento
        await new Promise(r => setTimeout(r, 500));
        // Reconectar
        const url = document.getElementById('stream-url').value.trim();
        if (url) {
            const res = await fetch(`${API}/api/rooms/${ROOM_ID}/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ stream_url: url })
            });
            const data = await res.json();
            if (data.status === 'ok') {
                loadFrame();
                if (regions.length > 0) {
                    await startExtraction(true);
                }
                showToast('Stream reiniciado!', 'success');
            } else {
                showToast(`Erro: ${data.error}`, 'error');
            }
        }
    } catch (err) {
        showToast('Erro ao reiniciar', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="rotate-cw" style="width:14px;height:14px;"></i>';
        if (window.lucide) lucide.createIcons();
    }
}

function loadFrame() {
    const img = previewImg;
    const ts = Date.now();
    img.onload = () => {
        frameLoaded = true;
        img.style.display = 'block';
        document.getElementById('preview-placeholder').style.display = 'none';
        resizeCanvas();
        drawRegions();
    };
    img.onerror = () => {
        // Silently fail, will retry
    };
    img.src = `${API}/api/rooms/${ROOM_ID}/frame?t=${ts}`;
}

function refreshFrame() {
    loadFrame();
}

function startFrameRefresh() {
    // Polling frame a frame para preview ao vivo
    if (frameRefreshInterval) clearInterval(frameRefreshInterval);
    frameRefreshInterval = setInterval(() => {
        const img = previewImg;
        const ts = Date.now();
        const newImg = new Image();
        newImg.onload = () => {
            img.src = newImg.src;
            img.style.display = 'block';
            document.getElementById('preview-placeholder').style.display = 'none';
            if (!frameLoaded) {
                frameLoaded = true;
                resizeCanvas();
                drawRegions();
            }
        };
        newImg.src = `${API}/api/rooms/${ROOM_ID}/frame?t=${ts}`;
    }, 500); // ~2 fps — leve e confiável
}

function stopFrameRefresh() {
    if (frameRefreshInterval) {
        clearInterval(frameRefreshInterval);
        frameRefreshInterval = null;
    }
    // Voltar para frame estático
    loadFrame();
}

// ============================================================
// Canvas - Region Selection
// ============================================================
function setupCanvas() {
    canvas.addEventListener('mousedown', onMouseDown);
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseup', onMouseUp);
    canvas.addEventListener('mouseleave', onMouseUp);

    // Touch support
    canvas.addEventListener('touchstart', onTouchStart, { passive: false });
    canvas.addEventListener('touchmove', onTouchMove, { passive: false });
    canvas.addEventListener('touchend', onTouchEnd);

    window.addEventListener('resize', () => {
        resizeCanvas();
        drawRegions();
    });
}

function resizeCanvas() {
    if (!frameLoaded) return;
    // Match canvas internal resolution to its display size
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
    console.log(`Canvas resized: ${canvas.width}x${canvas.height}`);
}

function getImageRect() {
    // Since we use object-fit: fill, image fills the entire container
    const rect = canvas.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
}

function getCanvasPos(e) {
    const imgRect = getImageRect();
    // Clamp to 0-1 range
    let x = (e.clientX - imgRect.left) / imgRect.width;
    let y = (e.clientY - imgRect.top) / imgRect.height;
    x = Math.max(0, Math.min(1, x));
    y = Math.max(0, Math.min(1, y));
    return { x, y };
}

function onMouseDown(e) {
    if (!currentTool || !frameLoaded) return;
    isDrawing = true;
    drawStart = getCanvasPos(e);
    drawCurrent = { ...drawStart };
}

function onMouseMove(e) {
    if (!isDrawing) return;
    drawCurrent = getCanvasPos(e);
    drawRegions();
    drawCurrentRect();
}

function onMouseUp(e) {
    if (!isDrawing) return;
    isDrawing = false;
    drawCurrent = getCanvasPos(e);

    const x = Math.min(drawStart.x, drawCurrent.x);
    const y = Math.min(drawStart.y, drawCurrent.y);
    const w = Math.abs(drawCurrent.x - drawStart.x);
    const h = Math.abs(drawCurrent.y - drawStart.y);

    // Ignore tiny selections
    if (w < 0.01 || h < 0.01) {
        drawRegions();
        return;
    }

    // Remove existing region of same type (only allow 1 of each)
    regions = regions.filter(r => r.type !== currentTool);

    regions.push({
        type: currentTool,
        label: REGION_LABELS[currentTool] || currentTool,
        value: REGION_DEFAULT_VALUES[currentTool] || currentTool,
        x, y,
        width: w,
        height: h,
    });

    drawRegions();
    updateRegionsList();
    updatePayloadPreview();
    saveRegions();
}

// Touch handlers
function onTouchStart(e) {
    e.preventDefault();
    if (!currentTool || !frameLoaded) return;
    const touch = e.touches[0];
    isDrawing = true;
    const rect = canvas.getBoundingClientRect();
    drawStart = {
        x: (touch.clientX - rect.left) / rect.width,
        y: (touch.clientY - rect.top) / rect.height
    };
    drawCurrent = { ...drawStart };
}

function onTouchMove(e) {
    e.preventDefault();
    if (!isDrawing) return;
    const touch = e.touches[0];
    const rect = canvas.getBoundingClientRect();
    drawCurrent = {
        x: (touch.clientX - rect.left) / rect.width,
        y: (touch.clientY - rect.top) / rect.height
    };
    drawRegions();
    drawCurrentRect();
}

function onTouchEnd(e) {
    if (!isDrawing) return;
    isDrawing = false;
    const x = Math.min(drawStart.x, drawCurrent.x);
    const y = Math.min(drawStart.y, drawCurrent.y);
    const w = Math.abs(drawCurrent.x - drawStart.x);
    const h = Math.abs(drawCurrent.y - drawStart.y);
    if (w < 0.01 || h < 0.01) { drawRegions(); return; }
    regions = regions.filter(r => r.type !== currentTool);
    regions.push({ type: currentTool, label: REGION_LABELS[currentTool] || currentTool, value: REGION_DEFAULT_VALUES[currentTool] || currentTool, x, y, width: w, height: h });
    drawRegions();
    updateRegionsList();
    saveRegions();
}

function drawRegions() {
    if (!frameLoaded) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (const region of regions) {
        const color = REGION_COLORS[region.type] || REGION_COLORS.lote;
        const px = region.x * canvas.width;
        const py = region.y * canvas.height;
        const pw = region.width * canvas.width;
        const ph = region.height * canvas.height;

        // Fill
        ctx.fillStyle = color.fill;
        ctx.fillRect(px, py, pw, ph);

        // Border
        ctx.strokeStyle = color.stroke;
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 3]);
        ctx.strokeRect(px, py, pw, ph);
        ctx.setLineDash([]);

        // Label
        const label = region.label || region.type;
        ctx.font = 'bold 13px Inter, sans-serif';
        const textMetrics = ctx.measureText(label);
        const textW = textMetrics.width + 12;
        const textH = 22;

        ctx.fillStyle = color.stroke;
        ctx.beginPath();
        ctx.roundRect(px, py - textH - 2, textW, textH, 4);
        ctx.fill();

        ctx.fillStyle = '#000';
        ctx.fillText(label, px + 6, py - 8);
    }
}

function drawCurrentRect() {
    if (!currentTool) return;
    const color = REGION_COLORS[currentTool];

    const x = Math.min(drawStart.x, drawCurrent.x) * canvas.width;
    const y = Math.min(drawStart.y, drawCurrent.y) * canvas.height;
    const w = Math.abs(drawCurrent.x - drawStart.x) * canvas.width;
    const h = Math.abs(drawCurrent.y - drawStart.y) * canvas.height;

    ctx.fillStyle = color.fill;
    ctx.fillRect(x, y, w, h);

    ctx.strokeStyle = color.stroke;
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.strokeRect(x, y, w, h);
}

// ============================================================
// Tool Selection
// ============================================================
function setTool(type) {
    currentTool = currentTool === type ? null : type;

    document.querySelectorAll('.toolbar .tool-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.type === currentTool);
    });

    // Change cursor
    canvas.style.cursor = currentTool ? 'crosshair' : 'default';
}

function addCustomField() {
    const modal = document.getElementById('custom-field-modal');
    modal.classList.add('active');
    const nameInput = document.getElementById('cf-name');
    const keyInput = document.getElementById('cf-key');
    nameInput.value = '';
    keyInput.value = '';
    document.getElementById('cf-type').value = 'text';
    setTimeout(() => nameInput.focus(), 100);

    // Auto-preencher chave ao digitar o nome
    nameInput.oninput = () => {
        keyInput.value = nameInput.value.trim().toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');
    };
}

function closeCustomFieldModal() {
    document.getElementById('custom-field-modal').classList.remove('active');
}

function confirmAddCustomField() {
    const name = document.getElementById('cf-name').value.trim();
    const key = document.getElementById('cf-key').value.trim() || name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');
    const contentType = document.getElementById('cf-type').value;

    if (!name) {
        showToast('Digite um nome para o campo', 'error');
        return;
    }
    if (!key) return;

    // Verificar se já existe
    if (REGION_LABELS[key]) {
        showToast(`Campo "${name}" já existe`, 'error');
        return;
    }

    // Atribuir cor
    const color = CUSTOM_COLORS[customColorIndex % CUSTOM_COLORS.length];
    customColorIndex++;

    REGION_COLORS[key] = color;
    REGION_LABELS[key] = name;

    // Adicionar botão na toolbar (antes do btn +)
    const toolbar = document.getElementById('region-toolbar');
    const addBtn = document.getElementById('btn-add-field');
    const btn = document.createElement('button');
    btn.className = 'tool-btn';
    btn.dataset.type = key;
    btn.onclick = () => setTool(key);
    btn.style.borderColor = color.stroke;
    btn.style.color = color.text;
    btn.innerHTML = `<i data-lucide="text-cursor-input" style="width:14px;height:14px;"></i> ${name}`;
    toolbar.insertBefore(btn, addBtn);

    // Adicionar data card no side panel
    const sidePanel = document.querySelector('.side-panel');
    const regionsCard = sidePanel.querySelector('.card:has(.regions-list)') || sidePanel.children[3];
    const card = document.createElement('div');
    card.className = 'card data-card';
    card.id = `card-custom-${key}`;
    card.innerHTML = `
        <div class="data-label">
            <i data-lucide="text-cursor-input" style="width:13px;height:13px;color:${color.text}"></i>
            ${name} <span class="conf-badge" id="conf-${key}"></span>
        </div>
        <div class="data-value" id="data-custom-${key}" style="color:${color.text}">—</div>
    `;
    sidePanel.insertBefore(card, regionsCard);

    // Recriar ícones lucide
    if (window.lucide) lucide.createIcons();

    closeCustomFieldModal();
    setTool(key);
    showToast(`Campo "${name}" adicionado — desenhe a região no preview`, 'success');
    updateRegionsList();
    updatePayloadPreview();
}

// Restaurar um tipo customizado (botão + card) — chamado no loadRoom
function restoreCustomType(key, label) {
    if (REGION_LABELS[key]) return; // já existe

    const color = CUSTOM_COLORS[customColorIndex % CUSTOM_COLORS.length];
    customColorIndex++;
    REGION_COLORS[key] = color;
    REGION_LABELS[key] = label;

    // Adicionar botão na toolbar
    const toolbar = document.getElementById('region-toolbar');
    const addBtn = document.getElementById('btn-add-field');
    const btn = document.createElement('button');
    btn.className = 'tool-btn';
    btn.dataset.type = key;
    btn.onclick = () => setTool(key);
    btn.style.borderColor = color.stroke;
    btn.style.color = color.text;
    btn.innerHTML = `<i data-lucide="text-cursor-input" style="width:14px;height:14px;"></i> ${label}`;
    toolbar.insertBefore(btn, addBtn);

    // Adicionar data card
    const sidePanel = document.querySelector('.side-panel');
    const regionsCard = sidePanel.querySelector('.card:has(.regions-list)') || sidePanel.children[3];
    const card = document.createElement('div');
    card.className = 'card data-card';
    card.id = `card-custom-${key}`;
    card.innerHTML = `
        <div class="data-label">
            <i data-lucide="text-cursor-input" style="width:13px;height:13px;color:${color.text}"></i>
            ${label} <span class="conf-badge" id="conf-${key}"></span>
        </div>
        <div class="data-value" id="data-custom-${key}" style="color:${color.text}">—</div>
    `;
    sidePanel.insertBefore(card, regionsCard);

    if (window.lucide) lucide.createIcons();
}

// Fechar modal com Escape e clique no overlay
document.getElementById('custom-field-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeCustomFieldModal();
});
document.getElementById('cf-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') confirmAddCustomField();
    if (e.key === 'Escape') closeCustomFieldModal();
});

// ============================================================
// Regions Management
// ============================================================
function updateRegionsList() {
    const list = document.getElementById('regions-list');
    const count = document.getElementById('regions-count');

    // Merge: regions drawn + custom types registered but not drawn yet
    const DEFAULT_TYPES = ['lote', 'nome', 'valor'];
    const drawnTypes = new Set(regions.map(r => r.type));
    const pendingCustom = [];
    for (const key of Object.keys(REGION_LABELS)) {
        if (!DEFAULT_TYPES.includes(key) && !drawnTypes.has(key)) {
            pendingCustom.push({ type: key, label: REGION_LABELS[key], value: REGION_DEFAULT_VALUES[key] || key, pending: true });
        }
    }

    const allItems = [...regions, ...pendingCustom];
    count.textContent = `${allItems.length} regiões`;

    if (allItems.length === 0) {
        list.innerHTML = '<div style="color: var(--text-muted); font-size: 0.85rem; text-align: center; padding: 1rem;">Selecione uma ferramenta e desenhe na imagem</div>';
        return;
    }

    list.innerHTML = allItems.map((r, i) => {
        const isDefault = DEFAULT_TYPES.includes(r.type);
        const isPending = !!r.pending;
        const realIndex = isPending ? -1 : i; // -1 = not in regions array
        const customColor = REGION_COLORS[r.type];
        const badgeStyle = customColor ? `background:${customColor.fill};color:${customColor.text};border-color:${customColor.stroke}` : '';

        if (isPending) {
            return `
            <div class="region-item" style="opacity:0.6;">
                <div class="region-info" style="flex:1;">
                    <span class="region-badge ${r.type}" ${badgeStyle ? `style="${badgeStyle}"` : ''}>${REGION_LABELS[r.type] || r.type}</span>
                    <span style="font-size: 0.72rem; color: var(--yellow); font-style:italic;">
                        Desenhe no preview
                    </span>
                </div>
                <button class="btn btn-outline btn-sm" onclick="removeCustomType('${r.type}')" style="padding: 0.2rem 0.5rem;">✕</button>
            </div>`;
        }

        return `
        <div class="region-item">
            <div class="region-info" style="flex:1;">
                <span class="region-badge ${r.type}" ${badgeStyle ? `style="${badgeStyle}"` : ''}>${REGION_LABELS[r.type] || r.type}</span>
                <input type="text" class="input" value="${r.value || r.type}"
                    onchange="updateRegionValue(${realIndex}, this.value)"
                    ${isDefault ? 'readonly' : ''}
                    style="font-size:0.72rem; padding:0.15rem 0.4rem; width:80px; background:var(--surface);border:1px solid var(--border);border-radius:3px;${isDefault ? 'opacity:0.6;cursor:not-allowed;' : ''}"
                    title="${isDefault ? 'Chave fixa' : 'Chave no payload JSON'}" placeholder="chave">
                <span style="font-size: 0.72rem; color: var(--text-muted);">
                    ${(r.width * 100).toFixed(0)}×${(r.height * 100).toFixed(0)}%
                </span>
            </div>
            <button class="btn btn-outline btn-sm" onclick="removeRegion(${realIndex})" style="padding: 0.2rem 0.5rem;">✕</button>
        </div>
    `}).join('');
}

function updateRegionValue(index, value) {
    regions[index].value = value.trim().toLowerCase().replace(/\s+/g, '_');
    updatePayloadPreview();
    saveRegions();
}

function removeRegion(index) {
    regions.splice(index, 1);
    updateRegionsList();
    drawRegions();
    saveRegions();
}

function removeCustomType(key) {
    // Remove from label/color maps
    delete REGION_LABELS[key];
    delete REGION_COLORS[key];
    delete REGION_DEFAULT_VALUES[key];
    // Remove toolbar button
    const toolBtn = document.querySelector(`.tool-btn[data-type="${key}"]`);
    if (toolBtn) toolBtn.remove();
    // Remove data card
    const card = document.getElementById(`card-custom-${key}`);
    if (card) card.remove();
    // Also remove drawn region if exists
    regions = regions.filter(r => r.type !== key);
    updateRegionsList();
    drawRegions();
    updatePayloadPreview();
    showToast('Campo removido', 'success');
}

function clearRegions() {
    regions = [];
    updateRegionsList();
    drawRegions();
    saveRegions();
}

async function saveRegions() {
    if (regions.length === 0) {
        showToast('Desenhe pelo menos uma região', 'error');
        return;
    }

    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/regions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ regions })
        });
        const data = await res.json();
        showToast(`${data.count} regiões salvas!`, 'success');
    } catch (err) {
        showToast('Erro ao salvar regiões', 'error');
    }
}

async function saveAuctionId() {
    const auctionId = document.getElementById('auction-id').value.trim();
    if (!auctionId) {
        showToast('Digite o ID do leilão', 'error');
        return;
    }
    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/auction-id`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ auction_id: auctionId })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            showToast('Auction ID salvo!', 'success');
        }
    } catch (err) {
        showToast('Erro ao salvar Auction ID', 'error');
    }
}

// ============================================================
// Extraction Control
// ============================================================
async function startExtraction(silent = false) {
    // Auto-save regions first
    if (regions.length > 0) {
        await saveRegions();
    }

    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/start`, { method: 'POST' });
        const data = await res.json();

        if (res.ok) {
            if (!silent) showToast(`OCR iniciado! Intervalo: ${data.interval}s`, 'success');
            setRunningState(true);
            startFrameRefresh();
            startDebugRefresh();
            startReportRefresh();
        } else {
            if (!silent) showToast(`Erro: ${data.detail}`, 'error');
        }
    } catch (err) {
        if (!silent) showToast('Erro ao iniciar extração', 'error');
    }
}

async function stopExtraction() {
    try {
        await fetch(`${API}/api/rooms/${ROOM_ID}/stop`, { method: 'POST' });
        showToast('OCR parado', 'info');
        setRunningState(false);
        stopFrameRefresh();
        stopDebugRefresh();
        stopReportRefresh();
    } catch (err) {
        showToast('Erro ao parar', 'error');
    }
}

function setRunningState(running) {
    isRunning = running;
    updateConnectButton();
}

// ============================================================
// Interval
// ============================================================
function updateIntervalLabel() {
    const val = parseFloat(document.getElementById('interval-slider').value).toFixed(1);
    document.getElementById('interval-label').textContent = `${val}s`;
}

async function saveInterval() {
    const interval = parseFloat(document.getElementById('interval-slider').value);
    try {
        await fetch(`${API}/api/rooms/${ROOM_ID}/interval`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval })
        });
        showToast(`Intervalo atualizado: ${interval}s`, 'success');
    } catch (err) {
        showToast('Erro ao salvar intervalo', 'error');
    }
}

// ============================================================
// WebSocket - Real-time data
// ============================================================
function connectWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${ROOM_ID}`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        document.getElementById('ws-status').className = 'connection-status connected';
        document.getElementById('ws-status').innerHTML = '<span class="status-dot active"></span> Conectado';
    };

    ws.onclose = () => {
        document.getElementById('ws-status').className = 'connection-status disconnected';
        document.getElementById('ws-status').innerHTML = '<span class="status-dot idle"></span> Desconectado';
        // Reconnect after 3s
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = () => {
        ws.close();
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.type === 'extraction') {
            updateDataDisplay(msg.data);
            addLogEntry(msg.data, msg.timestamp);
        } else if (msg.type === 'error') {
            showToast(`Erro OCR: ${msg.message}`, 'error');
        } else if (msg.type === 'stream_error') {
            showToast(msg.message, 'error');
        }
    };

    // Keep alive
    setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send('ping');
        }
    }, 30000);
}

// Fallback: polling HTTP quando WS não conecta
let pollingInterval = null;

function startPolling() {
    if (pollingInterval) return;
    pollingInterval = setInterval(async () => {
        if (!isRunning) return;
        try {
            const res = await fetch(`${API}/api/rooms/${ROOM_ID}/latest`);
            const json = await res.json();
            if (json.data) {
                updateDataDisplay(json.data);
            }
        } catch (e) { /* ignore */ }
    }, 2000);
}

function stopPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
}

// Iniciar polling como backup
startPolling();

function updateDataDisplay(data) {
    // Atualizar cards fixos
    const loteEl = document.getElementById('data-lote');
    const nomeEl = document.getElementById('data-nome');
    const valorEl = document.getElementById('data-valor');

    if (data.lote !== undefined) {
        loteEl.textContent = data.lote || '—';
    }
    if (data.nome !== undefined) {
        nomeEl.textContent = data.nome || '—';
    }
    if (data.valor !== undefined) {
        valorEl.textContent = data.valor ? `R$ ${data.valor}` : '—';
    }

    // Atualizar cards customizados
    for (const r of regions) {
        const customEl = document.getElementById(`data-custom-${r.type}`);
        if (customEl && data[r.type] !== undefined) {
            customEl.textContent = data[r.type] || '—';
        }
    }

    // Atualizar badges de confiança
    if (data.confidence) {
        for (const key of Object.keys(data.confidence)) {
            updateConfBadge(`conf-${key}`, data.confidence[key]);
        }
    }

    // Montar payload
    buildPayload(data);

    // Rastrear lances: novo valor = novo lance
    if (data.valor && data.valor !== '0' && data.valor !== lastValue) {
        lastValue = data.valor;
        currentLotBids.push({
            value: data.valor,
            payload: { ...currentPayload },
            captured_at: new Date().toISOString()
        });
    }

    // Detectar troca de lote com estabilização (evita leituras falsas do OCR)
    if (data.lote && data.lote !== '0') {
        // Filtrar ruído: ignorar lote "1" se já temos um lote multi-dígito confirmado
        // O OCR lê "1" do pedaço da palavra "LOTE" cortada na imagem
        const isNoise = (data.lote === '1' && lastLote && lastLote.length > 1);

        console.log(`[LOT-READ] Lido: "${data.lote}" | Atual: "${lastLote}" | Pendente: "${pendingLote}" (${pendingLoteCount}x) | Ruído: ${isNoise}`);

        if (data.lote !== lastLote && !isNoise) {
            // Novo candidato a lote — verificar estabilidade
            if (data.lote === pendingLote) {
                pendingLoteCount++;
            } else {
                // Mudou de candidato — resetar contagem
                pendingLote = data.lote;
                pendingLoteCount = 1;
            }

            // Só confirmar troca após N leituras consecutivas iguais
            if (pendingLoteCount >= LOT_STABLE_READS) {
                if (lastLote !== null) {
                    onLotChange(lastLote, data.lote);
                } else {
                    // Primeiro lote detectado
                    lotStartTime = new Date().toISOString();
                }
                lastLote = data.lote;
                pendingLote = null;
                pendingLoteCount = 0;
                console.log(`[LOT] ✅ Lote confirmado: ${data.lote} (após ${LOT_STABLE_READS} leituras)`);
            }
        } else {
            // Mesmo lote ou ruído — resetar candidato pendente
            pendingLote = null;
            pendingLoteCount = 0;
        }
    }
}

function buildPayload(data) {
    const auctionId = document.getElementById('auction-id')?.value?.trim() || null;
    currentPayload = {};
    if (auctionId) currentPayload.auctionId = auctionId;
    for (const r of regions) {
        const key = r.value || r.type;
        const val = data[r.type];
        currentPayload[key] = (val && val !== '0') ? val : null;
    }
    updatePayloadPreview();
}

function updatePayloadPreview() {
    const el = document.getElementById('payload-json');
    if (!el) return;
    const preview = {};
    const auctionId = document.getElementById('auction-id')?.value?.trim() || null;
    if (auctionId) preview.auctionId = auctionId;
    for (const r of regions) {
        const key = r.value || r.type;
        preview[key] = currentPayload[key] || null;
    }
    el.textContent = JSON.stringify(preview, null, 2);
}

async function onLotChange(oldLot, newLot) {
    const now = new Date().toISOString();
    const auctionId = document.getElementById('auction-id')?.value?.trim() || null;
    const payload = { ...(auctionId ? { auctionId } : {}), ...currentPayload, _lote_anterior: oldLot, _lote_atual: newLot, _timestamp: now };
    console.log('[POST] Troca de lote detectada:', payload);

    // Adicionar ao log visual
    const logEl = document.getElementById('payload-log');
    if (logEl) {
        const time = new Date().toLocaleTimeString('pt-BR');
        const entry = document.createElement('div');
        entry.style.cssText = 'padding:0.4rem;border-bottom:1px solid var(--border);font-size:0.75rem;font-family:monospace;';
        entry.innerHTML = `<span style="color:var(--text-muted)">${time}</span> Lote ${oldLot} → ${newLot} <span style="color:var(--yellow)">(${currentLotBids.length} lances)</span>`;
        logEl.prepend(entry);
    }

    // Salvar relatório do lote anterior no backend
    const finalBid = currentLotBids.length > 0 ? currentLotBids[currentLotBids.length - 1].value : null;
    const lotReport = {
        auction_id: auctionId,
        lot_number: oldLot,
        started_at: lotStartTime || now,
        ended_at: now,
        final_value: finalBid,
        bid_count: currentLotBids.length,
        extra_data: { ...(auctionId ? { auctionId } : {}), ...currentPayload },
        bids: currentLotBids
    };

    try {
        await fetch(`${API}/api/rooms/${ROOM_ID}/lot-report`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(lotReport)
        });
        console.log(`[REPORT] Lote ${oldLot} salvo: ${currentLotBids.length} lances`);
    } catch (e) {
        console.error('[REPORT] Erro ao salvar relatório:', e);
    }

    // Atualizar card de relatório
    addLotToReport(oldLot, currentLotBids, lotStartTime || now, now, finalBid);

    // Reset para novo lote
    currentLotBids = [];
    lastValue = null;
    lotStartTime = now;
}

function addLotToReport(lotNumber, bids, startedAt, endedAt, finalValue) {
    const container = document.getElementById('report-lots');
    if (!container) return;

    // Remove placeholder
    const ph = container.querySelector('.report-empty');
    if (ph) ph.remove();

    // Atualizar contagem
    const countEl = document.getElementById('report-count');
    const current = parseInt(countEl?.textContent || '0');
    if (countEl) countEl.textContent = current + 1;

    const bidCount = Array.isArray(bids) ? bids.length : (bids || 0);
    const startTime = new Date(startedAt).toLocaleTimeString('pt-BR');
    const endTime = new Date(endedAt).toLocaleTimeString('pt-BR');

    const lotEl = document.createElement('div');
    lotEl.className = 'report-lot-row';
    lotEl.innerHTML = `
        <span class="region-badge lote" style="font-size:0.72rem;min-width:70px;text-align:center;">LOTE ${lotNumber}</span>
        <span style="font-size:0.8rem;color:var(--text-secondary);">${bidCount} lance${bidCount !== 1 ? 's' : ''}</span>
        <span style="font-size:0.85rem;color:var(--yellow);font-weight:700;flex:1;text-align:right;">${finalValue ? 'R$ ' + finalValue : '—'}</span>
        <span style="font-size:0.65rem;color:var(--text-muted);white-space:nowrap;">${startTime} — ${endTime}</span>
    `;
    container.prepend(lotEl);
}

async function loadLotReport() {
    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/lot-report`);
        const data = await res.json();
        const container = document.getElementById('report-lots');
        const countEl = document.getElementById('report-count');
        if (!container) return;

        if (data.lots && data.lots.length > 0) {
            container.innerHTML = '';
            if (countEl) countEl.textContent = data.lots.length;
            // Mostrar do mais recente ao mais antigo
            for (const lot of data.lots.reverse()) {
                const bidCount = lot.bid_count || 0;
                const startTime = lot.started_at ? new Date(lot.started_at).toLocaleTimeString('pt-BR') : '—';
                const endTime = lot.ended_at ? new Date(lot.ended_at).toLocaleTimeString('pt-BR') : '—';
                const lotEl = document.createElement('div');
                lotEl.className = 'report-lot-row';
                lotEl.innerHTML = `
                    <span class="region-badge lote" style="font-size:0.72rem;min-width:70px;text-align:center;">LOTE ${lot.lot_number}</span>
                    <span class="region-badge" style="font-size:0.72rem;background:var(--accent-glow);color:var(--accent);border:1px solid rgba(108,92,231,0.3);" title="${bidCount} lance${bidCount !== 1 ? 's' : ''}"><i data-lucide="gavel" style="width:12px;height:12px;"></i> ${bidCount}</span>
                    <span style="font-size:0.85rem;color:var(--yellow);font-weight:700;flex:1;text-align:right;white-space:nowrap;">${lot.final_value ? 'R$ ' + lot.final_value : '—'}</span>
                    <span style="font-size:0.65rem;color:var(--text-muted);white-space:nowrap;">${endTime}</span>
                `;
                container.appendChild(lotEl);
            }
        } else {
            container.innerHTML = '<div class="report-empty" style="color:var(--text-muted);font-size:0.8rem;text-align:center;padding:0.7rem;">Aguardando dados do lote</div>';
            if (countEl) countEl.textContent = '0';
        }
    } catch (e) {
        console.error('[REPORT] Erro ao carregar relatório:', e);
    }
    if (window.lucide) lucide.createIcons();
}

// Refresh automático do relatório (lê do banco a cada 5s)
let reportRefreshInterval = null;
function startReportRefresh() {
    if (reportRefreshInterval) clearInterval(reportRefreshInterval);
    reportRefreshInterval = setInterval(loadLotReport, 5000);
}
function stopReportRefresh() {
    if (reportRefreshInterval) { clearInterval(reportRefreshInterval); reportRefreshInterval = null; }
}

async function clearLotReport() {
    const confirmed = await showConfirmDialog('Limpar Relatório', 'Limpar todo o relatório de lotes? Esta ação não pode ser desfeita.');
    if (!confirmed) return;
    try {
        await fetch(`${API}/api/rooms/${ROOM_ID}/lot-report`, { method: 'DELETE' });
        const container = document.getElementById('report-lots');
        if (container) container.innerHTML = '<div class="report-empty" style="color:var(--text-muted);font-size:0.8rem;text-align:center;padding:0.7rem;">Aguardando troca de lote</div>';
        const countEl = document.getElementById('report-count');
        if (countEl) countEl.textContent = '0';
        showToast('Relatorio limpo', 'success');
    } catch (e) {
        showToast('Erro ao limpar relatório', 'error');
    }
    // Limpar log de POST visual
    const logEl = document.getElementById('payload-log');
    if (logEl) logEl.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem; text-align: center; padding: 0.5rem;">Aguardando troca de lote</div>';
}

async function loadPostLog() {
    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/post-log`);
        const data = await res.json();
        const logEl = document.getElementById('payload-log');
        if (!logEl || !data.logs || data.logs.length === 0) return;

        logEl.innerHTML = '';
        for (const log of data.logs) {
            const time = log.timestamp ? new Date(log.timestamp).toLocaleTimeString('pt-BR') : '—';
            const entry = document.createElement('div');
            entry.style.cssText = 'padding:0.4rem;border-bottom:1px solid var(--border);font-size:0.75rem;font-family:monospace;';
            entry.innerHTML = `<span style="color:var(--text-muted)">${time}</span> Lote ${log.old_lot} → ${log.new_lot} <span style="color:var(--yellow)">(${log.bid_count} lances)</span>`;
            logEl.appendChild(entry);
        }
    } catch (e) {
        console.error('[POST-LOG] Erro ao carregar:', e);
    }
}

async function exportLotReport() {
    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/lot-report`);
        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `relatorio_lotes_${ROOM_ID}_${new Date().toISOString().slice(0,10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
    } catch(e) {
        showToast('Erro ao exportar', 'error');
    }
}

function updateConfBadge(elementId, conf) {
    const el = document.getElementById(elementId);
    if (!el || conf === undefined) return;

    el.textContent = `${conf}%`;

    // Color coding
    if (conf >= 80) {
        el.style.background = 'rgba(0, 214, 143, 0.2)';
        el.style.color = '#00d68f';
    } else if (conf >= 50) {
        el.style.background = 'rgba(255, 217, 61, 0.2)';
        el.style.color = '#ffd93d';
    } else {
        el.style.background = 'rgba(255, 86, 86, 0.2)';
        el.style.color = '#ff5656';
    }
}

function addLogEntry(data, timestamp) {
    const container = document.getElementById('log-container');

    // Remove empty state message
    if (container.querySelector('div[style]')) {
        container.innerHTML = '';
    }

    const time = new Date(timestamp).toLocaleTimeString('pt-BR');
    const parts = [];
    if (data.lote) parts.push(`L${data.lote}`);
    if (data.nome) parts.push(data.nome.substring(0, 30));
    if (data.valor) parts.push(`R$${data.valor}`);

    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `
        <span class="log-time">${time}</span>
        <span>${parts.join(' • ') || 'Sem dados'}</span>
    `;

    container.insertBefore(entry, container.firstChild);

    // Keep max 100 entries
    while (container.children.length > 100) {
        container.removeChild(container.lastChild);
    }
}

function clearLog() {
    document.getElementById('log-container').innerHTML = '<div style="color: var(--text-muted); font-size: 0.85rem; text-align: center; padding: 1rem;">Log limpo</div>';
}

// ============================================================
// Delete Room
// ============================================================
function deleteRoom() {
    document.getElementById('delete-modal').classList.add('active');
}

function closeDeleteModal() {
    document.getElementById('delete-modal').classList.remove('active');
}

async function confirmDeleteRoom() {
    const btn = document.getElementById('btn-confirm-delete');
    btn.textContent = 'Excluindo...';
    btn.disabled = true;

    try {
        await fetch(`${API}/api/rooms/${ROOM_ID}`, { method: 'DELETE' });
        showToast('Sala excluída', 'info');
        window.location.href = '/';
    } catch (err) {
        showToast('Erro ao excluir', 'error');
        btn.textContent = 'Excluir';
        btn.disabled = false;
        closeDeleteModal();
    }
}

// ============================================================
// Toast
// ============================================================
function showToast(msg, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = `toast ${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3000);
}

// ============================================================
// Debug Crops
// ============================================================
let debugInterval = null;

async function refreshDebugImages() {
    const container = document.getElementById('debug-crops');
    try {
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/debug`);
        const json = await res.json();
        const files = json.files || [];

        if (files.length === 0) {
            container.innerHTML = '<div style="color: var(--text-muted); font-size: 0.85rem; text-align: center; padding: 0.5rem;">Nenhum crop disponível</div>';
            return;
        }

        // Agrupar por tipo (lote, valor, nome)
        const groups = {};
        for (const f of files) {
            const type = f.split('_')[0]; // lote, valor, nome
            if (!groups[type]) groups[type] = [];
            groups[type].push(f);
        }

        const ts = Date.now();
        let html = '';
        for (const [type, typeFiles] of Object.entries(groups)) {
            const color = REGION_COLORS[type] || REGION_COLORS.lote;
            html += `<div style="margin-bottom: 0.5rem;">
                <div style="font-size: 0.7rem; font-weight: 700; text-transform: uppercase; color: ${color.text}; margin-bottom: 0.3rem;">${type}</div>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">`;
            for (const file of typeFiles) {
                const label = file.includes('raw') ? 'Raw' : file.includes('processed') ? 'Processado' : file.split('_').pop().replace('.png', '');
                html += `<div style="text-align: center;">
                    <img src="${API}/api/rooms/${ROOM_ID}/debug/${file}?t=${ts}" 
                         style="max-width: 150px; max-height: 80px; border-radius: 4px; border: 1px solid var(--border); background: #000;"
                         alt="${file}" title="${file}">
                    <div style="font-size: 0.6rem; color: var(--text-muted); margin-top: 2px;">${label}</div>
                </div>`;
            }
            html += '</div></div>';
        }
        container.innerHTML = html;
    } catch (e) {
        console.error('Debug refresh error:', e);
    }
}

function startDebugRefresh() {
    if (debugInterval) clearInterval(debugInterval);
    debugInterval = setInterval(refreshDebugImages, 3000);
    refreshDebugImages();
}

function stopDebugRefresh() {
    if (debugInterval) {
        clearInterval(debugInterval);
        debugInterval = null;
    }
}

// ============================================================
// Generic Confirm Dialog (replaces native confirm())
// ============================================================
function showConfirmDialog(title, message, confirmText = 'Confirmar') {
    return new Promise((resolve) => {
        const overlay = document.getElementById('confirm-dialog');
        document.getElementById('confirm-dialog-title').textContent = title;
        document.getElementById('confirm-dialog-message').textContent = message;

        const okBtn = document.getElementById('confirm-dialog-ok');
        const cancelBtn = document.getElementById('confirm-dialog-cancel');

        // Update confirm button text
        okBtn.innerHTML = `<i data-lucide="check" style="width:14px;height:14px;"></i> ${confirmText}`;
        if (window.lucide) lucide.createIcons();

        function cleanup() {
            overlay.classList.remove('active');
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            overlay.removeEventListener('click', onOverlay);
        }

        function onOk() { cleanup(); resolve(true); }
        function onCancel() { cleanup(); resolve(false); }
        function onOverlay(e) { if (e.target === overlay) { cleanup(); resolve(false); } }

        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
        overlay.addEventListener('click', onOverlay);

        overlay.classList.add('active');
    });
}

// ============================================================
// Region Templates
// ============================================================
async function loadTemplates() {
    try {
        const res = await fetch(`${API}/api/templates`);
        const templates = await res.json();
        const select = document.getElementById('template-select');
        if (!select) return;

        select.innerHTML = '<option value="">Selecionar template...</option>';
        templates.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.textContent = `${t.name}  ·  ${t.regions.length} regiões`;
            select.appendChild(opt);
        });

        // Hide delete button when no template selected
        updateDeleteTemplateButton();
    } catch (err) {
        console.error('Erro ao carregar templates:', err);
    }
}

function updateDeleteTemplateButton() {
    const select = document.getElementById('template-select');
    const btn = document.getElementById('btn-delete-template');
    if (!btn) return;
    btn.style.display = (select && select.value) ? 'flex' : 'none';
    if (window.lucide) lucide.createIcons();
}

function openSaveTemplateModal() {
    if (regions.length === 0) {
        showToast('Defina pelo menos uma região antes de salvar como template', 'error');
        return;
    }
    const modal = document.getElementById('save-template-modal');
    const input = document.getElementById('template-name-input');
    input.value = '';
    input.style.borderColor = '';
    modal.classList.add('active');
    if (window.lucide) lucide.createIcons();
    setTimeout(() => input.focus(), 100);

    input.onkeydown = (e) => {
        if (e.key === 'Enter') confirmSaveTemplate();
    };
}

function closeSaveTemplateModal() {
    document.getElementById('save-template-modal').classList.remove('active');
}

async function confirmSaveTemplate() {
    const input = document.getElementById('template-name-input');
    const name = input.value.trim();
    if (!name) {
        input.style.borderColor = 'var(--red)';
        input.focus();
        return;
    }

    closeSaveTemplateModal();

    try {
        const res = await fetch(`${API}/api/templates`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                regions: regions.map(r => ({
                    type: r.type,
                    label: r.label,
                    value: r.value,
                    x: r.x,
                    y: r.y,
                    width: r.width,
                    height: r.height
                }))
            })
        });

        if (res.ok) {
            showToast(`Template "${name}" salvo com sucesso`, 'success');
            loadTemplates();
        } else {
            showToast('Erro ao salvar template', 'error');
        }
    } catch (err) {
        showToast('Erro ao salvar template', 'error');
    }
}

async function applyTemplate() {
    const select = document.getElementById('template-select');
    const val = select.value;

    updateDeleteTemplateButton();

    if (!val) return;

    try {
        const wasRunning = isRunning;

        // Stop OCR if running
        if (wasRunning) {
            await fetch(`${API}/api/rooms/${ROOM_ID}/stop`, { method: 'POST' });
            isRunning = false;
        }

        // Apply template
        const res = await fetch(`${API}/api/rooms/${ROOM_ID}/apply-template`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ template_id: val })
        });

        if (res.ok) {
            const data = await res.json();
            await loadRoom();
            drawRegions();
            showToast(`Template "${data.template_name}" aplicado com ${data.count} regiões`, 'success');

            // Auto-restart OCR if it was running
            if (wasRunning) {
                const startRes = await fetch(`${API}/api/rooms/${ROOM_ID}/start`, { method: 'POST' });
                if (startRes.ok) {
                    isRunning = true;
                    showToast('OCR reiniciado automaticamente', 'info');
                }
            }
        } else {
            showToast('Erro ao aplicar template', 'error');
        }
    } catch (err) {
        showToast('Erro ao aplicar template', 'error');
    }
}

async function deleteSelectedTemplate() {
    const select = document.getElementById('template-select');
    const templateId = select.value;
    if (!templateId) {
        showToast('Selecione um template primeiro', 'error');
        return;
    }

    const templateName = select.options[select.selectedIndex]?.textContent?.split('  ·  ')[0] || 'template';
    const ok = await confirmDialog('Excluir Template', `Tem certeza que deseja excluir o template "${templateName}"? Esta ação é irreversível.`);
    if (!ok) return;

    try {
        await fetch(`${API}/api/templates/${templateId}`, { method: 'DELETE' });
        showToast(`Template "${templateName}" excluído`, 'success');
        loadTemplates();
    } catch (err) {
        showToast('Erro ao excluir template', 'error');
    }
}

// ============================================================
// Start
// ============================================================
init();
