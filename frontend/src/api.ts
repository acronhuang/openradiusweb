import axios from 'axios';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

/**
 * Pull the backend's `detail` message out of an axios error if present,
 * otherwise return the supplied fallback. Use to avoid swallowing
 * actionable server-side messages like
 *   "Cannot delete: LDAP server is referenced by 3 RADIUS realm(s)..."
 *   "Realm 'corp' already exists"
 *   "Account temporarily locked due to repeated failed login attempts."
 *
 * Handles two response shapes:
 *   - DomainError → {"detail": "human-readable string"}
 *   - FastAPI 422 → {"detail": [{"loc":["body","field"], "msg":"...", "type":"..."}]}
 *     → flattened to "field: msg; field: msg"
 */
export function extractErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === 'string' && detail.length > 0) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const parts = detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = Array.isArray(d?.loc) ? d.loc.slice(1).join('.') : '';
        const msg = typeof d?.msg === 'string' ? d.msg : '';
        return field && msg ? `${field}: ${msg}` : (msg || field);
      })
      .filter(Boolean);
    if (parts.length > 0) return parts.join('; ');
  }
  return fallback;
}

// Add JWT token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('orw_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle 401 responses
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('orw_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export default api;
