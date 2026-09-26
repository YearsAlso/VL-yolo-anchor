/** Axios-based API client for the FastAPI backend (127.0.0.1:8765). */

import axios from "axios";
import type { ExportSummary, ImageItem, InspectionReport, OBBBox, TaskPlan } from "../types";

const api = axios.create({
  baseURL: "http://127.0.0.1:8765",
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
  return url.startsWith("http") ? url : `http://127.0.0.1:8765${url}`;
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
export async function getLabelBoxes(task: string, imageName: string): Promise<OBBBox[]> {
  const res = await api.get<{ image: string; boxes: OBBBox[] }>(
    `/api/tasks/${encodeURIComponent(task)}/labels/${encodeURIComponent(imageName)}`,
  );
  return res.data.boxes;
}
