/** Renders a backend diagnostic report (the `run.py doctor` result). */

import { Alert, Button, Card, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { CheckResult, CheckStatus, DiagnosticReport } from "../types";

/** Presentation for one check status: Tag colour plus a Chinese label. */
export const STATUS_META: Record<CheckStatus, { color: string; label: string }> = {
  ok: { color: "success", label: "通过" },
  warn: { color: "warning", label: "警告" },
  fail: { color: "error", label: "失败" },
  skip: { color: "default", label: "跳过" },
};

/** A coloured status tag, shared by every panel that reports a check result. */
export function StatusTag({ status }: { status: CheckStatus }) {
  const meta = STATUS_META[status];
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

/** Props for {@link DiagnosticsPanel}. */
interface DiagnosticsPanelProps {
  /** Latest report, or `null` before the first run. */
  report: DiagnosticReport | null;
  loading?: boolean;
  /** Fetch a fresh report; `deep` also probes the model endpoints (network calls). */
  onRefresh: (deep: boolean) => void;
}

/** Self-check panel: check list, hints, and the optional connectivity probes. */
export default function DiagnosticsPanel({ report, loading, onRefresh }: DiagnosticsPanelProps) {
  return (
    <Card
      title={
        <Space>
          平台自检
          {report && <StatusTag status={report.status} />}
        </Space>
      }
      extra={
        <Space>
          <Button size="small" loading={loading} onClick={() => onRefresh(false)}>
            自检
          </Button>
          <Tooltip title="同时探测 llm/vl 两个端点（会发起网络请求，耗时数十秒）">
            <Button size="small" loading={loading} onClick={() => onRefresh(true)}>
              深度自检
            </Button>
          </Tooltip>
        </Space>
      }
    >
      {!report && <Alert type="info" showIcon message="尚未运行自检。" />}
      {report && (
        <Space direction="vertical" style={{ width: "100%" }}>
          {report.status === "fail" && (
            <Alert
              type="error"
              showIcon
              message="存在失败项，平台可能无法产出真实标注。"
              description="按下方「建议」逐项修复后重新自检。"
            />
          )}
          <Table<CheckResult>
            size="small"
            rowKey="id"
            pagination={false}
            dataSource={report.checks}
            columns={[
              { title: "状态", dataIndex: "status", width: 90, render: (s: CheckStatus) => <StatusTag status={s} /> },
              { title: "检查项", dataIndex: "id", width: 240 },
              {
                title: "结论",
                dataIndex: "summary",
                render: (_: string, row: CheckResult) => (
                  <>
                    <div>{row.summary}</div>
                    {row.hint && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {row.hint}
                      </Typography.Text>
                    )}
                    {row.detail && (
                      <div>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }} copyable>
                          {row.detail}
                        </Typography.Text>
                      </div>
                    )}
                  </>
                ),
              },
            ]}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            生成时间：{report.generated_at}
          </Typography.Text>
          {report.probes.length > 0 && (
            <Table
              size="small"
              rowKey={(row) => `${row.role}-${row.latency_ms ?? 0}`}
              pagination={false}
              title={() => "端点探测"}
              dataSource={report.probes}
              columns={[
                { title: "角色", dataIndex: "role", width: 90 },
                {
                  title: "结果",
                  dataIndex: "status",
                  width: 90,
                  render: (s: CheckStatus) => <StatusTag status={s} />,
                },
                { title: "耗时(ms)", dataIndex: "latency_ms", width: 100 },
                { title: "HTTP", dataIndex: "http_status", width: 80 },
                { title: "信息", dataIndex: "message" },
              ]}
            />
          )}
        </Space>
      )}
    </Card>
  );
}
