/** Page 4: dataset split/export and ready-to-run training command. */

import { Alert, Button, Card, message, Select, Space, Statistic, Typography } from "antd";
import { useState } from "react";
import { getExportSummary, listTasks, runSplit } from "../services/api";
import type { ExportSummary } from "../types";

const { Paragraph } = Typography;

/** Training Export tab. */
export default function TrainingExportPage() {
  const [tasks, setTasks] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | undefined>();
  const [summary, setSummary] = useState<ExportSummary | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSplit = async () => {
    if (!selected) {
      message.warning("Please select a task first.");
      return;
    }
    setBusy(true);
    try {
      await runSplit(selected);
      setSummary(await getExportSummary(selected));
      message.success("Dataset exported.");
    } catch (err) {
      message.error(`Split/export failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card
        title="Dataset export"
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
            <Button type="primary" loading={busy} onClick={handleSplit}>
              Run Split & Export
            </Button>
          </Space>
        }
      >
        {summary ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Space size="large">
              {Object.entries(summary.splits).map(([splitName, count]) => (
                <Statistic key={splitName} title={splitName} value={count} />
              ))}
            </Space>
            <Alert type="success" showIcon message={`data.yaml: ${summary.dataset_yaml}`} />
            <Card type="inner" title="Training command">
              <Paragraph copyable code>
                {summary.train_command}
              </Paragraph>
            </Card>
          </Space>
        ) : (
          <Alert type="info" showIcon message="Run the split step to export a ready-to-train YOLO-OBB dataset." />
        )}
      </Card>
    </Space>
  );
}
