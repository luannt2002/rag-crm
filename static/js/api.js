/**
 * RAGbot API Client — reusable fetch wrapper.
 * Inspired by CustomAPI pattern: centralized error handling, loading states, timeout.
 */
/**
 * RAGbot API Client — reusable fetch wrapper with JWT auth.
 * Token lấy tự động từ /api/ragbot/test/tokens/self (init lần đầu).
 */
const API_BASE = window.location.origin + '/api/ragbot/test';
let _apiToken = null;

class RagbotAPI {
  /**
   * Lấy JWT token từ server (lazy init, cache trong memory).
   * @returns {Promise<string>} JWT Bearer token
   */
  static async _getToken() {
    if (_apiToken) return _apiToken;
    try {
      const resp = await fetch(API_BASE + '/tokens/self');
      const json = await resp.json();
      if (json?.ok && json.token) {
        _apiToken = json.token;
        return _apiToken;
      }
    } catch (e) {}
    return '';
  }

  /**
   * Core request method — gửi HTTP request có Bearer token.
   * @param {string} method - HTTP method (GET/POST/PATCH/DELETE)
   * @param {string} path - API path (append vào API_BASE)
   * @param {object} options - { data, params, headers, timeout }
   * @returns {Promise<{ok: boolean, data: any, status: number, error: string|null}>}
   */
  static async request({ method, path, data = null, params = {}, headers = {}, timeout = 120000 }) {
    const url = new URL(API_BASE + path, window.location.origin);
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined) url.searchParams.set(k, v);
    });

    const token = await this._getToken();
    const isFormData = (typeof FormData !== 'undefined') && data instanceof FormData;
    const finalHeaders = {
      ...(token ? { 'Authorization': 'Bearer ' + token } : {}),
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...headers,
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    try {
      const resp = await fetch(url.toString(), {
        method: method.toUpperCase(),
        headers: finalHeaders,
        body: data ? (isFormData ? data : JSON.stringify(data)) : undefined,
        signal: controller.signal,
      });
      clearTimeout(timer);

      const json = await resp.json().catch(() => null);

      // Auto-refresh token on 401 — retry once with fresh token
      if (resp.status === 401 && _apiToken) {
        _apiToken = null;
        const freshToken = await this._getToken();
        if (freshToken) {
          finalHeaders['Authorization'] = 'Bearer ' + freshToken;
          const retry = await fetch(url.toString(), {
            method: method.toUpperCase(),
            headers: finalHeaders,
            body: data ? (isFormData ? data : JSON.stringify(data)) : undefined,
          });
          const retryJson = await retry.json().catch(() => null);
          if (retry.ok && retryJson?.ok !== false) {
            return { ok: true, data: retryJson, status: retry.status, error: null };
          }
          const retryErr = retryJson?.detail || retryJson?.error || retryJson?.message || `HTTP ${retry.status}`;
          return { ok: false, data: retryJson, status: retry.status, error: retryErr };
        }
      }

      if (resp.ok && json?.ok !== false) {
        return { ok: true, data: json, status: resp.status, error: null };
      }

      const errMsg = json?.detail || json?.error || json?.message || `HTTP ${resp.status}`;
      return { ok: false, data: json, status: resp.status, error: errMsg };

    } catch (err) {
      clearTimeout(timer);
      if (err.name === 'AbortError') {
        return { ok: false, data: null, status: 0, error: 'Request timed out' };
      }
      return { ok: false, data: null, status: 0, error: err.message || 'Network error' };
    }
  }

  /**
   * GET request.
   * @param {string} path - API path
   * @param {object} params - Query parameters
   * @returns {Promise<{ok, data, status, error}>}
   */
  static async get(path, params = {}) {
    return this.request({ method: 'GET', path, params });
  }

  /**
   * POST request.
   * @param {string} path - API path
   * @param {object} data - Request body
   * @returns {Promise<{ok, data, status, error}>}
   */
  static async post(path, data) {
    return this.request({ method: 'POST', path, data });
  }

  /**
   * PATCH request.
   * @param {string} path - API path
   * @param {object} data - Request body
   * @returns {Promise<{ok, data, status, error}>}
   */
  static async patch(path, data) {
    return this.request({ method: 'PATCH', path, data });
  }

  /**
   * DELETE request.
   * @param {string} path - API path
   * @param {object} data - Request body (optional)
   * @returns {Promise<{ok, data, status, error}>}
   */
  static async del(path, data = null) {
    return this.request({ method: 'DELETE', path, data });
  }
}

// Expose globally
window.RagbotAPI = RagbotAPI;
