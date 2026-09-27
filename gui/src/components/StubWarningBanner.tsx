/** Permanent warning while the pipeline is configured with the offline stub. */

import { Alert } from "antd";
import { useEffect, useState } from "react";
import { errorMessage, getConfig } from "../services/api";
import type { EffectiveConfig, ModelRole } from "../types";

const ROLES: ModelRole[] = ["llm", "vl"];

/**
 * Banner that stays on screen while `provider` is `stub`.
 *
 * The stub emits deterministic fake boxes and is indistinguishable from a working
 * model unless something says so — a platform that quietly trains on fake labels
 * is worse than one that refuses to start, so the warning is permanent rather
 * than a dismissible toast. When the configuration cannot be read at all the
 * banner degrades to "cannot confirm" instead of vanishing: silence would be
 * indistinguishable from "everything is configured".
 */
export default function StubWarningBanner() {
  const [config, setConfig] = useState<EffectiveConfig | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then((loaded) => {
        if (!cancelled) {
          setConfig(loaded);
          setError("");
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(errorMessage(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message="无法确认模型配置"
        description={`读取 /api/config 失败：${error}。请确认后端已启动（start_gui 脚本），以及带鉴权时本浏览器已填入 token。`}
      />
    );
  }
  if (!config) {
    return null;
  }

  const stubbed = ROLES.filter((role) => config[role].provider.value === "stub");
  if (stubbed.length === 0) {
    return null;
  }

  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      message={`当前使用离线 stub（${stubbed.join(" / ")}）：产出的是确定性假框，禁止用于训练。`}
      description="在「设置」页把对应角色的 provider 改为 remote，并填写 base_url 与模型名；改完需重启后端生效。"
    />
  );
}
