// === HELPERS — delegate to shared UI ===
const esc = UI.esc.bind(UI);
const toast = UI.toast.bind(UI);

// === Parse bot_id and channel from URL ===
const pathParts = window.location.pathname.split('/');
const BOT_ID = pathParts[3] || '';
const CHANNEL = pathParts[4] || 'web';
const AUDIT_PAGE_SIZE = 20;

let botConfig = null;
let documents = [];
let linkValid = false;
let validateTimer = null;
let auditNextCursor = null;

// === EVENT DELEGATION — no inline onclick ===
document.getElementById('backBtn').addEventListener('click', () => { location.href = '/demo-ragbot'; });
document.getElementById('clearChatBtn').addEventListener('click', clearChat);
document.getElementById('sendBtn').addEventListener('click', sendMsg);
document.getElementById('msgInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) sendMsg();
});
document.getElementById('tabBar').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (tab) switchTab(tab.dataset.tab);
});
document.getElementById('addDocBtn').addEventListener('click', toggleAddDocForm);
document.getElementById('addLinkBtn').addEventListener('click', toggleAddLinkForm);
document.getElementById('addUploadBtn').addEventListener('click', toggleAddUploadForm);
document.getElementById('cancelDocBtn').addEventListener('click', toggleAddDocForm);
document.getElementById('cancelLinkBtn').addEventListener('click', toggleAddLinkForm);
document.getElementById('cancelUploadBtn').addEventListener('click', toggleAddUploadForm);
document.getElementById('submitDocBtn').addEventListener('click', addDocument);
document.getElementById('submitUploadBtn').addEventListener('click', uploadDocumentFile);
document.getElementById('addLinkDocBtn').addEventListener('click', addLinkDocument);
document.getElementById('linkUrl').addEventListener('input', debouncedValidateLink);
document.getElementById('auditQueryBtn').addEventListener('click', () => loadAudit());
document.getElementById('auditClearBtn').addEventListener('click', clearAuditFilter);

// Event delegation for doc delete buttons + audit "Load more"
document.addEventListener('click', (e) => {
  const delBtn = e.target.closest('[data-delete-doc]');
  if (delBtn) {
    deleteDoc(delBtn.dataset.deleteDoc, delBtn.dataset.docName);
    return;
  }
  const viewChunksBtn = e.target.closest('[data-view-chunks]');
  if (viewChunksBtn) {
    toggleDocChunks(viewChunksBtn.dataset.viewChunks);
    return;
  }
  // Đóng chunks panel — quay lại danh sách doc
  const chunksCloseBtn = e.target.closest('[data-chunks-close]');
  if (chunksCloseBtn) {
    closeChunksPanel();
    return;
  }
  // Filter buttons — reset loadedCount + re-render
  const filterBtn = e.target.closest('[data-chunks-filter]');
  if (filterBtn) {
    const docId = filterBtn.dataset.doc;
    const panel = document.getElementById(`chunks-panel-${docId}`);
    if (!panel) return;
    panel._filter = filterBtn.dataset.chunksFilter;
    panel._loadedCount = 30; // reset khi đổi filter
    panel.querySelectorAll('.chunks-filter-btn').forEach(b => b.classList.remove('active'));
    filterBtn.classList.add('active');
    renderChunkList(docId);
    return;
  }
  // Load more — tăng loadedCount
  const loadMoreBtnChunks = e.target.closest('[data-load-more]');
  if (loadMoreBtnChunks) {
    const docId = loadMoreBtnChunks.dataset.loadMore;
    const panel = document.getElementById(`chunks-panel-${docId}`);
    if (panel) {
      panel._loadedCount += (panel._loadStep || 30);
      renderChunkList(docId);
    }
    return;
  }
  const loadMoreBtn = e.target.closest('#auditLoadMore');
  if (loadMoreBtn) {
    loadAuditPage();
  }
});

// Search box debounced (300ms)
document.addEventListener('input', (e) => {
  const searchEl = e.target.closest('[data-chunks-search]');
  if (searchEl) {
    const docId = searchEl.dataset.chunksSearch;
    const panel = document.getElementById(`chunks-panel-${docId}`);
    if (!panel) return;
    clearTimeout(panel._searchDebounce);
    panel._searchDebounce = setTimeout(() => {
      panel._search = searchEl.value.trim();
      panel._loadedCount = 30; // reset khi search
      renderChunkList(docId);
    }, 300);
  }
});

// Config panel — delegated (rendered dynamically)
document.getElementById('configContent').addEventListener('click', (e) => {
  if (e.target.closest('#saveConfigBtn')) saveConfig();
  if (e.target.closest('#refreshEventsBtn')) loadBotEvents();
});

// Tab switch → reload bot events khi switch sang config
document.getElementById('tabBar').addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]');
  if (tab && tab.dataset.tab === 'config' && botConfig) {
    // Auto-refresh events panel khi user mở tab Cấu hình
    setTimeout(() => loadBotEvents(), 100);
  }
});

// === INIT ===
async function init() {
  document.getElementById('botMeta').textContent = BOT_ID + ' / ' + CHANNEL;
  // botConfig MUST load first — DELETE /chat needs tenant_id from it.
  await loadBotInfo();
  await RagbotAPI.del('/chat', { bot_id: BOT_ID, channel_type: CHANNEL, tenant_id: (botConfig && botConfig.tenant_id) || undefined });
  addMsg('system', 'Bắt đầu cuộc trò chuyện mới');
  loadDocuments();
}

async function loadBotInfo() {
  const res = await RagbotAPI.get('/bots');
  if (res.ok) {
    botConfig = res.data.data.find(b => b.bot_id === BOT_ID && b.channel_type === CHANNEL);
    if (botConfig) {
      document.getElementById('botTitle').textContent = botConfig.bot_name;
      document.title = 'RAGbot - ' + botConfig.bot_name;
      renderConfig();
    }
  }
}

// === TABS ===
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + tab));
}

