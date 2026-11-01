import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { LegacyAccountControl, LegacyMigrationBoundary } from "../../src/components/LegacyAccountControl";
import "../../src/styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><LegacyMigrationBoundary>
  <LegacyAccountControl busy={false} />
  <button type="button">Legacy workspace</button>
</LegacyMigrationBoundary></StrictMode>);
