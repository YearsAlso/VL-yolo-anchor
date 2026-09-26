/** Page 2: run annotation and review OBB boxes on the canvas. */

import { Alert, Button, Card, Empty, List, message, Select, Space, Spin } from "antd";
import { useEffect, useState } from "react";
import OBBCanvas from "../components/OBBCanvas";
import { getLabelBoxes, imageUrl, listImages, listTasks, runAnnotate } from "../services/api";
import type { ImageItem, OBBBox } from "../types";

/** Annotation Review tab: auto-annotate then inspect boxes visually. */
export default function AnnotationReviewPage() {
  const [tasks, setTasks] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | undefined>();
  const [images, setImages] = useState<ImageItem[]>([]);
  const [current, setCurrent] = useState<ImageItem | undefined>();
  const [boxes, setBoxes] = useState<OBBBox[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void listTasks().then(setTasks);
  }, []);

  useEffect(() => {
    if (!selected) {
      return;
    }
    void listImages(selected).then((imgs) => {
      setImages(imgs);
      setCurrent(imgs[0]);
    });
  }, [selected]);

  useEffect(() => {
    if (!selected || !current) {
      setBoxes([]);
      return;
    }
    void getLabelBoxes(selected, current.name)
      .then(setBoxes)
      .catch(() => setBoxes([])); // 404 = no label yet -> empty canvas
  }, [selected, current]);

  const handleAnnotate = async () => {
    if (!selected) {
      message.warning("Please select a task first.");
      return;
    }
    setBusy(true);
    try {
      await runAnnotate(selected);
      message.success("Annotation finished. Labels written to ai_labels/.");
      if (current) {
        try {
          setBoxes(await getLabelBoxes(selected, current.name));
        } catch {
          setBoxes([]);
        }
      }
    } catch (err) {
      message.error(`Annotate failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card
        title="Annotate & Review"
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
            <Button type="primary" loading={busy} onClick={handleAnnotate}>
              Run Annotate
            </Button>
          </Space>
        }
      >
        {busy && <Spin />}
        {!busy && !selected && <Alert type="info" showIcon message="Select a task, then run annotation." />}
      </Card>

      <Card title="Canvas">
        <Space align="start" size="large">
          <div>
            {current ? (
              <OBBCanvas imageUrl={imageUrl(current.url)} boxes={boxes} />
            ) : (
              <Empty description="No images in task" />
            )}
          </div>
          <List
            size="small"
            header={<strong>Images ({images.length})</strong>}
            style={{ width: 260, maxHeight: 480, overflow: "auto" }}
            dataSource={images}
            bordered
            renderItem={(item) => (
              <List.Item
                onClick={() => setCurrent(item)}
                style={{ cursor: "pointer", background: current?.name === item.name ? "#e6f4ff" : undefined }}
              >
                {item.name}
              </List.Item>
            )}
          />
        </Space>
      </Card>
    </Space>
  );
}
