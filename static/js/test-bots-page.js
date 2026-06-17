const esc = UI.esc.bind(UI);
const toast = UI.toast.bind(UI);
let bots = [];

async function loadBots() {
  const el = document.getElementById('botList');
  el.innerHTML = UI.spinner('Đang tải danh sách bot...');
  const res = await RagbotAPI.get('/bots');
  if (!res.ok) { el.innerHTML = UI.empty('Lỗi tải danh sách bot', res.error); return; }
  bots = res.data.data;
  renderBots();
  updateStats();
}

function updateStats() {
  document.getElementById('statBots').textContent = bots.length;
  document.getElementById('statDocs').textContent = bots.reduce((s, b) => s + (b.doc_count || 0), 0);
  document.getElementById('statChunks').textContent = bots.reduce((s, b) => s + (b.chunk_count || 0), 0);
}

function renderBots() {
  const el = document.getElementById('botList');
  if (bots.length === 0) { el.innerHTML = UI.empty('Chưa có bot nào', 'Tạo bot đầu tiên để bắt đầu test'); return; }
  // Use data-attributes to avoid XSS in onclick
  el.innerHTML = '<div class="bot-grid">' + bots.map((b, i) => `
    <div class="bot-card" data-idx="${i}">
      <button class="delete-btn" data-del="${i}" title="Xóa bot">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
      </button>
      <div class="card-header">
        <div class="bot-icon">${b.channel_type === 'zalo' ? 'Z' : b.channel_type === 'messenger' ? 'M' : b.channel_type === 'api' ? 'A' : 'W'}</div>
        <div class="bot-info">
          <div class="bot-name">${esc(b.bot_name)}</div>
          <div class="bot-id">${esc(b.bot_id)} / ${esc(b.channel_type)}</div>
        </div>
      </div>
      <div class="card-body">
        <div class="prompt-preview">${esc(b.system_prompt || 'Chưa có system prompt')}</div>
      </div>
      <div class="card-footer">
        <div class="card-stat"><span class="num">${b.doc_count}</span> tài liệu</div>
        <div class="card-stat"><span class="num">${b.chunk_count}</span> chunks</div>
        <div class="card-stat">${UI.formatDateShort(b.created_at)}</div>
      </div>
    </div>
  `).join('') + '</div>';

}

function showCreateModal() { document.getElementById('createModal').classList.add('active'); document.getElementById('fBotName').focus(); }
function hideModal() { document.getElementById('createModal').classList.remove('active'); }

async function createBot() {
  const name = document.getElementById('fBotName').value.trim();
  const botId = document.getElementById('fBotId').value.trim();
  if (!name || !botId) { toast('Vui lòng điền Tên Bot và Bot ID', 'error'); return; }

  const btn = document.getElementById('createBtn');
  UI.btnLoading(btn, true, 'Đang tạo...');

  // Temperature + max_tokens dropped from create form — server picks
  // from system_config (llm_default_temperature / llm_default_max_tokens).
  // Tenant ID hard-defaulted to 32 via hidden input (test workspace).
  // bypass_token_limit=true sent inline; server validates tenant-level
  // and rolls back the create on permission error.
  const body = {
    bot_name: name, bot_id: botId,
    channel_type: document.getElementById('fChannel').value,
    system_prompt: document.getElementById('fPrompt').value.trim(),
    bypass_token_limit: true,
  };
  const tid = document.getElementById('fTenantId').value.trim();
  if (tid) body.tenant_id = parseInt(tid);

  const res = await RagbotAPI.post('/bots', body);
  UI.btnLoading(btn, false, 'Tạo Bot');
  if (res.ok) {
    toast('Đã tạo bot (bypass token đã bật)!', 'success');
    hideModal();
    loadBots();
  } else {
    toast(res.error, 'error');
  }
}

async function deleteBot(botUuid, botName) {
  if (!confirm(`Xóa "${botName}"? Tất cả tài liệu và chunks sẽ bị xóa.`)) return;
  const res = await RagbotAPI.del('/bots/' + botUuid);
  if (res.ok) { toast('Đã xóa bot', 'success'); loadBots(); }
  else toast(res.error, 'error');
}

// Slugify bot_id (client-side mirror of backend _slugify_bot_id):
//   "thông tư - 09/2020/TT-NHNN" → "thong-tu-09-2020-tt-nhnn"
//   "Bot Name 2024!" → "bot-name-2024"
function slugifyBotId(raw) {
  if (!raw) return '';
  // 1+2: lowercase + strip Vietnamese diacritics (NFD → drop combining marks)
  let s = raw.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  // VN special: đ → d
  s = s.replace(/đ/g, 'd');
  // 3: non-alphanumeric → dash
  s = s.replace(/[^a-z0-9]+/g, '-');
  // 4+5: collapse + strip dashes
  return s.replace(/-+/g, '-').replace(/^-+|-+$/g, '');
}

// Auto-convert bot_id on blur (preserve raw input during typing for UX,
// then slugify when user moves focus away — they see the final URL-safe value).
document.getElementById('fBotId').addEventListener('blur', (e) => {
  const raw = e.target.value.trim();
  if (!raw) return;
  const slugged = slugifyBotId(raw);
  if (slugged !== raw) {
    e.target.value = slugged;
    document.getElementById('fBotIdHint').innerHTML =
      `✓ Đã chuẩn hóa: <strong>${slugged}</strong> (từ "${raw}")`;
    document.getElementById('fBotIdHint').style.color = 'var(--success, #047857)';
  } else {
    document.getElementById('fBotIdHint').textContent =
      'Tự convert sang slug khi blur (dấu cách → "-", bỏ dấu tiếng Việt, lowercase).';
    document.getElementById('fBotIdHint').style.color = '';
  }
});

// Event listeners — no inline onclick
document.getElementById('showCreateBtn').addEventListener('click', showCreateModal);
document.getElementById('modalCloseBtn').addEventListener('click', hideModal);
document.getElementById('modalCancelBtn').addEventListener('click', hideModal);
document.getElementById('createBtn').addEventListener('click', createBot);
document.getElementById('createModal').addEventListener('click', e => { if (e.target.classList.contains('modal-overlay')) hideModal(); });

// Bot list event delegation — register ONCE, not per render
document.getElementById('botList').addEventListener('click', e => {
  const del = e.target.closest('[data-del]');
  if (del) { e.stopPropagation(); const b = bots[del.dataset.del]; deleteBot(b.id, b.bot_name); return; }
  const card = e.target.closest('[data-idx]');
  if (card) { const b = bots[card.dataset.idx]; window.location.href = '/demo-ragbot/bot/' + encodeURIComponent(b.bot_id) + '/' + encodeURIComponent(b.channel_type); }
});

loadBots();
