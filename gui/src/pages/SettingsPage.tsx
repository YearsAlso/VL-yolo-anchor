/** Page 5: view and edit the effective configuration (settings onboarding). */

import { LockOutlined } from "@ant-design/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Input,
  Row,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useState } from "react";
import DiagnosticsPanel from "../components/DiagnosticsPanel";
import {
  ConfigValidationError,
  ConfigWriteBlockedError,
  errorMessage,
  getAuthToken,
  getConfig,
  getDiagnostics,
  isUnauthorized,
  putConfig,
  setAuthToken,
  testConfig,
} from "../services/api";
import type {
  ConfigPatch,
  DiagnosticReport,
  EffectiveConfig,
  FieldView,
  IgnoredReason,
  ModelRole,
  ProbeResult,
  RolePatch,
  RoleView,
  WriteResult,
} from "../types";

const ROLES: ModelRole[] = ["llm", "vl"];

const ROLE_LABEL: Record<ModelRole, string> = {
  llm: "llm（PlanAgent：任务描述 → 训练计划）",
  vl: "vl（AnnotateAgent：图像 → OBB 标注）",
};

const SOURCE_LABEL: Record<FieldView["source"], string> = {
  env: "环境变量",
  overrides: "设置页",
  yaml: "global.yaml",
  secrets: "密文库",
  default: "内置默认",
};

const IGNORED_REASON_LABEL: Record<IgnoredReason, string> = {
  env_locked: "被环境变量锁定",
  read_only: "配置目录只读",
  not_writable: "写入失败（目录不可写）",
  no_secret_store: "密文库不可用",
};

/** Editable state of one role's form. */
interface RoleDraft {
  provider: "stub" | "remote";
  base_url: string;
  model: string;
  /** Typed value; empty means "leave the stored key alone" unless `clearKey`. */
  api_key: string;
  /** Set by the 「清除」 button to submit an empty key (delete). */
  clearKey: boolean;
}

/** Build a form draft from the loaded configuration. */
function draftOf(view: RoleView): RoleDraft {
  return {
    provider: view.provider.value === "remote" ? "remote" : "stub",
    base_url: String(view.base_url.value ?? ""),
    model: String(view.model.value ?? ""),
    api_key: "",
    clearKey: false,
  };
}

/** Source badge for one field. */
function SourceTag({ field }: { field: FieldView }) {
  return <Tag color={field.source === "default" ? "default" : "blue"}>{SOURCE_LABEL[field.source]}</Tag>;
}

/** A lock marker with an explanation, shown next to environment-locked fields. */
function LockedHint({ field }: { field: FieldView }) {
  if (!field.locked) {
    return null;
  }
  return (
    <Tooltip title={`该字段由环境变量 ${field.locked_by} 固定，设置页无法修改。`}>
      <Typography.Text type="secondary">
        <LockOutlined /> {field.locked_by}
      </Typography.Text>
    </Tooltip>
  );
}

