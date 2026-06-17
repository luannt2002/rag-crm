const esc = UI.esc.bind(UI);
const toast = UI.toast.bind(UI);

// ============================================================
// TAB SWITCHING
// ============================================================
const TAB_LOADERS = {
  config: loadConfig,
  redis: null,
  tokens: loadTokens,
  models: loadModels,
};
const loadedTabs = new Set();

function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + tab));
  if (!loadedTabs.has(tab) && TAB_LOADERS[tab]) {
    loadedTabs.add(tab);
    TAB_LOADERS[tab]();
  }
}

document.getElementById('tabBar').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (tab) switchTab(tab.dataset.tab);
});

// ============================================================
// TAB 1: SYSTEM CONFIG
// ============================================================
let configData = [];

async function loadConfig() {
  const el = document.getElementById('configContent');
  el.innerHTML = UI.spinner('Dang tai cau hinh...');
  const res = await RagbotAPI.get('/admin/config');
  if (!res.ok) { el.innerHTML = UI.empty('Loi tai cau hinh', res.error); return; }
  configData = res.data.data || res.data.configs || [];
  if (Array.isArray(res.data)) configData = res.data;
  renderConfig();
}

function renderConfig() {
  const el = document.getElementById('configContent');
  if (!configData || configData.length === 0) {
    el.innerHTML = UI.empty('Khong co cau hinh nao');
    return;
  }
  const rows = configData.map((c, i) => {
    const key = c.key || c.config_key || '';
    const val = c.value || c.config_value || '';
    return '<tr data-idx="' + i + '">' +
      '<td class="mono">' + esc(key) + '</td>' +
      '<td class="config-value" data-key="' + esc(key) + '">' + esc(String(val)) + '</td>' +
      '<td><button class="btn btn-outline btn-sm config-edit-btn" data-key="' + esc(key) + '" data-val="' + esc(String(val)) + '">Sua</button></td>' +
      '</tr>';
  });
  el.innerHTML = '<div style="overflow-x:auto"><table class="data-table"><thead><tr><th>Key</th><th>Gia tri</th><th>Hanh dong</th></tr></thead><tbody>' + rows.join('') + '</tbody></table></div>';
}

document.getElementById('configContent').addEventListener('click', (e) => {
  const editBtn = e.target.closest('.config-edit-btn');
  if (editBtn) {
    startEditConfig(editBtn.dataset.key, editBtn.dataset.val);
    return;
  }
  const saveBtn = e.target.closest('.config-save-btn');
  if (saveBtn) {
    saveConfig(saveBtn.dataset.key);
    return;
  }
  const cancelBtn = e.target.closest('.config-cancel-btn');
  if (cancelBtn) {
    renderConfig();
    return;
  }
});

function startEditConfig(key, currentVal) {
  const td = document.querySelector('.config-value[data-key="' + CSS.escape(key) + '"]');
  if (!td) return;
  const tr = td.parentElement;
  const actionTd = tr.querySelector('td:last-child');

  td.innerHTML = '';
  const input = document.createElement('input');
  input.type = 'text';
  input.value = currentVal;
  input.className = 'inline-edit-input';
  input.style.cssText = 'padding:6px 10px;border:1px solid var(--primary);border-radius:6px;font-size:13px;outline:none;box-shadow:0 0 0 2px var(--primary-light);width:100%;font-family:inherit;';
  td.appendChild(input);
  input.focus();
  input.select();

  actionTd.innerHTML = '<div class="inline-edit">' +
    '<button class="btn btn-primary btn-sm config-save-btn" data-key="' + esc(key) + '">Luu</button>' +
    '<button class="btn btn-ghost btn-sm config-cancel-btn">Huy</button></div>';

  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') saveConfig(key);
    if (ev.key === 'Escape') renderConfig();
  });
}

async function saveConfig(key) {
  const td = document.querySelector('.config-value[data-key="' + CSS.escape(key) + '"]');
  if (!td) return;
  const input = td.querySelector('input');
  if (!input) return;
  const newVal = input.value;

  const saveBtn = document.querySelector('.config-save-btn[data-key="' + CSS.escape(key) + '"]');
  if (saveBtn) UI.btnLoading(saveBtn, true, 'Dang luu...');

  const res = await RagbotAPI.request({ method: 'PUT', path: '/admin/config/' + encodeURIComponent(key), data: { value: newVal } });
  if (res.ok) {
    toast('Da cap nhat ' + key, 'success');
    loadConfig();
  } else {
    toast(res.error || 'Loi cap nhat', 'error');
    if (saveBtn) UI.btnLoading(saveBtn, false, 'Luu');
  }
}

// ============================================================
// TAB 2: REDIS DEBUG (READ-ONLY)
// ============================================================
let redisKeys = [];

document.getElementById('loadRedisBtn').addEventListener('click', loadRedisKeys);