// === CHAT ===
function addMsg(role, content, meta) {
  const area = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : role === 'system' ? 'system' : 'bot');
  div.innerHTML = role === 'user' ? esc(content) : UI.formatMd(content || '');
  if (meta && role === 'bot') {
    const m = document.createElement('div');
    m.className = 'meta';
    const d = meta.debug || {};
    m.innerHTML = `<span>Chunks: ${meta.chunks_used}</span><span>Score: ${(d.score_avg||meta.top_score||0).toFixed(3)} avg (${(d.score_min||0).toFixed(3)}~${(d.score_max||0).toFixed(3)})</span><span>History: ${d.history_messages||0} msgs</span><span>Tokens: ${meta.tokens.prompt} in / ${meta.tokens.completion} out</span><span>Cost: $${(meta.cost_usd||0).toFixed(6)}</span><span>${meta.duration_ms||0}ms</span>`;
    div.appendChild(m);

    // Build chunk_id → full content map (từ debug=full payload)
    const fullMap = {};
    (meta.retrieved_chunks_content || []).forEach(function(c) {
      if (c && c.chunk_id) fullMap[String(c.chunk_id)] = c.content || '';
    });

    // ── Block 1: Chunks LLM THỰC SỰ TRÍCH DẪN (citations) — highlight vàng ──
    const cits = meta.citations || [];
    const cited = document.createElement('div');
    cited.className = 'cited';
    if (cits.length > 0) {
      cited.innerHTML = '<b>📌 Chunks được trích dẫn (' + cits.length + '):</b>';
      cits.forEach(function(cit, i) {
        const item = document.createElement('div');
        item.className = 'cited-item';
        const cid = String(cit.chunk_id || '');
        const score = (cit.score || 0).toFixed(3);
        const docName = esc(cit.document_name || '(không tên)');
        const quote = esc(cit.quote || '');
        const full = esc(fullMap[cid] || '');
        item.innerHTML = '<span class="cited-badge">#' + (i+1) + '</span> '
          + '<span class="cited-id">' + esc(cid.substring(0, 8)) + '</span> '
          + '<b>' + docName + '</b> '
          + '<span style="color:#92400e;font-size:11px">score: ' + score + '</span>'
          + (quote ? '<div class="cited-quote">' + quote + '</div>' : '')
          + (full ? '<div class="cited-full">' + full + '</div>' : '');
        item.addEventListener('click', function() {
          item.classList.toggle('expanded');
        });
        cited.appendChild(item);
      });
    } else {
      cited.innerHTML = '<b>📌 Chunks được trích dẫn:</b> <span class="cited-empty">LLM không cite chunk nào (free-form answer, có thể là refuse hoặc chitchat)</span>';
    }
    div.appendChild(cited);

    // ── Block 2: All sources LLM xem (graded_chunks) — COLLAPSED mặc định,
    // click header mới mở (giữ chat ngắn; chỉ thông số + cited chunk hiện sẵn).
    if (meta.sources && meta.sources.length > 0) {
      const s = document.createElement('div');
      s.className = 'sources';
      const hdr = document.createElement('div');
      hdr.className = 'sources-toggle-header';
      hdr.style.cssText = 'cursor:pointer;user-select:none;font-weight:600;color:#475569';
      const lbl = function(open) {
        return (open ? '▾' : '▸') + ' 📚 Tất cả chunks LLM xem (' + meta.sources.length + ')'
          + (open ? '' : ' — click để xem detail');
      };
      hdr.innerHTML = lbl(false);
      const list = document.createElement('div');
      list.className = 'sources-list';
      list.style.display = 'none';
      hdr.addEventListener('click', function() {
        const open = list.style.display === 'none';
        list.style.display = open ? 'block' : 'none';
        hdr.innerHTML = lbl(open);
      });
      s.appendChild(hdr);
      meta.sources.forEach(function(src, i) {
        const p = document.createElement('div');
        p.className = 'source-item';
        const name = esc(src.document_name || '(không tên)');
        const score = (src.score || 0).toFixed(3);
        const preview = esc(src.preview || '');
        // Mark item nếu nằm trong citations (LLM thực sự dùng)
        const isCited = cits.some(function(c){ return c.chunk_id && src.chunk_id_str && String(c.chunk_id) === String(src.chunk_id_str); });
        p.innerHTML = '<span class="source-badge">#' + (i+1) + '</span> '
          + '<b>' + name + '</b> (đoạn ' + src.chunk_index + ', score: ' + score + ')'
          + (isCited ? ' <span style="color:#ca8a04;font-weight:600;font-size:10px">★ CITED</span>' : '')
          + '<div class="source-preview">' + preview + (src.preview && src.preview.length >= 200 ? '...' : '') + '</div>'
          + '<span class="source-toggle">▼ Click để xem full chunk</span>';
        // Click expand full content nếu có (cần debug=full)
        p.addEventListener('click', function() {
          const prev = p.querySelector('.source-preview');
          const toggle = p.querySelector('.source-toggle');
          if (!prev) return;
          if (prev.classList.contains('expanded')) {
            prev.classList.remove('expanded');
            prev.innerHTML = preview + (src.preview && src.preview.length >= 200 ? '...' : '');
            if (toggle) toggle.textContent = '▼ Click để xem full chunk';
          } else {
            // Tìm full content trong fullMap theo chunk_index (fallback dùng preview nếu không có)
            // Note: source không trả chunk_id nên match theo position trong graded_chunks
            const fullByIdx = (meta.retrieved_chunks_content || [])[i];
            const fullContent = (fullByIdx && fullByIdx.content) ? fullByIdx.content : (src.preview || '');
            prev.classList.add('expanded');
            prev.textContent = fullContent;
            if (toggle) toggle.textContent = '▲ Thu gọn';
          }
        });
        list.appendChild(p);
      });
      s.appendChild(list);
      div.appendChild(s);
    }
  }
  area.appendChild(div);
  area.scrollTop = area.scrollHeight;
}

function showTyping() {
  const area = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'typing-indicator'; div.id = 'typing';
  div.innerHTML = '<span></span><span></span><span></span>';
  area.appendChild(div);
  area.scrollTop = area.scrollHeight;
}
function hideTyping() { const el = document.getElementById('typing'); if (el) el.remove(); }

async function sendMsg() {
  const input = document.getElementById('msgInput');
  const q = input.value.trim();
  if (!q) return;
  input.value = '';
  document.getElementById('sendBtn').disabled = true;

  addMsg('user', q);
  showTyping();

  const res = await RagbotAPI.post('/chat', { bot_id: BOT_ID, channel_type: CHANNEL, tenant_id: (botConfig && botConfig.tenant_id) || undefined, question: q, debug: 'full' });
  hideTyping();
  if (res.ok) {
    addMsg('bot', res.data.answer, res.data);
  } else if (res.data && res.data.blocked && res.data.answer) {
    // Soft-block (e.g. documents_not_ready) — backend returns HTTP 200
    // với ok=false + blocked=true + answer. Render answer như bot reply
    // để user thấy lý do bot không trả lời.
    addMsg('bot', res.data.answer, res.data);
    // Nếu block vì docs chưa sẵn sàng, kick off doc poll
    if (res.data.blocked_reason === 'documents_not_ready') {
      loadDocuments();
    }
  } else {
    addMsg('system', 'Error: ' + (res.error || 'Unknown'));
  }
  document.getElementById('sendBtn').disabled = false;
  input.focus();
}

async function clearChat() {
  if (!confirm('Xóa sạch lịch sử chat + dữ liệu thống kê?')) return;
  const res = await RagbotAPI.del('/chat', { bot_id: BOT_ID, channel_type: CHANNEL, tenant_id: (botConfig && botConfig.tenant_id) || undefined });
  document.getElementById('chatMessages').innerHTML = '';
  const d = res.data || {};
  addMsg('system', `Đã xóa ${d.deleted_messages || 0} tin nhắn và ${d.deleted_logs || 0} bản ghi thống kê`);
  // Clear audit panel
  auditNextCursor = null;
  document.getElementById('auditContent').innerHTML = UI.empty('Chọn khoảng thời gian và bấm "Truy vấn" để xem thống kê.');
}

