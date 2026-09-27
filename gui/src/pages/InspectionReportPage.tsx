/** Page 3: run inspection and browse the error report. */

import { Alert, Button, Card, Col, message, Progress, Row, Select, Space, Statistic, Table, Tabs, Tag } from "antd";
import { useState } from "react";
import { errorMessage, getHistory, getIndexStats, getReport, listTasks, runInspect } from "../services/api";
import type { CallEntry, InspectionError, InspectionReport, IndexStats, RunEntry, TaskHistory } from "../types";

const ERROR_KEYS: { key: keyof InspectionReport["errors"]; title: string }[] = [
  { key: "missing_labels", title: "Missing labels" },
  { key: "duplicate_boxes", title: "Duplicate boxes" },
  { key: "class_error", title: "Class errors" },
  { key: "coords_out_of_range", title: "Coords out of range" },
  { key: "size_anomaly", title: "Size anomalies" },
];

/** Error table for one error category. */
function ErrorTable({ errors }: { errors: InspectionError[] }) {
  return (
    <Table
      size="small"
      rowKey={(_, i) => String(i)}
      dataSource={errors}
      pagination={false}
      columns={[
        { title: "Image", dataIndex: "image", width: 180 },
        { title: "Box/Line", dataIndex: "bbox_index", width: 100, render: (v: unknown) => JSON.stringify(v) ?? "-" },
        { title: "Detail", dataIndex: "detail" },
        { title: "Suggestion", dataIndex: "suggestion" },
      ]}
      locale={{ emptyText: "No errors" }}
    />
  );
}

/** Tag colour for an audit-log status (`ok` / `error` / `fail` / `stub`). */
function logStatusColor(status: string): string {
  if (status === "ok") {
    return "success";
  }
  if (status === "stub") {
    return "warning";
  }
  if (status === "error" || status === "fail") {
    return "error";
  }
  return "default";
}

/** Run and model-call tables backed by the on-disk audit logs. */
function HistoryTables({ history }: { history: TaskHistory }) {
  return (
    <Tabs
      items={[
        {
          key: "runs",
          label: (
            <span>
              Runs <Tag>{history.runs.length}</Tag>
            </span>
          ),
          children: (
            <Table<RunEntry>
              size="small"
              rowKey={(row) => `${row.step}-${row.started_at}`}
              dataSource={history.runs}
              pagination={false}
              locale={{ emptyText: "No runs recorded" }}
              columns={[
                { title: "Step", dataIndex: "step", width: 110 },
                {
                  title: "Status",
                  dataIndex: "status",
                  width: 100,
                  render: (s: string) => <Tag color={logStatusColor(s)}>{s}</Tag>,
                },
                { title: "Started (UTC)", dataIndex: "started_at", width: 210 },
                { title: "Duration (ms)", dataIndex: "duration_ms", width: 120 },
                { title: "Items", dataIndex: "items", width: 80 },
                { title: "Message", dataIndex: "message" },
              ]}
            />
          ),
        },
        {
          key: "calls",
          label: (
            <span>
              Model calls <Tag>{history.calls.length}</Tag>
            </span>
          ),
          children: (
            <Table<CallEntry>
              size="small"
              rowKey={(row) => `${row.role}-${row.started_at}-${row.duration_ms}`}
              dataSource={history.calls}
              pagination={false}
              locale={{ emptyText: "No model calls recorded" }}
              columns={[
                { title: "Role", dataIndex: "role", width: 70 },
                { title: "Provider", dataIndex: "provider", width: 90 },
                { title: "Model", dataIndex: "model", width: 180 },
                { title: "Host", dataIndex: "host", width: 180 },
                {
                  title: "Status",
                  dataIndex: "status",
                  width: 90,
                  render: (s: string) => <Tag color={logStatusColor(s)}>{s}</Tag>,
                },
                { title: "HTTP", dataIndex: "http_status", width: 70 },
                { title: "Duration (ms)", dataIndex: "duration_ms", width: 120 },
                { title: "Started (UTC)", dataIndex: "started_at", width: 210 },
              ]}
            />
          ),
        },
      ]}
    />
  );
}

