import type { ReactNode } from "react";

export function ExternalLink({ href, onOpen, children }: {
  href: string; onOpen: (url: string) => void; children: ReactNode;
}) {
  return <a href={href} target="_blank" rel="noopener noreferrer" onClick={event => {
    // Keep ordinary link affordances (copy URL, modifier clicks, context menu).
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpen(href);
  }}>{children}</a>;
}
