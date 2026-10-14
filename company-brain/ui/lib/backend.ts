// Single entry point for server-side calls to the FastAPI backend.
//
// Mock mode runs lib/mock/backend.ts in-process instead, so the dashboard
// works with only Supabase behind it (demo deploys, or while the backend
// is down). It's on when FLOWITHM_MOCK_BACKEND is truthy, or when it's
// unset and FLOWITHM_API_URL is unset too. Set FLOWITHM_MOCK_BACKEND=false
// to force the real backend.
import { handleMockRequest } from "./mock/backend";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export function isMockBackend(): boolean {
  const flag = (process.env.FLOWITHM_MOCK_BACKEND || "").trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(flag)) return true;
  if (["0", "false", "no", "off"].includes(flag)) return false;
  return !process.env.FLOWITHM_API_URL;
}

/** fetch() against the backend. `path` starts with "/" and may carry a
 *  query string, e.g. "/history?limit=20". */
export function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  if (isMockBackend()) return handleMockRequest(path, init);
  return fetch(`${API_URL}${path}`, init);
}
