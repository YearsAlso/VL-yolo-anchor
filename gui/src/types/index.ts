/** Shared TypeScript interfaces for the VL-YOLO-Anchor GUI. */

/** A YOLO-OBB oriented bounding box (four clockwise normalized points). */
export interface OBBBox {
  cls: number;
  /** Flat [x1, y1, x2, y2, x3, y3, x4, y4], normalized to [0, 1]. */
  points: number[];
  conf: number;
}

/** An image in a task's dataset. */
export interface ImageItem {
  name: string;
  url: string;
  width?: number;
  height?: number;
}

/** Which directory a set of label boxes came from. */
export type LabelSource = "candidate_labels" | "ai_labels";

/** Per-image label payload including its source directory. */
export interface LabelResult {
  boxes: OBBBox[];
  source: LabelSource;
}

/** High-level task info. */
export interface TaskInfo {
  name: string;
  description: string;
  created?: string;
}

/** Structured training plan (mirrors config/task_template.yaml). */
export interface TaskPlan {
  task_overview?: Record<string, unknown>;
  classes: Record<string, string>;
  min_defect_pixels: number;
  conf_threshold: number;
  exclude_items: string[];
  obb_rule: string;
  split: {
    train_ratio: number;
    val_ratio: number;
    test_ratio: number;
    stratified: boolean;
  };
  model: {
    yolo_version?: string;
    imgsz?: number;
  };
  hyperparameters: Record<string, unknown>;
  inspection: {
    iou_duplicate_threshold: number;
    size_outlier_ratio: number;
    check_missing_labels: boolean;
    check_duplicate_boxes: boolean;
    check_class_errors: boolean;
    check_coords_out_of_range: boolean;
    check_modality_rule_conflict: boolean;
  };
  metrics: string[];
}

/** A single inspection error entry. */
export interface InspectionError {
  image?: string;
  bbox_index?: number | number[];
  line?: number;
  detail: string;
  suggestion?: string;
}

/** Inspection report (mirrors the backend InspectAgent output). */
export interface InspectionReport {
  task: string;
  num_images: number;
  num_boxes: number;
  errors: {
    missing_labels: InspectionError[];
    duplicate_boxes: InspectionError[];
    class_error: InspectionError[];
    coords_out_of_range: InspectionError[];
    size_anomaly: InspectionError[];
  };
  quality_score: number;
  candidate_dir: string;
}

/** Dataset export summary (split step output). */
export interface ExportSummary {
  task: string;
  splits: Record<string, number>;
  dataset_yaml: string;
  train_command: string;
}

// --------------------------------------------------------------------------- //
// Configuration, diagnostics, and metadata (mirrors the backend pydantic models
// in src/core/config_store.py, src/core/diagnostics.py and
// src/core/metadata_store.py — field names are the API contract).
// --------------------------------------------------------------------------- //

/** The two model roles: PlanAgent's text LLM and AnnotateAgent's vision model. */
export type ModelRole = "llm" | "vl";

/** Which layer supplied an effective configuration value. */
export type ConfigSource = "env" | "overrides" | "yaml" | "secrets" | "default";

/** Value types a configuration field can hold (`null` marks a masked secret). */
export type ConfigValue = string | number | boolean | null;

/** One configuration field plus where its value came from. */
export interface FieldView {
  /** Effective value; always `null` for `api_key`. */
  value: ConfigValue;
  source: ConfigSource;
  /** True when an environment variable fixed the value; the UI must not offer to edit it. */
  locked: boolean;
  locked_by: string;
  editable: boolean;
}

/** One model role's resolved configuration. */
export interface RoleView {
  role: ModelRole;
  provider: FieldView;
  base_url: FieldView;
  model: FieldView;
  timeout_s: FieldView;
  api_key: FieldView;
  has_api_key: boolean;
  /** `****3f2a` style hint; empty when no key is set or it is too short to mask. */
  api_key_masked: string;
}