// === DOCUMENTS ===
function toggleAddDocForm() {
  document.getElementById('addDocForm').classList.toggle('active');
  document.getElementById('addLinkForm').classList.remove('active');
  document.getElementById('addUploadForm').classList.remove('active');
}
function toggleAddLinkForm() {
  document.getElementById('addLinkForm').classList.toggle('active');
  document.getElementById('addDocForm').classList.remove('active');
  document.getElementById('addUploadForm').classList.remove('active');
}
function toggleAddUploadForm() {
  document.getElementById('addUploadForm').classList.toggle('active');
  document.getElementById('addDocForm').classList.remove('active');
  document.getElementById('addLinkForm').classList.remove('active');
}

async function uploadDocumentFile() {
  const title = document.getElementById('uploadDocTitle').value.trim();
  const fileInput = document.getElementById('uploadDocFile');
  const file = fileInput.files[0];
  if (!title) { toast('Vui lòng điền tên tài liệu', 'error'); return; }
  if (!file) { toast('Vui lòng chọn file PDF', 'error'); return; }

  const btn = document.getElementById('submitUploadBtn');
  UI.btnLoading(btn, true, 'Đang tải lên...');
  const fd = new FormData();
  fd.append('title', title);
  fd.append('file', file);
  const _tid_q = botConfig && botConfig.tenant_id ? '?tenant_id=' + botConfig.tenant_id : '';
  const res = await RagbotAPI.post('/bots/' + BOT_ID + '/' + CHANNEL + '/documents/upload' + _tid_q, fd);
  UI.btnLoading(btn, false, 'Tải lên & Xử lý');
  if (res.ok) {
    toast(`Đã upload ${res.data.filename}: ${res.data.chunks} chunks`, 'success');
    document.getElementById('uploadDocTitle').value = '';
    fileInput.value = '';
    toggleAddUploadForm();
    loadDocuments();
  } else {
    toast(res.error || 'Upload thất bại', 'error');
  }
}

async function loadDocuments() {
  // Re-render config khi documents change để doc link list cập nhật
  const _afterLoad = () => { if (botConfig) renderConfig(); };

  const el = document.getElementById('docList');
  el.innerHTML = UI.spinner('Đang tải tài liệu...');
  const _tid = botConfig && botConfig.tenant_id ? { tenant_id: botConfig.tenant_id } : {};
  const res = await RagbotAPI.get('/bots/' + BOT_ID + '/' + CHANNEL + '/documents', _tid);
  if (res.ok) {
    documents = res.data.documents;
    document.getElementById('docBadge').textContent = documents.length;
    renderDocuments();
    _afterLoad();
  } else {
    el.innerHTML = UI.empty('Không thể tải tài liệu', res.error);
  }
}

function renderDocuments() {
  const el = document.getElementById('docList');
  if (documents.length === 0) {
    el.innerHTML = '<div class="empty">Chưa có tài liệu. Thêm tài liệu để bắt đầu test RAG.</div>';
    return;
  }
  const STATUS_BADGE = {
    ready:      { label: 'Sẵn sàng',         cls: 'badge-success' },
    preparing:  { label: '⏳ Đang chuẩn bị',  cls: 'badge-warning' },
    processing: { label: '⚙ Đang xử lý',      cls: 'badge-info' },
    failed:     { label: '✗ Thất bại',        cls: 'badge-danger' },
  };
  const STEP_LABEL = {
    chunking:  'Đang chia chunk...',
    enriching: 'Đang enrich (Haiku)...',
    embedding: 'Đang tạo vector...',
    indexing:  'Đang index DB...',
    active:    'Hoàn tất',
    failed:    'Lỗi',
  };
  const fmtEta = (s) => {
    if (s == null || s <= 0) return '';
    if (s < 60) return `~${s}s`;
    if (s < 3600) return `~${Math.round(s/60)} phút`;
    return `~${(s/3600).toFixed(1)} giờ`;
  };
  el.innerHTML = '<div class="doc-list">' + documents.map(d => {
    const isHttp = d.source_url && (d.source_url.startsWith('https://') || d.source_url.startsWith('http://'));
    const urlLine = isHttp
      ? `<p class="doc-url"><a href="${esc(d.source_url)}" target="_blank" rel="noopener" class="primary-link" title="${esc(d.source_url)}">${esc(d.source_url)}</a></p>`
      : (d.source_url ? `<p class="doc-url doc-url-muted" title="${esc(d.source_url)}">${esc(d.source_url)}</p>` : '');
    const created = d.created_at ? new Date(d.created_at).toLocaleString('vi') : '-';
    const badge = STATUS_BADGE[d.status] || { label: esc(d.status || d.state || 'unknown'), cls: 'badge-muted' };
    const sizeLine = d.content_chars ? ` &middot; ${d.content_chars.toLocaleString('vi')} chars` : '';

    // Progress bar — render khi đang xử lý + có progress_percent
    let progressBar = '';
    const inFlight = d.status === 'preparing' || d.status === 'processing';
    if (inFlight && d.progress_percent != null) {
      const pct = Math.max(0, Math.min(100, d.progress_percent));
      const stepLbl = STEP_LABEL[d.current_step] || (d.current_step || '');
      const etaTxt = fmtEta(d.eta_seconds);
      const chunkLine = (d.chunks_total && d.chunks_processed != null)
        ? `${d.chunks_processed.toLocaleString('vi')}/${d.chunks_total.toLocaleString('vi')} chunks`
        : '';
      const detailLine = [stepLbl, chunkLine, etaTxt && `còn ${etaTxt}`].filter(Boolean).join(' · ');
      progressBar = `
        <div class="doc-progress">
          <div class="doc-progress-bar"><div class="doc-progress-fill" style="width:${pct}%"></div></div>
          <p class="doc-progress-text">${pct}% · ${esc(detailLine)}</p>
        </div>`;
    } else if (inFlight) {
      // Fallback: chưa có progress_percent → indicator spinner
      progressBar = `<div class="doc-progress"><p class="doc-progress-text">⏳ Worker đang pickup...</p></div>`;
    }

    const canViewChunks = d.chunk_count > 0 && d.status === 'ready';
    return `
    <div class="doc-item" data-doc-id="${esc(d.id)}" data-doc-status="${esc(d.status)}">
      <div class="doc-item-row">
        <div class="doc-info">
          <h4>${esc(d.document_name)} <span class="badge ${badge.cls}">${badge.label}</span></h4>
          ${urlLine}
          <p class="doc-meta">${d.chunk_count} chunks${sizeLine} &middot; ${created}</p>
          ${progressBar}
        </div>
        <div class="doc-actions-right">
          ${canViewChunks ? `
            <button class="btn btn-ghost btn-sm" data-view-chunks="${esc(d.id)}" title="Xem ${d.chunk_count} chunks">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
              Chunks
            </button>
          ` : ''}
          <button class="btn btn-danger btn-sm" data-delete-doc="${esc(d.id)}" data-doc-name="${esc(d.document_name)}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            Xóa
          </button>
        </div>
      </div>
    </div>`;
  }).join('') + '</div>';

  // Auto-refresh while any doc is still preparing/processing
  const inFlightAny = documents.some(d => d.status === 'preparing' || d.status === 'processing');
  if (inFlightAny) {
    clearTimeout(window._docPollTimer);
    window._docPollTimer = setTimeout(() => loadDocuments(), 3000);
  }
}

