/** Page 3: run inspection and browse the error report. */

import { Alert, Button, Card, Col, message, Progress, Row, Select, Space, Statistic, Table, Tabs, Tag } from "antd";
import { useState } from "react";
import { getReport, listTasks, runInspect } from "../services/api";
import type { InspectionError, InspectionReport } from "../types";

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

/** Inspection Report tab. */
export default function InspectionReportPage() {
  const [tasks, setTasks] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | undefined>();
  const [report, setReport] = useState<InspectionReport | null>(null);
  const [busy, setBusy] = useState(false);

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
      message.error(`Inspect failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
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
              onChange={setSelected}
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
    </Space>
  );
}
