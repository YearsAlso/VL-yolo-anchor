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
