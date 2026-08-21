import { useEffect, useMemo, useState } from "react";
import { Crosshair, Pencil, Plus, Trash2, X } from "lucide-react";
import type {
  CheckPointInput,
  CheckPointPlanning,
  NavSection,
  RouteNode,
  VisualReference,
} from "../types";

interface PickedCoordinate {
  latitude: number;
  longitude: number;
  revision: number;
}

interface CheckPointEditorProps {
  nodes: RouteNode[];
  sections: NavSection[];
  checkPoints: VisualReference[];
  planning: CheckPointPlanning;
  busy: boolean;
  pickedCoordinate: PickedCoordinate | null;
  pickingFromMap: boolean;
  onPickingFromMapChange: (active: boolean) => void;
  onReplace: (checkPoints: CheckPointInput[]) => Promise<boolean>;
}

interface Draft {
  id: string | null;
  name: string;
  latitude: string;
  longitude: string;
  linkedSectionId: string;
}

function initialDraft(sections: NavSection[]): Draft {
  const recommended = sections.find((section) => section.phase === "CRUISE") ?? sections[0];
  return {
    id: null,
    name: "",
    latitude: "",
    longitude: "",
    linkedSectionId: recommended?.id ?? "",
  };
}

export function CheckPointEditor({
  nodes,
  sections,
  checkPoints,
  planning,
  busy,
  pickedCoordinate,
  pickingFromMap,
  onPickingFromMapChange,
  onReplace,
}: CheckPointEditorProps) {
  const [draft, setDraft] = useState<Draft>(() => initialDraft(sections));
  const [editorOpen, setEditorOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const sectionById = useMemo(
    () => new Map(sections.map((section) => [section.id, section])),
    [sections],
  );
  const projectionById = useMemo(
    () => new Map(planning.projections.map((item) => [item.checkpoint_id, item])),
    [planning.projections],
  );
  const issueById = useMemo(() => {
    const grouped = new Map<string, string[]>();
    planning.issues.forEach((issue) => {
      if (!issue.checkPointId) return;
      grouped.set(issue.checkPointId, [...(grouped.get(issue.checkPointId) ?? []), issue.message]);
    });
    return grouped;
  }, [planning.issues]);

  useEffect(() => {
    if (!pickedCoordinate) return;
    setDraft((current) => ({
      ...current,
      latitude: pickedCoordinate.latitude.toFixed(6),
      longitude: pickedCoordinate.longitude.toFixed(6),
    }));
    setEditorOpen(true);
    onPickingFromMapChange(false);
  }, [pickedCoordinate, onPickingFromMapChange]);

  const reset = () => {
    setDraft(initialDraft(sections));
    setEditorOpen(false);
    onPickingFromMapChange(false);
  };

  const inputs = checkPoints.map<CheckPointInput>((item) => ({
    id: item.id,
    name: item.name,
    latitude_deg: item.latitude_deg,
    longitude_deg: item.longitude_deg,
    linked_section_id: item.linked_section_id ?? "",
  }));

  const latitude = Number(draft.latitude);
  const longitude = Number(draft.longitude);
  const draftValid =
    Boolean(draft.name.trim() && draft.linkedSectionId) &&
    Number.isFinite(latitude) &&
    latitude >= -90 &&
    latitude <= 90 &&
    Number.isFinite(longitude) &&
    longitude >= -180 &&
    longitude <= 180;
  const submitLabel = draft.id
    ? saving ? "保存中…" : "保存"
    : saving ? "追加中…" : "追加";

  const save = async () => {
    if (!draftValid) return;
    const updated: CheckPointInput = {
      ...(draft.id ? { id: draft.id } : {}),
      name: draft.name.trim(),
      latitude_deg: latitude,
      longitude_deg: longitude,
      linked_section_id: draft.linkedSectionId,
    };
    setSaving(true);
    try {
      const succeeded = await onReplace(
        draft.id
          ? inputs.map((item) => (item.id === draft.id ? updated : item))
          : [...inputs, updated],
      );
      if (succeeded) reset();
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="checkpoint-editor" aria-label="チェックポイント設定">
      <div className="section-heading-row">
        <div>
          <h3>チェックポイント</h3>
          <p>偏流と到着予定時刻を確認する地点を、地図または座標から追加します。</p>
        </div>
        <button
          className="secondary-button checkpoint-add-button"
          type="button"
          aria-label="チェックポイントを追加"
          onClick={() => {
            setDraft(initialDraft(sections));
            setEditorOpen(true);
          }}
          disabled={busy || !sections.length}
        >
          <Plus aria-hidden="true" size={16} />
          追加
        </button>
      </div>

      {editorOpen && (
        <div className="checkpoint-form">
          <div className="checkpoint-form-heading">
            <strong>{draft.id ? "チェックポイントを編集" : "チェックポイントを追加"}</strong>
            <button className="icon-button" type="button" onClick={reset} aria-label="編集を閉じる">
              <X aria-hidden="true" size={16} />
            </button>
          </div>
          <label>
            <span>名称</span>
            <input
              value={draft.name}
              maxLength={100}
              onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
              placeholder="例: 岩瀬ダム"
            />
          </label>
          <label>
            <span>関連Leg</span>
            <select
              value={draft.linkedSectionId}
              onChange={(event) =>
                setDraft((current) => ({ ...current, linkedSectionId: event.target.value }))
              }
            >
              <option value="">Legを選択</option>
              {sections.map((section) => {
                const from = nodeById.get(section.from_node_id)?.name ?? "?";
                const to = nodeById.get(section.to_node_id)?.name ?? "?";
                return (
                  <option key={section.id} value={section.id}>
                    Leg {section.sequence + 1}: {from} → {to}（{section.phase}）
                  </option>
                );
              })}
            </select>
          </label>
          <label>
            <span>緯度</span>
            <input
              type="number"
              min="-90"
              max="90"
              step="0.000001"
              value={draft.latitude}
              onChange={(event) =>
                setDraft((current) => ({ ...current, latitude: event.target.value }))
              }
            />
          </label>
          <label>
            <span>経度</span>
            <input
              type="number"
              min="-180"
              max="180"
              step="0.000001"
              value={draft.longitude}
              onChange={(event) =>
                setDraft((current) => ({ ...current, longitude: event.target.value }))
              }
            />
          </label>
          <button
            className={`secondary-button checkpoint-map-pick${pickingFromMap ? " is-active" : ""}`}
            type="button"
            onClick={() => onPickingFromMapChange(!pickingFromMap)}
          >
            <Crosshair aria-hidden="true" size={16} />
            {pickingFromMap ? "地図上の地点をクリック" : "地図から座標を選択"}
          </button>
          <div className="checkpoint-form-actions">
            <button className="secondary-button" type="button" onClick={reset}>キャンセル</button>
            <button
              className="primary-button"
              type="button"
              disabled={!draftValid || saving || busy}
              onClick={() => void save()}
            >
              {submitLabel}
            </button>
          </div>
        </div>
      )}

      {!checkPoints.length ? (
        <p className="checkpoint-empty">まだチェックポイントはありません。</p>
      ) : (
        <div className="checkpoint-list">
          {checkPoints.map((item) => {
            const projection = projectionById.get(item.id);
            const messages = issueById.get(item.id) ?? [];
            const section = item.linked_section_id
              ? sectionById.get(item.linked_section_id)
              : undefined;
            return (
              <article className={`checkpoint-item${messages.length ? " has-error" : ""}`} key={item.id}>
                <div>
                  <strong>{item.name}</strong>
                  <span>
                    Leg {section ? section.sequence + 1 : "?"}・
                    {item.latitude_deg.toFixed(5)}, {item.longitude_deg.toFixed(5)}
                  </span>
                  {projection && (
                    <span>
                      Leg内 {projection.along_section_distance_nm.toFixed(1)} NM / 累積 {projection.cumulative_distance_nm.toFixed(1)} NM / 横ずれ {projection.cross_track_distance_nm.toFixed(2)} NM
                    </span>
                  )}
                  {messages.map((message) => <em key={message}>{message}</em>)}
                </div>
                <div className="checkpoint-item-actions">
                  <button
                    className="icon-button"
                    type="button"
                    aria-label={`${item.name}を編集`}
                    disabled={busy}
                    onClick={() => {
                      setDraft({
                        id: item.id,
                        name: item.name,
                        latitude: String(item.latitude_deg),
                        longitude: String(item.longitude_deg),
                        linkedSectionId: item.linked_section_id ?? "",
                      });
                      setEditorOpen(true);
                    }}
                  >
                    <Pencil aria-hidden="true" size={15} />
                  </button>
                  <button
                    className="icon-button danger-icon-button"
                    type="button"
                    aria-label={`${item.name}を削除`}
                    onClick={() => {
                      if (!window.confirm(`Check Point「${item.name}」を削除しますか？`)) return;
                      void onReplace(inputs.filter((candidate) => candidate.id !== item.id));
                    }}
                    disabled={busy}
                  >
                    <Trash2 aria-hidden="true" size={15} />
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
      <small className="checkpoint-guidance">巡航区間では15〜20 NM程度の間隔が配置の目安です。上昇・降下を含むLegも、実際の巡航区間に投影できる場合は選択できます。</small>
    </section>
  );
}
