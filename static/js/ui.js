/**
 * RAGbot UI Utilities — shared toast, spinner, escaping, loading states.
 */

const UI = {
  /** Escape HTML to prevent XSS */
  esc(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
  },

  /** Show toast notification */
  toast(msg, type = '') {
    let t = document.getElementById('toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'toast';
      t.className = 'toast';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.className = 'toast show ' + type;
    clearTimeout(t._tid);
    t._tid = setTimeout(() => t.className = 'toast', 3500);
  },

  /** Return spinner HTML */
  spinner(text = 'Loading...') {
    return `<div class="loading"><div class="spinner"></div> ${this.esc(text)}</div>`;
  },

  /** Return empty state HTML */
  empty(title, subtitle = '') {
    return `<div class="empty"><h3>${this.esc(title)}</h3>${subtitle ? '<p>' + this.esc(subtitle) + '</p>' : ''}</div>`;
  },

  /** Set button loading state */
  btnLoading(btn, loading, originalText = null) {
    if (loading) {
      btn._originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = originalText || 'Loading...';
    } else {
      btn.disabled = false;
      btn.textContent = btn._originalText || originalText || 'Submit';
    }
  },

  /** Format date to Vietnamese locale */
  formatDate(isoStr) {
    if (!isoStr) return '-';
    return new Date(isoStr).toLocaleString('vi');
  },

  /** Format date short (date only) */
  formatDateShort(isoStr) {
    if (!isoStr) return '-';
    return new Date(isoStr).toLocaleDateString('vi');
  },

  /** Format cost as USD */
  formatCost(usd) {
    return '$' + (usd || 0).toFixed(6);
  },

  /** Format number with separator */
  formatNum(n) {
    return (n || 0).toLocaleString();
  },

  /** Basic markdown to HTML (bold, code, bullets) */
  formatMd(text) {
    return this.esc(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`(.+?)`/g, '<code class="md-code">$1</code>')
      .replace(/\n- /g, '\n&bull; ')
      .replace(/\n(\d+)\. /g, '\n$1. ')
      .replace(/\n/g, '<br>');
  },

};

// Expose globally
window.UI = UI;
