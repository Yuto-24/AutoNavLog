import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { ApiClient, ApiError, fileToBase64 } from "./api";
import {
  candidateFromKey,
  formFromProject,
  initialPlanningForm,
  qnhHpa,
} from "./forms";
import type { PlanningForm } from "./forms";
import { Header } from "./components/Header";
import { ImportPlanPanel } from "./components/ImportPlanPanel";
import { NavLogTable } from "./components/NavLogTable";
import { PasteDialog } from "./components/PasteDialog";
import { ProgressRail } from "./components/ProgressRail";
import { RouteWorkspace } from "./components/RouteWorkspace";
import { StatusPanel } from "./components/StatusPanel";
import type { NavSection, WebState } from "./types";
import { useModalFocusTrap } from "./useModalFocusTrap";

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
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0);
  const navLogRef = useRef<HTMLDivElement | null>(null);
  const kmzDialogRef = useModalFocusTrap<HTMLElement>(Boolean(pendingKmz));

  const applyState = (next: WebState) => {
    setState(next);
    setForm((current) => {
      let updated = current;
      if (!current.departureAirportId && next.airports.length) {
        const seeded = initialPlanningForm(next.airports);
        updated = { ...current, ...seeded };
      }
      if (next.project) {
        updated = formFromProject(next.project, updated, next.airports);
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
  }, [api, bootstrapAttempt]);

  useEffect(() => {
    if (!pendingKmz) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        setPendingKmz(null);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [busy, pendingKmz]);

  const runTask = async <Result,>(
    action: () => Promise<Result>,
    options: {
      apply?: (result: Result) => void;
      success?: string;
      fallbackError?: string;
    } = {},
  ): Promise<Result | undefined> => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      options.apply?.(result);
      if (options.success) setNotice(options.success);
      return result;
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : options.fallbackError ?? "処理に失敗しました。",
      );
      return undefined;
    } finally {
      setBusy(false);
    }
  };

  const run = (action: () => Promise<WebState>, success?: string) =>
    runTask(action, { apply: applyState, success });

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
    await runTask(
      async () => importEncodedFile(file.name, await fileToBase64(file)),
      { fallbackError: "ファイルを読み込めません。" },
    );
  };

  const handlePasteImport = async () => {
    const imported = await run(
      async () =>
        api.request<WebState>("/api/import", {
          method: "POST",
          body: { filename: "pasted.kml", kml_text: pastedKml },
        }),
      "貼付KMLから経路候補を読み込みました。",
    );
    if (!imported) return;
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
            total_usable_fuel_gal: form.totalUsableFuelGal,
            default_variation_deg_east: form.variationDegEast,
            manual_qnh_hpa: qnhHpa(form),
            tgl_count: form.tglCount,
            all_leg_altitude_ft_msl: form.allLegAltitudeFtMsl,
            use_penultimate_as_vrep: form.usePenultimateAsVrep,
            manual_qnh_confirmed: form.manualQnhConfirmed,
          },
        }),
      "経路を確定しました。目的空港と場周経路高度を確認してください。",
    );
  };

  const handleConfirmDestination = async () => {
    if (!state?.project) return;
    await run(
      () =>
        api.request<WebState>("/api/destination/confirm", {
          method: "POST",
          body: {
            destination_airport_id: form.destinationAirportId,
            selected_pattern_altitude_ft_msl:
              form.destinationPatternAltitudeFtMsl,
          },
        }),
      "目的空港と今回採用する場周経路高度を確定しました。",
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
  };

  const updatePayload = () => {
    if (!state?.project) throw new Error("Projectがありません。");
    const arrival = state.project.metadata.ui_state?.arrival_plan ?? null;
    const orderedNodes = [...state.project.route_nodes].sort((a, b) => a.sequence - b.sequence);
    const fallbackVrep = orderedNodes.length >= 3 ? orderedNodes.at(-2)?.id ?? null : null;
    return {
      flight_date: form.flightDate,
      departure_time_jst: form.departureTimeJst,
      total_usable_fuel_gal: form.totalUsableFuelGal,
      default_variation_deg_east: form.variationDegEast,
      manual_qnh_hpa: qnhHpa(form),
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
      manual_qnh_confirmed: form.manualQnhConfirmed,
    };
  };

  const handleCalculate = async () => {
    const calculated = await run(async () => {
      await api.request<WebState>("/api/project", {
        method: "PUT",
        body: updatePayload(),
      });
      return api.request<WebState>("/api/calculate", { method: "POST" });
    }, "NAV LOGを計算しました。準備状況と各値を確認してください。");
    if (calculated?.outcome) {
      window.requestAnimationFrame(() => {
        navLogRef.current?.focus({ preventScroll: true });
        navLogRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      });
    }
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

  const handleNew = async () => {
    if (state?.project && !window.confirm("現在の未保存入力を閉じて新規作業を始めますか？")) {
      return;
    }
    const reset = await runTask(
      async () => {
        await api.resetSession();
        return true;
      },
      { fallbackError: "新規作業を開始できませんでした。" },
    );
    if (reset) window.location.reload();
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
    await runTask(api.downloadTransferAid.bind(api), {
      success: "A4転記補助HTMLのダウンロードを開始しました。",
      fallbackError: "出力できませんでした。",
    });
  };

  if (!state) {
    return (
      <main className="loading-screen">
        <div className="loading-mark">A</div>
        <strong>AutoNavLogを起動しています</strong>
        {error && (
          <>
            <p role="alert">{error}</p>
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                setError(null);
                setBootstrapAttempt((attempt) => attempt + 1);
              }}
            >
              起動を再試行
            </button>
          </>
        )}
      </main>
    );
  }

  const selectedCandidate = candidateFromKey(state.import.candidates, form.candidateKey);
  const selectedArrival = state.project?.metadata.ui_state?.arrival_plan ?? null;
  const destinationConfirmed = Boolean(
    state.project &&
      selectedArrival?.selected_pattern_altitude_ft_msl !== null &&
      selectedArrival?.selected_pattern_altitude_ft_msl !== undefined &&
      selectedArrival.selected_pattern_altitude_source &&
      state.project.destination_airport_id === form.destinationAirportId &&
      selectedArrival.selected_pattern_altitude_ft_msl ===
        form.destinationPatternAltitudeFtMsl,
  );

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
        <div
          className={`message-bar ${error ? "message-error" : "message-success"}`}
          role={error ? "alert" : "status"}
          aria-live={error ? "assertive" : "polite"}
        >
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
          destinationConfirmed={destinationConfirmed}
          busy={busy}
          onFile={handleFile}
          onPaste={() => setPasteOpen(true)}
          onConfirmRoute={handleConfirmRoute}
          onConfirmDestination={handleConfirmDestination}
        />
        <RouteWorkspace
          candidate={selectedCandidate}
          project={state.project}
          altitudeGuidance={state.altitudeGuidance}
          outcome={state.outcome}
          onSectionChange={handleSectionChange}
        />
        <StatusPanel
          runtime={state.runtime}
          readiness={state.readiness}
          projectExists={Boolean(state.project)}
          canCalculate={destinationConfirmed}
          outcomeExists={Boolean(state.outcome)}
          busy={busy}
          onCalculate={handleCalculate}
          onAcknowledge={handleAcknowledge}
          onDownload={handleDownload}
        />
      </main>

      {state.outcome && (
        <div
          ref={navLogRef}
          className="nav-log-focus-target"
          tabIndex={-1}
          aria-label="計算済みNAV LOG"
        >
          <NavLogTable outcome={state.outcome} />
        </div>
      )}

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
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => {
            if (!busy) setPendingKmz(null);
          }}
        >
          <section
            ref={kmzDialogRef}
            className="modal-panel compact-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="kmz-dialog-title"
            tabIndex={-1}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="modal-heading">
              <div>
                <h2 id="kmz-dialog-title">KMZ内のKMLを選択</h2>
                <p>飛行経路を含む文書を1件選択してください。</p>
              </div>
            </div>
            <label className="field-group">
              <span>KML文書</span>
              <select
                data-modal-autofocus
                value={selectedKmzDocument}
                onChange={(event) => setSelectedKmzDocument(event.target.value)}
              >
                {pendingKmz.candidates.map((candidate) => (
                  <option key={candidate} value={candidate}>{candidate}</option>
                ))}
              </select>
            </label>
            <div className="modal-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => setPendingKmz(null)}
                disabled={busy}
              >
                キャンセル
              </button>
              <button
                className="primary-button"
                type="button"
                disabled={!selectedKmzDocument || busy}
                onClick={async () => {
                  await runTask(
                    () => importEncodedFile(
                      pendingKmz.filename,
                      pendingKmz.contentBase64,
                      selectedKmzDocument,
                    ),
                    { fallbackError: "KMZを読み込めません。" },
                  );
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