async function loadRedisKeys() {
  const el = document.getElementById('redisContent');
  const btn = document.getElementById('loadRedisBtn');
  UI.btnLoading(btn, true, 'Dang tai...');
  el.innerHTML = UI.spinner('Dang tai Redis keys...');
  document.getElementById('redisDetail').innerHTML = '';

  const res = await RagbotAPI.get('/admin/redis/keys');
  UI.btnLoading(btn, false, 'Tai Keys');
  if (!res.ok) { el.innerHTML = UI.empty('Loi tai Redis keys', res.error); return; }
  redisKeys = res.data.keys || res.data.data || [];
  if (Array.isArray(res.data)) redisKeys = res.data;
  renderRedisKeys();
}

function renderRedisKeys() {
  const el = document.getElementById('redisContent');
  if (!redisKeys || redisKeys.length === 0) {
    el.innerHTML = UI.empty('Khong co key nao trong Redis');
    return;
  }
  const rows = redisKeys.map((k, i) => {
    const key = typeof k === 'string' ? k : (k.key || k.name || '');
    const type = typeof k === 'string' ? '-' : (k.type || '-');
    const ttl = typeof k === 'string' ? '-' : (k.ttl != null ? (k.ttl === -1 ? 'Khong het han' : k.ttl + 's') : '-');
    return '<tr class="redis-row" data-key="' + esc(key) + '">' +
      '<td class="mono" style="cursor:pointer;color:var(--primary)">' + esc(key) + '</td>' +
      '<td>' + esc(type) + '</td>' +
      '<td>' + esc(String(ttl)) + '</td>' +
      '</tr>';
  });
  el.innerHTML = '<div style="overflow-x:auto"><table class="data-table"><thead><tr><th>Key</th><th>Type</th><th>TTL</th></tr></thead><tbody>' + rows.join('') + '</tbody></table></div>';
}

document.getElementById('redisContent').addEventListener('click', (e) => {
  const row = e.target.closest('.redis-row');
  if (row) loadRedisKeyDetail(row.dataset.key);
});

async function loadRedisKeyDetail(key) {
  const el = document.getElementById('redisDetail');
  el.innerHTML = '<div class="key-detail"><div class="loading"><div class="spinner"></div> Dang tai...</div></div>';

  const res = await RagbotAPI.get('/admin/redis/key/' + encodeURIComponent(key));
  if (!res.ok) {
    el.innerHTML = '<div class="key-detail"><p style="color:var(--danger)">' + esc(res.error) + '</p></div>';
    return;
  }

  const data = res.data;
  let valueStr;
  if (typeof data.value === 'object') {
    valueStr = JSON.stringify(data.value, null, 2);
  } else {
    valueStr = String(data.value != null ? data.value : JSON.stringify(data, null, 2));
  }

  const detailEl = document.createElement('div');
  detailEl.className = 'key-detail';

  const header = document.createElement('div');
  header.className = 'key-detail-header';
  const h4 = document.createElement('h4');
  h4.textContent = key;
  const closeBtn = document.createElement('button');
  closeBtn.className = 'btn btn-ghost btn-sm';
  closeBtn.textContent = 'Dong';
  closeBtn.addEventListener('click', () => { el.innerHTML = ''; });
  header.appendChild(h4);
  header.appendChild(closeBtn);

  const pre = document.createElement('pre');
  pre.textContent = valueStr;

  detailEl.appendChild(header);
  detailEl.appendChild(pre);
  el.innerHTML = '';
  el.appendChild(detailEl);
}

// ============================================================
// TAB 3: API TOKENS
// ============================================================
let tokensData = [];

async function loadTokens() {
  const el = document.getElementById('tokensContent');
  el.innerHTML = UI.spinner('Dang tai tokens...');
  const res = await RagbotAPI.get('/tokens');
  if (!res.ok) { el.innerHTML = UI.empty('Loi tai tokens', res.error); return; }
  tokensData = res.data.data || res.data.tokens || [];
  if (Array.isArray(res.data)) tokensData = res.data;
  renderTokens();
}

function renderTokens() {
  const el = document.getElementById('tokensContent');
  if (!tokensData || tokensData.length === 0) {
    el.innerHTML = UI.empty('Chua co API token nao', 'Tao token moi de bat dau');
    return;
  }
  const rows = tokensData.map(t => {
    const name = t.service_name || t.name || '';
    const role = t.role || '-';
    const rlVal = t.rate_limit_value || t.rate_limit || '-';
    const rlWin = t.rate_limit_window || '-';
    const rateStr = (rlVal !== '-' && rlWin !== '-') ? rlVal + ' req / ' + rlWin + 's' : '-';
    const version = t.version || t.token_version || '-';
    const status = t.status || (t.revoked ? 'revoked' : 'active');
    const isActive = status === 'active';
    return '<tr>' +
      '<td class="mono">' + esc(name) + '</td>' +
      '<td>' + esc(role) + '</td>' +
      '<td>' + esc(rateStr) + '</td>' +
      '<td>' + esc(String(version)) + '</td>' +
      '<td><span class="status-badge ' + esc(status) + '">' + esc(status) + '</span></td>' +
      '<td>' +
        (isActive ? '<button class="btn btn-warning btn-sm token-regen-btn" data-name="' + esc(name) + '">Regenerate</button> ' : '') +
        (isActive ? '<button class="btn btn-danger btn-sm token-revoke-btn" data-name="' + esc(name) + '">Revoke</button>' : '') +
      '</td>' +
      '</tr>';
  });
  el.innerHTML = '<div style="overflow-x:auto"><table class="data-table"><thead><tr><th>Service</th><th>Role</th><th>Rate Limit</th><th>Version</th><th>Trang thai</th><th>Hanh dong</th></tr></thead><tbody>' + rows.join('') + '</tbody></table></div>';
}

