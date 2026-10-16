import { API_BASE_URL } from './config.js';

export async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
    });
  } catch {
    throw new Error(`Cannot reach the backend at ${API_BASE_URL}. Start FastAPI and retry.`);
  }
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return data;
}

export const getTwin = () => request('/api/twin/state');
export const getGeoJSON = () => request('/api/twin/geojson');
export const getStatus = () => request('/api/simulation/status');
export const getEvents = () => request('/api/simulation/events');
export const postJSON = (path, body) => request(path, { method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
export const websocketURL = () => `${API_BASE_URL.replace(/^http/, 'ws')}/ws/updates`;
