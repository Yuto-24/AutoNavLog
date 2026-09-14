import { ApplicationError } from "./application";
import type { AutoNavLogApplication, ImportRouteInput, ConfirmRouteInput, UpdateProjectInput, ProgressListener } from "./application";
import type { CheckPointInput, WebState, WorkingRecovery } from "./types";
import { LocalClient } from "./localClient";

export class LocalApplication implements AutoNavLogApplication {
  private client: Pick<LocalClient, "request" | "dispose"> | undefined;
  constructor(private readonly createClient: () => Pick<LocalClient, "request" | "dispose"> = () => new LocalClient()) {}

  private async execute(operation: string, input?: unknown): Promise<WebState> {
    try {
      return await (this.client ??= this.createClient()).request<WebState>(operation, input);
    } catch (error) {
      if (error instanceof ApplicationError) throw error;
      throw new ApplicationError("処理を実行できませんでした。画面を再読み込みしてください。", "APPLICATION_UNAVAILABLE");
    }
  }

  bootstrap(recovery?: WorkingRecovery) { return this.execute("bootstrap", recovery); }
  importRoute(input: ImportRouteInput) { return this.execute("importRoute", input); }
  confirmRoute(input: ConfirmRouteInput) { return this.execute("confirmRoute", input); }
  updateProject(input: UpdateProjectInput) { return this.execute("updateProject", input); }
  updateAndRecalculate(input: UpdateProjectInput) { return this.execute("updateAndRecalculate", input); }
  renameRouteNode(nodeId: string, name: string) { return this.execute("renameRouteNode", { node_id: nodeId, name }); }
  replaceCheckPoints(checkPoints: CheckPointInput[]) { return this.execute("replaceCheckPoints", { check_points: checkPoints }); }
  acknowledge(key: string, checked: boolean) { return this.execute("acknowledge", { key, checked }); }
  async calculate(onProgress?: ProgressListener) {
    // Report only known milestones; finer progress is deferred to #159.
    onProgress?.({ percent: 0, message: "計算を開始しています。" });
    const state = await this.execute("calculate");
    onProgress?.({ percent: 100, message: "計算が完了しました。" });
    return state;
  }
  private async persistenceUnavailable(): Promise<WebState> {
    throw new ApplicationError("ブラウザへのProject保存・読込・削除は未対応です（#124）。", "LOCAL_PERSISTENCE_UNAVAILABLE", { issue: 124 });
  }
  saveProject(_name: string) { return this.persistenceUnavailable(); }
  loadProject(_projectId: string) { return this.persistenceUnavailable(); }
  deleteProject(_projectId: string) { return this.persistenceUnavailable(); }
  async newWork() {
    this.client?.dispose();
    this.client = undefined;
  }
}
