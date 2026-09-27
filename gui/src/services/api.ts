/** Axios-based API client for the FastAPI backend (127.0.0.1:8765). */

import axios from "axios";
import type { ExportSummary, ImageItem, InspectionReport, LabelResult, TaskPlan } from "../types";

// API base resolution, highest priority first:
// 1. VITE_API_BASE build arg — set to "" for the Docker/nginx image so the SPA
//    calls the same-origin "/api" that nginx reverse-proxies to the backend.
// 2. Dev server — relative, so the Vite proxy forwards "/api" to the backend
//    (same-origin: no CORS preflight, no tainted canvas).
// 3. Desktop (Tauri) production — no proxy, target the local backend directly
//    and rely on the backend's CORS allowlist for the tauri origin.
export const API_BASE: string =
  import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "" : "http://127.0.0.1:8765");

const api = axios.create({
  baseURL: API_BASE,
  timeout: 120_000,
});

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
