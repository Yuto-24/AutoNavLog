import { useEffect, useRef } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => !element.hidden && element.getAttribute("aria-hidden") !== "true",
  );
}

export function useModalFocusTrap<ElementType extends HTMLElement>(open: boolean) {
  const dialogRef = useRef<ElementType | null>(null);

  useEffect(() => {
    if (!open || !dialogRef.current) return;
    const dialog = dialogRef.current;
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const initial =
      dialog.querySelector<HTMLElement>("[data-modal-autofocus]") ??
      focusableElements(dialog)[0] ??
      dialog;
    initial.focus();

    const trapTab = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusable = focusableElements(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", trapTab);
    return () => {
      document.removeEventListener("keydown", trapTab);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [open]);

  return dialogRef;
}
