/** Axios-based API client for the FastAPI backend (127.0.0.1:8765). */

import axios from "axios";
import type {
  ConfigPatch,
  DiagnosticReport,
  EffectiveConfig,
  ExportSummary,
  ImageItem,
  IndexStats,
  InspectionReport,
  LabelResult,
  ModelRole,
  ProbeResult,
  TaskHistory,
  TaskPlan,
  WriteResult,
} from "../types";

// API base resolution, highest priority first:
// 1. VITE_API_BASE build arg — set to "" for the Docker/nginx image so the SPA
//    calls the same-origin "/api" that nginx reverse-proxies to the backend.
// 2. Dev server — relative, so the Vite proxy forwards "/api" to the backend
//    (same-origin: no CORS preflight, no tainted canvas).
// 3. Desktop (Tauri) production — no proxy, target the local backend directly
//    and rely on the backend's CORS allowlist for the tauri origin.
export const API_BASE: string =
  import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "" : "http://127.0.0.1:8765");

/** localStorage key holding a token pasted into the UI. */
const TOKEN_STORAGE_KEY = "vl_auth_token";

/** Token baked in at build time, for a deployment that serves a fixed token. */
const BUILD_TOKEN: string = import.meta.env.VITE_AUTH_TOKEN ?? "";

const api = axios.create({
  baseURL: API_BASE,
  timeout: 120_000,
});

/**
 * Read the bearer token the backend expects.
 *
 * The build-time value wins so an operator cannot be locked out by a stale token
 * in a browser profile; otherwise a value the user pasted into this browser is
 * used. Both may be empty — the backend only requires a token when it was
 * configured with one.
 */
export function getAuthToken(): string {
  return BUILD_TOKEN || window.localStorage.getItem(TOKEN_STORAGE_KEY) || "";
}

/** Store (or clear, with an empty string) the token used for API calls. */
export function setAuthToken(token: string): void {
  if (token) {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } else {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  }
}

// Every request carries the token: the auth middleware rejects unauthenticated
// calls to everything but /api/health, and a per-call option would be forgotten
// exactly once — in the one new endpoint that needs it most.
api.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

/** True when a failure was a 401 (missing or wrong bearer token). */
export function isUnauthorized(err: unknown): boolean {
  return axios.isAxiosError(err) && err.response?.status === 401;
}