document.getElementById('tokensContent').addEventListener('click', async (e) => {
  const regenBtn = e.target.closest('.token-regen-btn');
  if (regenBtn) {
    const name = regenBtn.dataset.name;
    if (!confirm('Regenerate token cho "' + name + '"? Token cu se het hieu luc.')) return;
    UI.btnLoading(regenBtn, true, 'Dang...');
    const res = await RagbotAPI.post('/tokens/' + encodeURIComponent(name) + '/regenerate');
    if (res.ok) { toast('Da regenerate token ' + name, 'success'); loadTokens(); }
    else { toast(res.error, 'error'); UI.btnLoading(regenBtn, false, 'Regenerate'); }
    return;
  }
  const revokeBtn = e.target.closest('.token-revoke-btn');
  if (revokeBtn) {
    const name = revokeBtn.dataset.name;
    if (!confirm('Revoke token "' + name + '"? Hanh dong nay khong the hoan tac.')) return;
    UI.btnLoading(revokeBtn, true, 'Dang...');
    const res = await RagbotAPI.del('/tokens/' + encodeURIComponent(name));
    if (res.ok) { toast('Da revoke token ' + name, 'success'); loadTokens(); }
    else { toast(res.error, 'error'); UI.btnLoading(revokeBtn, false, 'Revoke'); }
    return;
  }
});

// Create Token Modal
document.getElementById('showCreateTokenBtn').addEventListener('click', () => {
  document.getElementById('createTokenModal').classList.add('active');
  document.getElementById('fServiceName').focus();
});
document.getElementById('tokenModalCloseBtn').addEventListener('click', hideTokenModal);
document.getElementById('tokenModalCancelBtn').addEventListener('click', hideTokenModal);
document.getElementById('createTokenModal').addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay')) hideTokenModal();
});

function hideTokenModal() {
  document.getElementById('createTokenModal').classList.remove('active');
  document.getElementById('fServiceName').value = '';
  document.getElementById('fDescription').value = '';
  document.getElementById('fRole').value = 'service';
  document.getElementById('fRateLimitValue').value = '120';
  document.getElementById('fRateLimitWindow').value = '60';
}

document.getElementById('createTokenBtn').addEventListener('click', async () => {
  const serviceName = document.getElementById('fServiceName').value.trim();
  if (!serviceName) { toast('Vui long nhap ten dich vu', 'error'); return; }

  const btn = document.getElementById('createTokenBtn');
  UI.btnLoading(btn, true, 'Dang tao...');

  const body = {
    service_name: serviceName,
    description: document.getElementById('fDescription').value.trim() || undefined,
    role: document.getElementById('fRole').value,
    rate_limit_value: parseInt(document.getElementById('fRateLimitValue').value) || 120,
    rate_limit_window: parseInt(document.getElementById('fRateLimitWindow').value) || 60,
  };

  const res = await RagbotAPI.post('/tokens', body);
  UI.btnLoading(btn, false, 'Tao Token');
  if (res.ok) {
    toast('Da tao token cho ' + serviceName, 'success');
    hideTokenModal();
    loadTokens();
  } else {
    toast(res.error, 'error');
  }
});

// ============================================================
// TAB 4: AI MODELS (READ-ONLY)
// ============================================================
async function loadModels() {
  const el = document.getElementById('modelsContent');
  el.innerHTML = UI.spinner('Dang tai models...');
  const res = await RagbotAPI.get('/admin/models');
  if (!res.ok) { el.innerHTML = UI.empty('Loi tai models', res.error); return; }
  const models = res.data.data || res.data.models || [];
  renderModels(Array.isArray(res.data) ? res.data : models);
}

function renderModels(models) {
  const el = document.getElementById('modelsContent');
  if (!models || models.length === 0) {
    el.innerHTML = UI.empty('Chua co AI model nao duoc cau hinh');
    return;
  }
  const rows = models.map(m => {
    const name = m.model_name || m.name || m.model || '';
    const provider = m.provider || '-';
    const purpose = m.purpose || m.use_case || '-';
    const isDefault = m.is_default || m.default || false;
    return '<tr>' +
      '<td class="mono">' + esc(name) + '</td>' +
      '<td>' + esc(provider) + '</td>' +
      '<td>' + esc(purpose) + '</td>' +
      '<td>' + (isDefault ? '<span class="status-badge active">Mac dinh</span>' : '-') + '</td>' +
      '</tr>';
  });
  el.innerHTML = '<div style="overflow-x:auto"><table class="data-table"><thead><tr><th>Model</th><th>Provider</th><th>Muc dich</th><th>Mac dinh</th></tr></thead><tbody>' + rows.join('') + '</tbody></table></div>';
}

// ============================================================
// INIT
// ============================================================
loadedTabs.add('config');
loadConfig();
