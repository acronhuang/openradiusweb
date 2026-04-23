import axios from 'axios';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

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
