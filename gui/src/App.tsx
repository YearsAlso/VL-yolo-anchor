import { Tabs } from "antd";
import StubWarningBanner from "./components/StubWarningBanner";
import AnnotationReviewPage from "./pages/AnnotationReviewPage";
import InspectionReportPage from "./pages/InspectionReportPage";
import SettingsPage from "./pages/SettingsPage";
import TaskPlanPage from "./pages/TaskPlanPage";
import TrainingExportPage from "./pages/TrainingExportPage";

/** Root app: five-tab layout over the FastAPI backend. */
export default function App() {
  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: "0 auto" }}>
      <h2>VL-YOLO-Anchor — YOLO-OBB 训练计划生成与智能标注平台</h2>
      {/* Above the tabs: a stub-configured platform produces fake labels, and
          that has to be visible from every tab, not just the settings one. */}
      <StubWarningBanner />
      <Tabs
        defaultActiveKey="plan"
        items={[
          { key: "plan", label: "任务与计划", children: <TaskPlanPage /> },
          { key: "review", label: "标注审核", children: <AnnotationReviewPage /> },
          { key: "inspect", label: "质检报告", children: <InspectionReportPage /> },
          { key: "export", label: "训练导出", children: <TrainingExportPage /> },
          { key: "settings", label: "设置", children: <SettingsPage /> },
        ]}
      />
    </div>
  );
}
