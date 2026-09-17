import { AccountSyncControl } from "./components/AccountSyncControl";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { ApplicationError } from "./application";
import type { AutoNavLogApplication, UpdateProjectInput } from "./application";
import { readSession, writeSession, clearSession, RECOVERY_FAILED, emptyCheckPointDraft, projectDraft, restoreProjectDraft } from "./applicationSession";
import type { ApplicationSession, PendingKmz, NodeNameDraft, VorColumn } from "./applicationSession";
import { routeImportInput } from "./fileInput";
import { PlatformError, type PlatformCapabilities, type FileContentSource } from "./platform";
import type { FileInputProps } from "./BrowserFileInput";
import { exportNavLog } from "./navLogExport";
import {
  candidateFromKey,
  departureAirportForCandidate,
  destinationAirportForCandidate,
  formFromProject,
  ftdWeatherSettings,
  initialPlanningForm,
  patternAltitudeFtMsl,
  touchAndGoCount,
  usableFuelGal,
  validDepartureTimeJst,
  validFlightDate,
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
import { AccountNotice } from "./components/AccountControl";
import { Header } from "./components/Header";
import { InformationDialog } from "./components/InformationDialog";
import { ImportPlanPanel } from "./components/ImportPlanPanel";
import { NavLogTable } from "./components/NavLogTable";
import { PasteDialog } from "./components/PasteDialog";
import { ProgressRail } from "./components/ProgressRail";
import { RouteWorkspace } from "./components/RouteWorkspace";
import { StatusPanel } from "./components/StatusPanel";
import { CalculationProgressOverlay } from "./components/CalculationProgressOverlay";
import type { CheckPointInput, FlightPhase, NavSection, Project, WebState } from "./types";
import { useModalFocusTrap } from "./useModalFocusTrap";
import { createInformationState } from "./releaseNotes";
import type { InformationData } from "./releaseNotes";
import releaseNotesData from "./generated/releaseNotes.json";

type ActiveOperation = "calculate" | null;
type DraftSaveWaiter = (saved: boolean) => void;

interface DraftSaveRequest {
  generation: number;
  projectId: string;
  payload: UpdateProjectInput;
  useCanonicalFallback: boolean;
  syncDerivedArrival: boolean;
  waiters: DraftSaveWaiter[];
}

const ROUTE_EDITOR_VREP_REASON = "経路画面で指定したVREP計画高度";
const DRAFT_AUTOSAVE_FAILURE = "Projectを自動保存できませんでした。入力内容は画面に保持されています。";

function inboundMetric(value: number | null, digits = 1): string {
  return value === null ? "—" : value.toFixed(digits);
}

function inboundPoint(latitude: number | null | undefined, longitude: number | null | undefined): string {
  if (latitude === null || latitude === undefined || longitude === null || longitude === undefined) {
    return "—";
  }
  return `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`;
}

function patternRequestBasis(next: WebState): string | null {
  const project = next.project;
  if (!project) return null;
  return [
    project.id,
    project.destination_airport_id,
    project.route_nodes.map((node) => node.id + ":" + node.sequence).join(","),
    project.metadata.ui_state?.arrival_plan?.visual_reporting_point_node_id ?? "",
  ].join("|");
}

function App({ application, platform, FileInput }: { application: AutoNavLogApplication; platform: PlatformCapabilities; FileInput: ComponentType<FileInputProps> }) {
  const [informationState] = useState(() => createInformationState(platform.persistence.values));
  const { hasUnreadInformation, hasUnreadKnownIssues, markInformationSeen } = informationState;
  const [recovery] = useState(() => readSession(platform.session));
  const [vorColumns, setVorColumns] = useState<VorColumn[]>([{ id: 0, stationIdentifier: null }]);
  const [checkPointDraft, setCheckPointDraft] = useState(emptyCheckPointDraft);
  const [nodeNameDraft, setNodeNameDraft] = useState<NodeNameDraft>({ id: null, name: "" });
  const [sessionReady, setSessionReady] = useState(false);
  const sessionDiscarded = useRef(false);
  const latestSession = useRef<ApplicationSession | null>(null);
  const restoredPattern = useRef<string | null>(null);
  const lifecycle = useRef(0);
  const restoredCandidate = useRef<string | null>(null);
  const [kmzOpen, setKmzOpen] = useState(false);
  const [state, setState] = useState<WebState | null>(null);
  const [form, setForm] = useState<PlanningForm>(() => initialPlanningForm());
  const [altitudeInputs, setAltitudeInputs] = useState<Record<string, string>>({});
  const [navLogDrafts, setNavLogDrafts] = useState<NavLogEditDrafts>({});
  const [navLogEditErrors, setNavLogEditErrors] = useState<NavLogEditErrors>({});
  const [navLogEditVersion, setNavLogEditVersion] = useState(0);
  const [draftAutosaveVersion, setDraftAutosaveVersion] = useState(0);
  const [calculationInputsAreLocallyCurrent, setCalculationInputsAreLocallyCurrent] =
    useState(true);
  const [navLogEditStatus, setNavLogEditStatus] = useState<{
    kind: "idle" | "pending" | "saving" | "saved" | "error";
    message: string;
  }>({ kind: "idle", message: "入力欄を編集すると自動再計算します。" });
  const [busy, setBusy] = useState(false);
  const [activeOperation, setActiveOperation] = useState<ActiveOperation>(null);
  const [calculationProgress, setCalculationProgress] = useState({
    percent: 0,
    message: "計算を開始しています。",
  });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [informationOpen, setInformationOpen] = useState(false);
  const [informationData] = useState<InformationData>(() => releaseNotesData as InformationData);
  const [knownIssuesUnread, setKnownIssuesUnread] = useState(() => hasUnreadKnownIssues(releaseNotesData as InformationData));
  const [informationUnread, setInformationUnread] = useState(() => hasUnreadInformation(releaseNotesData as InformationData));
  const [pastedKml, setPastedKml] = useState("");
  const [pasteMessage, setPasteMessage] = useState<string | null>(null);
  const pasteInFlight = useRef(false);
  const pasteReturnFocus = useRef<HTMLElement | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [projectName, setProjectName] = useState("未保存の新規作業");
  const [pendingKmz, setPendingKmz] = useState<PendingKmz | null>(null);
  const [selectedKmzDocument, setSelectedKmzDocument] = useState("");
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0);
  const navLogRef = useRef<HTMLDivElement | null>(null);
  const kmzDialogRef = useModalFocusTrap<HTMLElement>(kmzOpen && Boolean(pendingKmz));
  const calculationInputGenerationRef = useRef(0);
  const navLogEditPendingRef = useRef(false);
  const navLogDraftsRef = useRef<NavLogEditDrafts>({});
  const projectIdRef = useRef<string | null | undefined>(undefined);
  const canonicalProjectRef = useRef<Project | null>(null);
  const draftAutosaveGenerationRef = useRef(0);
  const draftAutosaveTimerRef = useRef<number | null>(null);
  const draftAutosaveQueuedRef = useRef<DraftSaveRequest | null>(null);
  const draftAutosaveRunningRef = useRef<DraftSaveRequest | null>(null);
  const draftAutosaveDrainRef = useRef<Promise<void> | null>(null);
  const navLogRecalculationRef = useRef<Promise<void>>(Promise.resolve());
  const destinationPatternGenerationRef = useRef(0);
  const destinationPatternBasisRef = useRef<string | null | undefined>(undefined);
  const destinationPatternRecalculationRef = useRef<Promise<void>>(Promise.resolve());
  const updatePayloadRef = useRef<(
    sectionOverrides?: NavSection[],
    selectedPatternAltitudeFtMsl?: number,
    invalidFallbackProject?: Project,
  ) => UpdateProjectInput>(() => { throw new Error("Projectがありません。"); });

  const altitudeGuidanceBySection = useMemo(
    () => new Map(
      (state?.altitudeGuidance.sections ?? []).map((guidance) => [
        guidance.sectionId,
        guidance,
      ]),
    ),
    [state?.altitudeGuidance.sections],
  );

  const invalidateCalculationInputs = () => {
    calculationInputGenerationRef.current += 1;
    setCalculationInputsAreLocallyCurrent(false);
    if (navLogEditPendingRef.current) {
      setNavLogEditVersion(calculationInputGenerationRef.current);
    }
    return calculationInputGenerationRef.current;
  };

  const invalidateDestinationPatternRequests = () => {
    destinationPatternGenerationRef.current += 1;
    return destinationPatternGenerationRef.current;
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

  const markDraftForAutosave = () => {
    if (!projectIdRef.current) return;
    draftAutosaveGenerationRef.current += 1;
    setDraftAutosaveVersion(draftAutosaveGenerationRef.current);
  };

  const applyState = (
    next: WebState,
    options: {
      syncCalculationInputs?: boolean;
      syncDerivedArrival?: boolean;
      freshImport?: boolean;
    } = {},
  ) => {
    const nextProjectId = next.project?.id ?? null;
    const nextPatternBasis = patternRequestBasis(next);
    const patternBasisChanged = destinationPatternBasisRef.current !== nextPatternBasis;
    const initialState = projectIdRef.current === undefined;
    const projectChanged =
      !initialState && projectIdRef.current !== nextProjectId;
    const syncCalculationInputs =
      options.syncCalculationInputs || initialState || projectChanged;
    if (syncCalculationInputs || patternBasisChanged) {
      invalidateDestinationPatternRequests();
    }
    if (syncCalculationInputs) {
      setCalculationInputsAreLocallyCurrent(true);
    }
    if (projectChanged) {
      cancelPendingRecalculation();
      setVorColumns([{ id: 0, stationIdentifier: null }]);
      setCheckPointDraft(emptyCheckPointDraft());
      setNodeNameDraft({ id: null, name: "" });
    }
    canonicalProjectRef.current = next.project;
    projectIdRef.current = nextProjectId;
    destinationPatternBasisRef.current = nextPatternBasis;
    if (projectChanged || initialState) {
      setProjectName(next.project?.name ?? "未保存の新規作業");
      setSelectedProjectId(
        nextProjectId && next.savedProjects.some((project) => project.id === nextProjectId)
          ? nextProjectId
          : "",
      );
    }
    setState((current) => {
      if (
        !syncCalculationInputs &&
        current?.project &&
        next.project &&
        current.project.id === next.project.id
      ) {
        const derivedArrivalSections = options.syncDerivedArrival
          ? new Map(
              next.project.sections
                .filter((section) => section.phase === "VISUAL_ARRIVAL")
                .map((section) => [section.id, section]),
            )
          : null;
        return {
          ...next,
          project: {
            ...next.project,
            sections: current.project.sections.map(
              (section) => derivedArrivalSections?.get(section.id) ?? section,
            ),
          },
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
      if (options.freshImport) {
        const onlyCandidate = next.import.candidates.length === 1
          ? next.import.candidates[0]
          : undefined;
        updated = {
          ...updated,
          candidateKey: onlyCandidate
            ? `${onlyCandidate.kind}:${onlyCandidate.index}`
            : "",
          departureAirportId: "",
          destinationAirportId: "",
          destinationPatternAltitudeFtMsl: "",
          routeUseConfirmed: false,
          polygonRouteConfirmed: false,
        };
      } else if (!selectedExists) {
        const onlyCandidate = next.import.candidates.length === 1
          ? next.import.candidates[0]
          : undefined;
        updated = {
          ...updated,
          candidateKey: onlyCandidate
            ? `${onlyCandidate.kind}:${onlyCandidate.index}`
            : "",
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
    } else if (options.syncDerivedArrival && next.project) {
      setAltitudeInputs((current) => ({
        ...current,
        ...Object.fromEntries(
          next.project!.sections
            .filter((section) => section.phase === "VISUAL_ARRIVAL")
            .map((section) => [section.id, String(section.planned_altitude_ft_msl)]),
        ),
      }));
    }
  };

  useEffect(() => {
    let active = true;
    lifecycle.current += 1;
    const bootstrap = async () => {
      let restored = recovery.session;
      let failed = recovery.failed;
      let next: WebState;
      try {
        next = await application.bootstrap(restored?.working);
      } catch (reason) {
        if (!restored || !(reason instanceof ApplicationError) ||
            !["VALIDATION_FAILED", "SESSION_RECOVERY_INVALID", "PROJECT_NOT_FOUND"].includes(reason.code)) throw reason;
        restored = undefined;
        failed = true;
        clearSession(platform.session.storage);
        next = await application.bootstrap();
      }
      if (!active) return;
      applyState(next);
      if (restored) {
        // Canonical project remains the validated Application snapshot; raw UI values stay separate.
        if (next.project && restored.projectDraft) {
          setState({ ...next, project: restoreProjectDraft(next.project, restored.projectDraft) });
        }
        setForm(restored.form);
        restoredPattern.current = restored.form.destinationPatternAltitudeFtMsl;
        restoredCandidate.current = restored.form.candidateKey;
        setAltitudeInputs(restored.altitudeInputs);
        navLogDraftsRef.current = restored.navLogDrafts;
        setNavLogDrafts(restored.navLogDrafts);
        setCalculationInputsAreLocallyCurrent(restored.calculationInputsAreLocallyCurrent);
        setVorColumns(restored.vorColumns);
        setCheckPointDraft(restored.checkPointDraft);
        setNodeNameDraft(restored.nodeNameDraft);
        setPastedKml(restored.pastedKml);
        setProjectName(restored.projectName);
        setSelectedProjectId(restored.selectedProjectId);
        setPendingKmz(restored.pendingKmz);
        setSelectedKmzDocument(restored.selectedKmzDocument);
      }
      if (failed) setNotice(RECOVERY_FAILED);
      setSessionReady(true);
    };
    void bootstrap().catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "起動に失敗しました。");
    });
    return () => { active = false; lifecycle.current += 1; };
  }, [application, bootstrapAttempt]);

  useLayoutEffect(() => {
    if (!sessionReady || !state?.workingRecovery || sessionDiscarded.current) return;
    const snapshot: ApplicationSession = {
      checkPointDraft, nodeNameDraft, vorColumns,
      version: 1, working: state.workingRecovery, form, altitudeInputs, navLogDrafts,
      projectDraft: projectDraft(state.project), calculationInputsAreLocallyCurrent, pastedKml,
      projectName, selectedProjectId, pendingKmz, selectedKmzDocument,
    };
    latestSession.current = snapshot;
    if (!writeSession(snapshot, platform.session.storage)) {
      setNotice("作業状態を一時保存できません。再読み込みすると未保存の作業を失う可能性があります。");
    }
  }, [sessionReady, state, form, altitudeInputs, navLogDrafts, checkPointDraft, nodeNameDraft, vorColumns,
    calculationInputsAreLocallyCurrent, pastedKml, projectName, selectedProjectId, pendingKmz, selectedKmzDocument]);

  useEffect(() => {
    if (!state || state.project) return;
    if (restoredCandidate.current === form.candidateKey) return;
    restoredCandidate.current = null;
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
      };
    });
  }, [form.candidateKey, state]);

  useEffect(() => {
    if (!pendingKmz) { setKmzOpen(false); return; }
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

  const applyCommittedFailure = (reason: unknown) => {
    if (reason instanceof ApplicationError && reason.committedState &&
        reason.committedState.project?.id === projectIdRef.current) {
      // Retain raw UI drafts while advancing canonical recovery and its storage token.
      applyState(reason.committedState, { syncCalculationInputs: false });
      return true;
    }
    return false;
  };

  const runTask = async <Result,>(
    action: () => Promise<Result>,
    options: {
      apply?: (result: Result) => void;
      success?: string;
      fallbackError?: string;
      operation?: Exclude<ActiveOperation, null>;
    } = {},
  ): Promise<Result | undefined> => {
    const epoch = lifecycle.current;
    setBusy(true);
    setActiveOperation(options.operation ?? null);
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      if (epoch !== lifecycle.current) return undefined;
      options.apply?.(result);
      if (options.success) setNotice(options.success);
      return result;
    } catch (reason) {
      if (epoch !== lifecycle.current) return undefined;
      applyCommittedFailure(reason);
      setError(
        reason instanceof Error
          ? reason.message
          : options.fallbackError ?? "処理に失敗しました。",
      );
      return undefined;
    } finally {
      if (epoch === lifecycle.current) {
        setBusy(false);
        setActiveOperation(null);
      }
    }
  };

  const run = (
    action: () => Promise<WebState>,
    success?: string,
    options: {
      syncCalculationInputs?: boolean;
      operation?: Exclude<ActiveOperation, null>;
      freshImport?: boolean;
    } = {},
  ) => {
    const generation = calculationInputGenerationRef.current;
    return runTask(action, {
      apply: (next) => applyState(next, {
        ...options,
        syncCalculationInputs: options.syncCalculationInputs &&
          generation === calculationInputGenerationRef.current,
      }),
      success, operation: options.operation,
    });
  };

  const setTrackedForm: typeof setForm = (value) => {
    invalidateCalculationInputs();
    markDraftForAutosave();
    setForm(value);
  };

  const importEncodedFile = async (
    filename: string,
    contentBase64: string,
    kmzDocument?: string,
  ) => {
    invalidateDestinationPatternRequests();
    try {
      const next = await application.importRoute({
        filename,
        content_base64: contentBase64,
        kmz_kml_filename: kmzDocument ?? null,
      });
      applyState(next, { freshImport: true });
      setNotice("経路候補を読み込みました。地図と記載順を確認してください。");
      setPendingKmz(null);
      setSelectedKmzDocument("");
    } catch (reason) {
      if (reason instanceof ApplicationError && reason.code === "KMZ_DOCUMENT_SELECTION_REQUIRED") {
        setPendingKmz({ filename, contentBase64, candidates: reason.details.candidates ?? [] });
        setKmzOpen(true);
        setSelectedKmzDocument(reason.details.candidates?.[0] ?? "");
      } else {
        throw reason;
      }
    }
  };

  const handleFile = async (file: FileContentSource) => {
    await runTask(
      async () => {
        const input = routeImportInput(await platform.files.read(file));
        await importEncodedFile(input.filename, input.content_base64);
      },
      { fallbackError: "ファイルを読み込めません。" },
    );
  };

  const handlePasteImport = async (source: "manual" | "clipboard" = "manual") => {
    if (busy || pasteInFlight.current) return;
    if (source === "clipboard") {
      pasteReturnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    pasteInFlight.current = true;
    try {
      await runTask(async () => {
        let text = pastedKml;
        const showManualPaste = (message: string) => {
          setPasteMessage(message);
          setPasteOpen(true);
        };
        if (source === "clipboard") {
          try {
            // Keep this call in the click's activation, before any other await.
            text = await platform.clipboard.readText();
          } catch (reason) {
            const message = reason instanceof PlatformError ? reason.message : "クリップボードを読み取れませんでした。";
            showManualPaste(`${message}下の欄にKMLを貼り付けてください。`);
            return;
          }
        }
        if (!text.trim()) {
          showManualPaste("読み込むテキストがありません。下の欄にKMLを貼り付けてください。");
          return;
        }
        setPastedKml(text);
        setPasteMessage(null);
        invalidateDestinationPatternRequests();
        let next: WebState;
        try {
          next = await application.importRoute({ filename: "pasted.kml", kml_text: text });
        } catch (reason) {
          showManualPaste(reason instanceof Error ? reason.message : "KMLを読み込めません。内容を確認してください。");
          return;
        }
        applyState(next, { freshImport: true });
        setNotice("貼付KMLから経路候補を読み込みました。");
        setPasteOpen(false);
      });
    } finally {
      pasteInFlight.current = false;
    }
  };

  const handleConfirmRoute = async () => {
    if (!state) return;
    const fuelGal = usableFuelGal(form);
    if (fuelGal === null) {
      setError("FUELは0より大きく200 gal以下で入力してください。");
      return;
    }
    const tglCount = touchAndGoCount(form);
    if (tglCount === null) {
      setError("TGLは0～20の整数で入力してください。");
      return;
    }
    const candidate = candidateFromKey(state.import.candidates, form.candidateKey);
    if (!candidate) {
      setError("飛行経路候補を選択してください。");
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
    const ftdWeather = ftdWeatherSettings(form);
    if (form.weatherMode === "FTD" && ftdWeather === null) {
      setError("FTD固定気象は、地上と5,000 ftの風向・風速を範囲内で入力してください。");
      return;
    }
    cancelPendingRecalculation();
    invalidateDestinationPatternRequests();
    const confirmed = await run(
      () =>
        application.confirmRoute({
          candidate_kind: candidate.kind,
          candidate_index: candidate.index,
          point_indices: [],
          route_use_confirmed: form.routeUseConfirmed,
          polygon_route_confirmed: form.polygonRouteConfirmed,
          flight_date: form.flightDate,
          departure_time_jst: form.departureTimeJst,
          total_usable_fuel_gal: fuelGal,
          default_variation_deg_east: form.variationDegEast,
          weather_mode: form.weatherMode,
          ftd_weather: form.weatherMode === "FTD" ? ftdWeather : null,
          run_up_included: form.runUpIncluded,
          nose_fairing_enabled: form.noseFairingEnabled,
          air_conditioning_enabled: form.airConditioningEnabled,
          descent_rate_fpm: form.descentRateFpm,
          tgl_count: tglCount,
          all_leg_altitude_ft_msl: form.allLegAltitudeFtMsl,
          use_penultimate_as_vrep: form.usePenultimateAsVrep,
        }),
      "経路を確定し、目的空港の場周経路高度を適用しました。",
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

  const handleAltitudeInputChange = (sectionId: string, value: string) => {
    const inputMode = altitudeGuidanceBySection.get(sectionId)?.inputMode;
    if (inputMode !== undefined && inputMode !== "EDITABLE") return;
    invalidateCalculationInputs();
    markDraftForAutosave();
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
    const guidance = altitudeGuidanceBySection.get(sectionId);
    const fixed = guidance !== undefined && guidance.inputMode !== "EDITABLE";
    const safeChanges = fixed
      ? Object.fromEntries(
          Object.entries(changes).filter(
            ([key]) => key !== "planned_altitude_ft_msl" && key !== "phase",
          ),
        ) as Partial<NavSection>
      : changes;
    if (Object.keys(safeChanges).length === 0) return;
    if (invalidate) {
      invalidateCalculationInputs();
      markDraftForAutosave();
    }
    setState((current) => {
      if (!current?.project) return current;
      const currentSection = current.project.sections.find(
        (section) => section.id === sectionId,
      );
      const arrival = current.project.metadata.ui_state?.arrival_plan;
      const changedVrepAltitude =
        currentSection?.phase === "VISUAL_ARRIVAL"
        && typeof safeChanges.planned_altitude_ft_msl === "number"
        && safeChanges.planned_altitude_ft_msl !== currentSection.planned_altitude_ft_msl;
      const nextArrival = changedVrepAltitude && arrival
        ? {
            ...arrival,
            altitude_mode: "MANUAL_NON_STANDARD_ENTRY" as const,
            manual_vrep_altitude_ft_msl: safeChanges.planned_altitude_ft_msl ?? null,
            manual_override_reason:
              arrival.manual_override_reason ?? ROUTE_EDITOR_VREP_REASON,
          }
        : arrival;
      return {
        ...current,
        project: {
          ...current.project,
          sections: current.project.sections.map((section) =>
            section.id === sectionId ? { ...section, ...safeChanges } : section,
          ),
          metadata: nextArrival
            ? {
                ...current.project.metadata,
                ui_state: {
                  ...current.project.metadata.ui_state,
                  arrival_plan: nextArrival,
                },
              }
            : current.project.metadata,
        },
      };
    });
  };

  const handleRenameRouteNode = async (nodeId: string, name: string) => {
    const next = await application.renameRouteNode(nodeId, name);
    applyState(next, { syncCalculationInputs: false });
  };

  const updatePayload = (
    sectionOverrides?: NavSection[],
    selectedPatternAltitudeFtMsl?: number,
    invalidFallbackProject?: Project,
  ): UpdateProjectInput => {
    const payloadSections = sectionOverrides ?? state?.project?.sections ?? [];
    if (!state?.project) throw new Error("Projectがありません。");
    const fallbackProject = invalidFallbackProject?.id === state.project.id
      ? invalidFallbackProject
      : undefined;
    const canonicalSections = new Map(
      (fallbackProject ?? state.project).sections.map((section) => [section.id, section]),
    );
    const fuelGal = usableFuelGal(form) ?? fallbackProject?.total_usable_fuel_gal ?? null;
    if (fuelGal === null) {
      throw new Error("FUELは0より大きく200 gal以下で入力してください。");
    }
    const tglCount = touchAndGoCount(form) ?? fallbackProject?.tgl_count ?? null;
    if (tglCount === null) {
      throw new Error("TGLは0～20の整数で入力してください。");
    }
    const flightDate = validFlightDate(form.flightDate)
      ? form.flightDate
      : fallbackProject?.flight_date;
    if (!flightDate) throw new Error("DATEを入力してください。");
    const departureTimeJst = validDepartureTimeJst(form.departureTimeJst)
      ? form.departureTimeJst
      : fallbackProject?.planned_departure_time_jst.slice(11, 16);
    if (!departureTimeJst) throw new Error("ETD JSTを入力してください。");
    const destinationElevation = state.airports.find(
      (airport) => airport.id === state.project?.destination_airport_id,
    )?.elevationFtMsl ?? null;
    const localPatternAltitude = patternAltitudeFtMsl(form.destinationPatternAltitudeFtMsl);
    const localPatternIsValid = localPatternAltitude !== null
      && (destinationElevation === null || localPatternAltitude > destinationElevation);
    const resolvedPatternAltitude = selectedPatternAltitudeFtMsl
      ?? (localPatternIsValid ? localPatternAltitude : undefined)
      ?? fallbackProject?.metadata.ui_state?.arrival_plan?.selected_pattern_altitude_ft_msl;
    if (resolvedPatternAltitude === undefined) {
      throw new Error("場周経路高度は飛行場標高より高い100 ft単位で入力してください。");
    }
    const invalidAltitudeSectionIds = new Set<string>();
    const plannedAltitudes = new Map(
      payloadSections.map((section) => {
        const guidance = altitudeGuidanceBySection.get(section.id);
        const fixed = guidance !== undefined && guidance.inputMode !== "EDITABLE";
        const canonicalSection = canonicalSections.get(section.id) ?? section;
        const rawAltitude = (
          fixed
            ? String(
                guidance.fixedAltitudeFtMsl
                  ?? canonicalSection.planned_altitude_ft_msl,
              )
            : sectionOverrides
            ? String(section.planned_altitude_ft_msl)
            : altitudeInputs[section.id] ?? String(section.planned_altitude_ft_msl)
        ).trim();
        if (!rawAltitude) {
          if (fallbackProject && canonicalSection) {
            invalidAltitudeSectionIds.add(section.id);
            return [section.id, canonicalSection.planned_altitude_ft_msl] as const;
          }
          throw new Error("すべてのLegに計画高度を入力してください。");
        }
        const altitude = Number(rawAltitude);
        if (
          !Number.isFinite(altitude) ||
          altitude < 100 ||
          altitude > 25000 ||
          altitude % 100 !== 0
        ) {
          if (fallbackProject && canonicalSection) {
            invalidAltitudeSectionIds.add(section.id);
            return [section.id, canonicalSection.planned_altitude_ft_msl] as const;
          }
          throw new Error("計画高度は100～25,000 ftの範囲で100 ft単位にしてください。");
        }
        return [section.id, altitude] as const;
      }),
    );
    const localArrival = state.project.metadata.ui_state?.arrival_plan ?? null;
    const invalidVrepAltitude = payloadSections.some(
      (section) => section.phase === "VISUAL_ARRIVAL"
        && invalidAltitudeSectionIds.has(section.id),
    );
    const arrival = invalidVrepAltitude && fallbackProject
      ? fallbackProject.metadata.ui_state?.arrival_plan ?? null
      : localArrival;
    let weatherMode = form.weatherMode;
    let ftdWeather = ftdWeatherSettings(form);
    if (weatherMode === "FTD" && ftdWeather === null && fallbackProject) {
      weatherMode = fallbackProject.weather_mode;
      ftdWeather = fallbackProject.ftd_weather;
    }
    if (weatherMode === "FTD" && ftdWeather === null) {
      throw new Error(
        "FTD固定気象は、地上と5,000 ftの風向・風速を範囲内で入力してください。",
      );
    }
    const orderedNodes = [...state.project.route_nodes].sort((a, b) => a.sequence - b.sequence);
    const fallbackVrep = orderedNodes.length >= 3 ? orderedNodes.at(-2)?.id ?? null : null;
    return {
      flight_date: flightDate,
      departure_time_jst: departureTimeJst,
      total_usable_fuel_gal: fuelGal,
      default_variation_deg_east: form.variationDegEast,
      weather_mode: weatherMode,
      ftd_weather: weatherMode === "FTD" ? ftdWeather : null,
      run_up_included: form.runUpIncluded,
      nose_fairing_enabled: form.noseFairingEnabled,
      air_conditioning_enabled: form.airConditioningEnabled,
      descent_rate_fpm: form.descentRateFpm,
      tgl_count: tglCount,
      sections: payloadSections.map((section) => {
        const guidance = altitudeGuidanceBySection.get(section.id);
        const fixed = guidance !== undefined && guidance.inputMode !== "EDITABLE";
        const canonicalSection = canonicalSections.get(section.id) ?? section;
        return {
          section_id: section.id,
          planned_altitude_ft_msl:
            plannedAltitudes.get(section.id) ?? canonicalSection.planned_altitude_ft_msl,
          phase: fixed ? canonicalSection.phase : section.phase,
          manual_wind_direction_deg: section.manual_wind_direction_deg,
          manual_wind_speed_kt: section.manual_wind_speed_kt,
          manual_wind_by_phase: section.manual_wind_by_phase ?? {},
          manual_temperature_c: section.manual_temperature_c,
          manual_temperature_c_by_phase: section.manual_temperature_c_by_phase ?? {},
          manual_tas_kt_by_phase: section.manual_tas_kt_by_phase ?? {},
          manual_tas_kt: section.manual_tas_kt,
        };
      }),
      visual_reporting_point_node_id: arrival?.visual_reporting_point_node_id ?? fallbackVrep,
      selected_pattern_altitude_ft_msl: resolvedPatternAltitude,
      arrival_altitude_mode: arrival?.altitude_mode ?? "STANDARD_DISTANCE_RULE",
      manual_vrep_altitude_ft_msl: arrival?.manual_vrep_altitude_ft_msl ?? null,
      manual_vrep_reason: arrival?.manual_override_reason ?? null,
    };
  };

  updatePayloadRef.current = updatePayload;

  const showDraftAutosaveFailure = () => {
    setError(DRAFT_AUTOSAVE_FAILURE);
  };

  const pendingDraftAutosave = (): DraftSaveRequest | null => (
    draftAutosaveQueuedRef.current
  );

  const drainDraftAutosaves = () => {
    if (draftAutosaveDrainRef.current) return;
    const drain = (async () => {
      while (draftAutosaveQueuedRef.current) {
        const request = draftAutosaveQueuedRef.current;
        draftAutosaveQueuedRef.current = null;
        draftAutosaveRunningRef.current = request;
        let saved = false;
        try {
          const next = await application.updateProject(request.payload);
          saved = true;
          if (request.projectId === projectIdRef.current && next.project) {
            canonicalProjectRef.current = next.project;
            const queued = pendingDraftAutosave();
            if (queued?.projectId === request.projectId && queued.useCanonicalFallback) {
              queued.payload = updatePayloadRef.current(
                undefined,
                undefined,
                next.project,
              );
            }
          }
          if (
            request.projectId === projectIdRef.current
            && request.generation === draftAutosaveGenerationRef.current
          ) {
            applyState(next, {
              syncCalculationInputs: false,
              syncDerivedArrival: request.syncDerivedArrival,
            });
            setDraftAutosaveVersion(0);
            setError((current) => current === DRAFT_AUTOSAVE_FAILURE ? null : current);
          }
        } catch {
          if (
            request.projectId === projectIdRef.current
            && request.generation === draftAutosaveGenerationRef.current
          ) {
            showDraftAutosaveFailure();
          }
        } finally {
          draftAutosaveRunningRef.current = null;
          request.waiters.forEach((resolve) => resolve(saved));
        }
      }
    })().finally(() => {
      draftAutosaveDrainRef.current = null;
      if (draftAutosaveQueuedRef.current) drainDraftAutosaves();
    });
    draftAutosaveDrainRef.current = drain;
  };

  const enqueueDraftAutosave = (
    generation: number,
    useCanonicalFallback: boolean,
    force = false,
  ): Promise<boolean> => {
    const projectId = projectIdRef.current;
    if (!projectId) return Promise.resolve(true);
    const payload = updatePayloadRef.current(
      undefined,
      undefined,
      useCanonicalFallback ? canonicalProjectRef.current ?? undefined : undefined,
    );
    const visualArrival = state?.project?.sections.find(
      (section) => section.phase === "VISUAL_ARRIVAL",
    );
    const visualArrivalInput = visualArrival
      ? (altitudeInputs[visualArrival.id]
        ?? String(visualArrival.planned_altitude_ft_msl)).trim()
      : "";
    const visualArrivalAltitude = Number(visualArrivalInput);
    const visualArrivalInputIsValid = Boolean(
      visualArrival
      && visualArrivalInput
      && Number.isFinite(visualArrivalAltitude)
      && visualArrivalAltitude >= 100
      && visualArrivalAltitude <= 25000
      && visualArrivalAltitude % 100 === 0,
    );
    const requestedPatternAltitude = payload.selected_pattern_altitude_ft_msl;
    const appliedPatternAltitude = state?.project
      ?.metadata.ui_state?.arrival_plan?.selected_pattern_altitude_ft_msl;
    const syncDerivedArrival = visualArrivalInputIsValid
      && typeof requestedPatternAltitude === "number"
      && requestedPatternAltitude !== appliedPatternAltitude;
    return new Promise((resolve) => {
      const running = draftAutosaveRunningRef.current;
      if (
        !force
        && running?.projectId === projectId
        && running.generation === generation
      ) {
        running.waiters.push(resolve);
        return;
      }
      const queued = draftAutosaveQueuedRef.current;
      if (queued?.projectId === projectId) {
        queued.generation = generation;
        queued.payload = payload;
        queued.useCanonicalFallback = useCanonicalFallback;
        queued.syncDerivedArrival = syncDerivedArrival;
        queued.waiters.push(resolve);
      } else {
        draftAutosaveQueuedRef.current = {
          generation,
          projectId,
          payload,
          useCanonicalFallback,
          syncDerivedArrival,
          waiters: [resolve],
        };
      }
      drainDraftAutosaves();
    });
  };

  const flushDraftAutosave = async (strict: boolean): Promise<boolean> => {
    if (draftAutosaveTimerRef.current !== null) {
      window.clearTimeout(draftAutosaveTimerRef.current);
      draftAutosaveTimerRef.current = null;
    }
    return enqueueDraftAutosave(
      draftAutosaveGenerationRef.current,
      !strict,
      strict,
    );
  };

  const waitForDraftAutosaves = async (): Promise<void> => {
    while (
      draftAutosaveDrainRef.current
      || draftAutosaveRunningRef.current
      || draftAutosaveQueuedRef.current
    ) {
      if (!draftAutosaveDrainRef.current && draftAutosaveQueuedRef.current) {
        drainDraftAutosaves();
      }
      const drain = draftAutosaveDrainRef.current;
      if (drain) await drain;
    }
  };

  const discardPendingDraftAutosave = async (): Promise<void> => {
    if (draftAutosaveTimerRef.current !== null) {
      window.clearTimeout(draftAutosaveTimerRef.current);
      draftAutosaveTimerRef.current = null;
    }
    draftAutosaveGenerationRef.current += 1;
    setDraftAutosaveVersion(0);
    await waitForDraftAutosaves();
  };

  useEffect(() => {
    if (draftAutosaveVersion === 0 || !state?.project) return;
    if (draftAutosaveTimerRef.current !== null) {
      window.clearTimeout(draftAutosaveTimerRef.current);
    }
    const generation = draftAutosaveVersion;
    draftAutosaveTimerRef.current = window.setTimeout(() => {
      draftAutosaveTimerRef.current = null;
      try {
        void enqueueDraftAutosave(generation, true).catch(showDraftAutosaveFailure);
      } catch {
        showDraftAutosaveFailure();
      }
    }, 300);
    return () => {
      if (draftAutosaveTimerRef.current !== null) {
        window.clearTimeout(draftAutosaveTimerRef.current);
        draftAutosaveTimerRef.current = null;
      }
    };
  }, [draftAutosaveVersion, state?.project?.id]);

  const destinationPatternProjectBasis = state ? patternRequestBasis(state) : null;
  const destinationPatternProjectId = state?.project?.id ?? null;
  const destinationPatternDestinationId = state?.project?.destination_airport_id ?? null;
  const destinationPatternApplied = state?.project?.metadata.ui_state?.arrival_plan
    ?.selected_pattern_altitude_ft_msl ?? null;
  const destinationPatternElevation = state?.airports.find(
    (airport) => airport.id === destinationPatternDestinationId,
  )?.elevationFtMsl ?? null;
  const destinationPatternRecalculates = Boolean(state?.outcome);

  useEffect(() => {
    if (restoredPattern.current === form.destinationPatternAltitudeFtMsl) return;
    restoredPattern.current = null;
    const selectedPattern = patternAltitudeFtMsl(form.destinationPatternAltitudeFtMsl);
    if (
      !destinationPatternRecalculates ||
      destinationPatternProjectBasis === null ||
      destinationPatternProjectId === null ||
      destinationPatternDestinationId === null ||
      selectedPattern === null ||
      (destinationPatternElevation !== null && selectedPattern <= destinationPatternElevation) ||
      selectedPattern === destinationPatternApplied
    ) return;
    const requestGeneration = destinationPatternGenerationRef.current;
    const calculationGeneration = calculationInputGenerationRef.current;
    const requestBasis = destinationPatternProjectBasis;
    let cancelled = false;
    const timeout = window.setTimeout(() => {
      destinationPatternRecalculationRef.current =
        destinationPatternRecalculationRef.current.then(async () => {
          if (
            cancelled ||
            requestGeneration !== destinationPatternGenerationRef.current ||
            calculationGeneration !== calculationInputGenerationRef.current ||
            requestBasis !== destinationPatternBasisRef.current
          ) return;
          const requestEpoch = lifecycle.current;
          try {
            const next = await (destinationPatternRecalculates
              ? application.updateAndRecalculate(updatePayloadRef.current(undefined, selectedPattern))
              : application.updateProject(updatePayloadRef.current(undefined, selectedPattern)));
            if (
              cancelled ||
              requestGeneration !== destinationPatternGenerationRef.current ||
              calculationGeneration !== calculationInputGenerationRef.current ||
              requestBasis !== destinationPatternBasisRef.current ||
              patternRequestBasis(next) !== requestBasis
            ) return;
            applyState(next, { syncCalculationInputs: true });
          } catch (reason) {
            if (requestEpoch !== lifecycle.current) return;
            applyCommittedFailure(reason);
            if (
              cancelled ||
              requestGeneration !== destinationPatternGenerationRef.current ||
              calculationGeneration !== calculationInputGenerationRef.current ||
              requestBasis !== destinationPatternBasisRef.current
            ) return;
            setError(
              reason instanceof Error
                ? reason.message
                : "場周経路高度を更新できませんでした。",
            );
          }
        });
    }, 600);
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [
    application,
    destinationPatternApplied,
    destinationPatternDestinationId,
    destinationPatternElevation,
    destinationPatternProjectBasis,
    destinationPatternProjectId,
    destinationPatternRecalculates,
    form.destinationPatternAltitudeFtMsl,
  ]);

  const handleNavLogEdit = (
    sectionId: string,
    phase: FlightPhase,
    field: NavLogEditableField,
    value: string,
  ) => {
    const inputSection = state?.project?.sections.find((section) => section.id === sectionId);
    if (!inputSection || !state?.project) return;
    const inputMode = altitudeGuidanceBySection.get(sectionId)?.inputMode;
    if (field === "plannedAltitude" && inputMode !== undefined && inputMode !== "EDITABLE") {
      return;
    }
    const currentDraft =
      navLogDraftsRef.current[sectionId] ?? draftFromSection(inputSection);
    const nextDraft = field === "temperature"
      ? {
          ...currentDraft,
          temperatureByPhase: {
            ...currentDraft.temperatureByPhase,
            [phase]: value,
          },
        }
      : field === "tas"
        ? {
            ...currentDraft,
            tasByPhase: {
              ...currentDraft.tasByPhase,
              [phase]: value,
            },
          }
      : field === "windDirection"
        ? {
            ...currentDraft,
            windDirectionByPhase: {
              ...currentDraft.windDirectionByPhase,
              [phase]: value,
            },
          }
        : field === "windSpeed"
          ? {
              ...currentDraft,
              windSpeedByPhase: {
                ...currentDraft.windSpeedByPhase,
                [phase]: value,
              },
            }
          : { ...currentDraft, [field]: value };
    const next: NavLogEditDrafts = {
      ...navLogDraftsRef.current,
      [sectionId]: nextDraft,
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
    setCalculationInputsAreLocallyCurrent(false);
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
        const requestEpoch = lifecycle.current;
        try {
          const editedSections = state.project!.sections.map((section) =>
            applyDraftToSection(section, navLogDrafts[section.id]),
          );
          const next = await application.updateAndRecalculate(updatePayload(editedSections));
          if (
            requestGeneration !== calculationInputGenerationRef.current ||
            !navLogEditPendingRef.current
          ) return;
          applyState(next, { syncCalculationInputs: true });
          navLogEditPendingRef.current = false;
          setNavLogEditVersion(0);
          setNavLogEditStatus({ kind: "saved", message: "自動再計算しました。" });
        } catch (reason) {
          if (requestEpoch !== lifecycle.current) return;
          const committed = applyCommittedFailure(reason);
          if (requestGeneration !== calculationInputGenerationRef.current) return;
          if (committed) {
            navLogEditPendingRef.current = false;
            setNavLogEditVersion(0);
          }
          setNavLogEditStatus({
            kind: "error",
            message: `${reason instanceof Error ? reason.message : "自動再計算に失敗しました。"} 直前の正常な計算結果を表示中です。`,
          });
        }
      });
    }, 700);
    return () => window.clearTimeout(timeout);
  }, [altitudeInputs, application, form, navLogDrafts, navLogEditVersion, state?.outcome, state?.project]);

  const handleCalculate = async () => {
    cancelPendingRecalculation();
    invalidateDestinationPatternRequests();
    setCalculationProgress({ percent: 0, message: "計算を開始しています。" });
    const calculated = await run(async () => {
      const saved = await flushDraftAutosave(true);
      if (!saved) throw new Error(DRAFT_AUTOSAVE_FAILURE);
      return application.calculate(setCalculationProgress);
    }, "NAV LOGを計算しました。準備状況と各値を確認してください。", {
      syncCalculationInputs: true,
      operation: "calculate",
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

  const handleSave = async (name: string) => {
    if (!state?.project) return;
    invalidateDestinationPatternRequests();
    const saved = await run(
      async () => {
        const draftSaved = await flushDraftAutosave(true);
        if (!draftSaved) throw new Error(DRAFT_AUTOSAVE_FAILURE);
        return application.saveProject(name);
      },
      "Projectをローカルへ保存しました。",
    );
    if (saved?.project) {
      setProjectName(saved.project.name);
      setSelectedProjectId(saved.project.id);
    }
  };

  const handleReplaceCheckPoints = async (checkPoints: CheckPointInput[]) => {
    const updated = await run(
      () =>
        application.replaceCheckPoints(checkPoints),
      "Check Pointを更新しました。NAV LOGを再計算してください。",
    );
    return updated !== undefined;
  };

  const handleLoad = async () => {
    if (!selectedProjectId) return;
    cancelPendingRecalculation();
    invalidateDestinationPatternRequests();
    const loaded = await run(
      async () => {
        const draftSaved = await flushDraftAutosave(false);
        if (!draftSaved) throw new Error(DRAFT_AUTOSAVE_FAILURE);
        return application.loadProject(selectedProjectId);
      },
      undefined,
      { syncCalculationInputs: true },
    );
    if (!loaded) return;
    setNotice(
      loaded.outcome
        ? loaded.readiness.calculationIsCurrent
          ? "保存済みProjectと最後の計算結果を開きました。"
          : "保存済みProjectと直前の計算結果を開きました。入力が変更されているため再計算してください。"
        : "保存済みProjectを開きました。NAV LOGを計算してください。",
    );
  };

  const handleDelete = async () => {
    if (!selectedProjectId) return;
    const selected = state?.savedProjects.find((project) => project.id === selectedProjectId);
    const selectedName =
      selected?.kind === "LATEST" ? "Latest" : (selected?.name ?? "選択中の経路");
    if (!window.confirm(`保存済みProject「${selectedName}」を削除しますか？`)) {
      return;
    }
    cancelPendingRecalculation();
    invalidateDestinationPatternRequests();
    const deletingCurrentProject = selectedProjectId === state?.project?.id;
    const deleted = await runTask(
      async () => {
        if (deletingCurrentProject) {
          await discardPendingDraftAutosave();
        } else {
          const draftSaved = await flushDraftAutosave(false);
          if (!draftSaved) throw new Error(DRAFT_AUTOSAVE_FAILURE);
        }
        return application.deleteProject(selectedProjectId);
      },
      { success: "保存済みProjectを削除しました。", fallbackError: "削除できませんでした。" },
    );
    if (deleted) {
      setSelectedProjectId("");
      applyState(deleted, { syncCalculationInputs: true });
    }
  };

  const handleNew = async () => {
    if (state?.project && !window.confirm("現在の未保存入力を閉じて新規作業を始めますか？")) {
      return;
    }
    cancelPendingRecalculation();
    invalidateDestinationPatternRequests();
    const reset = await runTask(
      async () => {
        await discardPendingDraftAutosave();
        try {
          clearSession(platform.session.storage);
        } catch {
          throw new Error("作業状態を破棄できませんでした。ブラウザのストレージ設定を確認してください。");
        }
        sessionDiscarded.current = true;
        try {
          await application.newWork();
        } catch (reason) {
          sessionDiscarded.current = false;
          if (latestSession.current) writeSession(latestSession.current, platform.session.storage);
          throw reason;
        }
        return true;
      },
      { fallbackError: "新規作業を開始できませんでした。" },
    );
    if (reset) {
      window.location.reload();
    }
  };

  const handleAcknowledge = async (ackKey: string, checked: boolean) => {
    await run(() =>
      application.acknowledge(ackKey, checked),
    );
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
  const selectedDepartureAirport = state.airports.find(
    (airport) => airport.id === form.departureAirportId,
  ) ?? null;
  const selectedArrival = state.project?.metadata.ui_state?.arrival_plan ?? null;
  const destinationPatternAltitude = patternAltitudeFtMsl(
    form.destinationPatternAltitudeFtMsl,
  );
  const validDestinationPatternAltitude = (
    destinationPatternAltitude !== null &&
    (selectedDestinationAirport === null ||
      destinationPatternAltitude > selectedDestinationAirport.elevationFtMsl)
  ) ? destinationPatternAltitude : null;
  const destinationApplied = Boolean(
    state.project &&
      selectedArrival?.selected_pattern_altitude_ft_msl !== null &&
      selectedArrival?.selected_pattern_altitude_ft_msl !== undefined &&
      selectedArrival.selected_pattern_altitude_source &&
      state.project.destination_airport_id === form.destinationAirportId &&
      state.project.departure_airport_id === form.departureAirportId &&
      selectedArrival.selected_pattern_altitude_ft_msl === validDestinationPatternAltitude,
  );
  const hasCalculationBlocker = state.readiness.issues.some(
    (issue) => issue.severity === "BLOCKER" && issue.code !== "RECALCULATION_REQUIRED",
  );
  const canCalculate = destinationApplied && !hasCalculationBlocker;
  const validFtdWeather = ftdWeatherSettings(form);
  const canConfirmRoute = Boolean(
    selectedCandidate &&
      form.routeUseConfirmed &&
      (selectedCandidate.kind !== "polygon" || form.polygonRouteConfirmed) &&
      selectedDepartureAirport &&
      selectedDestinationAirport &&
      usableFuelGal(form) !== null &&
      (form.weatherMode !== "FTD" || validFtdWeather !== null),
  );
  const calculationIsCurrent =
    state.readiness.calculationIsCurrent && calculationInputsAreLocallyCurrent;
  const currentRjfmGuidance = calculationIsCurrent
    ? state.outcome?.rjfm_departure_guidance ?? null
    : null;
  const currentRjfmInboundGuidance = calculationIsCurrent
    ? state.outcome?.rjfm_inbound_guidance ?? null
    : null;

  return (
    <div className="app-shell">
      <AccountSyncControl sync={application.sync} onChange={() => {
        if (application.refreshProjects) void application.refreshProjects().then(next => {
          setState(current => current ? { ...current, savedProjects: next.savedProjects, storageWarning: next.storageWarning } : current);
        }).catch(() => {});
      }} onResolved={async id => {
        cancelPendingRecalculation();
        await discardPendingDraftAutosave();
        const next = await application.refreshProjects?.();
        if (next?.savedProjects.some(project => project.id === id)) {
          applyState(await application.loadProject(id), { syncCalculationInputs: true });
        } else {
          await application.newWork();
          applyState(await application.bootstrap(), { syncCalculationInputs: true });
        }
      }} />
      <Header
        auth={application.auth}
        appVersion={state.runtime.appVersion}
        projectName={projectName}
        revision={state.project?.revision ?? null}
        savedProjects={state.savedProjects}
        selectedProjectId={selectedProjectId}
        busy={busy}
        informationUnread={informationUnread}
        knownIssuesUnread={knownIssuesUnread}
        onInformation={() => {
          setInformationOpen(true);
        }}
        onProjectNameChange={setProjectName}
        onSelectedProjectIdChange={setSelectedProjectId}
        onLoad={handleLoad}
        onDelete={handleDelete}
        onSave={handleSave}
        onNew={handleNew}
      />
      <ProgressRail activeStep={state.readiness.workflowStep} />
      <AccountNotice auth={application.auth} />

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

      {state.storageWarning && <div className="message-bar message-error" role="alert">{state.storageWarning}</div>}

      <main className="app-workspace">
        <ImportPlanPanel
          importState={state.import}
          airports={state.airports}
          form={form}
          setForm={setTrackedForm}
          projectExists={Boolean(state.project)}
          busy={busy}
          onResumePaste={pastedKml && !pasteOpen ? () => setPasteOpen(true) : undefined}
          onResumeKmz={pendingKmz && !kmzOpen ? () => setKmzOpen(true) : undefined}
          fileInput={<FileInput busy={busy} onFile={handleFile} />}
          onPaste={() => void handlePasteImport("clipboard")}
        />
        <RouteWorkspace
          onOpenExternalUrl={(url) => {
            try { platform.openExternalUrl(url); }
            catch (reason) {
              setError(reason instanceof Error ? reason.message : "外部リンクを開けません。");
            }
          }}
          checkPointDraft={checkPointDraft}
          setCheckPointDraft={setCheckPointDraft}
          nodeNameDraft={nodeNameDraft}
          setNodeNameDraft={setNodeNameDraft}
          candidate={selectedCandidate}
          project={state.project}
          altitudeGuidance={state.altitudeGuidance}
          rjfmMapReference={state.rjfmMapReference}
          outcome={state.outcome}
          calculationIsCurrent={calculationIsCurrent}
          altitudeInputs={altitudeInputs}
          destinationAirport={selectedDestinationAirport}
          destinationPatternAltitudeFtMsl={form.destinationPatternAltitudeFtMsl}
          checkPointPlanning={state.checkPointPlanning}
          busy={busy}
          routeUseConfirmed={form.routeUseConfirmed}
          polygonRouteConfirmed={form.polygonRouteConfirmed}
          canConfirmRoute={canConfirmRoute}
          onAltitudeInputChange={handleAltitudeInputChange}
          onDestinationPatternAltitudeChange={(value) => {
            invalidateDestinationPatternRequests();
            invalidateCalculationInputs();
            if (!state?.outcome) markDraftForAutosave();
            setForm((current) => ({
              ...current,
              destinationPatternAltitudeFtMsl: value,
            }));
          }}
          onSectionChange={handleSectionChange}
          onRenameRouteNode={handleRenameRouteNode}
          onRouteUseConfirmedChange={(checked) =>
            setTrackedForm((current) => ({ ...current, routeUseConfirmed: checked }))
          }
          onPolygonRouteConfirmedChange={(checked) =>
            setTrackedForm((current) => ({
              ...current,
              polygonRouteConfirmed: checked,
            }))
          }
          onConfirmRoute={handleConfirmRoute}
          onReplaceCheckPoints={handleReplaceCheckPoints}
        />
        <StatusPanel
          runtime={state.runtime}
          readiness={state.readiness}
          projectExists={Boolean(state.project)}
          weatherMode={state.project?.weather_mode ?? form.weatherMode}
          canCalculate={canCalculate}
          outcomeExists={Boolean(state.outcome)}
          busy={busy}
          activeOperation={activeOperation}
          onCalculate={handleCalculate}
          onAcknowledge={handleAcknowledge}
        />
      </main>

      {state.outcome && state.project && (
        <div
          ref={navLogRef}
          className="nav-log-focus-target"
          tabIndex={-1}
          aria-label="計算済みNAV LOG"
        >
          <div className="navlog-export-actions">
            <button className="secondary-button" disabled={busy} onClick={() => void runTask(
              () => platform.files.save(exportNavLog(state)),
              { success: "NAV LOG JSONのダウンロードを開始しました。" },
            )}>NAV LOG JSONをダウンロード</button>
            <button className="secondary-button" disabled={busy} onClick={() => void runTask(
              () => platform.clipboard.writeText(new TextDecoder().decode(exportNavLog(state).content)),
              { success: "NAV LOG JSONをコピーしました。" },
            )}>NAV LOG JSONをコピー</button>
          </div>
          <NavLogTable
            vorColumns={vorColumns}
            setVorColumns={setVorColumns}
            outcome={state.outcome}
            destinationWind={state.destinationWind}
            altitudeGuidance={state.altitudeGuidance}
            rjfmGuidance={currentRjfmGuidance}
            project={state.project}
            drafts={navLogDrafts}
            editErrors={navLogEditErrors}
            editStatus={navLogEditStatus}
            onEdit={handleNavLogEdit}
          />
        </div>
      )}
      {currentRjfmInboundGuidance && (
        <aside className="rjfm-inbound-guidance" aria-label="RJFM帰路の経路延長案内">
          <div className="rjfm-guidance-heading">
            <div>
              <span className="rjfm-guidance-eyebrow">RJFM INBOUND OPERATIONAL GUIDANCE</span>
              <h3>UMK後の経路延長</h3>
            </div>
            <span className="rjfm-inbound-guidance-status" role="status">
              {currentRjfmInboundGuidance.status}
            </span>
          </div>
          <p className="rjfm-guidance-intro">{currentRjfmInboundGuidance.message}</p>
          {currentRjfmInboundGuidance.status === "AVAILABLE" && (
            <dl className="rjfm-inbound-guidance-metrics" data-testid="rjfm-inbound-guidance-diagnostics">
              <div><dt>推奨 MZE DME</dt><dd>{inboundMetric(currentRjfmInboundGuidance.rounded_dme_nm)} NM</dd></div>
              <div><dt>丸め後延長距離</dt><dd>{inboundMetric(currentRjfmInboundGuidance.extra_distance_nm)} NM</dd></div>
              <div><dt>丸め後予測ETE</dt><dd>{inboundMetric(currentRjfmInboundGuidance.predicted_ete_min)} min</dd></div>
              <div><dt>最小KS4-3余裕</dt><dd>{inboundMetric(currentRjfmInboundGuidance.minimum_boundary_clearance_nm)} NM</dd></div>
              <div><dt>Raw DME / 延長距離</dt><dd>{inboundMetric(currentRjfmInboundGuidance.raw_dme_nm)} NM / {inboundMetric(currentRjfmInboundGuidance.raw_extra_distance_nm)} NM</dd></div>
              <div><dt>Raw ETE / 余裕</dt><dd>{inboundMetric(currentRjfmInboundGuidance.raw_predicted_ete_min)} min / {inboundMetric(currentRjfmInboundGuidance.raw_minimum_boundary_clearance_nm)} NM</dd></div>
              <div><dt>Raw / 丸め後Turn高度</dt><dd>{inboundMetric(currentRjfmInboundGuidance.raw_turn_altitude_ft_msl)} ft / {inboundMetric(currentRjfmInboundGuidance.rounded_turn_altitude_ft_msl)} ft</dd></div>
              <div><dt>探索方位 / 実方位</dt><dd>{inboundMetric(currentRjfmInboundGuidance.bearing_magnetic_deg, 2)}°M / {inboundMetric(currentRjfmInboundGuidance.actual_bearing_magnetic_deg, 2)}°M</dd></div>
              <div><dt>Raw / 丸め後Turn Point</dt><dd>{inboundPoint(currentRjfmInboundGuidance.raw_turn_point?.latitude_deg, currentRjfmInboundGuidance.raw_turn_point?.longitude_deg)} / {inboundPoint(currentRjfmInboundGuidance.rounded_turn_point?.latitude_deg, currentRjfmInboundGuidance.rounded_turn_point?.longitude_deg)}</dd></div>
            </dl>
          )}
          <p className="rjfm-inbound-guidance-note">
            この案内はwarningのみです。NAV LOGの物理経路・距離・針路を変更しません。
          </p>
        </aside>
      )}

      <footer className="app-footer">
        <span>AutoNavLogは非公式の地上準備支援ツールです。</span>
        <span>参照 {state.runtime.referenceRevision} / 性能 {state.runtime.performanceRevision}</span>
      </footer>

      {activeOperation === "calculate" && (
        <CalculationProgressOverlay {...calculationProgress}
          onSignOut={application.auth?.getState().account
            ? () => { void application.auth!.signOut().catch(() => {}); } : undefined} />
      )}

      <PasteDialog
        open={pasteOpen}
        value={pastedKml}
        message={pasteMessage}
        returnFocusRef={pasteReturnFocus}
        busy={busy}
        onChange={setPastedKml}
        onClose={() => setPasteOpen(false)}
        onImport={() => void handlePasteImport()}
      />

      <InformationDialog
        open={informationOpen}
        releases={informationData.information.releases}
        knownIssues={informationData.information.knownIssues ?? []}
        onClose={() => setInformationOpen(false)}
        onOpened={() => {
          markInformationSeen(informationData);
          setInformationUnread(false);
          setKnownIssuesUnread(false);
        }}
      />

      {pendingKmz && kmzOpen && (
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
