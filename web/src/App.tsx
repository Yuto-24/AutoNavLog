import { useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { ApiClient, ApiError, fileToBase64 } from "./api";
import { candidateFromKey, formFromProject, initialPlanningForm } from "./forms";
import type { PlanningForm } from "./forms";
import { Header } from "./components/Header";
import { ImportPlanPanel } from "./components/ImportPlanPanel";
import { NavLogTable } from "./components/NavLogTable";
import { PasteDialog } from "./components/PasteDialog";
import { ProgressRail } from "./components/ProgressRail";
import { RouteWorkspace } from "./components/RouteWorkspace";
import { StatusPanel } from "./components/StatusPanel";
import type { NavSection, WebState } from "./types";

interface PendingKmz {
  filename: string;
  contentBase64: string;
  candidates: string[];
}

function App() {
  const api = useMemo(() => new ApiClient(), []);
  const [state, setState] = useState<WebState | null>(null);
  const [form, setForm] = useState<PlanningForm>(() => initialPlanningForm());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pastedKml, setPastedKml] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [pendingKmz, setPendingKmz] = useState<PendingKmz | null>(null);
  const [selectedKmzDocument, setSelectedKmzDocument] = useState("");

  const applyState = (next: WebState) => {
    setState(next);
    setForm((current) => {
      let updated = current;
      if (!current.departureAirportId && next.airports.length) {
        const seeded = initialPlanningForm(next.airports);
        updated = { ...current, ...seeded };
      }
      if (next.project) {
        updated = formFromProject(next.project, updated);
      }
      const selectedExists = next.import.candidates.some(
        (candidate) => `${candidate.kind}:${candidate.index}` === updated.candidateKey,
      );
      if (!selectedExists && next.import.candidates[0]) {
        updated = {
          ...updated,
          candidateKey: `${next.import.candidates[0].kind}:${next.import.candidates[0].index}`,
        };
      }
      return updated;
    });
  };

  useEffect(() => {
    let active = true;
    api
      .bootstrap()
      .then((next) => {
        if (active) applyState(next);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "起動に失敗しました。");
      });
    return () => {
      active = false;
    };
  }, [api]);

  const run = async (action: () => Promise<WebState>, success?: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const next = await action();
      applyState(next);
      if (success) setNotice(success);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "処理に失敗しました。");
    } finally {
      setBusy(false);
    }
  };

  const importEncodedFile = async (
    filename: string,
    contentBase64: string,
    kmzDocument?: string,
  ) => {
    try {
      const next = await api.request<WebState>("/api/import", {
        method: "POST",
        body: {
          filename,
          content_base64: contentBase64,
          kmz_kml_filename: kmzDocument ?? null,
        },
      });
      applyState(next);
      setForm((current) => ({
        ...current,
        routeUseConfirmed: false,
        polygonRouteConfirmed: false,
      }));
      setNotice("経路候補を読み込みました。地図と記載順を確認してください。");
      setPendingKmz(null);
      setSelectedKmzDocument("");
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === "KMZ_DOCUMENT_SELECTION_REQUIRED") {
        setPendingKmz({ filename, contentBase64, candidates: reason.candidates });
        setSelectedKmzDocument(reason.candidates[0] ?? "");
      } else {
        throw reason;
      }
    }
  };

  const handleFile = async (file: File) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await importEncodedFile(file.name, await fileToBase64(file));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ファイルを読み込めません。");
    } finally {
      setBusy(false);
    }
  };

  const handlePasteImport = async () => {
    await run(
      async () =>
        api.request<WebState>("/api/import", {
          method: "POST",
          body: { filename: "pasted.kml", kml_text: pastedKml },
        }),
      "貼付KMLから経路候補を読み込みました。",
    );
    setPasteOpen(false);
  };

  const handleConfirmRoute = async () => {
    if (!state) return;
    const candidate = candidateFromKey(state.import.candidates, form.candidateKey);
    if (!candidate) {
      setError("飛行経路にする形状を選択してください。");
      return;
    }
    await run(
      () =>
        api.request<WebState>("/api/route/confirm", {
          method: "POST",
          body: {
            candidate_kind: candidate.kind,
            candidate_index: candidate.index,
            point_indices: [],
            route_use_confirmed: form.routeUseConfirmed,
            polygon_route_confirmed: form.polygonRouteConfirmed,
            flight_date: form.flightDate,
            departure_time_jst: form.departureTimeJst,
            departure_airport_id: form.departureAirportId,
            destination_airport_id: form.destinationAirportId,
            pilot_name: form.pilotName,
            ship_identifier: form.shipIdentifier,
            total_usable_fuel_gal: form.totalUsableFuelGal,
            default_variation_deg_east: form.variationDegEast,
            manual_qnh_hpa: form.manualQnhHpa ? Number(form.manualQnhHpa) : null,
            tgl_count: form.tglCount,
            all_leg_altitude_ft_msl: form.allLegAltitudeFtMsl,
            use_penultimate_as_vrep: form.usePenultimateAsVrep,
            defaults_confirmed: form.defaultsConfirmed,
            manual_qnh_confirmed: form.manualQnhConfirmed,
          },
        }),
      "経路とVREPを確定しました。Leg入力を確認してください。",
    );
  };

  const handleSectionChange = (sectionId: string, changes: Partial<NavSection>) => {
    setState((current) => {
      if (!current?.project) return current;
      return {
        ...current,
        project: {
          ...current.project,
          sections: current.project.sections.map((section) =>
            section.id === sectionId ? { ...section, ...changes } : section,
          ),
        },
      };
    });
    setForm((current) => ({ ...current, defaultsConfirmed: false }));
  };

  const updatePayload = () => {
    if (!state?.project) throw new Error("Projectがありません。");
    const arrival = state.project.metadata.ui_state?.arrival_plan ?? null;
    const orderedNodes = [...state.project.route_nodes].sort((a, b) => a.sequence - b.sequence);
    const fallbackVrep = orderedNodes.length >= 3 ? orderedNodes.at(-2)?.id ?? null : null;
    return {
      flight_date: form.flightDate,
      departure_time_jst: form.departureTimeJst,
      pilot_name: form.pilotName,
      ship_identifier: form.shipIdentifier,
      total_usable_fuel_gal: form.totalUsableFuelGal,
      default_variation_deg_east: form.variationDegEast,
      manual_qnh_hpa: form.manualQnhHpa ? Number(form.manualQnhHpa) : null,
      tgl_count: form.tglCount,
      sections: state.project.sections.map((section) => ({
        section_id: section.id,
        planned_altitude_ft_msl: section.planned_altitude_ft_msl,
        phase: section.phase,
        manual_wind_direction_deg: section.manual_wind_direction_deg,
        manual_wind_speed_kt: section.manual_wind_speed_kt,
        manual_temperature_c: section.manual_temperature_c,
        manual_tas_kt: section.manual_tas_kt,
      })),
      visual_reporting_point_node_id: arrival?.visual_reporting_point_node_id ?? fallbackVrep,
      arrival_altitude_mode: arrival?.altitude_mode ?? "STANDARD_DISTANCE_RULE",
      manual_vrep_altitude_ft_msl: arrival?.manual_vrep_altitude_ft_msl ?? null,
      manual_vrep_reason: arrival?.manual_override_reason ?? null,
      defaults_confirmed: form.defaultsConfirmed,
      manual_qnh_confirmed: form.manualQnhConfirmed,
    };
  };

  const handleCalculate = async () => {
    await run(async () => {
      const updated = await api.request<WebState>("/api/project", {
        method: "PUT",
        body: updatePayload(),
      });
      applyState(updated);
      return api.request<WebState>("/api/calculate", { method: "POST" });
    }, "NAV LOGを計算しました。準備状況と各値を確認してください。");
  };

  const handleSave = async () => {
    if (!state?.project) return;
    await run(
      () =>
        api.request<WebState>("/api/projects/save", {
          method: "POST",
          body: { name: state.project?.name },
        }),
      "Projectをローカルへ保存しました。",
    );
  };

  const handleLoad = async () => {
    if (!selectedProjectId) return;
    await run(
      () =>
        api.request<WebState>("/api/projects/load", {
          method: "POST",
          body: { project_id: selectedProjectId },
        }),
      "保存済みProjectを開きました。再計算してください。",
    );
  };

  const handleNew = () => {
    if (state?.project && !window.confirm("現在の未保存入力を閉じて新規作業を始めますか？")) {
      return;
    }
    api.resetSession();
    window.location.reload();
  };

  const handleAcknowledge = async (ackKey: string, checked: boolean) => {
    await run(() =>
      api.request<WebState>(`/api/acknowledgements/${encodeURIComponent(ackKey)}`, {
        method: "PUT",
        body: { checked },
      }),
    );
  };

  const handleDownload = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.downloadTransferAid();
      setNotice("A4転記補助HTMLのダウンロードを開始しました。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "出力できませんでした。");
    } finally {
      setBusy(false);
    }
  };

  if (!state) {
    return (
      <main className="loading-screen">
        <div className="loading-mark">A</div>
        <strong>AutoNavLogを起動しています</strong>
        {error && <p>{error}</p>}
      </main>
    );
  }

  const selectedCandidate = candidateFromKey(state.import.candidates, form.candidateKey);

  return (
    <div className="app-shell">
      <Header
        projectName={state.project?.name ?? "未保存の新規作業"}
        revision={state.project?.revision ?? null}
        savedProjects={state.savedProjects}
        selectedProjectId={selectedProjectId}
        busy={busy}
        onSelectedProjectIdChange={setSelectedProjectId}
        onLoad={handleLoad}
        onSave={handleSave}
        onNew={handleNew}
      />
      <ProgressRail activeStep={state.readiness.workflowStep} />

      {(error || notice) && (
        <div className={`message-bar ${error ? "message-error" : "message-success"}`} role="status">
          {error ? <AlertCircle aria-hidden="true" size={18} /> : <CheckCircle2 aria-hidden="true" size={18} />}
          <span>{error ?? notice}</span>
          <button
            className="icon-button"
            type="button"
            onClick={() => { setError(null); setNotice(null); }}
            aria-label="メッセージを閉じる"
          >
            <X aria-hidden="true" size={17} />
          </button>
        </div>
      )}

      <main className="app-workspace">
        <ImportPlanPanel
          importState={state.import}
          airports={state.airports}
          form={form}
          setForm={setForm}
          projectExists={Boolean(state.project)}
          busy={busy}
          onFile={handleFile}
          onPaste={() => setPasteOpen(true)}
          onConfirmRoute={handleConfirmRoute}
        />
        <RouteWorkspace
          candidate={selectedCandidate}
          project={state.project}
          outcome={state.outcome}
          onSectionChange={handleSectionChange}
        />
        <StatusPanel
          runtime={state.runtime}
          readiness={state.readiness}
          projectExists={Boolean(state.project)}
          outcomeExists={Boolean(state.outcome)}
          calculateEnabled={form.defaultsConfirmed}
          busy={busy}
          onCalculate={handleCalculate}
          onAcknowledge={handleAcknowledge}
          onDownload={handleDownload}
        />
      </main>

      {state.outcome && <NavLogTable outcome={state.outcome} />}

      <footer className="app-footer">
        <span>AutoNavLogは非公式の地上準備支援ツールです。</span>
        <span>参照 {state.runtime.referenceRevision} / 性能 {state.runtime.performanceRevision}</span>
      </footer>

      <PasteDialog
        open={pasteOpen}
        value={pastedKml}
        busy={busy}
        onChange={setPastedKml}
        onClose={() => setPasteOpen(false)}
        onImport={handlePasteImport}
      />

      {pendingKmz && (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel compact-modal" role="dialog" aria-modal="true">
            <div className="modal-heading">
              <div>
                <h2>KMZ内のKMLを選択</h2>
                <p>飛行経路を含む文書を1件選択してください。</p>
              </div>
            </div>
            <label className="field-group">
              <span>KML文書</span>
              <select
                value={selectedKmzDocument}
                onChange={(event) => setSelectedKmzDocument(event.target.value)}
              >
                {pendingKmz.candidates.map((candidate) => (
                  <option key={candidate} value={candidate}>{candidate}</option>
                ))}
              </select>
            </label>
            <div className="modal-actions">
              <button className="secondary-button" type="button" onClick={() => setPendingKmz(null)}>
                キャンセル
              </button>
              <button
                className="primary-button"
                type="button"
                disabled={!selectedKmzDocument || busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await importEncodedFile(
                      pendingKmz.filename,
                      pendingKmz.contentBase64,
                      selectedKmzDocument,
                    );
                  } catch (reason) {
                    setError(reason instanceof Error ? reason.message : "KMZを読み込めません。");
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                選択KMLを読み込む
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export default App;
