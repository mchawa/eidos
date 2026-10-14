/**
 * Stand-in for optional Mermaid dependencies that the air-gapped single-file portal bundle
 * deliberately does not ship (ELK layout engine, KaTeX math, Cytoscape-based mindmap/architecture
 * diagrams). Mermaid only loads them lazily when a diagram explicitly asks for that feature; the portal's
 * generated diagrams (C4 flowcharts, screen/flow state machines) never do. See vite.config.ts.
 */
function unavailable(feature: string): never {
  throw new Error(`${feature} is not bundled in the Eidos portal (size budget, DLT-2026-041).`);
}

const stub: any = function unbundledOptionalDependency() {
  return unavailable("This Mermaid feature");
};
stub.use = () => stub;
stub.renderToString = () => unavailable("KaTeX math rendering");
stub.default = stub;

export default stub;
