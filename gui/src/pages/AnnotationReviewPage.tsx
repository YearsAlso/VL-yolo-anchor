/** Page 2: run annotation and review OBB boxes on the canvas. */

import { Alert, Badge, Button, Card, Empty, List, message, Select, Space, Spin, Tag } from "antd";
import { useEffect, useState } from "react";
import OBBCanvas from "../components/OBBCanvas";
import {
  getLabelBoxes,
  imageUrl,
  listImages,
  listLabeledImages,
  listTasks,
  runAnnotate,
} from "../services/api";
import type { ImageItem, LabelSource, OBBBox } from "../types";

/** Annotation Review tab: auto-annotate then inspect boxes visually. */
export default function AnnotationReviewPage() {
  const [tasks, setTasks] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | undefined>();
  const [images, setImages] = useState<ImageItem[]>([]);
  const [labeledSet, setLabeledSet] = useState<Set<string>>(new Set());
  const [current, setCurrent] = useState<ImageItem | undefined>();
  const [boxes, setBoxes] = useState<OBBBox[]>([]);
  const [source, setSource] = useState<LabelSource | undefined>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listTasks()
      .then(setTasks)
      .catch((err) => message.error(`Failed to load tasks: ${(err as Error).message}`));
  }, []);

  useEffect(() => {
    if (!selected) {
      setImages([]);
      setLabeledSet(new Set());
      return;
    }
    void Promise.all([listImages(selected), listLabeledImages(selected)]).then(([imgs, labeled]) => {
      setImages(imgs);
      setLabeledSet(new Set(labeled));
      setCurrent(imgs[0]);
    });
  }, [selected]);

  useEffect(() => {
    if (!selected || !current) {
      setBoxes([]);
      setSource(undefined);
      return;
    }
    // Guard against races: a slow response for a previously selected image
    // must never overwrite the boxes of the currently selected one.
    let cancelled = false;
    getLabelBoxes(selected, current.name)
      .then((res) => {
        if (cancelled) {
          return;
        }
        setBoxes(res.boxes);
        setSource(res.source);
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setBoxes([]);
        setSource(undefined);
      });
    return () => {
      cancelled = true;
    };
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
      const labeled = await listLabeledImages(selected);
      setLabeledSet(new Set(labeled));
      if (current) {
        try {
          const res = await getLabelBoxes(selected, current.name);
          setBoxes(res.boxes);
          setSource(res.source);
        } catch {
          setBoxes([]);
          setSource(undefined);
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

      <Card
        title={
          <Space>
            <span>Canvas</span>
            {current && <strong>{current.name}</strong>}
            {source && (
              <Tag color={source === "candidate_labels" ? "gold" : "blue"}>
                {source === "candidate_labels" ? "candidate_labels (edited)" : "ai_labels (raw)"}
              </Tag>
            )}
          </Space>
        }
      >
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
                <Badge status={labeledSet.has(item.name) ? "success" : "default"} text={item.name} />
              </List.Item>
            )}
          />
        </Space>
      </Card>
    </Space>
  );
}
