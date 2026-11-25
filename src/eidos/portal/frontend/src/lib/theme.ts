import * as React from "react";

export function readDocumentDarkMode(): boolean {
  return (
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark")
  );
}

/**
 * SSR-safe reactive dark-mode hook. Honors an explicit `darkMode` boolean when provided; otherwise
 * reads `document.documentElement.classList.contains("dark")` and observes `class` attribute
 * mutations so theme toggles re-render embedded diagrams across all portal views without
 * prop-drilling.
 */
export function useDarkMode(explicitDarkMode?: boolean): boolean {
  const [observedDark, setObservedDark] = React.useState<boolean>(() =>
    explicitDarkMode !== undefined ? explicitDarkMode : readDocumentDarkMode(),
  );

  React.useEffect(() => {
    if (explicitDarkMode !== undefined) return;
    if (typeof document === "undefined") return;

    const sync = () => {
      const next = readDocumentDarkMode();
      setObservedDark((prev) => (prev === next ? prev : next));
    };
    sync();

    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, [explicitDarkMode]);

  return explicitDarkMode !== undefined ? explicitDarkMode : observedDark;
}