/** Settings tab: model roles, secret handling, self-check. */
export default function SettingsPage() {
  const [config, setConfig] = useState<EffectiveConfig | null>(null);
  const [drafts, setDrafts] = useState<Record<ModelRole, RoleDraft> | null>(null);
  const [loadError, setLoadError] = useState("");
  const [needsToken, setNeedsToken] = useState(false);
  const [tokenDraft, setTokenDraft] = useState(getAuthToken());

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<ModelRole | null>(null);
  const [probes, setProbes] = useState<Partial<Record<ModelRole, ProbeResult>>>({});
  const [lastResult, setLastResult] = useState<WriteResult | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [report, setReport] = useState<DiagnosticReport | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const loaded = await getConfig();
      setConfig(loaded);
      setDrafts({ llm: draftOf(loaded.llm), vl: draftOf(loaded.vl) });
      setLoadError("");
      setNeedsToken(false);
    } catch (err) {
      setNeedsToken(isUnauthorized(err));
      setLoadError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshDiagnostics = useCallback(async (deep: boolean) => {
    setDiagnosing(true);
    try {
      setReport(await getDiagnostics(deep));
    } catch (err) {
      message.error(`自检失败：${errorMessage(err)}`);
    } finally {
      setDiagnosing(false);
    }
  }, []);

  const updateDraft = (role: ModelRole, patch: Partial<RoleDraft>) => {
    setDrafts((prev) => (prev ? { ...prev, [role]: { ...prev[role], ...patch } } : prev));
  };

  /** Assemble the patch from what actually changed, skipping locked fields. */
  const buildPatch = (): ConfigPatch => {
    if (!config || !drafts) {
      return {};
    }
    const patch: ConfigPatch = {};
    for (const role of ROLES) {
      const view = config[role];
      const draft = drafts[role];
      const rolePatch: RolePatch = {};
      if (draft.provider !== (view.provider.value === "remote" ? "remote" : "stub")) {
        rolePatch.provider = draft.provider;
      }
      if (draft.base_url !== String(view.base_url.value ?? "")) {
        rolePatch.base_url = draft.base_url.trim();
      }
      if (draft.model !== String(view.model.value ?? "")) {
        rolePatch.model = draft.model.trim();
      }
      if (draft.clearKey) {
        rolePatch.api_key = "";
      } else if (draft.api_key.trim() && draft.api_key.trim() !== "***") {
        rolePatch.api_key = draft.api_key.trim();
      }
      if (Object.keys(rolePatch).length > 0) {
        patch[role] = rolePatch;
      }
    }
    return patch;
  };

  const handleSave = async () => {
    if (!config || !drafts) {
      return;
    }
    const patch = buildPatch();
    if (Object.keys(patch).length === 0) {
      message.info("没有需要保存的修改。");
      return;
    }
    setSaving(true);
    setErrors([]);
    try {
      const result = await putConfig(patch);
      setLastResult(result);
      // Reflect what actually landed on disk (a switch to stub clears endpoint
      // fields) instead of what was typed, so the form never lies about state.
      setDrafts((prev) => {
        if (!prev) {
          return prev;
        }
        const next = { ...prev };
        for (const role of ROLES) {
          const written = result.written[role];
          if (!written) {
            continue;
          }
          const draft = { ...next[role], api_key: "", clearKey: false };
          if (written.provider !== undefined) {
            draft.provider = written.provider === "remote" ? "remote" : "stub";
          }
          if (written.base_url !== undefined) {
            draft.base_url = written.base_url;
          }
          if (written.model !== undefined) {
            draft.model = written.model;
          }
          next[role] = draft;
        }
        return next;
      });
      if (result.ignored.length > 0) {
        message.warning("部分字段被跳过，请查看下方说明。");
      } else {
        message.success("已保存。");
      }
    } catch (err) {
      if (err instanceof ConfigWriteBlockedError) {
        setLastResult(err.result);
        message.warning("没有任何字段被写入。");
      } else if (err instanceof ConfigValidationError) {
        setErrors(err.errors);
      } else if (isUnauthorized(err)) {
        setNeedsToken(true);
        message.error("鉴权失败（401）：请填入正确的访问 token。");
      } else {
        message.error(`保存失败：${errorMessage(err)}`);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (role: ModelRole) => {
    if (!drafts) {
      return;
    }
    const draft = drafts[role];
    setTesting(role);
    try {
      const result = await testConfig({
        role,
        base_url: draft.base_url.trim() || undefined,
        model: draft.model.trim() || undefined,
        api_key: draft.api_key.trim() || undefined,
      });
      setProbes((prev) => ({ ...prev, [role]: result }));
    } catch (err) {
      message.error(`测试连接失败：${errorMessage(err)}`);
    } finally {
      setTesting(null);
    }
  };

  const applyToken = async () => {
    setAuthToken(tokenDraft.trim());
    await load();
  };

  if (needsToken) {
    return (
      <Card title="需要访问 token">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert
            type="warning"
            showIcon
            message="后端启用了鉴权（VL_ANCHOR_AUTH_TOKEN），当前请求被拒绝。"
            description="填入与后端一致的 token。token 只保存在本浏览器（localStorage），不会写入任何配置文件。"
          />
          <Input.Password
            placeholder="VL_ANCHOR_AUTH_TOKEN"
            value={tokenDraft}
            onChange={(event) => setTokenDraft(event.target.value)}
          />
          <Button type="primary" onClick={() => void applyToken()}>
            保存并重试
          </Button>
        </Space>
      </Card>
    );
  }

  if (!config || !drafts) {
    return (
      <Card loading={loading} title="设置">
        {loadError && <Alert type="error" showIcon message="读取配置失败" description={loadError} />}
      </Card>
    );
  }

  const { storage, secrets } = config;
  const saveDisabled = saving || !storage.config_writable;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {!storage.config_writable && (
        <Alert
          type="warning"
          showIcon
          message="配置目录只读，设置页无法保存。"
          description="请改用环境变量（VL_VL_PROVIDER / VL_VL_BASE_URL / VL_VL_NAME 等）配置，或给配置目录写权限。"
        />
      )}
      {!storage.config_persistent && (
        <Alert
          type="warning"
          showIcon
          message="配置目录来自容器镜像层：写回的内容在容器重建后丢失。"
          description="请在 docker-compose.yml 中挂载 ./config:/app/config，或用环境变量配置。"
        />
      )}
      {secrets.available ? (
        <Alert
          type="success"
          showIcon
          message="加密密文库可用，密钥将以密文写入 config/secrets.db。"
          description={`主密钥来自 ${secrets.master_key_source}。主密钥丢失后密文不可恢复。`}
        />
      ) : (
        <Alert type="info" showIcon message="加密密文库不可用，密钥只能来自环境变量。" description={secrets.reason} />
      )}
      {errors.length > 0 && (
        <Alert
          type="error"
          showIcon
          message="提交的配置未通过校验"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          }
        />
      )}

      <Row gutter={16}>
        {ROLES.map((role) => {
          const view = config[role];
          const draft = drafts[role];
          const apiKeyDisabled = view.api_key.locked || !secrets.available || !storage.config_writable;
          const probe = probes[role];
          return (
            <Col span={12} key={role}>
              <Card title={ROLE_LABEL[role]}>
                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                  <div>
                    <Space>
                      <span>provider</span>
                      <SourceTag field={view.provider} />
                      <LockedHint field={view.provider} />
                    </Space>
                    <Select
                      style={{ width: "100%" }}
                      value={draft.provider}
                      disabled={view.provider.locked || !storage.config_writable}
                      onChange={(value: "stub" | "remote") => updateDraft(role, { provider: value })}
                      options={[
                        { value: "stub", label: "stub（离线假框，仅用于跑通流程）" },
                        { value: "remote", label: "remote（调用 OpenAI 兼容端点）" },
                      ]}
                    />
                  </div>
                  <div>
                    <Space>
                      <span>base_url</span>
                      <SourceTag field={view.base_url} />
                      <LockedHint field={view.base_url} />
                    </Space>
                    <Input
                      placeholder="http://127.0.0.1:8000/v1"
                      value={draft.base_url}
                      disabled={view.base_url.locked || !storage.config_writable}
                      onChange={(event) => updateDraft(role, { base_url: event.target.value })}
                    />
                  </div>
                  <div>
                    <Space>
                      <span>model</span>
                      <SourceTag field={view.model} />
                      <LockedHint field={view.model} />
                    </Space>
                    <Input
                      placeholder="qwen2.5-vl-7b-instruct"
                      value={draft.model}
                      disabled={view.model.locked || !storage.config_writable}
                      onChange={(event) => updateDraft(role, { model: event.target.value })}
                    />
                  </div>
                  <div>
                    <Space>
                      <span>api_key</span>
                      <SourceTag field={view.api_key} />
                      <Badge
                        status={view.has_api_key ? "success" : "default"}
                        text={view.has_api_key ? `已设置 ${view.api_key_masked}` : "未设置"}
                      />
                      <LockedHint field={view.api_key} />
                    </Space>
                    <Input.Password
                      placeholder={view.has_api_key ? view.api_key_masked : "未设置"}
                      value={draft.api_key}
                      disabled={apiKeyDisabled}
                      onChange={(event) => updateDraft(role, { api_key: event.target.value, clearKey: false })}
                    />
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {view.api_key.locked
                        ? `${view.api_key.locked_by} 优先于密文库，环境变量存在时改库无效。`
                        : secrets.available
                          ? "留空或填 *** 表示不改动；密钥以密文存于 config/secrets.db。"
                          : secrets.reason}
                    </Typography.Text>
                    <div style={{ marginTop: 8 }}>
                      <Button
                        size="small"
                        danger
                        disabled={apiKeyDisabled}
                        onClick={() => updateDraft(role, { api_key: "", clearKey: true })}
                      >
                        清除密钥
                      </Button>
                      {draft.clearKey && <Typography.Text type="danger"> 保存后将删除已存密钥</Typography.Text>}
                    </div>
                  </div>
                  <Space>
                    <Button size="small" loading={testing === role} onClick={() => void handleTest(role)}>
                      测试连接
                    </Button>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      超时 {String(view.timeout_s.value)}s
                    </Typography.Text>
                  </Space>
                  {probe && (
                    <Alert
                      type={probe.status === "ok" ? "success" : "error"}
                      showIcon
                      message={`${probe.status === "ok" ? "连接成功" : "连接失败"}：${probe.message}`}
                      description={
                        <>
                          <div>
                            延迟 {probe.latency_ms ?? "-"} ms · HTTP {probe.http_status ?? "-"}
                          </div>
                          {probe.hint && <div>{probe.hint}</div>}
                        </>
                      }
                    />
                  )}
                </Space>
              </Card>
            </Col>
          );
        })}
      </Row>

      <Card>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space>
            <Button type="primary" loading={saving} disabled={saveDisabled} onClick={() => void handleSave()}>
              保存
            </Button>
            <Button loading={loading} onClick={() => void load()}>
              重新读取
            </Button>
            <Typography.Text type="secondary">写回文件：{storage.overrides_path}</Typography.Text>
          </Space>
          {saveDisabled && <Typography.Text type="warning">配置目录只读，请改用环境变量。</Typography.Text>}
          {lastResult && (
            <Alert
              type={lastResult.written && Object.keys(lastResult.written).length > 0 ? "success" : "warning"}
              showIcon
              message={
                lastResult.restart_required
                  ? "已写入配置；需重启后端进程后生效（当前进程仍使用启动时的配置）。"
                  : "没有字段被写入。"
              }
              description={
                <Space direction="vertical" size={4} style={{ width: "100%" }}>
                  {Object.entries(lastResult.written).map(([role, fields]) => (
                    <div key={role}>
                      {role}：{Object.entries(fields).map(([field, value]) => `${field}=${value}`).join("、")}
                    </div>
                  ))}
                  {lastResult.ignored.map((item) => (
                    <div key={`${item.role}.${item.field}`}>
                      {item.role}.{item.field} 被跳过：{IGNORED_REASON_LABEL[item.reason]}
                    </div>
                  ))}
                </Space>
              }
            />
          )}
        </Space>
      </Card>

      <Card title="存储与服务（只读）">
        <Descriptions size="small" column={2} bordered>
          <Descriptions.Item label="配置目录">
            {storage.config_dir}（{storage.config_writable ? "可写" : "只读"}）
          </Descriptions.Item>
          <Descriptions.Item label="配置持久">
            {storage.config_persistent ? "是" : "否（容器镜像层，重建即丢失）"}
          </Descriptions.Item>
          <Descriptions.Item label="global.yaml">{storage.config_path}</Descriptions.Item>
          <Descriptions.Item label="overrides.yaml">{storage.overrides_path}</Descriptions.Item>
          <Descriptions.Item label="数据目录">{String(storage.data_dir.value)}</Descriptions.Item>
          <Descriptions.Item label="任务目录">{String(storage.tasks_root.value)}</Descriptions.Item>
          <Descriptions.Item label="日志目录">{String(storage.logs_dir.value)}</Descriptions.Item>
          <Descriptions.Item label="索引库">
            {String(storage.db_path.value)}（{storage.db_enabled.value ? "启用" : "已关闭"}）
          </Descriptions.Item>
          <Descriptions.Item label="服务地址">
            {String(config.server.host.value)}:{String(config.server.port.value)}
          </Descriptions.Item>
          <Descriptions.Item label="API 鉴权">
            {config.server.auth_enabled ? `已启用（token 来自 ${config.server.auth_token_source}）` : "未启用"}
          </Descriptions.Item>
          <Descriptions.Item label="推理默认值">
            batch={String(config.inference.batch_size.value)}、conf={String(config.inference.conf_threshold.value)}、
            min_pixels={String(config.inference.min_defect_pixels.value)}、
            CPU 回退={config.inference.enable_cpu_fallback.value ? "是" : "否"}
          </Descriptions.Item>
          <Descriptions.Item label="密文库">
            {secrets.store_path || "（未配置）"}（{secrets.available ? "可用" : "不可用"}）
          </Descriptions.Item>
        </Descriptions>
        <Divider style={{ margin: "12px 0" }} />
        <Typography.Text type="secondary">
          以上为只读信息：修改请用环境变量或 config/global.yaml。任务级阈值写在每个任务的 plan.yaml 中。
        </Typography.Text>
      </Card>

      <DiagnosticsPanel report={report} loading={diagnosing} onRefresh={(deep) => void refreshDiagnostics(deep)} />
    </Space>
  );
}
