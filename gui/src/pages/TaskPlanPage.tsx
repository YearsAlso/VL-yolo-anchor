/** Page 1: task creation and plan generation. */

import { InboxOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Input, message, Select, Space, Spin, Upload } from "antd";
import { useState } from "react";
import { createTask, listTasks, runPlan } from "../services/api";
import type { TaskPlan } from "../types";

/** Task & Plan tab: describe the task, generate the YOLO-OBB training plan. */
export default function TaskPlanPage() {
  const [tasks, setTasks] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<string | undefined>();
  const [plan, setPlan] = useState<TaskPlan | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshTasks = async () => {
    setTasks(await listTasks());
  };

  const handleCreate = async () => {
    if (!name.trim()) {
      message.warning("Please enter a task name.");
      return;
    }
    setBusy(true);
    try {
      await createTask(name.trim(), description);
      message.success(`Task '${name.trim()}' created.`);
      await refreshTasks();
      setSelected(name.trim());
    } catch (err) {
      message.error(`Create failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handlePlan = async () => {
    if (!selected) {
      message.warning("Please select a task first.");
      return;
    }
    setBusy(true);
    try {
      const result = (await runPlan(selected)) as unknown as TaskPlan;
      setPlan(result);
      message.success("Plan generated.");
    } catch (err) {
      message.error(`Plan failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card title="1. Describe your task">
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Input
            placeholder="Task name, e.g. pv_crack_obb"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input.TextArea
            rows={4}
            placeholder="Natural language: industry, imaging modality (EL/PL/visible/infrared/X-ray), defect types..."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <Space>
            <Button type="primary" loading={busy} onClick={handleCreate}>
              Create Task
            </Button>
            <Select
              style={{ minWidth: 220 }}
              placeholder="Existing tasks"
              value={selected}
              onChange={setSelected}
              options={tasks.map((t) => ({ value: t, label: t }))}
              onDropdownVisibleChange={(open) => open && void refreshTasks()}
            />
          </Space>
        </Space>
      </Card>

      <Card title="2. Dataset images (optional placeholder upload)">
        <Upload.Dragger multiple beforeUpload={() => false} maxCount={50}>
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">Click or drag images here (backend import via CLI/API)</p>
        </Upload.Dragger>
      </Card>

      <Card
        title="3. Generated training plan"
        extra={
          <Button type="primary" loading={busy} onClick={handlePlan}>
            Generate Plan
          </Button>
        }
      >
        {busy && <Spin />}
        {!busy && !plan && <Alert type="info" showIcon message="Create a task and click Generate Plan." />}
        {!busy && plan && (
          <Descriptions bordered column={2} size="small">
            <Descriptions.Item label="Classes" span={2}>
              {Object.entries(plan.classes ?? {})
                .map(([id, clsName]) => `${id}: ${clsName}`)
                .join(", ")}
            </Descriptions.Item>
            <Descriptions.Item label="OBB rule">{plan.obb_rule}</Descriptions.Item>
            <Descriptions.Item label="Conf threshold">{plan.conf_threshold}</Descriptions.Item>
            <Descriptions.Item label="Min defect pixels">{plan.min_defect_pixels}</Descriptions.Item>
            <Descriptions.Item label="Model">{plan.model?.yolo_version}</Descriptions.Item>
            <Descriptions.Item label="Split">
              train {plan.split?.train_ratio} / val {plan.split?.val_ratio} / test {plan.split?.test_ratio}
            </Descriptions.Item>
            <Descriptions.Item label="Metrics">{(plan.metrics ?? []).join(", ")}</Descriptions.Item>
          </Descriptions>
        )}
      </Card>
    </Space>
  );
}