/** State of the encrypted secret store. */
export interface SecretsView {
  available: boolean;
  /** Actionable explanation when `available` is false. */
  reason: string;
  /** Variable name that supplied the master key — never the key itself. */
  master_key_source: string;
  store_path: string;
}

/** Where the platform reads and writes. */
export interface StorageView {
  data_dir: FieldView;
  tasks_root: FieldView;
  logs_dir: FieldView;
  db_path: FieldView;
  db_enabled: FieldView;
  config_dir: string;
  config_path: string;
  overrides_path: string;
  config_writable: boolean;
  /** False inside a container whose `config/` comes from the image layer. */
  config_persistent: boolean;
}

/** HTTP server settings; token values are never included. */
export interface ServerView {
  host: FieldView;
  port: FieldView;
  auth_enabled: boolean;
  auth_token_source: string;
}

/** Platform-wide inference fallbacks, shown read-only. */
export interface InferenceView {
  batch_size: FieldView;
  conf_threshold: FieldView;
  min_defect_pixels: FieldView;
  enable_cpu_fallback: FieldView;
}

/** Complete effective configuration as served by `GET /api/config`. */
export interface EffectiveConfig {
  llm: RoleView;
  vl: RoleView;
  storage: StorageView;
  server: ServerView;
  inference: InferenceView;
  secrets: SecretsView;
}

/** Submitted changes for one role; `null`/omitted leaves the field untouched. */
export interface RolePatch {
  provider?: "stub" | "remote";
  base_url?: string;
  model?: string;
  /** `""` deletes the stored key; `"***"` means "leave it as it is". */
  api_key?: string;
}

/** Submitted changes for both roles. */
export interface ConfigPatch {
  llm?: RolePatch;
  vl?: RolePatch;
}

/** Why a submitted field was not written. */
export type IgnoredReason = "env_locked" | "read_only" | "not_writable" | "no_secret_store";

/** A submitted field that was not written, and why. */
export interface IgnoredField {
  role: string;
  field: string;
  reason: IgnoredReason;
}

/** Outcome of `PUT /api/config`. */
export interface WriteResult {
  /** `role -> field -> value`; a stored key is echoed as `"***"`. */
  written: Record<string, Record<string, string>>;
  ignored: IgnoredField[];
  overrides_path: string;
  restart_required: boolean;
}

/** Severity of one diagnostic check; the report's status is the worst of them. */
export type CheckStatus = "ok" | "warn" | "fail" | "skip";

/** One diagnostic check. */
export interface CheckResult {
  id: string;
  status: CheckStatus;
  summary: string;
  detail: string;
  /** Actionable fix suggestion; empty when nothing is wrong. */
  hint: string;
}

/** Result of probing one model endpoint. */
export interface ProbeResult {
  role: ModelRole;
  status: CheckStatus;
  latency_ms: number | null;
  http_status: number | null;
  message: string;
  hint: string;
}

/** Aggregated self-check report. */
export interface DiagnosticReport {
  status: CheckStatus;
  generated_at: string;
  checks: CheckResult[];
  /** Present only for a deep run. */
  probes: ProbeResult[];
}

/** One `run_step` execution, read from the on-disk audit log. */
export interface RunEntry {
  step: string;
  status: string;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  items: number | null;
  message: string;
}

/** One model call, read from the on-disk audit log. */
export interface CallEntry {
  role: string;
  provider: string;
  model: string;
  host: string;
  status: string;
  http_status: number | null;
  duration_ms: number;
  started_at: string;
}

/** A task's execution history (always served from `run_history.jsonl`). */
export interface TaskHistory {
  task: string;
  runs: RunEntry[];
  calls: CallEntry[];
  source: string;
}

/** Cross-task counts from the SQLite metadata index. */
export interface IndexStats {
  enabled: boolean;
  available: boolean;
  db_path: string;
  db_size_bytes: number;
  last_indexed_at: string;
  tasks: number;
  runs: number;
  calls: number;
  diagnostics: number;
}
