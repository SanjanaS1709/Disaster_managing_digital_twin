const configuredBase = window.FIELDOPS_API_URL;
const apiHost = location.hostname === 'localhost' ? '127.0.0.1' : location.hostname;
export const API_BASE_URL = configuredBase || `${location.protocol}//${apiHost}:8000`;
export const IST_OFFSET_MINUTES = 330;
