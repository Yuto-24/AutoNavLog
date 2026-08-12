import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { ApiClient, ApiError, fileToBase64 } from "./api";
import {
  candidateFromKey,
  departureAirportForCandidate,
  destinationAirportForCandidate,
  formFromProject,
  initialPlanningForm,
  patternAltitudeFtMsl,
  qnhHpa,
  variationForDeparture,
} from "./forms";
import type { PlanningForm } from "./forms";
import {
  applyDraftToSection,
  draftFromSection,
  hasNavLogEditErrors,
  validateNavLogDrafts,
} from "./navLogEditing";
import type {
  NavLogEditDrafts, NavLogEditErrors, NavLogEditableField,
} from "./navLogEditing";
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
  const [altitudeInputs, setAltitudeInputs] = useState<Record<string, string>>({});
  const [navLogDrafts, setNavLogDrafts] = useState<NavLogEditDrafts>({});
  const [navLogEditErrors, setNavLogEditErrors] = useState<NavLogEditErrors>({});
  const [navLogEditVersion, setNavLogEditVersion] = useState(0);
  const [navLogEditStatus, setNavLogEditStatus] = useState<{
    kind: "idle" | "pending" | "saving" | "saved" | "error";
    message: string;
  }>({ kind: "idle", message: "入力欄を編集すると自動再計算します。" });
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
  const calculationInputGenerationRef = useRef(0);
  const navLogEditPendingRef = useRef(false);
  const navLogDraftsRef = useRef<NavLogEditDrafts>({});
  const projectIdRef = useRef<string | null | undefined>(undefined);
  const navLogRecalculationRef = useRef<Promise<void>>(Promise.resolve());

  const invalidateCalculationInputs = () => {
    calculationInputGenerationRef.current += 1;
    if (navLogEditPendingRef.current) {
      setNavLogEditVersion(calculationInputGenerationRef.current);
    }
    return calculationInputGenerationRef.current;
  };

  const cancelPendingRecalculation = () => {
    calculationInputGenerationRef.current += 1;
    navLogEditPendingRef.current = false;
    setNavLogEditVersion(0);
    setNavLogEditStatus({
      kind: "idle",
      message: "入力欄を編集すると自動再計算します。",
    });
  };

  const applyState = (
    next: WebState,
    options: { syncCalculationInputs?: boolean } = {},
  ) => {
    const nextProjectId = next.project?.id ?? null;
    const projectChanged =
      projectIdRef.current !== undefined && projectIdRef.current !== nextProjectId;
    const syncCalculationInputs =
      options.syncCalculationInputs || projectIdRef.current === undefined || projectChanged;
    if (projectChanged) cancelPendingRecalculation();
    projectIdRef.current = nextProjectId;
    setState((current) => {
      if (
        !syncCalculationInputs &&
        current?.project &&
        next.project &&
        current.project.id === next.project.id
      ) {
        return {
          ...next,
          project: { ...next.project, sections: current.project.sections },
        };
      }
      return next;
    });
    setForm((current) => {
      let updated = current;
      if (!current.departureAirportId && next.airports.length) {
        const seeded = initialPlanningForm(next.airports);
        updated = { ...current, ...seeded };
      }
      if (next.project && syncCalculationInputs) {
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
    if (syncCalculationInputs) {
      const drafts = next.project && next.outcome
        ? Object.fromEntries(
            next.project.sections.map((section) => [section.id, draftFromSection(section)]),
          )
        : {};
      navLogDraftsRef.current = drafts;
      navLogEditPendingRef.current = false;
      setNavLogDrafts(drafts);
      setNavLogEditErrors({});
      setAltitudeInputs(
        next.project
          ? Object.fromEntries(
              next.project.sections.map((section) => [
                section.id,
                String(section.planned_altitude_ft_msl),
              ]),
            )
          : {},
      );
    }
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
    if (!state || state.project) return;
    const candidate = candidateFromKey(state.import.candidates, form.candidateKey);
    if (!candidate) return;
    const departure = departureAirportForCandidate(candidate, state.airports);
    const destination = destinationAirportForCandidate(candidate, state.airports);
    setForm((current) => {
      const destinationAirportId = destination?.id ?? "";
      const departureAirportId = departure?.id ?? "";
      const destinationPatternAltitudeFtMsl = destination
        ? String(destination.patternAltitudeFtMsl)
        : "";
      if (
        current.destinationAirportId === destinationAirportId &&
        current.departureAirportId === departureAirportId &&
        current.destinationPatternAltitudeFtMsl === destinationPatternAltitudeFtMsl
      ) {
        return current;
      }
      return {
        ...current,
        departureAirportId,
        destinationAirportId,
        destinationPatternAltitudeFtMsl,
        variationDegEast: departure
          ? variationForDeparture(departure)
          : current.variationDegEast,
        manualQnhConfirmed:
          current.departureAirportId === departureAirportId
            ? current.manualQnhConfirmed
            : false,
      };
    });
  }, [form.candidateKey, state]);

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

  const run = (
    action: () => Promise<WebState>,
    success?: string,
    options: { syncCalculationInputs?: boolean } = {},
  ) => runTask(action, { apply: (next) => applyState(next, options), success });

  const setTrackedForm: typeof setForm = (value) => {
    invalidateCalculationInputs();
    setForm(value);
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
    if (!form.destinationAirportId) {
      setError("KML終点から5 NM以内に目的空港が見つかりません。経路終点を確認してください。");
      return;
    }
    if (!form.departureAirportId) {
      setError("KML始点から5 NM以内に出発空港が見つかりません。経路始点を確認してください。");
      return;
    }
    cancelPendingRecalculation();
    const confirmed = await run(
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
      { syncCalculationInputs: true },
    );
    if (confirmed?.project) {
      setAltitudeInputs(
        Object.fromEntries(
          confirmed.project.sections.map((section) => [
            section.id,
            section.phase === "VISUAL_ARRIVAL"
              ? String(section.planned_altitude_ft_msl)
              : "",
          ]),
        ),
      );
    }
  };

  const handleConfirmDestination = async () => {
    if (!state?.project) return;
    const selectedPatternAltitude = patternAltitudeFtMsl(
      form.destinationPatternAltitudeFtMsl,
    );
    if (selectedPatternAltitude === null) {
      setError("場周経路高度は100～25,000 ftの範囲で100 ft単位にしてください。");
      return;
    }
    cancelPendingRecalculation();
    const confirmed = await run(
      () =>
        api.request<WebState>("/api/destination/confirm", {
          method: "POST",
          body: {
            departure_airport_id: form.departureAirportId || undefined,
            destination_airport_id: form.destinationAirportId,
            selected_pattern_altitude_ft_msl: selectedPatternAltitude,
          },
        }),
      "目的空港と今回採用する場周経路高度を確定しました。",
    );
    const confirmedProject = confirmed?.project;
    if (confirmedProject) {
      setAltitudeInputs((current) => ({
        ...current,
        ...Object.fromEntries(
          confirmedProject.sections
            .filter((section) => section.phase === "VISUAL_ARRIVAL")
            .map((section) => [section.id, String(section.planned_altitude_ft_msl)]),
        ),
      }));
    }
  };

  const handleAltitudeInputChange = (sectionId: string, value: string) => {
    invalidateCalculationInputs();
    setAltitudeInputs((current) => ({ ...current, [sectionId]: value }));
    const altitude = Number(value);
    if (value.trim() && Number.isFinite(altitude)) {
      const project = state?.project;
      const section = project?.sections.find((item) => item.id === sectionId);
      if (project && section && state?.outcome) {
        const nextDrafts = {
          ...navLogDraftsRef.current,
          [sectionId]: {
            ...(navLogDraftsRef.current[sectionId] ?? draftFromSection(section)),
            plannedAltitude: value,
          },
        };
        navLogDraftsRef.current = nextDrafts;
        setNavLogDrafts(nextDrafts);
        setNavLogEditErrors(
          validateNavLogDrafts(project.sections, nextDrafts),
        );
      }
      handleSectionChange(sectionId, { planned_altitude_ft_msl: altitude }, false);
    }
  };

  const handleSectionChange = (
    sectionId: string,
    changes: Partial<NavSection>,
    invalidate = true,
  ) => {
    if (invalidate) invalidateCalculationInputs();
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

  const updatePayload = (sectionOverrides?: NavSection[]) => {
    const payloadSections = sectionOverrides ?? state?.project?.sections ?? [];
    if (!state?.project) throw new Error("Projectがありません。");
    const plannedAltitudes = new Map(
      payloadSections.map((section) => {
        const rawAltitude = (
          sectionOverrides
            ? String(section.planned_altitude_ft_msl)
            : altitudeInputs[section.id] ?? String(section.planned_altitude_ft_msl)
        ).trim();
        if (!rawAltitude) {
          throw new Error("すべてのLegに計画高度を入力してください。");
        }
        const altitude = Number(rawAltitude);
        if (
          !Number.isFinite(altitude) ||
          altitude < 100 ||
          altitude > 25000 ||
          altitude % 100 !== 0
        ) {
          throw new Error("計画高度は100～25,000 ftの範囲で100 ft単位にしてください。");
        }
        return [section.id, altitude] as const;
      }),
    );
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
      sections: payloadSections.map((section) => ({
        section_id: section.id,
        planned_altitude_ft_msl:
          plannedAltitudes.get(section.id) ?? section.planned_altitude_ft_msl,
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

  const handleNavLogEdit = (
    sectionId: string,
    field: NavLogEditableField,
    value: string,
  ) => {
    const inputSection = state?.project?.sections.find((section) => section.id === sectionId);
    if (!inputSection || !state?.project) return;
    const next = {
      ...navLogDraftsRef.current,
      [sectionId]: {
        ...(navLogDraftsRef.current[sectionId] ?? draftFromSection(inputSection)),
        [field]: value,
      },
    };
    const errors = validateNavLogDrafts(state.project.sections, next);
    navLogDraftsRef.current = next;
    setNavLogDrafts(next);
    setNavLogEditErrors(errors);
    setNavLogEditStatus(
      hasNavLogEditErrors(errors)
        ? { kind: "error", message: "入力を確認してください。直前の正常な計算結果を表示中です。" }
        : { kind: "pending", message: "入力待ち…自動再計算を予約しました。" },
    );
    navLogEditPendingRef.current = true;
    calculationInputGenerationRef.current += 1;
    setNavLogEditVersion(calculationInputGenerationRef.current);
  };

  useEffect(() => {
    if (!state?.project || !state.outcome || navLogEditVersion === 0) return;
    const requestGeneration = navLogEditVersion;
    const errors = validateNavLogDrafts(state.project.sections, navLogDrafts);
    setNavLogEditErrors(errors);
    if (hasNavLogEditErrors(errors)) return;
    const timeout = window.setTimeout(() => {
      navLogRecalculationRef.current = navLogRecalculationRef.current.then(async () => {
        if (
          requestGeneration !== calculationInputGenerationRef.current ||
          !navLogEditPendingRef.current
        ) return;
        setNavLogEditStatus({ kind: "saving", message: "自動再計算中…" });
        try {
          const editedSections = state.project!.sections.map((section) =>
            applyDraftToSection(section, navLogDrafts[section.id]),
          );
          const next = await api.request<WebState>("/api/project/recalculate", {
            method: "POST",
            body: updatePayload(editedSections),
          });
          if (
            requestGeneration !== calculationInputGenerationRef.current ||
            !navLogEditPendingRef.current
          ) return;
          applyState(next, { syncCalculationInputs: true });
          navLogEditPendingRef.current = false;
          setNavLogEditVersion(0);
          setNavLogEditStatus({ kind: "saved", message: "自動再計算しました。" });
        } catch (reason) {
          if (requestGeneration !== calculationInputGenerationRef.current) return;
          setNavLogEditStatus({
            kind: "error",
            message: `${reason instanceof Error ? reason.message : "自動再計算に失敗しました。"} 直前の正常な計算結果を表示中です。`,
          });
        }
      });
    }, 700);
    return () => window.clearTimeout(timeout);
  }, [altitudeInputs, api, form, navLogDrafts, navLogEditVersion, state?.outcome, state?.project]);

  const handleCalculate = async () => {
    cancelPendingRecalculation();
    const calculated = await run(async () => {
      await api.request<WebState>("/api/project", {
        method: "PUT",
        body: updatePayload(),
      });
      return api.calculate();
    }, "NAV LOGを計算しました。準備状況と各値を確認してください。", {
      syncCalculationInputs: true,
    });
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
    cancelPendingRecalculation();
    await run(
      () =>
        api.request<WebState>("/api/projects/load", {
          method: "POST",
          body: { project_id: selectedProjectId },
        }),
      "保存済みProjectを開きました。再計算してください。",
      { syncCalculationInputs: true },
    );
  };

  const handleNew = async () => {
    if (state?.project && !window.confirm("現在の未保存入力を閉じて新規作業を始めますか？")) {
      return;
    }
    cancelPendingRecalculation();
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
  const selectedDestinationAirport = state.airports.find(
    (airport) => airport.id === form.destinationAirportId,
  ) ?? null;
  const selectedArrival = state.project?.metadata.ui_state?.arrival_plan ?? null;
  const destinationConfirmed = Boolean(
    state.project &&
      selectedArrival?.selected_pattern_altitude_ft_msl !== null &&
      selectedArrival?.selected_pattern_altitude_ft_msl !== undefined &&
      selectedArrival.selected_pattern_altitude_source &&
      state.project.destination_airport_id === form.destinationAirportId &&
      state.project.departure_airport_id === form.departureAirportId &&
      selectedArrival.selected_pattern_altitude_ft_msl ===
        patternAltitudeFtMsl(form.destinationPatternAltitudeFtMsl),
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
          setForm={setTrackedForm}
          projectExists={Boolean(state.project)}
          busy={busy}
          onFile={handleFile}
          onPaste={() => setPasteOpen(true)}
          onConfirmRoute={handleConfirmRoute}
        />
        <RouteWorkspace
          candidate={selectedCandidate}
          project={state.project}
          altitudeGuidance={state.altitudeGuidance}
          outcome={state.outcome}
          altitudeInputs={altitudeInputs}
          destinationAirport={selectedDestinationAirport}
          destinationPatternAltitudeFtMsl={form.destinationPatternAltitudeFtMsl}
          onAltitudeInputChange={handleAltitudeInputChange}
          onDestinationPatternAltitudeChange={(value) =>
            setTrackedForm((current) => ({
              ...current,
              destinationPatternAltitudeFtMsl: value,
            }))
          }
          onSectionChange={handleSectionChange}
        />
        <StatusPanel
          runtime={state.runtime}
          readiness={state.readiness}
          projectExists={Boolean(state.project)}
          canCalculate={destinationConfirmed}
          destinationConfirmed={destinationConfirmed}
          destinationReady={patternAltitudeFtMsl(form.destinationPatternAltitudeFtMsl) !== null}
          outcomeExists={Boolean(state.outcome)}
          busy={busy}
          onCalculate={handleCalculate}
          onConfirmDestination={handleConfirmDestination}
          onAcknowledge={handleAcknowledge}
          onDownload={handleDownload}
        />
      </main>

      {state.outcome && state.project && (
        <div
          ref={navLogRef}
          className="nav-log-focus-target"
          tabIndex={-1}
          aria-label="計算済みNAV LOG"
        >
          <NavLogTable
            outcome={state.outcome}
            project={state.project}
            drafts={navLogDrafts}
            editErrors={navLogEditErrors}
            editStatus={navLogEditStatus}
            onEdit={handleNavLogEdit}
          />
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