async function addDocument() {
  const title = document.getElementById('docTitle').value.trim();
  const content = document.getElementById('docContent').value.trim();
  if (!title || !content) { toast('Vui lòng điền tiêu đề và nội dung', 'error'); return; }

  const btn = document.getElementById('submitDocBtn');
  UI.btnLoading(btn, true, 'Đang thêm...');
  const _tid_q = botConfig && botConfig.tenant_id ? '?tenant_id=' + botConfig.tenant_id : '';
  const res = await RagbotAPI.post('/bots/' + BOT_ID + '/' + CHANNEL + '/documents' + _tid_q, { title, content, source_type: 'manual' });
  UI.btnLoading(btn, false, 'Thêm tài liệu');
  if (res.ok) {
    toast(`Đã thêm tài liệu: ${res.data.chunks} chunks`, 'success');
    document.getElementById('docTitle').value = '';
    document.getElementById('docContent').value = '';
    toggleAddDocForm();
    loadDocuments();
  } else {
    toast(res.error, 'error');
  }
}

// === LINK VALIDATION ===
function debouncedValidateLink() {
  clearTimeout(validateTimer);
  const url = document.getElementById('linkUrl').value.trim();
  if (!url) {
    document.getElementById('linkValidation').innerHTML = '';
    document.getElementById('addLinkDocBtn').disabled = true;
    linkValid = false;
    return;
  }
  document.getElementById('linkValidation').innerHTML = '<div class="link-status checking"><div class="spinner spinner-xs"></div> Validating...</div>';
  validateTimer = setTimeout(() => validateLink(url), 600);
}

async function validateLink(url) {
  const res = await RagbotAPI.post('/validate-link', { url });
  const el = document.getElementById('linkValidation');
  if (res.ok && res.data.ok) {
    el.innerHTML = `<div class="link-status valid">Valid ${esc(res.data.type)} (${esc(res.data.access)})</div>`;
    linkValid = true;
    document.getElementById('addLinkDocBtn').disabled = false;
  } else {
    el.innerHTML = `<div class="link-status invalid">${esc(res.data?.error || res.error)}</div>`;
    linkValid = false;
    document.getElementById('addLinkDocBtn').disabled = true;
  }
}

async function addLinkDocument() {
  const title = document.getElementById('linkDocTitle').value.trim();
  const url = document.getElementById('linkUrl').value.trim();
  if (!title || !url) { toast('Vui lòng điền tiêu đề và URL', 'error'); return; }

  const btn = document.getElementById('addLinkDocBtn');
  UI.btnLoading(btn, true, 'Đang tải & xử lý...');
  const _tid_q2 = botConfig && botConfig.tenant_id ? '?tenant_id=' + botConfig.tenant_id : '';
  const res = await RagbotAPI.post('/bots/' + BOT_ID + '/' + CHANNEL + '/documents' + _tid_q2, { title, url, source_type: 'google_link' });
  UI.btnLoading(btn, false, 'Xác thực & Thêm');
  if (res.ok) {
    toast(`Đã thêm tài liệu từ link: ${res.data.chunks} chunks`, 'success');
    document.getElementById('linkDocTitle').value = '';
    document.getElementById('linkUrl').value = '';
    document.getElementById('linkValidation').innerHTML = '';
    linkValid = false;
    toggleAddLinkForm();
    loadDocuments();
  } else {
    toast(res.error, 'error');
  }
}

async function deleteDoc(docId, docName) {
  if (!confirm(`Xóa tài liệu "${docName}"?`)) return;
  const res = await RagbotAPI.del('/documents/' + docId);
  if (res.ok) { toast('Đã xóa tài liệu', 'success'); loadDocuments(); }
  else toast(res.error, 'error');
}

// === AUDIT — temp table snapshot, keyset pagination ===
async function loadAudit() {
  const el = document.getElementById('auditContent');
  const btn = document.getElementById('auditQueryBtn');
  el.innerHTML = UI.spinner('Đang truy vấn...');
  UI.btnLoading(btn, true, 'Đang truy vấn...');

  const params = { page_size: AUDIT_PAGE_SIZE };
  if (botConfig && botConfig.tenant_id) params.tenant_id = botConfig.tenant_id;
  const df = document.getElementById('auditFrom').value;
  const dt = document.getElementById('auditTo').value;
  if (df) params.date_from = df;
  if (dt) params.date_to = dt;

  const res = await RagbotAPI.get('/bots/' + BOT_ID + '/' + CHANNEL + '/audit', params);
  UI.btnLoading(btn, false, 'Truy vấn');

  if (!res.ok) {
    el.innerHTML = UI.empty('Không thể tải dữ liệu audit', res.error);
    return;
  }
  auditNextCursor = res.data.next_cursor || null;
  renderAudit(res.data);
}