/** Human-readable message for any error thrown by this module. */
export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data as { detail?: unknown } | undefined;
    if (typeof detail?.detail === "string") {
      return detail.detail;
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

/** A 409 from `PUT /api/config`: nothing was written, and the body says why. */
export class ConfigWriteBlockedError extends Error {
  readonly result: WriteResult;

  constructor(result: WriteResult) {
    super("没有任何字段被写入");
    this.name = "ConfigWriteBlockedError";
    this.result = result;
  }
}

/** A 422 from `PUT /api/config`: the merged configuration would be invalid. */
export class ConfigValidationError extends Error {
  readonly errors: string[];

  constructor(errors: string[]) {
    super(errors.join(" "));
    this.name = "ConfigValidationError";
    this.errors = errors;
  }
}

/** List all task names. */
export async function listTasks(): Promise<string[]> {
  const res = await api.get<string[]>("/api/tasks");
  return res.data;
}

/** Create a new task. */
export async function createTask(name: string, description: string): Promise<{ name: string; path: string }> {
  const res = await api.post("/api/tasks", { name, description });
  return res.data;
}

/** Run one pipeline step (plan/annotate/inspect/split). */
export async function runStep(
  task: string,
  step: "plan" | "annotate" | "inspect" | "split",
): Promise<Record<string, unknown>> {
  const res = await api.post(`/api/tasks/${encodeURIComponent(task)}/step`, { step });
  return res.data.result;
}

/** Convenience wrappers for the four steps. */
export const runPlan = (task: string) => runStep(task, "plan");
export const runAnnotate = (task: string) => runStep(task, "annotate");
export const runInspect = (task: string) => runStep(task, "inspect");
export const runSplit = (task: string) => runStep(task, "split");

/** Fetch the latest inspection report for a task. */
export async function getReport(task: string): Promise<InspectionReport> {
  const res = await api.get<{ report: InspectionReport }>(`/api/tasks/${encodeURIComponent(task)}/report`);
  return res.data.report;
}

/** Fetch the training plan (from the task config) — plan step result passthrough. */
export async function getPlan(task: string): Promise<TaskPlan> {
  const res = await api.get(`/api/tasks/${encodeURIComponent(task)}/plan`);
  return res.data as TaskPlan;
}

/** List images in a task. */
export async function listImages(task: string): Promise<ImageItem[]> {
  const res = await api.get<ImageItem[]>(`/api/tasks/${encodeURIComponent(task)}/images`);
  return res.data;
}

/** Build an absolute image URL from the API-relative one. */
export function imageUrl(url: string): string {
  return url.startsWith("http") ? url : `${API_BASE}${url}`;
}

/** Fetch the export summary produced by the split step. */
export async function getExportSummary(task: string): Promise<ExportSummary> {
  const res = await api.get(`/api/tasks/${encodeURIComponent(task)}/export`);
  return res.data as ExportSummary;
}

/** List images in a task that have a label file (candidate or AI). */
export async function listLabeledImages(task: string): Promise<string[]> {
  const res = await api.get<{ images: string[] }>(`/api/tasks/${encodeURIComponent(task)}/labels`);
  return res.data.images;
}

/** Fetch the OBB boxes for one image (candidate labels take priority). */
export async function getLabelBoxes(task: string, imageName: string): Promise<LabelResult> {
  const res = await api.get<LabelResult & { image: string }>(
    `/api/tasks/${encodeURIComponent(task)}/labels/${encodeURIComponent(imageName)}`,
  );
  return { boxes: res.data.boxes, source: res.data.source };
}

/** Fetch the effective configuration with per-field provenance. */
export async function getConfig(): Promise<EffectiveConfig> {
  const res = await api.get<EffectiveConfig>("/api/config");
  return res.data;
}

/**
 * Apply configuration changes.
 *
 * @throws ConfigWriteBlockedError when nothing could be written (409)
 * @throws ConfigValidationError when the merged configuration is invalid (422)
 */
export async function putConfig(patch: ConfigPatch): Promise<WriteResult> {
  try {
    const res = await api.put<WriteResult>("/api/config", patch);
    return res.data;
  } catch (err) {
    if (axios.isAxiosError(err)) {
      if (err.response?.status === 409) {
        throw new ConfigWriteBlockedError(err.response.data as WriteResult);
      }
      if (err.response?.status === 422) {
        const detail = err.response.data as { detail?: { errors?: string[] } };
        throw new ConfigValidationError(detail.detail?.errors ?? ["提交的配置未通过校验。"]);
      }
    }
    throw err;
  }
}

/** Probe one model endpoint with a single minimal request. */
export async function testConfig(request: {
  role: ModelRole;
  base_url?: string;
  model?: string;
  api_key?: string;
}): Promise<ProbeResult> {
  const res = await api.post<ProbeResult>("/api/config/test", request);
  return res.data;
}

/** Run the backend self-check (`deep` also probes the endpoints). */
export async function getDiagnostics(deep = false): Promise<DiagnosticReport> {
  const res = await api.get<DiagnosticReport>("/api/diagnostics", { params: { deep } });
  return res.data;
}

/** Fetch a task's execution history, read from the on-disk audit logs. */
export async function getHistory(task: string, limit = 50): Promise<TaskHistory> {
  const res = await api.get<TaskHistory>(`/api/tasks/${encodeURIComponent(task)}/history`, {
    params: { limit },
  });
  return res.data;
}

/** Fetch cross-task counts from the metadata index (503 when the index is unusable). */
export async function getIndexStats(): Promise<IndexStats> {
  const res = await api.get<IndexStats>("/api/index/stats");
  return res.data;
}