/** Inspection Report tab. */
export default function InspectionReportPage() {
  const [tasks, setTasks] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | undefined>();
  const [report, setReport] = useState<InspectionReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<TaskHistory | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [indexStats, setIndexStats] = useState<IndexStats | null>(null);
  const [indexError, setIndexError] = useState("");

  /**
   * Load history for a task.
   *
   * History comes from the on-disk audit logs, so it works with no database at
   * all; the cross-task index is a separate, optional fetch whose 503 (disabled
   * or broken SQLite) only greys out one line instead of failing the page.
   */
  const loadHistory = async (task: string) => {
    setHistoryError("");
    setIndexError("");
    try {
      setHistory(await getHistory(task));
    } catch (err) {
      setHistory(null);
      setHistoryError(errorMessage(err));
    }
    try {
      setIndexStats(await getIndexStats());
    } catch (err) {
      setIndexStats(null);
      setIndexError(errorMessage(err));
    }
  };

  const selectTask = (task: string) => {
    setSelected(task);
    void loadHistory(task);
  };

  const handleInspect = async () => {
    if (!selected) {
      message.warning("Please select a task first.");
      return;
    }
    setBusy(true);
    try {
      await runInspect(selected);
      setReport(await getReport(selected));
      message.success("Inspection finished.");
    } catch (err) {
      message.error(`Inspect failed: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
      await loadHistory(selected);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card
        title="Inspection"
        extra={
          <Space>
            <Select
              style={{ minWidth: 220 }}
              placeholder="Select task"
              value={selected}
              onChange={selectTask}
              options={tasks.map((t) => ({ value: t, label: t }))}
              onDropdownVisibleChange={(open) => open && void listTasks().then(setTasks)}
            />
            <Button type="primary" loading={busy} onClick={handleInspect}>
              Run Inspect
            </Button>
          </Space>
        }
      >
        {report && (
          <>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="Images" value={report.num_images} />
              </Col>
              <Col span={6}>
                <Statistic title="Boxes" value={report.num_boxes} />
              </Col>
              <Col span={6}>
                <Statistic title="Total errors" value={Object.values(report.errors).reduce((s, e) => s + e.length, 0)} />
              </Col>
              <Col span={6}>
                <Statistic
                  title="Quality score"
                  value={report.quality_score * 100}
                  precision={1}
                  suffix="%"
                />
              </Col>
            </Row>
            <Progress percent={report.quality_score * 100} status={report.quality_score > 0.9 ? "success" : "exception"} />
          </>
        )}
        {!report && <Alert type="info" showIcon message="Run inspection to generate a report." />}
      </Card>

      {report && (
        <Card title="Errors by category">
          <Tabs
            items={ERROR_KEYS.map(({ key, title }) => ({
              key,
              label: (
                <span>
                  {title} <Tag>{report.errors[key].length}</Tag>
                </span>
              ),
              children: <ErrorTable errors={report.errors[key]} />,
            }))}
          />
        </Card>
      )}

      <Card
        title="Execution history"
        extra={
          <Button size="small" disabled={!selected} onClick={() => selected && void loadHistory(selected)}>
            Refresh
          </Button>
        }
      >
        {historyError && (
          <Alert type="warning" showIcon message="Could not read execution history" description={historyError} />
        )}
        {history && (
          <>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`Source: ${history.source}（磁盘审计日志，与索引库无关）`}
            />
            <HistoryTables history={history} />
          </>
        )}
        {!history && !historyError && (
          <Alert type="info" showIcon message="Select a task to see its run and model-call history." />
        )}
        {indexError ? (
          <Alert
            style={{ marginTop: 12 }}
            type="warning"
            showIcon
            message="Metadata index unavailable"
            description={`${indexError}。索引只是可重建的投影，缺失时任务流程与历史记录仍然可用。`}
          />
        ) : (
          indexStats && (
            <Alert
              style={{ marginTop: 12 }}
              type={indexStats.available ? "success" : "warning"}
              showIcon
              message={`索引库 ${indexStats.db_path}：tasks=${indexStats.tasks} runs=${indexStats.runs} calls=${indexStats.calls} diagnostics=${indexStats.diagnostics}`}
              description={`最近索引时间：${indexStats.last_indexed_at || "（无）"}`}
            />
          )
        )}
      </Card>
    </Space>
  );
}
