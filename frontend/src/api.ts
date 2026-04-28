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
 */
export function extractErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  return typeof detail === 'string' && detail.length > 0 ? detail : fallback;
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