async function loadAuditPage() {
  if (!auditNextCursor) return;
  const btn = document.getElementById('auditLoadMore');
  if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }

  const params = { page_size: AUDIT_PAGE_SIZE, cursor: auditNextCursor };
  if (botConfig && botConfig.tenant_id) params.tenant_id = botConfig.tenant_id;
  const df = document.getElementById('auditFrom').value;
  const dt = document.getElementById('auditTo').value;
  if (df) params.date_from = df;
  if (dt) params.date_to = dt;

  const res = await RagbotAPI.get('/bots/' + BOT_ID + '/' + CHANNEL + '/audit', params);
  if (!res.ok) {
    toast('Không thể tải thêm', 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Tải thêm'; }
    return;
  }

  auditNextCursor = res.data.next_cursor || null;

  // Append new rows to existing table
  const tbody = document.querySelector('.req-table tbody');
  if (tbody && res.data.requests) {
    const E = UI.esc;
    for (const r of res.data.requests) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="status-badge ${E(r.status)}">${E(r.status)}</span></td>
        <td>${r.duration_ms}ms</td>
        <td>${r.prompt_tokens} / ${r.completion_tokens}</td>
        <td>${UI.formatCost(r.cost_usd)}</td>
        <td>${E(r.model_name || '-')}</td>
        <td>${UI.formatDate(r.started_at)}</td>
        <td class="mono-sm">${E(String(r.message_id))}</td>`;
      tbody.appendChild(tr);
    }
  }

  // Update or remove load more button
  const container = document.getElementById('auditLoadMoreWrap');
  if (container) {
    if (auditNextCursor) {
      container.innerHTML = '<button class="btn btn-outline btn-sm" id="auditLoadMore">Tải thêm</button>';
    } else {
      container.innerHTML = '<div class="empty">Đã tải hết.</div>';
    }
  }
}

function clearAuditFilter() {
  document.getElementById('auditFrom').value = '';
  document.getElementById('auditTo').value = '';
  auditNextCursor = null;
  document.getElementById('auditContent').innerHTML = UI.empty('Chọn khoảng thời gian và bấm "Truy vấn" để xem thống kê.');
}

function renderAudit(data) {
  const s = data.stats;
  const inv = data.invocations;
  const ext = data.extremes || {};
  const el = document.getElementById('auditContent');
  const E = UI.esc;

  let html = '<div class="audit-overview">';
  html += _ac('Tổng yêu cầu', s.total_requests, `${s.success_count} thành công / ${s.failed_count} thất bại`);
  html += _ac('Thời gian phản hồi TB', s.duration.avg_ms.toFixed(0) + 'ms', `Min: ${s.duration.min_ms}ms / Max: ${s.duration.max_ms}ms`);
  html += _ac('Tổng token', UI.formatNum(s.tokens.sum_total), `TB: ${s.tokens.avg_total.toFixed(0)} / yêu cầu`);
  html += _ac('Tổng chi phí', UI.formatCost(s.cost.total_usd), `TB: ${UI.formatCost(s.cost.avg_usd)} / yêu cầu`);
  html += _ac('TB Token (vào/ra)', `${s.tokens.avg_prompt.toFixed(0)} / ${s.tokens.avg_completion.toFixed(0)}`, `Khoảng: ${s.tokens.min_total} - ${s.tokens.max_total}`);
  html += _ac('Lượt gọi LLM', inv.total, `${UI.formatNum(inv.total_prompt_tokens)} vào / ${UI.formatNum(inv.total_completion_tokens)} ra`);
  html += '</div>';

  // Extremes
  if (ext.slowest || ext.fastest || ext.most_expensive || ext.most_tokens) {
    html += '<h4 class="section-title">Cực trị</h4><div class="audit-overview">';
    if (ext.slowest) html += _ac('Chậm nhất', ext.slowest.duration_ms + 'ms', 'msg_id: ' + E(String(ext.slowest.message_id)));
    if (ext.fastest) html += _ac('Nhanh nhất', ext.fastest.duration_ms + 'ms', 'msg_id: ' + E(String(ext.fastest.message_id)));
    if (ext.most_expensive) html += _ac('Tốn nhất', UI.formatCost(ext.most_expensive.cost_usd), `${UI.formatNum(ext.most_expensive.total_tokens)} token — msg_id: ${E(String(ext.most_expensive.message_id))}`);
    if (ext.most_tokens) html += _ac('Nhiều token nhất', UI.formatNum(ext.most_tokens.total_tokens), 'msg_id: ' + E(String(ext.most_tokens.message_id)));
    html += '</div>';
  }

  // Request history
  const reqs = data.requests || [];
  if (reqs.length > 0) {
    html += '<h4 class="section-title">Lịch sử yêu cầu (' + s.total_requests + ')</h4>';
    html += '<div class="overflow-auto"><table class="req-table"><thead><tr>';
    html += '<th>Trạng thái</th><th>Thời gian</th><th>Token (vào/ra)</th><th>Chi phí</th><th>Model</th><th>Thời điểm</th><th>Msg ID</th>';
    html += '</tr></thead><tbody>';
    for (const r of reqs) {
      html += `<tr>
        <td><span class="status-badge ${E(r.status)}">${E(r.status)}</span></td>
        <td>${r.duration_ms}ms</td>
        <td>${r.prompt_tokens} / ${r.completion_tokens}</td>
        <td>${UI.formatCost(r.cost_usd)}</td>
        <td>${E(r.model_name || '-')}</td>
        <td>${UI.formatDate(r.started_at)}</td>
        <td class="mono-sm">${E(String(r.message_id))}</td>
      </tr>`;
    }
    html += '</tbody></table></div>';

    // Pagination: Load More button
    html += '<div id="auditLoadMoreWrap" class="flex-end mt-12">';
    if (data.next_cursor) {
      html += '<button class="btn btn-outline btn-sm" id="auditLoadMore">Tải thêm</button>';
    } else if (reqs.length >= AUDIT_PAGE_SIZE) {
      html += '<div class="empty">Đã tải hết.</div>';
    }
    html += '</div>';
  } else {
    html += UI.empty('Không có yêu cầu nào trong khoảng thời gian này', 'Hãy chat để tạo dữ liệu audit');
  }

  el.innerHTML = html;
}

function _ac(label, value, sub) {
  return `<div class="audit-stat"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
}

// === CONFIG ===
// Per-bot Temperature/Max Tokens hidden — system_config defaults
// (llm_default_temperature / llm_default_max_tokens) are the source of
// truth so test bots stay consistent with the platform baseline.
function renderConfig() {
  if (!botConfig) return;
  const el = document.getElementById('configContent');
  const promptChars = (botConfig.system_prompt || '').length;
  const docCount = documents.length;
  const docLinks = documents.map(d => {
    const isHttp = d.source_url && (d.source_url.startsWith('https://') || d.source_url.startsWith('http://'));
    const link = isHttp
      ? `<a href="${esc(d.source_url)}" target="_blank" rel="noopener" class="primary-link" title="${esc(d.source_url)}">${esc(d.source_url)}</a>`
      : `<span class="doc-url-muted">${esc(d.source_url || '(text inline)')}</span>`;
    const status = d.status === 'ready' ? '✓' : (d.status === 'failed' ? '✗' : '⏳');
    return `<li><span class="doc-link-status">${status}</span> <strong>${esc(d.document_name)}</strong> (${d.chunk_count} chunks)<br>${link}</li>`;
  }).join('');

  el.innerHTML = `
    <div class="config-form">
      <!-- 1. SYSTEM PROMPT -->
      <div class="config-section">
        <div class="config-section-header">
          <h3>📝 System Prompt</h3>
          <span class="config-meta">${promptChars.toLocaleString('vi')} chars</span>
        </div>
        <textarea id="cfgPrompt" rows="20" class="prompt-editor">${esc(botConfig.system_prompt)}</textarea>
        <div class="flex-end">
          <button class="btn btn-primary btn-sm" id="saveConfigBtn">💾 Lưu System Prompt</button>
        </div>
      </div>

      <!-- 2. DOCUMENT LINKS -->
      <div class="config-section">
        <div class="config-section-header">
          <h3>📚 Document Links (${docCount})</h3>
          <span class="config-meta">Nguồn dữ liệu bot đang dùng</span>
        </div>
        ${docCount === 0
          ? '<div class="empty">Chưa có tài liệu. Vào tab "Tài liệu" để thêm.</div>'
          : `<ul class="doc-link-list">${docLinks}</ul>`
        }
      </div>

      <!-- 3. BOT EVENTS / ACTIVITY LOG -->
      <div class="config-section">
        <div class="config-section-header">
          <h3>📋 Bot Events (recent 20)</h3>
          <span class="config-meta">audit_log + request_steps</span>
          <button class="btn btn-ghost btn-sm" id="refreshEventsBtn">🔄 Refresh</button>
        </div>
        <div id="botEventsContent"><div class="loading"><div class="spinner spinner-xs"></div> Đang tải events...</div></div>
      </div>

      <!-- Hidden meta (chỉ hiện khi click "Show advanced") -->
      <input type="hidden" id="cfgName" value="${esc(botConfig.bot_name)}">
    </div>
  `;

  // Load bot events ngay
  loadBotEvents();
}

// Open chunks panel cho 1 doc cụ thể — render vào #chunksPanelHost (FULL pane-docs).
// KHÔNG overlay, KHÔNG fixed. Ẩn các sibling (.doc-actions, .add-doc-form, #docList).
async function toggleDocChunks(docId) {
  const host = document.getElementById('chunksPanelHost');
  const pane = document.getElementById('pane-docs');
  if (!host || !pane) return;

  // Nếu panel hiện tại đang mở đúng doc này → đóng
  const existing = host.querySelector('.doc-chunks-panel');
  if (existing && existing.id === `chunks-panel-${docId}` && pane.classList.contains('viewing-chunks')) {
    closeChunksPanel();
    return;
  }

  // Tạo panel mới (replace existing nếu khác doc)
  host.innerHTML = `<div class="doc-chunks-panel" id="chunks-panel-${esc(docId)}"></div>`;
  pane.classList.add('viewing-chunks');
  // Scroll lên top của tab
  pane.scrollTop = 0;
  window.scrollTo({ top: 0, behavior: 'smooth' });

  const panel = document.getElementById(`chunks-panel-${docId}`);
  panel.innerHTML = '<div class="loading"><div class="spinner spinner-xs"></div> Đang tải chunks...</div>';
  const _tid_q = botConfig && botConfig.tenant_id ? '?tenant_id=' + botConfig.tenant_id : '';
  const res = await RagbotAPI.get(`/bots/${BOT_ID}/${CHANNEL}/chunking-info${_tid_q}`);
  if (!res.ok) {
    panel.innerHTML = `<div class="empty">Không tải được chunks: ${esc(res.error || '')}</div>`;
    return;
  }
  const data = res.data;
  const doc = (data.documents || []).find(d => d.document_id === docId);
  if (!doc) {
    panel.innerHTML = '<div class="empty">Doc không tìm thấy trong response.</div>';
    return;
  }
  const cfg = data.system_config || {};
  // Render header info + chunk accordion
  // Store full chunk list trên panel để filter/load-more không cần re-fetch
  panel._chunks = doc.sample_chunks || [];
  panel._filter = 'all'; // all | parent | leaf | haiku | issue
  panel._search = '';
  panel._loadedCount = 30; // initial render 30 chunks, click "Load more" → +30
  panel._loadStep = 30;
  panel._docInfo = doc;
  panel._sysCfg = cfg;

  panel.innerHTML = `
    <div class="chunks-panel-close-bar">
      <h2>📄 ${esc(doc.document_name)} <small>${doc.total_chunks} chunks</small></h2>
      <button class="chunks-panel-back" data-chunks-close="${esc(docId)}">← Quay lại danh sách</button>
    </div>
    <div class="chunks-header">
      <div class="chunks-stats">
        <span class="chunks-stat-pill"><strong>Strategy:</strong> ${esc(doc.detected_strategy || 'unknown')}</span>
        <span class="chunks-stat-pill"><strong>Total:</strong> ${doc.total_chunks}</span>
        <span class="chunks-stat-pill"><strong>Parents:</strong> ${doc.parent_count}</span>
        <span class="chunks-stat-pill"><strong>Leaves:</strong> ${doc.leaf_count}</span>
        <span class="chunks-stat-pill"><strong>Embedded:</strong> ${doc.embedded_count}/${doc.total_chunks}</span>
        <span class="chunks-stat-pill"><strong>Haiku:</strong> ${doc.haiku_enriched_count}</span>
        <span class="chunks-stat-pill"><strong>Size:</strong> ${doc.avg_chars}c avg · ${doc.min_chars}c–${doc.max_chars}c</span>
        <span class="chunks-stat-pill"><strong>Config:</strong> parent ${cfg.parent_chunk_size}c / child ${cfg.child_chunk_size}c · ${esc(cfg.enrichment_model || '')}</span>
      </div>
      <div class="chunks-toolbar">
        <input class="chunks-search" data-chunks-search="${esc(doc.document_id)}" placeholder="🔍 Tìm trong chunks (gõ rồi enter)..." value="">
        <div class="chunks-filter-btns">
          <button class="chunks-filter-btn active" data-chunks-filter="all" data-doc="${esc(doc.document_id)}">Tất cả</button>
          <button class="chunks-filter-btn" data-chunks-filter="parent" data-doc="${esc(doc.document_id)}">🌳 Parent</button>
          <button class="chunks-filter-btn" data-chunks-filter="leaf" data-doc="${esc(doc.document_id)}">🍃 Leaf</button>
          <button class="chunks-filter-btn" data-chunks-filter="haiku" data-doc="${esc(doc.document_id)}">⚠ Haiku</button>
          <button class="chunks-filter-btn" data-chunks-filter="issue" data-doc="${esc(doc.document_id)}" title="Chunks ngắn <50c hoặc bắt đầu bằng số/.">⚠ Issue</button>
        </div>
      </div>
    </div>
    <div class="chunks-loaded-count" id="count-${esc(doc.document_id)}"></div>
    <div class="chunks-list" id="chunks-list-${esc(doc.document_id)}"></div>
    <div class="chunks-load-more" id="loadmore-${esc(doc.document_id)}"></div>
  `;
  renderChunkList(docId);
}

// Đóng chunks panel — remove class viewing-chunks → docList + form upload hiện lại
function closeChunksPanel() {
  const host = document.getElementById('chunksPanelHost');
  const pane = document.getElementById('pane-docs');
  if (pane) pane.classList.remove('viewing-chunks');
  if (host) host.innerHTML = '';
  document.body.style.overflow = ''; // unlock (backward-compat nếu trước đó có lock)
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Render chunks: filter → load N chunks đầu (infinite scroll bằng "Load more")
function renderChunkList(docId) {
  const panel = document.getElementById(`chunks-panel-${docId}`);
  if (!panel || !panel._chunks) return;
  const listEl = document.getElementById(`chunks-list-${docId}`);
  const countEl = document.getElementById(`count-${docId}`);
  const loadMoreEl = document.getElementById(`loadmore-${docId}`);
  if (!listEl) return;

  // Filter
  const search = (panel._search || '').toLowerCase();
  const filter = panel._filter || 'all';
  const filtered = panel._chunks.filter(c => {
    if (filter === 'parent' && !c.is_parent) return false;
    if (filter === 'leaf' && c.is_parent) return false;
    if (filter === 'haiku' && !c.has_haiku_prefix) return false;
    if (filter === 'issue') {
      const preview = c.preview || '';
      const lastLine = preview.split('\n').filter(l => l.trim()).pop() || '';
      const startsBadly = /^[\s]*[\d\.\,]/.test(lastLine);
      const tooShort = c.chars < 50;
      if (!tooShort && !startsBadly) return false;
    }
    if (search) {
      const hay = (c.preview || '').toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });

  // Slice load count
  const loaded = Math.min(panel._loadedCount || 30, filtered.length);
  const visible = filtered.slice(0, loaded);

  // Count info
  if (countEl) {
    countEl.innerHTML = filtered.length > 0
      ? `Hiển thị <strong>${loaded.toLocaleString('vi')}</strong> / <strong>${filtered.length.toLocaleString('vi')}</strong> chunks${filter !== 'all' || search ? ' (sau filter)' : ''}`
      : '';
  }

  // Render cards
  if (visible.length === 0) {
    listEl.innerHTML = '<div class="chunks-empty">Không có chunk nào khớp filter/search.</div>';
    if (loadMoreEl) loadMoreEl.innerHTML = '';
    return;
  }
  listEl.innerHTML = visible.map(c => {
    const pageStr = c.page ? `Page ${esc(c.page)}` : 'Page N/A';
    const pathStr = c.path || 'N/A';
    let previewHtml;
    if (c.has_haiku_prefix && c.preview) {
      const lines = c.preview.split('\n');
      const firstLine = lines[0] || '';
      const rest = lines.slice(1).join('\n');
      previewHtml = `<span class="chunk-haiku-prefix">${esc(firstLine)}</span>\n${esc(rest)}`;
    } else {
      previewHtml = esc(c.preview || '');
    }
    return `
      <div class="chunk-card">
        <div class="chunk-card-header">
          <span class="chunk-card-id">Chunk ${c.chunk_index + 1}</span>
          <span class="chunk-card-meta">
            <span>${pageStr}</span>
            ${pathStr !== 'N/A' ? `<span>📂 ${esc(pathStr)}</span>` : ''}
          </span>
          <span class="chunk-card-flags">
            <span class="chunk-flag chunk-flag-size">${c.chars}c</span>
            ${c.is_parent
              ? '<span class="chunk-flag chunk-flag-parent">🌳 parent</span>'
              : '<span class="chunk-flag chunk-flag-leaf">🍃 leaf</span>'
            }
            ${c.has_haiku_prefix ? '<span class="chunk-flag chunk-flag-haiku">⚠ Haiku</span>' : ''}
          </span>
        </div>
        <div class="chunk-card-preview">${previewHtml}</div>
      </div>
    `;
  }).join('');

  // Load more button
  if (loadMoreEl) {
    if (loaded < filtered.length) {
      const remaining = filtered.length - loaded;
      const nextStep = Math.min(panel._loadStep || 30, remaining);
      loadMoreEl.innerHTML = `
        <button class="chunks-load-more-btn" data-load-more="${esc(docId)}">
          ⬇ Xem thêm ${nextStep} chunks (còn ${remaining.toLocaleString('vi')})
        </button>
      `;
    } else {
      loadMoreEl.innerHTML = filtered.length > 30
        ? `<div style="font-size:12px;color:var(--gray-500,#6b7280);">✓ Đã hiển thị toàn bộ ${filtered.length} chunks</div>`
        : '';
    }
  }
}

// Backward compat alias (cũ gọi renderChunkPage)
function renderChunkPage(docId) { renderChunkList(docId); }

// === CHUNK MODAL — click chunk card → mở full-screen overlay ===
let _currentModalState = null; // { docId, chunkIndex, filtered }

function openChunkModal(docId, chunkIndex) {
  const panel = document.getElementById(`chunks-panel-${docId}`);
  if (!panel || !panel._chunks) return;

  // Re-build filtered list giống renderChunkList để Prev/Next navigate đúng filter
  const search = (panel._search || '').toLowerCase();
  const filter = panel._filter || 'all';
  const filtered = panel._chunks.filter(c => {
    if (filter === 'parent' && !c.is_parent) return false;
    if (filter === 'leaf' && c.is_parent) return false;
    if (filter === 'haiku' && !c.has_haiku_prefix) return false;
    if (filter === 'issue') {
      const preview = c.preview || '';
      const lastLine = preview.split('\n').filter(l => l.trim()).pop() || '';
      const startsBadly = /^[\s]*[\d\.\,]/.test(lastLine);
      const tooShort = c.chars < 50;
      if (!tooShort && !startsBadly) return false;
    }
    if (search) {
      const hay = (c.preview || '').toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });

  // Find chunk position trong filtered array
  const idx = filtered.findIndex(c => c.chunk_index === chunkIndex);
  if (idx < 0) return;

  _currentModalState = { docId, chunkIndex, filtered, idx };
  renderChunkModal();
}

function renderChunkModal() {
  if (!_currentModalState) return;
  const { filtered, idx } = _currentModalState;
  const c = filtered[idx];
  if (!c) return;
  const pageStr = c.page ? `Page ${esc(c.page)}` : 'Page N/A';
  const pathStr = c.path || 'N/A';

  // Preview với Haiku highlight
  let contentHtml;
  if (c.has_haiku_prefix && c.preview) {
    const lines = c.preview.split('\n');
    const firstLine = lines[0] || '';
    const rest = lines.slice(1).join('\n');
    contentHtml = `<span class="chunk-haiku-prefix">${esc(firstLine)}</span>\n${esc(rest)}`;
  } else {
    contentHtml = esc(c.preview || '');
  }

  const flags = [];
  flags.push(`<span class="chunk-flag chunk-flag-size">${c.chars}c</span>`);
  flags.push(c.is_parent
    ? '<span class="chunk-flag chunk-flag-parent">🌳 parent</span>'
    : '<span class="chunk-flag chunk-flag-leaf">🍃 leaf</span>'
  );
  if (c.has_haiku_prefix) flags.push('<span class="chunk-flag chunk-flag-haiku">⚠ Haiku</span>');

  // Remove existing modal
  document.getElementById('chunkModalRoot')?.remove();

  const root = document.createElement('div');
  root.id = 'chunkModalRoot';
  root.className = 'chunk-modal-overlay';
  root.innerHTML = `
    <div class="chunk-modal" role="dialog" aria-modal="true">
      <div class="chunk-modal-header">
        <div class="chunk-modal-title">
          <span>📄 Chunk ${c.chunk_index + 1}</span>
          <span class="chunk-modal-title-flags">${flags.join('')}</span>
        </div>
        <button class="chunk-modal-close" id="chunkModalClose" title="Đóng (ESC)">✕</button>
      </div>
      <div class="chunk-modal-body">
        <div class="chunk-modal-meta">
          <span><strong>Page:</strong> ${pageStr.replace('Page ', '')}</span>
          ${pathStr !== 'N/A' ? `<span><strong>Path:</strong> 📂 ${esc(pathStr)}</span>` : ''}
          <span><strong>Chunk index:</strong> ${c.chunk_index + 1}</span>
          <span><strong>Chars:</strong> ${c.chars.toLocaleString('vi')}</span>
        </div>
        <div class="chunk-modal-content">${contentHtml}</div>
      </div>
      <div class="chunk-modal-footer">
        <div class="chunk-modal-nav">
          <button class="chunk-modal-nav-btn" id="chunkModalPrev" ${idx <= 0 ? 'disabled' : ''}>← Prev</button>
          <button class="chunk-modal-nav-btn" id="chunkModalNext" ${idx >= filtered.length - 1 ? 'disabled' : ''}>Next →</button>
        </div>
        <div class="chunk-modal-pos">${idx + 1} / ${filtered.length}</div>
      </div>
    </div>
  `;
  document.body.appendChild(root);
}

function closeChunkModal() {
  document.getElementById('chunkModalRoot')?.remove();
  _currentModalState = null;
}

function navChunkModal(delta) {
  if (!_currentModalState) return;
  const next = _currentModalState.idx + delta;
  if (next < 0 || next >= _currentModalState.filtered.length) return;
  _currentModalState.idx = next;
  _currentModalState.chunkIndex = _currentModalState.filtered[next].chunk_index;
  renderChunkModal();
}

// Modal click handlers (delegated)
document.addEventListener('click', (e) => {
  // Close button + overlay click (outside .chunk-modal)
  if (e.target.id === 'chunkModalClose' || (e.target.classList.contains('chunk-modal-overlay'))) {
    closeChunkModal();
    return;
  }
  if (e.target.closest('#chunkModalPrev')) { navChunkModal(-1); return; }
  if (e.target.closest('#chunkModalNext')) { navChunkModal(1); return; }

  // Chunk card click → open modal
  const card = e.target.closest('.chunk-card');
  if (card) {
    // Find docId từ panel parent
    const panel = card.closest('.doc-chunks-panel');
    if (!panel) return;
    const docId = panel.id.replace('chunks-panel-', '');
    // Find chunk index trong list (theo position của card)
    const cards = Array.from(panel.querySelectorAll('.chunk-card'));
    const cardPos = cards.indexOf(card);
    if (cardPos < 0) return;
    // Re-compute filtered → lấy chunk_index thật sự
    const panelObj = document.getElementById(`chunks-panel-${docId}`);
    if (!panelObj || !panelObj._chunks) return;
    const search = (panelObj._search || '').toLowerCase();
    const filter = panelObj._filter || 'all';
    const filtered = panelObj._chunks.filter(c => {
      if (filter === 'parent' && !c.is_parent) return false;
      if (filter === 'leaf' && c.is_parent) return false;
      if (filter === 'haiku' && !c.has_haiku_prefix) return false;
      if (filter === 'issue') {
        const preview = c.preview || '';
        const lastLine = preview.split('\n').filter(l => l.trim()).pop() || '';
        const startsBadly = /^[\s]*[\d\.\,]/.test(lastLine);
        const tooShort = c.chars < 50;
        if (!tooShort && !startsBadly) return false;
      }
      if (search) {
        const hay = (c.preview || '').toLowerCase();
        if (!hay.includes(search)) return false;
      }
      return true;
    });
    const chunkObj = filtered[cardPos];
    if (!chunkObj) return;
    openChunkModal(docId, chunkObj.chunk_index);
  }
});

// ESC key → close modal
document.addEventListener('keydown', (e) => {
  if (!_currentModalState) return;
  if (e.key === 'Escape') { closeChunkModal(); return; }
  if (e.key === 'ArrowLeft') { navChunkModal(-1); return; }
  if (e.key === 'ArrowRight') { navChunkModal(1); return; }
});

async function loadBotEvents() {
  const target = document.getElementById('botEventsContent');
  if (!target || !botConfig) return;
  // Use existing /audit endpoint — recent 20 events
  const today = new Date().toISOString().slice(0, 10);
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString().slice(0, 10);
  const _tid_q = botConfig.tenant_id ? '&tenant_id=' + botConfig.tenant_id : '';
  const res = await RagbotAPI.get(`/bots/${BOT_ID}/${CHANNEL}/audit?since=${since}&until=${today}&limit=20${_tid_q}`);
  if (!res.ok) {
    target.innerHTML = `<div class="empty">Chưa có event nào hoặc lỗi load: ${esc(res.error || '')}</div>`;
    return;
  }
  const events = (res.data && res.data.events) || [];
  if (events.length === 0) {
    target.innerHTML = '<div class="empty">Chưa có activity trong 24h gần nhất. Chat thử với bot để sinh event.</div>';
    return;
  }
  target.innerHTML = `
    <div class="event-list">
      ${events.map(ev => {
        const ts = ev.created_at ? new Date(ev.created_at).toLocaleString('vi') : '-';
        const action = ev.action || ev.event_type || 'unknown';
        const dataPreview = ev.data ? JSON.stringify(ev.data).slice(0, 150) : '';
        return `
          <div class="event-item">
            <div class="event-header">
              <span class="event-action">${esc(action)}</span>
              <span class="event-time">${esc(ts)}</span>
            </div>
            ${dataPreview ? `<div class="event-data">${esc(dataPreview)}${dataPreview.length >= 150 ? '...' : ''}</div>` : ''}
          </div>`;
      }).join('')}
    </div>
  `;
}

async function saveConfig() {
  const btn = document.getElementById('saveConfigBtn');
  UI.btnLoading(btn, true, 'Đang lưu...');
  // temperature + max_tokens omitted: server keeps existing per-bot
  // values when the field is absent (PATCH with None pydantic field).
  const body = {
    bot_name: document.getElementById('cfgName').value.trim() || undefined,
    system_prompt: document.getElementById('cfgPrompt').value.trim(),
  };
  const res = await RagbotAPI.patch('/bots/' + botConfig.id, body);
  UI.btnLoading(btn, false, 'Lưu thay đổi');
  if (res.ok) {
    toast('Đã lưu cấu hình! Thay đổi áp dụng từ tin nhắn tiếp theo.', 'success');
    loadBotInfo();
  } else {
    toast(res.error, 'error');
  }
}


// Init
init();
