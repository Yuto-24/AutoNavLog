import type { ImportRouteInput } from "./application";
import type { PlatformFile } from "./platform";

// Serialization for the existing shared Importer contract, independent of paths/DOM.
export function routeImportInput(file: PlatformFile): Extract<ImportRouteInput, { content_base64: string }> {
  const bytes = file.content;
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return { filename: file.name, content_base64: btoa(binary) };
}
