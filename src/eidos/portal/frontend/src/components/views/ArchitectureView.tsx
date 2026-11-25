import * as React from "react";
import { renderMermaidSvg, sanitizeMermaidCode } from "@/lib/mermaid";
import { hasSpecBodyOrChunk } from "@/lib/specChunks";
import {
  ZoomIn,
  ZoomOut,
  RotateCcw,
  ArrowUp,
  Box,
  Layers,
  FileCode,
  ShieldCheck,
  ChevronRight,
  Maximize2,
  Maximize,
  Minimize2,
  Expand,
  Shrink,
  Move,
  Sparkles,
  BookOpen,
  Crosshair,
  ExternalLink,
  ChevronDown,
  GitFork,
  X,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import { AsciiDocRenderer } from "@/components/ui/AsciiDocRenderer";
import { EntityMemoriesStrip } from "@/components/ui/EntityMemoriesStrip";
import { requestSpecNavigation } from "@/lib/specNav";
import {
  applyC4BlastRadiusEdges,
  applyC4ConnectionHighlight,
  computeCenterPanForNode,
  computeFullPageZoom,
  parseMermaidEdges,
  resolveSvgNodeId,
  restoreC4SvgStyles,
  type C4ConnectionHighlightResult,
  type HighlightDirection,
} from "@/lib/c4Highlight";
import {
  formatContainerType,
  formatDisplayTitle,
  formatProtocol,
  humanizeInvariantTitle,
  humanizeToken,
  resolveEntityTitle,
  useEntityLabels,
} from "@/lib/presentation";
import { cn } from "@/lib/utils";
import type { PortalData, SpecComponent, SpecContract, SpecInvariant } from "@/types";

export interface ArchitectureViewProps {
  data: PortalData;
  darkMode?: boolean;
  initialComponentId?: string;
  initialComponentNonce?: number;
  onClearSelectedComponent?: () => void;
}

const getTierStyle = (tierId?: string) => {
  switch (tierId) {
    case "tier_presentation":
      return { bg: "bg-sky-500/15", text: "text-sky-400", border: "border-sky-500/30", dot: "bg-sky-400" };
    case "tier_service":
      return { bg: "bg-amber-500/15", text: "text-amber-400", border: "border-amber-500/30", dot: "bg-amber-400" };
    case "tier_extensibility":
      return { bg: "bg-purple-500/15", text: "text-purple-400", border: "border-purple-500/30", dot: "bg-purple-400" };
    case "tier_core":
      return { bg: "bg-indigo-500/15", text: "text-indigo-400", border: "border-indigo-500/30", dot: "bg-indigo-400" };
    case "tier_persistence":
      return { bg: "bg-teal-500/15", text: "text-teal-400", border: "border-teal-500/30", dot: "bg-teal-400" };
    default:
      return { bg: "bg-slate-500/15", text: "text-slate-400", border: "border-slate-500/30", dot: "bg-slate-400" };
  }
};

function sanitizeNodeSlug(id: string): string {
  return id.replace(/[^a-zA-Z0-9_]/g, "_");
}

export function ArchitectureView({
  data,
  darkMode = true,
  initialComponentId,
  initialComponentNonce,
  onClearSelectedComponent,
}: ArchitectureViewProps) {
  const labelIndex = useEntityLabels();
  const [level, setLevelState] = React.useState<1 | 2 | 3>(1);
  const [selectedContainerId, setSelectedContainerId] = React.useState<string>(
    data.components[0]?.id || ""
  );
  const [zoom, setZoom] = React.useState<number>(1);
  const [pan, setPan] = React.useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isExpanded, setIsExpanded] = React.useState<boolean>(false);
  const [isFullPage, setIsFullPage] = React.useState<boolean>(false);
  const [highlightedNodeId, setHighlightedNodeId] = React.useState<string>("");
  const [highlightDirection, setHighlightDirection] = React.useState<HighlightDirection>("all");
  const [focusClickMode, setFocusClickMode] = React.useState<boolean>(false);
  const [connectionSummary, setConnectionSummary] = React.useState<C4ConnectionHighlightResult | null>(null);
  const [inspectorOpen, setInspectorOpen] = React.useState<boolean>(false);
  const [inspectedComponent, setInspectedComponent] = React.useState<SpecComponent | null>(null);
  const [focusedSubComponentId, setFocusedSubComponentId] = React.useState<string | null>(null);
  const [expandedSubSpecIds, setExpandedSubSpecIds] = React.useState<Record<string, boolean>>({});
  const [inspectedContract, setInspectedContract] = React.useState<SpecContract | null>(null);
  const [blastTargetId, setBlastTargetId] = React.useState<string>("");
  const [diagramError, setDiagramError] = React.useState<string | null>(null);
  const pendingHighlightNodeRef = React.useRef<string | null>(null);

  const selectHighlightNode = React.useCallback((nodeId: string) => {
    setHighlightedNodeId(nodeId);
    if (nodeId) {
      setBlastTargetId("");
    }
  }, []);

  const selectBlastTarget = React.useCallback((targetId: string) => {
    setBlastTargetId(targetId);
    if (targetId) {
      setHighlightedNodeId("");
    }
  }, []);

  const selectedContainerTitle = React.useMemo(() => {
    const comp = data.components.find((c) => c.id === selectedContainerId);
    return comp
      ? formatDisplayTitle(comp.title, comp.id)
      : resolveEntityTitle(selectedContainerId, labelIndex, "Container");
  }, [data.components, selectedContainerId, labelIndex]);

  React.useEffect(() => {
    if (!initialComponentId) return;
    const ctrMatch = (data.contracts || []).find((ct) => ct.id === initialComponentId);
    if (ctrMatch) {
      setInspectedContract(ctrMatch);
      return;
    }
    const cntMatch = (data.components || []).find((c) => c.id === initialComponentId);
    if (cntMatch) {
      setFocusedSubComponentId(null);
      setInspectedComponent(cntMatch);
      setInspectorOpen(true);
      return;
    }
    for (const c of data.components || []) {
      const sub = (c.internal_components || []).find((ic) => ic.id === initialComponentId);
      if (sub) {
        setSelectedContainerId(c.id);
        setLevelState(3);
        setFocusedSubComponentId(sub.id);
        setInspectedComponent(c);
        setInspectorOpen(true);
        return;
      }
    }
  }, [initialComponentId, initialComponentNonce, data.components, data.contracts]);

  // Client-side k-hop Blast Radius Simulator over data.edges
  const blastRadiusReport = React.useMemo(() => {
    if (!blastTargetId) return null;
    const edges = data.edges || [];
    const upstream1 = new Set<string>();
    const downstream1 = new Set<string>();
    const upstream2 = new Set<string>();
    const downstream2 = new Set<string>();

    edges.forEach((e) => {
      if (e.target === blastTargetId && e.source !== blastTargetId) {
        upstream1.add(e.source);
      }
      if (e.source === blastTargetId && e.target !== blastTargetId) {
        downstream1.add(e.target);
      }
    });

    edges.forEach((e) => {
      if (upstream1.has(e.target) && e.source !== blastTargetId && !upstream1.has(e.source)) {
        upstream2.add(e.source);
      }
      if (downstream1.has(e.source) && e.target !== blastTargetId && !downstream1.has(e.target)) {
        downstream2.add(e.target);
      }
    });

    const hop1 = new Set<string>([...upstream1, ...downstream1]);
    const hop2 = new Set<string>([...upstream2, ...downstream2]);
    const allImpacted = new Set<string>([blastTargetId, ...hop1, ...hop2]);

    return {
      targetId: blastTargetId,
      upstream1: Array.from(upstream1),
      downstream1: Array.from(downstream1),
      upstream2: Array.from(upstream2),
      downstream2: Array.from(downstream2),
      hop1,
      hop2,
      allImpacted,
    };
  }, [blastTargetId, data.edges]);

  const currentDiagramSource = React.useMemo(() => {
    if (level === 1) {
      return data.diagrams.c4_context || "graph TD\nUser[User]-->System[System]";
    } else if (level === 2) {
      return data.diagrams.c4_containers || "graph TD\nCore[System]";
    } else {
      const compMap = data.diagrams.c4_components || {};
      return (
        compMap[selectedContainerId] ||
        `graph TD\n${sanitizeNodeSlug(selectedContainerId || "System")}["${selectedContainerTitle}"]`
      );
    }
  }, [level, selectedContainerId, selectedContainerTitle, data.diagrams]);

  const parsedDiagram = React.useMemo(
    () => parseMermaidEdges(currentDiagramSource),
    [currentDiagramSource]
  );

  // Build humanized dropdown options for every node rendered in the active C4 diagram
  const highlightOptions = React.useMemo(() => {
    const sysId = data.overview?.system_id || "SYS";
    const sysSlug = sanitizeNodeSlug(sysId);
    const sysTitle = data.overview?.system_title || "System";
    const options: Array<{ nodeId: string; label: string; entityId?: string }> = [];
    const seen = new Set<string>();

    const extractInlineLabel = (nid: string): string | null => {
      const escaped = nid.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const m = new RegExp(`\\b${escaped}\\s*(?:\\[|\\(|\\{)\\s*"?([^"\\]\\)\\}]+)`, "m").exec(
        currentDiagramSource
      );
      if (!m) return null;
      const firstLine = m[1]
        .replace(/<[^>]+>/g, " ")
        .split(/\\n|\n/)[0]
        .replace(/\s+/g, " ")
        .trim();
      return firstLine || null;
    };

    const resolveOption = (nid: string): { nodeId: string; label: string; entityId?: string } => {
      if (nid === "SYS" || nid === sysSlug || nid === sysId) {
        return { nodeId: nid, label: sysTitle, entityId: sysId };
      }

      const cntMatch = data.components.find(
        (c) =>
          nid === sanitizeNodeSlug(c.id) ||
          nid === c.id ||
          nid === `peer_${sanitizeNodeSlug(c.id)}`
      );
      if (cntMatch) {
        return {
          nodeId: nid,
          label: formatDisplayTitle(cntMatch.title, cntMatch.id),
          entityId: cntMatch.id,
        };
      }

      const ctrMatch = (data.contracts || []).find(
        (ct) =>
          nid === `ctr_${sanitizeNodeSlug(ct.id)}` ||
          nid === sanitizeNodeSlug(ct.id) ||
          nid === ct.id
      );
      if (ctrMatch) {
        return {
          nodeId: nid,
          label: formatDisplayTitle(ctrMatch.title, ctrMatch.id),
          entityId: ctrMatch.id,
        };
      }

      for (const comp of data.components) {
        const subMatch = (comp.internal_components || []).find(
          (sub) =>
            sub.id &&
            (nid === `mod_${sanitizeNodeSlug(sub.id)}` ||
              nid === sanitizeNodeSlug(sub.id) ||
              nid === sub.id)
        );
        if (subMatch) {
          return {
            nodeId: nid,
            label: formatDisplayTitle(subMatch.title, subMatch.id),
            entityId: subMatch.id,
          };
        }
      }

      const perMatch = (data.personas || []).find(
        (p) => nid === sanitizeNodeSlug(p.id) || nid === p.id
      );
      if (perMatch) {
        return {
          nodeId: nid,
          label: formatDisplayTitle(perMatch.title, perMatch.id),
          entityId: perMatch.id,
        };
      }

      const inlineLabel = extractInlineLabel(nid);
      if (inlineLabel) {
        return { nodeId: nid, label: inlineLabel };
      }
      return {
        nodeId: nid,
        label: humanizeToken(nid.replace(/^(?:CNT|CMP|CTR|PER|EXT|mod|peer|ctr)_/i, "")),
      };
    };

    parsedDiagram.nodeIds.forEach((nid) => {
      if (seen.has(nid)) return;
      seen.add(nid);
      options.push(resolveOption(nid));
    });

    return options.sort((a, b) => a.label.localeCompare(b.label));
  }, [parsedDiagram, currentDiagramSource, data.overview, data.components, data.contracts, data.personas]);

  const selectedHighlightOption = React.useMemo(
    () => highlightOptions.find((opt) => opt.nodeId === highlightedNodeId) || null,
    [highlightOptions, highlightedNodeId]
  );

  // Unified SVG highlight pipeline for Component Connection Highlighting and Blast Radius
  const applyDiagramHighlights = React.useCallback(() => {
    const svgRoot = diagramRef.current?.querySelector("svg") || diagramRef.current;
    if (!svgRoot) return;

    if (highlightedNodeId) {
      const result = applyC4ConnectionHighlight(svgRoot, {
        selectedNodeId: highlightedNodeId,
        direction: highlightDirection,
        parsedEdges: parsedDiagram.edges,
        knownNodeIds: parsedDiagram.nodeIds,
      });
      setConnectionSummary(result);
      return;
    }

    restoreC4SvgStyles(svgRoot);
    setConnectionSummary(null);

    if (!blastRadiusReport) return;

    const nodes = svgRoot.querySelectorAll(
      '[data-c4-entity], [id*="CNT-"], [id*="CNT_"], [id*="CMP-"], [id*="CMP_"], [id*="SYS-"], .node'
    );
    const nodeMatchesEntity = (el: Element, entityId: string): boolean => {
      if (!entityId) return false;
      const explicit = el.getAttribute("data-c4-entity") || "";
      if (explicit === entityId) return true;
      const txt = el.textContent || "";
      const nid = el.id || "";
      const slug = sanitizeNodeSlug(entityId);
      return txt.includes(entityId) || nid.includes(slug) || nid.includes(entityId);
    };

    const impactedMermaidNodeIds = new Set<string>();

    nodes.forEach((n) => {
      const el = n as HTMLElement;
      const shape = el.querySelector<SVGElement>("rect, polygon, path, circle");
      const saveOrigShapeStyle = () => {
        if (!shape) return;
        if (shape.getAttribute("data-orig-stroke") === null) {
          shape.setAttribute("data-orig-stroke", shape.style.stroke || "");
          shape.setAttribute("data-orig-stroke-width", shape.style.strokeWidth || "");
          shape.setAttribute("data-orig-dasharray", shape.style.strokeDasharray || "");
        }
      };

      const isTarget = nodeMatchesEntity(el, blastRadiusReport.targetId);
      let isHop1 = false;
      let isHop2 = false;
      if (!isTarget) {
        blastRadiusReport.hop1.forEach((id) => {
          if (nodeMatchesEntity(el, id)) isHop1 = true;
        });
        if (!isHop1) {
          blastRadiusReport.hop2.forEach((id) => {
            if (nodeMatchesEntity(el, id)) isHop2 = true;
          });
        }
      }

      const resolvedNid = resolveSvgNodeId(el, parsedDiagram.nodeIds);
      if (isTarget || isHop1 || isHop2) {
        if (resolvedNid) impactedMermaidNodeIds.add(resolvedNid);
      }

      if (isTarget) {
        el.classList.add("blast-radius-target");
        el.setAttribute("data-blast-hop", "target");
        el.style.opacity = "1";
        el.style.filter = "drop-shadow(0 0 12px rgba(59, 130, 246, 0.95))";
        saveOrigShapeStyle();
        if (shape) {
          shape.style.stroke = "#3b82f6";
          shape.style.strokeWidth = "3.5px";
          shape.style.strokeDasharray = "";
        }
      } else if (isHop1) {
        el.classList.add("blast-radius-1-hop");
        el.setAttribute("data-blast-hop", "1-hop");
        el.style.opacity = "1";
        el.style.filter = "drop-shadow(0 0 10px rgba(244, 63, 94, 0.88))";
        saveOrigShapeStyle();
        if (shape) {
          shape.style.stroke = "#f43f5e";
          shape.style.strokeWidth = "3px";
          shape.style.strokeDasharray = "";
        }
      } else if (isHop2) {
        el.classList.add("blast-radius-2-hop");
        el.setAttribute("data-blast-hop", "2-hop");
        el.style.opacity = "0.94";
        el.style.filter = "drop-shadow(0 0 7px rgba(245, 158, 11, 0.78))";
        saveOrigShapeStyle();
        if (shape) {
          shape.style.stroke = "#f59e0b";
          shape.style.strokeWidth = "2.5px";
          shape.style.strokeDasharray = "5 3";
        }
      } else {
        el.classList.add("blast-radius-dimmed");
        el.setAttribute("data-blast-hop", "dimmed");
        el.style.opacity = "0.22";
        el.style.filter = "grayscale(0.45)";
      }
    });

    applyC4BlastRadiusEdges(svgRoot, {
      impactedNodeIds: impactedMermaidNodeIds,
      parsedEdges: parsedDiagram.edges,
      knownNodeIds: parsedDiagram.nodeIds,
    });
  }, [highlightedNodeId, highlightDirection, parsedDiagram, blastRadiusReport]);

  React.useEffect(() => {
    applyDiagramHighlights();
  }, [applyDiagramHighlights, level, selectedContainerId]);

  const activeTiers = React.useMemo(() => {
    const tierMap = new Map<string, { id: string; name: string }>();
    data.components.forEach((c) => {
      if (c.tier && !tierMap.has(c.tier)) {
        tierMap.set(c.tier, { id: c.tier, name: c.tier_name || c.tier });
      }
    });
    return Array.from(tierMap.values());
  }, [data.components]);

  const containerRef = React.useRef<HTMLDivElement>(null);
  const diagramRef = React.useRef<HTMLDivElement>(null);
  const isDraggingRef = React.useRef(false);
  const dragStartRef = React.useRef({ x: 0, y: 0, panX: 0, panY: 0 });
  const hasMovedRef = React.useRef(false);

  const autoFitDiagram = React.useCallback(() => {
    if (!diagramRef.current || !containerRef.current) return;
    const svgEl = diagramRef.current.querySelector("svg");
    if (!svgEl) return;

    let svgWidth = 0;
    let svgHeight = 0;

    if (svgEl.viewBox && svgEl.viewBox.baseVal && svgEl.viewBox.baseVal.width > 0) {
      svgWidth = svgEl.viewBox.baseVal.width;
      svgHeight = svgEl.viewBox.baseVal.height;
    } else {
      const bbox = svgEl.getBBox();
      svgWidth = bbox.width;
      svgHeight = bbox.height;
    }

    if (svgWidth === 0 || svgHeight === 0) {
      setZoom(1.1);
      setPan({ x: 0, y: 0 });
      return;
    }

    svgEl.removeAttribute("width");
    svgEl.removeAttribute("height");
    svgEl.style.width = `${svgWidth}px`;
    svgEl.style.height = `${svgHeight}px`;
    svgEl.style.maxWidth = "none";
    svgEl.style.maxHeight = "none";

    diagramRef.current.style.width = `${svgWidth}px`;
    diagramRef.current.style.height = `${svgHeight}px`;

    const containerWidth = containerRef.current.clientWidth - 80;
    const containerHeight = containerRef.current.clientHeight - 80;

    const scaleX = containerWidth / svgWidth;
    const scaleY = containerHeight / svgHeight;
    const optimalScale = Math.min(scaleX, scaleY);

    const clampedZoom = Number(Math.min(1.0, optimalScale).toFixed(2));
    setZoom(clampedZoom);
    setPan({ x: 0, y: 0 });
  }, []);

  const applyFullPageZoom = React.useCallback(
    (targetNodeId?: string) => {
      if (!diagramRef.current || !containerRef.current) return;
      const svgEl = diagramRef.current.querySelector("svg");
      if (!svgEl) return;

      let svgWidth = 0;
      let svgHeight = 0;
      if (svgEl.viewBox && svgEl.viewBox.baseVal && svgEl.viewBox.baseVal.width > 0) {
        svgWidth = svgEl.viewBox.baseVal.width;
        svgHeight = svgEl.viewBox.baseVal.height;
      } else if (typeof svgEl.getBBox === "function") {
        const bbox = svgEl.getBBox();
        svgWidth = bbox.width;
        svgHeight = bbox.height;
      }

      const nextZoom = computeFullPageZoom(
        { width: svgWidth, height: svgHeight },
        {
          width: containerRef.current.clientWidth || window.innerWidth,
          height: containerRef.current.clientHeight || window.innerHeight,
        }
      );

      let nextPan = { x: 0, y: 0 };
      const focusNode = targetNodeId ?? highlightedNodeId;
      if (focusNode && svgWidth > 0 && svgHeight > 0) {
        const nodeEls = Array.from(svgEl.querySelectorAll("g.node, .node"));
        const matchEl = nodeEls.find(
          (el) => resolveSvgNodeId(el, parsedDiagram.nodeIds) === focusNode
        ) as SVGGraphicsElement | undefined;
        if (matchEl && typeof matchEl.getBBox === "function") {
          try {
            const nodeBox = matchEl.getBBox();
            nextPan = computeCenterPanForNode(
              { width: svgWidth, height: svgHeight },
              nodeBox,
              nextZoom
            );
          } catch {
            nextPan = { x: 0, y: 0 };
          }
        }
      }

      setZoom(nextZoom);
      setPan(nextPan);
    },
    [highlightedNodeId, parsedDiagram.nodeIds]
  );

  const enterFullPage = React.useCallback(() => {
    setIsFullPage(true);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        applyFullPageZoom();
      });
    });
  }, [applyFullPageZoom]);

  const exitFullPage = React.useCallback(() => {
    setIsFullPage(false);
    requestAnimationFrame(() => {
      autoFitDiagram();
    });
  }, [autoFitDiagram]);

  // Body scroll lock & Escape dismissal for full-page stage (respects open Sheet drawers on top)
  React.useEffect(() => {
    if (!isFullPage) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (inspectorOpen || inspectedContract) return;
        e.preventDefault();
        exitFullPage();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [isFullPage, inspectorOpen, inspectedContract, exitFullPage]);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    isDraggingRef.current = true;
    hasMovedRef.current = false;
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      panX: pan.x,
      panY: pan.y,
    };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDraggingRef.current) return;
    const dx = e.clientX - dragStartRef.current.x;
    const dy = e.clientY - dragStartRef.current.y;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) {
      hasMovedRef.current = true;
    }
    setPan({
      x: dragStartRef.current.panX + dx,
      y: dragStartRef.current.panY + dy,
    });
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
    if (hasMovedRef.current) {
      window.setTimeout(() => {
        hasMovedRef.current = false;
      }, 0);
    }
  };

  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const minZoom = isFullPage ? 0.25 : 0.35;
    const maxZoom = isFullPage ? 4.0 : 3.0;
    const onNativeWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const delta = e.deltaY < 0 ? 0.1 : -0.1;
      setZoom((prev) => Math.min(maxZoom, Math.max(minZoom, Number((prev + delta).toFixed(2)))));
    };
    el.addEventListener("wheel", onNativeWheel, { passive: false });
    return () => el.removeEventListener("wheel", onNativeWheel);
  }, [isFullPage]);

  const setC4Level = (lvl: 1 | 2 | 3, preservePendingHighlight = false) => {
    if (!preservePendingHighlight) {
      pendingHighlightNodeRef.current = null;
      setHighlightedNodeId("");
    }
    setLevelState(lvl);
    setZoom(1);
  };

  const drillDownToContainer = (cid: string, preservePendingHighlight = false) => {
    if (!preservePendingHighlight) {
      pendingHighlightNodeRef.current = null;
      setHighlightedNodeId("");
    }
    setSelectedContainerId(cid);
    setLevelState(3);
    setZoom(1);
  };

  const highlightEntityOnDiagram = (
    targetLevel: 2 | 3,
    mermaidNodeId: string,
    containerId?: string
  ) => {
    closeC4Inspector();
    setBlastTargetId("");
    if (targetLevel === 2) {
      if (level === 2) {
        selectHighlightNode(mermaidNodeId);
      } else {
        pendingHighlightNodeRef.current = mermaidNodeId;
        setC4Level(2, true);
      }
      return;
    }
    if (targetLevel === 3 && containerId) {
      if (level === 3 && selectedContainerId === containerId) {
        selectHighlightNode(mermaidNodeId);
      } else {
        pendingHighlightNodeRef.current = mermaidNodeId;
        drillDownToContainer(containerId, true);
      }
    }
  };

  const openC4Inspector = (comp: SpecComponent, subCompId?: string | null) => {
    setFocusedSubComponentId(subCompId || null);
    setExpandedSubSpecIds({});
    setInspectedComponent(comp);
    setInspectorOpen(true);
  };

  const closeC4Inspector = () => {
    setInspectorOpen(false);
    setExpandedSubSpecIds({});
    onClearSelectedComponent?.();
  };

  const decorateC4Nodes = React.useCallback(() => {
    if (!diagramRef.current) return;
    const nodes = diagramRef.current.querySelectorAll(
      '[id*="CNT-"], [id*="CNT_"], [id*="CMP-"], [id*="CMP_"], [id*="SYS-"], [id*="SYS"], [id*="mod_"], [id*="peer_"], [id*="ctr_"], .node'
    );
    const sysTitle = data.overview?.system_title || "";
    const sysId = data.overview?.system_id || "SYS";

    nodes.forEach((node) => {
      node.classList.add("cursor-pointer");
      const text = node.textContent || "";
      const nodeId = node.id || "";

      if (level === 1) {
        if (
          (sysTitle && text.includes(sysTitle)) ||
          (sysId && text.includes(sysId)) ||
          nodeId.includes("SYS")
        ) {
          node.setAttribute("data-c4-entity", sysId);
          node.setAttribute("title", "Click to explore System Containers");
        }
      } else if (level === 2) {
        const matched = data.components.find(
          (c) =>
            nodeId.includes(sanitizeNodeSlug(c.id)) ||
            nodeId.includes(c.id) ||
            text.includes(c.title) ||
            text.includes(c.id)
        );
        if (matched) {
          const matchedTitle = formatDisplayTitle(matched.title, matched.id);
          node.setAttribute("data-c4-entity", matched.id);
          node.setAttribute("title", `Click to explore ${matchedTitle} components`);
        } else {
          node.setAttribute("title", "Click a container to explore its components");
        }
      } else if (level === 3) {
        const peerMatch = data.components.find(
          (c) =>
            nodeId.includes(`peer_${sanitizeNodeSlug(c.id)}`) ||
            (text.includes(c.id) && (text.includes("Related Container") || nodeId.includes("peer_")))
        );
        const ctrMatch = (data.contracts || []).find(
          (ct) =>
            nodeId.includes(`ctr_${sanitizeNodeSlug(ct.id)}`) ||
            nodeId.includes(sanitizeNodeSlug(ct.id)) ||
            text.includes(ct.id)
        );
        const currentComp = data.components.find((c) => c.id === selectedContainerId);
        const matchedSub = currentComp
          ? (currentComp.internal_components || []).find(
              (sub, idx) =>
                (sub.id && nodeId.includes(`mod_${sanitizeNodeSlug(sub.id)}`)) ||
                (sub.id && nodeId.includes(sanitizeNodeSlug(sub.id))) ||
                nodeId.includes(`mod_${sanitizeNodeSlug(selectedContainerId)}_${idx}`) ||
                (sub.title && text.includes(formatDisplayTitle(sub.title, sub.id))) ||
                (sub.title && text.includes(sub.title))
            )
          : undefined;

        if (peerMatch) {
          const peerTitle = formatDisplayTitle(peerMatch.title, peerMatch.id);
          node.setAttribute("data-c4-entity", peerMatch.id);
          node.setAttribute("title", `Click to open ${peerTitle}`);
        } else if (ctrMatch) {
          const ctrTitle = formatDisplayTitle(ctrMatch.title, ctrMatch.id);
          node.setAttribute("data-c4-entity", ctrMatch.id);
          node.setAttribute("title", `Click to view ${ctrTitle}`);
        } else {
          if (matchedSub?.id) {
            node.setAttribute("data-c4-entity", matchedSub.id);
          }
          node.setAttribute("title", "Click to view component details");
        }
      }
    });
  }, [level, data.overview, data.components, data.contracts, selectedContainerId]);

  // Post-render DOM event delegation for diagram node drill-down under Mermaid securityLevel: "strict"
  const handleDiagramClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (hasMovedRef.current) return;
    const target = e.target as Element | null;
    if (!target || !diagramRef.current) return;

    const node = target.closest(
      '[data-c4-entity], [id*="CNT-"], [id*="CNT_"], [id*="CMP-"], [id*="CMP_"], [id*="SYS-"], [id*="SYS"], [id*="ctr_"], [id*="peer_"], [id*="mod_"], .node'
    );
    if (!node || !diagramRef.current.contains(node)) return;
    e.stopPropagation();

    // Direct canvas connection-focus mode (toggle button or Shift/Alt+click)
    if (focusClickMode || e.shiftKey || e.altKey) {
      const resolvedNid = resolveSvgNodeId(node, parsedDiagram.nodeIds);
      if (resolvedNid) {
        selectHighlightNode(resolvedNid === highlightedNodeId ? "" : resolvedNid);
      }
      return;
    }

    const text = node.textContent || "";
    const nodeId = node.id || "";
    const entityAttr = node.getAttribute("data-c4-entity") || "";
    const sysTitle = data.overview?.system_title || "";
    const sysId = data.overview?.system_id || "SYS";

    if (level === 1) {
      if (
        entityAttr === sysId ||
        (sysTitle && text.includes(sysTitle)) ||
        (sysId && text.includes(sysId)) ||
        nodeId.includes("SYS")
      ) {
        setC4Level(2);
      }
      return;
    }

    if (level === 2) {
      const matched = data.components.find(
        (c) =>
          entityAttr === c.id ||
          nodeId.includes(sanitizeNodeSlug(c.id)) ||
          nodeId.includes(c.id) ||
          text.includes(c.title) ||
          text.includes(c.id)
      );
      if (matched) {
        drillDownToContainer(matched.id);
      }
      return;
    }

    if (level === 3) {
      const peerMatch = data.components.find(
        (c) =>
          entityAttr === c.id ||
          nodeId.includes(`peer_${sanitizeNodeSlug(c.id)}`) ||
          (text.includes(c.id) && (text.includes("Related Container") || nodeId.includes("peer_")))
      );
      if (peerMatch) {
        drillDownToContainer(peerMatch.id);
        return;
      }

      const ctrClicked = (data.contracts || []).find(
        (ct) =>
          entityAttr === ct.id ||
          nodeId.includes(`ctr_${sanitizeNodeSlug(ct.id)}`) ||
          nodeId.includes(sanitizeNodeSlug(ct.id)) ||
          text.includes(ct.id)
      );
      if (ctrClicked) {
        setInspectedContract(ctrClicked);
        return;
      }

      const currentComp = data.components.find((c) => c.id === selectedContainerId);
      if (currentComp) {
        const matchedSub = (currentComp.internal_components || []).find(
          (sub, idx) =>
            (sub.id && (entityAttr === sub.id || nodeId.includes(`mod_${sanitizeNodeSlug(sub.id)}`))) ||
            (sub.id && nodeId.includes(sanitizeNodeSlug(sub.id))) ||
            nodeId.includes(`mod_${sanitizeNodeSlug(selectedContainerId)}_${idx}`) ||
            (sub.title && text.includes(formatDisplayTitle(sub.title, sub.id))) ||
            (sub.title && text.includes(sub.title))
        );
        openC4Inspector(currentComp, matchedSub?.id || null);
      }
    }
  };

  const applyHighlightsRef = React.useRef(applyDiagramHighlights);
  applyHighlightsRef.current = applyDiagramHighlights;
  const applyFullPageZoomRef = React.useRef(applyFullPageZoom);
  applyFullPageZoomRef.current = applyFullPageZoom;
  const isFullPageRef = React.useRef(isFullPage);
  isFullPageRef.current = isFullPage;

  React.useEffect(() => {
    let isCancelled = false;
    const renderDiagram = async () => {
      if (!diagramRef.current) return;
      try {
        setDiagramError(null);
        const safeSource = sanitizeMermaidCode(currentDiagramSource);
        const { svg } = await renderMermaidSvg(
          "c4",
          safeSource || "graph TD\nSYS[System]",
          darkMode
        );
        if (!isCancelled && diagramRef.current) {
          diagramRef.current.innerHTML = svg;
          const svgEl = diagramRef.current.querySelector("svg");
          if (svgEl) {
            svgEl.style.maxWidth = "none";
          }
          decorateC4Nodes();
          if (pendingHighlightNodeRef.current) {
            const pending = pendingHighlightNodeRef.current;
            pendingHighlightNodeRef.current = null;
            selectHighlightNode(pending);
          } else {
            applyHighlightsRef.current();
          }
          requestAnimationFrame(() => {
            if (isFullPageRef.current) {
              applyFullPageZoomRef.current();
            } else {
              autoFitDiagram();
            }
          });
        }
      } catch (err) {
        if (!isCancelled) {
          if (diagramRef.current) {
            diagramRef.current.replaceChildren();
          }
          setDiagramError("Unable to render architecture diagram.");
          console.warn("Architecture diagram render error:", err);
        }
      }
    };

    renderDiagram();
    return () => {
      isCancelled = true;
    };
  }, [
    currentDiagramSource,
    darkMode,
    level,
    selectedContainerId,
    autoFitDiagram,
    decorateC4Nodes,
    selectHighlightNode,
  ]);

  const minZoomBound = isFullPage ? 0.25 : 0.35;
  const maxZoomBound = isFullPage ? 4.0 : 3.0;

  const inspectHighlightedEntity = () => {
    const eid = selectedHighlightOption?.entityId;
    if (!eid) return;
    const ctr = (data.contracts || []).find((ct) => ct.id === eid);
    if (ctr) {
      setInspectedContract(ctr);
      return;
    }
    const cnt = data.components.find((c) => c.id === eid);
    if (cnt) {
      openC4Inspector(cnt);
      return;
    }
    for (const c of data.components) {
      const sub = (c.internal_components || []).find((ic) => ic.id === eid);
      if (sub) {
        openC4Inspector(c, sub.id);
        return;
      }
    }
  };

  return (
    <div className="space-y-4">
      {/* Promoted In-Place C4 Diagram Stage (inline or fixed z-[45] Full Page dialog) */}
      <div
        id="c4DiagramStage"
        role={isFullPage ? "dialog" : undefined}
        aria-modal={isFullPage ? true : undefined}
        aria-label={isFullPage ? "Full-Page C4 Architecture Diagram" : undefined}
        className={cn(
          isFullPage
            ? "fixed inset-0 sm:inset-2 z-[45] flex flex-col rounded-none sm:rounded-2xl bg-background/95 backdrop-blur-md border border-border shadow-2xl overflow-hidden p-3 sm:p-4 gap-2.5"
            : "space-y-4"
        )}
      >
        {/* Top Toolbar / Controls */}
        <div className="flex flex-wrap items-center justify-between gap-2.5 p-3 sm:p-4 rounded-2xl bg-card border border-border md-elevation-1 shrink-0">
          {/* Architecture Zoom Level Switcher */}
          <div id="c4LevelSwitcher" className="flex items-center gap-1.5 bg-muted/60 p-1 rounded-full border border-border/60">
            <button
              id="btnC4Level1"
              onClick={() => setC4Level(1)}
              className={`px-3 py-1 text-xs font-medium rounded-full transition-all cursor-pointer ${
                level === 1
                  ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              System Context
            </button>
            <button
              id="btnC4Level2"
              onClick={() => setC4Level(2)}
              className={`px-3 py-1 text-xs font-medium rounded-full transition-all cursor-pointer ${
                level === 2
                  ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              Containers
            </button>
            <button
              id="btnC4Level3"
              onClick={() => setC4Level(3)}
              className={`px-3 py-1 text-xs font-medium rounded-full transition-all cursor-pointer ${
                level === 3
                  ? "bg-primary text-primary-foreground md-elevation-1 font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              Components
            </button>
          </div>

          {/* Containers Breadcrumbs */}
          {level === 2 && (
            <div id="c4Breadcrumbs" className="flex items-center gap-2 text-xs">
              <button
                onClick={() => setC4Level(1)}
                className="p-1 rounded-lg bg-muted/60 text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 cursor-pointer transition-colors"
                title="Return to System Context"
              >
                <ArrowUp className="h-3 w-3" />
                <span>System Context</span>
              </button>
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="font-semibold text-primary text-xs">Containers</span>
            </div>
          )}

          {/* Components Breadcrumbs and selector */}
          {level === 3 && (
            <div id="c4Breadcrumbs" className="flex items-center gap-2 text-xs min-w-0">
              <button
                onClick={() => setC4Level(1)}
                className="p-1 rounded-lg bg-muted/60 text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 cursor-pointer transition-colors shrink-0"
                title="Return to System Context"
              >
                <span>System Context</span>
              </button>
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              <button
                id="btnC4LevelUp"
                onClick={() => setC4Level(2)}
                className="p-1 rounded-lg bg-muted/60 text-muted-foreground hover:text-foreground flex items-center gap-1 px-2 cursor-pointer transition-colors shrink-0"
                title="Return to Containers"
              >
                <ArrowUp className="h-3 w-3" />
                <span>Containers</span>
              </button>
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              <select
                id="c4ContainerSelect"
                value={selectedContainerId}
                onChange={(e) => drillDownToContainer(e.target.value)}
                className="bg-card border border-border text-xs rounded-lg px-2.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary max-w-[200px] sm:max-w-[280px] md:max-w-[340px] truncate"
              >
                {data.components.map((c) => (
                  <option key={c.id} value={c.id}>
                    {formatDisplayTitle(c.title, c.id)}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Component & Connection Highlight Controls */}
          <div className="flex flex-wrap items-center gap-1.5">
            <div className="flex items-center gap-1 bg-muted/50 px-2 py-1 rounded-xl border border-border/60">
              <GitFork className="h-3.5 w-3.5 text-primary shrink-0" />
              <select
                id="c4HighlightSelect"
                aria-label="Highlight component connections"
                value={highlightedNodeId}
                onChange={(e) => selectHighlightNode(e.target.value)}
                className="bg-transparent text-xs text-foreground focus:outline-none max-w-[190px] truncate cursor-pointer"
              >
                <option value="">Highlight connections...</option>
                {highlightOptions.map((opt) => (
                  <option key={opt.nodeId} value={opt.nodeId}>
                    {opt.label}
                  </option>
                ))}
              </select>
              {highlightedNodeId && (
                <button
                  type="button"
                  onClick={() => selectHighlightNode("")}
                  className="p-0.5 rounded hover:bg-card text-muted-foreground hover:text-foreground cursor-pointer"
                  title="Clear connection highlight"
                  aria-label="Clear connection highlight"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>

            {highlightedNodeId && (
              <div
                className="flex items-center gap-0.5 bg-muted/60 p-0.5 rounded-full border border-border/60"
                role="group"
                aria-label="Connection direction filter"
              >
                {(
                  [
                    { key: "all", label: "All" },
                    { key: "incoming", label: "Incoming" },
                    { key: "outgoing", label: "Outgoing" },
                  ] as const
                ).map((dir) => (
                  <button
                    key={dir.key}
                    type="button"
                    onClick={() => setHighlightDirection(dir.key)}
                    className={cn(
                      "px-2 py-0.5 text-[11px] font-medium rounded-full transition-colors cursor-pointer",
                      highlightDirection === dir.key
                        ? "bg-primary text-primary-foreground font-semibold"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {dir.label}
                  </button>
                ))}
              </div>
            )}

            <button
              id="btnC4FocusMode"
              type="button"
              aria-pressed={focusClickMode}
              onClick={() => setFocusClickMode((prev) => !prev)}
              className={cn(
                "px-2.5 py-1 rounded-xl text-xs font-medium border flex items-center gap-1.5 transition-colors cursor-pointer",
                focusClickMode
                  ? "bg-primary/15 text-primary border-primary/40 font-semibold"
                  : "bg-muted/50 text-muted-foreground hover:text-foreground border-border/60"
              )}
              title="Click any diagram node to highlight its incoming and outgoing connections (or Shift+Click anytime)"
            >
              <Crosshair className="h-3.5 w-3.5" />
              <span>Focus Connections</span>
            </button>
          </div>

          {/* Zoom, Fit & Full Page Controls */}
          <div className="flex items-center gap-1.5">
            <div className="flex items-center gap-1 bg-muted/60 p-1 rounded-full border border-border/60">
              <button
                type="button"
                onClick={() => setZoom((z) => Math.max(minZoomBound, +(z - 0.15).toFixed(2)))}
                className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                title="Zoom Out"
              >
                <ZoomOut className="h-4 w-4" />
              </button>
              <span className="text-[11px] font-mono px-2 text-muted-foreground select-none font-semibold min-w-[42px] text-center">
                {Math.round(zoom * 100)}%
              </span>
              <button
                type="button"
                onClick={() => setZoom((z) => Math.min(maxZoomBound, +(z + 0.15).toFixed(2)))}
                className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                title="Zoom In"
              >
                <ZoomIn className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={autoFitDiagram}
                className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-primary transition-colors cursor-pointer"
                title={isFullPage ? "Fit All" : "Fit Diagram to Screen"}
              >
                <Maximize2 className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => {
                  setZoom(1);
                  setPan({ x: 0, y: 0 });
                }}
                className="px-2 py-1 rounded-full hover:bg-card text-[11px] font-medium text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                title="Reset Zoom to 100%"
              >
                {isFullPage ? "100%" : <RotateCcw className="h-3.5 w-3.5" />}
              </button>
              {!isFullPage && (
                <button
                  type="button"
                  onClick={() => {
                    setIsExpanded((prev) => !prev);
                    setTimeout(autoFitDiagram, 150);
                  }}
                  className="p-1.5 rounded-full hover:bg-card text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                  title={isExpanded ? "Collapse Diagram Height" : "Expand Diagram Height"}
                >
                  {isExpanded ? <Shrink className="h-3.5 w-3.5" /> : <Expand className="h-3.5 w-3.5" />}
                </button>
              )}
            </div>

            {isFullPage ? (
              <button
                id="btnC4ExitFullPage"
                type="button"
                onClick={exitFullPage}
                className="px-3 py-1.5 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 text-xs font-semibold flex items-center gap-1.5 cursor-pointer transition-colors shadow-xs"
                aria-label="Exit Full Page diagram view"
              >
                <Minimize2 className="h-3.5 w-3.5" />
                <span>Exit Full Page</span>
              </button>
            ) : (
              <button
                id="btnC4FullPage"
                type="button"
                aria-expanded={isFullPage}
                aria-label="Open diagram in Full Page view"
                onClick={enterFullPage}
                className="px-3 py-1.5 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary border border-primary/25 text-xs font-semibold flex items-center gap-1.5 cursor-pointer transition-colors"
                title="Open enlarged Full Page diagram view"
              >
                <Maximize className="h-3.5 w-3.5" />
                <span>Full Page</span>
              </button>
            )}
          </div>
        </div>

        {/* View Level Indicator & Tier Legend */}
        <div className="flex flex-wrap items-center justify-between gap-3 px-1 shrink-0">
          {level === 2 && activeTiers.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                Layers:
              </span>
              {activeTiers.map((tier) => {
                const style = getTierStyle(tier.id);
                return (
                  <span
                    key={tier.id}
                    className={cn(
                      "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium border",
                      style.bg,
                      style.text,
                      style.border
                    )}
                  >
                    <span className={cn("w-1.5 h-1.5 rounded-full", style.dot)} />
                    {tier.name}
                  </span>
                );
              })}
            </div>
          )}
          <div className="ml-auto">
            <span className="text-xs text-muted-foreground hidden sm:inline">
              {level === 1
                ? "System Context"
                : level === 2
                ? "Container Overview"
                : `Inside ${selectedContainerTitle}`}
            </span>
          </div>
        </div>

        {/* Main Diagram Viewport Card with Drag-to-Pan */}
        <Card
          id="c4DiagramContainer"
          ref={containerRef}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          className={cn(
            "p-4 sm:p-6 flex items-center justify-center overflow-hidden overscroll-contain bg-card/60 relative select-none transition-all duration-200",
            isFullPage ? "flex-1 min-h-0" : isExpanded ? "h-[800px]" : "h-[580px]"
          )}
        >
          {/* Floating Connection Summary Pill */}
          {highlightedNodeId && connectionSummary && (
            <div
              data-c4-connection-pill="true"
              className="absolute top-3 left-3 z-20 flex flex-wrap items-center gap-2 px-3 py-1.5 rounded-xl bg-background/90 backdrop-blur-md border border-primary/35 shadow-sm text-xs"
              onMouseDown={(e) => e.stopPropagation()}
            >
              <span className="font-semibold text-foreground">
                {selectedHighlightOption?.label ||
                  humanizeToken(highlightedNodeId.replace(/^(?:CNT|CMP|CTR|PER|EXT|mod|peer|ctr)_/i, ""))}
              </span>
              <span className="text-muted-foreground">•</span>
              <span className="text-emerald-500 font-medium">
                {connectionSummary.incomingCount} incoming
              </span>
              <span className="text-muted-foreground">•</span>
              <span className="text-indigo-400 font-medium">
                {connectionSummary.outgoingCount} outgoing
              </span>
              {selectedHighlightOption?.entityId && (
                <button
                  type="button"
                  onClick={inspectHighlightedEntity}
                  className="ml-1 px-2 py-0.5 rounded-md bg-primary/10 hover:bg-primary/20 text-primary font-medium text-[11px] cursor-pointer transition-colors"
                >
                  Inspect
                </button>
              )}
              <button
                type="button"
                onClick={() => selectHighlightNode("")}
                className="px-2 py-0.5 rounded-md bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground font-medium text-[11px] cursor-pointer transition-colors"
              >
                Clear
              </button>
            </div>
          )}

          <div className="absolute bottom-3 left-3 z-20 pointer-events-none flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-background/80 backdrop-blur-md border border-border/80 text-[10px] text-muted-foreground">
            <Move className="h-3 w-3 text-primary" />
            <span>
              {focusClickMode
                ? "Click a node to highlight connections • Drag to pan • Scroll to zoom"
                : "Drag to pan • Scroll to zoom • Shift+Click node to focus connections"}
            </span>
          </div>

          <div className="absolute bottom-3 right-3 z-20 flex items-center gap-1.5" onMouseDown={(e) => e.stopPropagation()}>
            <button
              type="button"
              onClick={autoFitDiagram}
              className="px-2.5 py-1 rounded-lg bg-background/80 hover:bg-background backdrop-blur-md border border-border/80 text-[11px] text-foreground font-medium flex items-center gap-1 cursor-pointer transition-colors shadow-xs"
              title={isFullPage ? "Fit All" : "Fit to View"}
            >
              <Maximize2 className="h-3 w-3 text-primary" />
              <span>{isFullPage ? "Fit All" : "Fit to View"}</span>
            </button>
            {isFullPage ? (
              <button
                type="button"
                onClick={exitFullPage}
                className="px-2.5 py-1 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 text-[11px] font-medium flex items-center gap-1 cursor-pointer transition-colors shadow-xs"
                title="Exit Full Page"
              >
                <Minimize2 className="h-3 w-3" />
                <span>Exit Full Page</span>
              </button>
            ) : (
              <button
                type="button"
                data-c4-fullpage-trigger="true"
                onClick={enterFullPage}
                className="px-2.5 py-1 rounded-lg bg-background/80 hover:bg-background backdrop-blur-md border border-primary/30 text-[11px] text-primary font-medium flex items-center gap-1 cursor-pointer transition-colors shadow-xs"
                title="Open Full Page Diagram"
              >
                <Maximize className="h-3 w-3" />
                <span>Full Page</span>
              </button>
            )}
          </div>

          {diagramError && (
            <div
              role="alert"
              className="absolute inset-x-4 top-4 z-30 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-xs text-rose-400"
            >
              {diagramError}
            </div>
          )}

          <div
            id="c4ActiveDiagram"
            ref={diagramRef}
            onClick={handleDiagramClick}
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: "center center",
            }}
            className="transition-transform duration-75 ease-out flex items-center justify-center shrink-0"
          />
        </Card>
      </div>

      {/* Client-Side Blast Radius Simulator Panel */}
      <Card className="p-4 rounded-2xl bg-card border border-border/80 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Crosshair className="h-4 w-4 text-rose-400" />
            <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
              Interactive Blast Radius Simulator
            </h4>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={blastTargetId}
              onChange={(e) => selectBlastTarget(e.target.value)}
              className="bg-muted/50 border border-border text-xs rounded-xl px-3 py-1.5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="">Select a container or component to inspect impact...</option>
              {data.components.map((c) => (
                <option key={c.id} value={c.id}>
                  {formatDisplayTitle(c.title, c.id)}
                </option>
              ))}
              {data.components.flatMap((c) =>
                (c.internal_components || []).map((ic) => (
                  <option key={`${c.id}-${ic.id}`} value={ic.id}>
                    ↳ {formatDisplayTitle(ic.title, ic.id)}
                  </option>
                ))
              )}
            </select>
            {blastTargetId && (
              <button
                type="button"
                onClick={() => setBlastTargetId("")}
                className="px-2.5 py-1 rounded-xl text-xs font-medium bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground cursor-pointer"
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {blastRadiusReport && (
          <div className="space-y-2.5 pt-2 border-t border-border/60 text-xs">
            <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-blue-500 shadow-[0_0_6px_rgba(59,130,246,0.9)]" />
                Selected ({resolveEntityTitle(blastRadiusReport.targetId, labelIndex)})
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-rose-500 shadow-[0_0_6px_rgba(244,63,94,0.85)]" />
                Direct Impact ({blastRadiusReport.hop1.size})
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full border border-dashed border-amber-400 bg-amber-500/30" />
                Indirect Impact ({blastRadiusReport.hop2.size})
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="p-3 rounded-xl bg-muted/20 border border-border/60 space-y-1.5">
                <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
                  Used By ({blastRadiusReport.upstream1.length + blastRadiusReport.upstream2.length})
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {blastRadiusReport.upstream1.map((id) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => requestSpecNavigation(id)}
                      className="px-2 py-0.5 rounded bg-rose-500/15 hover:bg-rose-500/25 text-rose-400 border border-rose-500/30 text-[11px] font-medium cursor-pointer"
                      title="Direct Impact"
                    >
                      {resolveEntityTitle(id, labelIndex)}
                    </button>
                  ))}
                  {blastRadiusReport.upstream2.map((id) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => requestSpecNavigation(id)}
                      className="px-2 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-dashed border-amber-500/30 text-[11px] font-medium cursor-pointer"
                      title="Indirect Impact"
                    >
                      {resolveEntityTitle(id, labelIndex)}
                    </button>
                  ))}
                  {blastRadiusReport.upstream1.length + blastRadiusReport.upstream2.length === 0 && (
                    <span className="text-muted-foreground italic text-[11px]">None</span>
                  )}
                </div>
              </div>
              <div className="p-3 rounded-xl bg-muted/20 border border-border/60 space-y-1.5">
                <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
                  Depends On ({blastRadiusReport.downstream1.length + blastRadiusReport.downstream2.length})
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {blastRadiusReport.downstream1.map((id) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => requestSpecNavigation(id)}
                      className="px-2 py-0.5 rounded bg-rose-500/15 hover:bg-rose-500/25 text-rose-400 border border-rose-500/30 text-[11px] font-medium cursor-pointer"
                      title="Direct Dependency"
                    >
                      {resolveEntityTitle(id, labelIndex)}
                    </button>
                  ))}
                  {blastRadiusReport.downstream2.map((id) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => requestSpecNavigation(id)}
                      className="px-2 py-0.5 rounded bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-dashed border-blue-500/30 text-[11px] font-medium cursor-pointer"
                      title="Indirect Dependency"
                    >
                      {resolveEntityTitle(id, labelIndex)}
                    </button>
                  ))}
                  {blastRadiusReport.downstream1.length + blastRadiusReport.downstream2.length === 0 && (
                    <span className="text-muted-foreground italic text-[11px]">None</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Container Cards List */}
      <div className="pt-12 mt-10 border-t-2 border-border/80 space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2">
          <div className="space-y-1">
            <h3 className="text-xl sm:text-2xl font-extrabold text-foreground tracking-tight flex items-center gap-3">
              <Box className="h-6 w-6 text-primary shrink-0" />
              <span>System Containers</span>
              <Badge variant="outline" className="text-xs px-2 py-0.5">
                {data.components.length} Containers
              </Badge>
            </h3>
            <p className="text-sm text-muted-foreground">
              Applications, services, and packages that make up the system
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {data.components.map((comp) => {
            const getDepIds = (list?: Array<string | { container?: string; id?: string }>) =>
              (list || [])
                .map((item) => (typeof item === "string" ? item : item?.container || item?.id || ""))
                .filter(Boolean);
            const inboundDeps = data.components.filter((c) =>
              getDepIds(c.dependencies?.internal_containers as Array<string | { container?: string; id?: string }>).includes(comp.id)
            );
            const outboundDeps = getDepIds(
              comp.dependencies?.internal_containers as Array<string | { container?: string; id?: string }>
            );
            const compTitle = formatDisplayTitle(comp.title, comp.id);
            const domainTitle =
              comp.domain && comp.domain !== "N/A"
                ? resolveEntityTitle(comp.domain, labelIndex)
                : "";
            return (
              <Card
                key={comp.id}
                className="hover:border-primary/50 transition-all p-5 space-y-3.5 group flex flex-col justify-between border border-border/80 bg-card rounded-2xl"
              >
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between gap-1.5 flex-wrap">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {comp.tier && (() => {
                        const style = getTierStyle(comp.tier);
                        return (
                          <span
                            className={cn(
                              "text-[10px] px-2 py-0.5 rounded-full font-semibold border",
                              style.bg,
                              style.text,
                              style.border
                            )}
                          >
                            {comp.tier_name || formatContainerType(comp.container_type)}
                          </span>
                        );
                      })()}
                      <Badge variant="outline">{formatContainerType(comp.container_type)}</Badge>
                    </div>
                  </div>
                  <h4 className="text-sm font-bold text-foreground group-hover:text-primary transition-colors">
                    {compTitle}
                  </h4>
                  {domainTitle && (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span className="px-2 py-0.5 rounded bg-muted text-[11px] text-muted-foreground font-medium">
                        {domainTitle}
                      </span>
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                    {comp.responsibility || comp.summary || "Core system container"}
                  </p>
                  <div className="flex items-center gap-2 pt-1 text-[11px] text-muted-foreground">
                    <span className="bg-muted/50 px-2 py-0.5 rounded border border-border/40">
                      {outboundDeps.length} {outboundDeps.length === 1 ? "dependency" : "dependencies"}
                    </span>
                    <span>•</span>
                    <span className="bg-muted/50 px-2 py-0.5 rounded border border-border/40">
                      {inboundDeps.length} {inboundDeps.length === 1 ? "dependent" : "dependents"}
                    </span>
                  </div>
                </div>
                <div className="pt-3.5 flex items-center justify-between gap-2 border-t-2 border-border/80 text-[11px]">
                  <button
                    onClick={() => drillDownToContainer(comp.id)}
                    className="flex-1 py-1.5 px-2.5 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary font-semibold flex items-center justify-center gap-1 transition-colors cursor-pointer"
                    title={`Explore ${compTitle} components`}
                  >
                    <span>Explore Components</span>
                    <ChevronRight className="h-3 w-3" />
                  </button>
                  <button
                    onClick={() => openC4Inspector(comp)}
                    className="py-1.5 px-2.5 rounded-lg bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground font-medium transition-colors cursor-pointer"
                    title={`View ${compTitle} details`}
                  >
                    Details
                  </button>
                </div>
              </Card>
            );
          })}
        </div>
      </div>

      {/* Boundary Interface Contracts Section */}
      {data.contracts && data.contracts.length > 0 && (
        <div id="architectureContractsSection" className="space-y-4 pt-6 border-t border-border/60">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground uppercase tracking-wider flex items-center gap-2">
              <FileCode className="h-4 w-4 text-emerald-400" />
              Boundary Interface Contracts
            </h3>
            <span className="text-xs text-muted-foreground">
              {data.contracts.length} {data.contracts.length === 1 ? "contract" : "contracts"}
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.contracts.map((ctr) => {
              const providerTitle = resolveEntityTitle(ctr.container, labelIndex);
              return (
                <Card
                  key={ctr.id}
                  onClick={() => setInspectedContract(ctr)}
                  className="p-4 space-y-2 border-border/80 bg-card hover:border-emerald-500/50 cursor-pointer transition-all flex flex-col justify-between group"
                >
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <h4 className="text-xs font-bold text-foreground group-hover:text-emerald-400 transition-colors">
                        {formatDisplayTitle(ctr.title, ctr.id)}
                      </h4>
                      <Badge variant="outline" className="text-[10px] shrink-0">
                        {formatProtocol(ctr.protocol, ctr.custom_protocol)}
                      </Badge>
                    </div>
                    {providerTitle && (
                      <div className="text-[11px] text-muted-foreground">
                        Provided by {providerTitle}
                      </div>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* Architectural Decisions Section */}
      {data.decisions && data.decisions.length > 0 && (
        <div id="architectureDecisionsSection" className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-foreground uppercase tracking-wider flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-amber-500" />
              Architectural Decisions
            </h3>
            <span className="text-xs text-muted-foreground">
              {data.decisions.length} {data.decisions.length === 1 ? "decision" : "decisions"}
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.decisions.map((dec, idx) => (
              <Card
                key={`${dec.id}-${idx}`}
                className="p-4 space-y-2 border-border/80 bg-card flex flex-col justify-between"
              >
                <div className="space-y-1.5">
                  {dec.source_id && (
                    <Badge variant="outline" className="text-[10px]">
                      {resolveEntityTitle(dec.source_id, labelIndex, "Architecture")}
                    </Badge>
                  )}
                  <h4 className="text-xs font-bold text-foreground">
                    {formatDisplayTitle(dec.title, dec.id)}
                  </h4>
                  <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">
                    {dec.decision}
                  </p>
                </div>
                {dec.rationale && (
                  <p className="text-[11px] text-muted-foreground/80 italic pt-1 border-t border-border/50 line-clamp-2">
                    Why: {dec.rationale}
                  </p>
                )}
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* Container Inspector Sheet Drawer (id="c4InspectorDrawer") */}
      <Sheet
        open={inspectorOpen}
        onOpenChange={(open) => {
          setInspectorOpen(open);
          if (!open) onClearSelectedComponent?.();
        }}
        title={
          inspectedComponent
            ? formatDisplayTitle(inspectedComponent.title, inspectedComponent.id)
            : "Container Details"
        }
        subtitle={
          inspectedComponent
            ? `${formatContainerType(inspectedComponent.container_type)}${
                inspectedComponent.tier_name ? ` • ${inspectedComponent.tier_name}` : ""
              }`
            : undefined
        }
      >
        <div id="c4InspectorDrawer" className="space-y-5">
          {inspectedComponent && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="primary">
                    {formatContainerType(inspectedComponent.container_type)}
                  </Badge>
                  {inspectedComponent.runtime && inspectedComponent.runtime !== "N/A" && (
                    <Badge variant="default">{inspectedComponent.runtime}</Badge>
                  )}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    highlightEntityOnDiagram(2, sanitizeNodeSlug(inspectedComponent.id))
                  }
                  className="h-7 text-[11px] gap-1.5"
                >
                  <GitFork className="h-3 w-3 text-primary" />
                  <span>Highlight on Diagram</span>
                </Button>
              </div>

              {(inspectedComponent.responsibility || inspectedComponent.summary) && (
                <div className="p-3.5 rounded-2xl bg-muted/20 border border-border/60 text-xs space-y-1">
                  <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
                    Responsibility
                  </span>
                  <p className="text-muted-foreground leading-relaxed">
                    {inspectedComponent.responsibility || inspectedComponent.summary}
                  </p>
                </div>
              )}

              {/* Focused Internal Component Full Specification */}
              {(() => {
                if (!focusedSubComponentId) return null;
                const focusedSub = (inspectedComponent.internal_components || []).find(
                  (sub) => sub.id === focusedSubComponentId
                );
                if (!focusedSub) return null;
                const focusedTitle = formatDisplayTitle(focusedSub.title, focusedSub.id);
                const hasFocusedBody = hasSpecBodyOrChunk(focusedSub.id, focusedSub.description);
                return (
                  <div
                    data-focused-component-section="true"
                    className="space-y-3 pt-1 pb-3 border-b border-border/60"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="space-y-0.5">
                        <span className="text-[10px] font-bold text-primary uppercase tracking-wider block">
                          Selected Component
                        </span>
                        <h3 className="text-sm font-bold text-foreground">{focusedTitle}</h3>
                      </div>
                      {focusedSub.id && (
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              highlightEntityOnDiagram(
                                3,
                                `mod_${sanitizeNodeSlug(focusedSub.id!)}`,
                                inspectedComponent.id
                              )
                            }
                            className="h-7 text-[11px] gap-1.5"
                          >
                            <GitFork className="h-3 w-3 text-primary" />
                            <span>Highlight on Diagram</span>
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              closeC4Inspector();
                              requestSpecNavigation(focusedSub.id!, { mode: "reader" });
                            }}
                            className="h-7 text-[11px] gap-1.5"
                          >
                            <ExternalLink className="h-3 w-3" />
                            <span>Open in Full Reader</span>
                          </Button>
                        </div>
                      )}
                    </div>
                    {focusedSub.responsibility && (
                      <p className="text-xs text-muted-foreground leading-relaxed">
                        {focusedSub.responsibility}
                      </p>
                    )}
                    <EntityMemoriesStrip
                      entityId={focusedSub.id}
                      memories={data.memories}
                      onBeforeNavigate={closeC4Inspector}
                    />
                    {hasFocusedBody && (
                      <div className="space-y-2">
                        <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                          <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                          <span>Full Specification</span>
                        </h4>
                        <AsciiDocRenderer
                          measure="full"
                          specId={focusedSub.id}
                          content={focusedSub.description || ""}
                        />
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Internal Components */}
              <div className="space-y-2">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Layers className="h-4 w-4 text-primary" />
                  Internal Components ({inspectedComponent.internal_components?.length || 0})
                </h4>
                <div className="space-y-2">
                  {(inspectedComponent.internal_components || []).map((sub, i) => {
                    const subKey = sub.id || String(i);
                    const subHasBody = hasSpecBodyOrChunk(sub.id, sub.description);
                    const isSubExpanded = Boolean(expandedSubSpecIds[subKey]);
                    return (
                    <div
                      key={subKey}
                      className={cn(
                        "p-3 rounded-xl border bg-muted/20 space-y-2 text-xs transition-all",
                        focusedSubComponentId && sub.id === focusedSubComponentId
                          ? "border-primary ring-2 ring-primary/30 bg-primary/5"
                          : "border-border/70"
                      )}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-bold text-foreground">
                          {formatDisplayTitle(sub.title, sub.id)}
                        </span>
                        <div className="flex items-center gap-1.5">
                          {subHasBody && sub.id !== focusedSubComponentId && (
                            <button
                              type="button"
                              onClick={() =>
                                setExpandedSubSpecIds((prev) => ({
                                  ...prev,
                                  [subKey]: !prev[subKey],
                                }))
                              }
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-muted hover:bg-muted/80 text-foreground border border-border/60 cursor-pointer transition-colors"
                            >
                              <ChevronDown
                                className={cn(
                                  "h-3 w-3 transition-transform",
                                  isSubExpanded && "rotate-180"
                                )}
                              />
                              <span>{isSubExpanded ? "Hide Specification" : "Full Specification"}</span>
                            </button>
                          )}
                          {sub.id && (
                            <>
                              <button
                                type="button"
                                onClick={() =>
                                  highlightEntityOnDiagram(
                                    3,
                                    `mod_${sanitizeNodeSlug(sub.id!)}`,
                                    inspectedComponent.id
                                  )
                                }
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-muted hover:bg-muted/80 text-foreground border border-border/60 cursor-pointer transition-colors"
                              >
                                <GitFork className="h-3 w-3 text-primary" />
                                <span>Highlight on Diagram</span>
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  closeC4Inspector();
                                  requestSpecNavigation(sub.id!, { mode: "reader" });
                                }}
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-primary/10 hover:bg-primary/20 text-primary border border-primary/20 cursor-pointer transition-colors"
                              >
                                <ExternalLink className="h-3 w-3" />
                                <span>Open in Full Reader</span>
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                      <p className="text-muted-foreground">{sub.responsibility}</p>
                      {subHasBody && isSubExpanded && sub.id !== focusedSubComponentId && (
                        <div className="pt-2">
                          <AsciiDocRenderer
                            measure="full"
                            specId={sub.id}
                            content={sub.description || ""}
                          />
                        </div>
                      )}
                      {sub.invariants && sub.invariants.length > 0 && (
                        <div className="pt-1.5 border-t border-border/50 space-y-1">
                          <span className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-1">
                            <ShieldCheck className="h-3 w-3" />
                            Verified Rules ({sub.invariants.length})
                          </span>
                          {sub.invariants.map((inv, idx) => (
                            <div
                              key={inv.id || idx}
                              className="p-2 rounded-lg bg-emerald-500/5 border border-emerald-500/20 text-[11px] space-y-0.5"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="font-semibold text-emerald-400">
                                  {humanizeInvariantTitle(inv as SpecInvariant)}
                                </span>
                                {inv.test_binding && (
                                  <span className="text-[10px] text-emerald-500 shrink-0">
                                    Test Verified
                                  </span>
                                )}
                              </div>
                              <p className="text-muted-foreground">{inv.rule}</p>
                            </div>
                          ))}
                        </div>
                      )}
                      {sub.decisions && sub.decisions.length > 0 && (
                        <div className="pt-1.5 border-t border-border/50 space-y-1">
                          <span className="text-[10px] font-bold text-amber-500 uppercase tracking-wider flex items-center gap-1">
                            <Sparkles className="h-3 w-3" />
                            Design Decisions ({sub.decisions.length})
                          </span>
                          {sub.decisions.map((dec, idx) => (
                            <div
                              key={dec.id || idx}
                              className="p-2 rounded-lg bg-amber-500/5 border border-amber-500/20 text-[11px] space-y-0.5"
                            >
                              <span className="font-semibold text-foreground block">
                                {formatDisplayTitle(dec.title, dec.id)}
                              </span>
                              <p className="text-muted-foreground">{dec.decision}</p>
                            </div>
                          ))}
                        </div>
                      )}
                      <EntityMemoriesStrip
                        entityId={sub.id}
                        memories={data.memories}
                        onBeforeNavigate={closeC4Inspector}
                        compact
                        headingLevel="h5"
                      />
                    </div>
                    );
                  })}
                </div>
              </div>

              {/* Container Team Memories */}
              <EntityMemoriesStrip
                entityId={inspectedComponent.id}
                memories={data.memories}
                onBeforeNavigate={closeC4Inspector}
              />

              {/* Container Rules */}
              {inspectedComponent.invariants && inspectedComponent.invariants.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <ShieldCheck className="h-4 w-4 text-emerald-400" />
                    Container Rules ({inspectedComponent.invariants.length})
                  </h4>
                  <div className="space-y-2">
                    {inspectedComponent.invariants.map((inv, idx) => (
                      <div
                        key={inv.id || idx}
                        className="p-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 text-xs space-y-1"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold text-emerald-400">
                            {humanizeInvariantTitle(inv as SpecInvariant)}
                          </span>
                          {inv.test_binding && (
                            <span className="text-[10px] text-emerald-500 shrink-0">
                              Test Verified
                            </span>
                          )}
                        </div>
                        <p className="text-muted-foreground leading-relaxed">{inv.rule}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Dependencies & Contracts */}
              <div className="space-y-3">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <FileCode className="h-4 w-4 text-indigo-400" />
                  Dependencies &amp; Interfaces
                </h4>
                <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1.5 text-xs">
                  <div className="flex items-center justify-between">
                    <p className="font-semibold text-foreground">Depends On</p>
                    <span className="text-[10px] text-muted-foreground">
                      {inspectedComponent.dependencies?.internal_containers?.length || 0} containers
                    </span>
                  </div>
                  {inspectedComponent.dependencies?.internal_containers &&
                  inspectedComponent.dependencies.internal_containers.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {(inspectedComponent.dependencies.internal_containers as Array<
                        string | { container?: string; id?: string; relationship?: string }
                      >).map((rawDep, idx) => {
                        const depId =
                          typeof rawDep === "string"
                            ? rawDep
                            : rawDep?.container || rawDep?.id || "";
                        const targetComp = data.components.find((c) => c.id === depId);
                        const depTitle = targetComp
                          ? formatDisplayTitle(targetComp.title, targetComp.id)
                          : resolveEntityTitle(depId, labelIndex);
                        return (
                          <button
                            key={`${depId}-${idx}`}
                            onClick={() => {
                              if (targetComp) openC4Inspector(targetComp);
                            }}
                            className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-[11px] font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-colors cursor-pointer"
                            title={depTitle}
                          >
                            <span>{depTitle}</span>
                            <ChevronRight className="h-3 w-3" />
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-[11px] italic">
                      None (foundational container)
                    </p>
                  )}
                </div>

                {/* Used By */}
                <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1.5 text-xs">
                  {(() => {
                    const dependents = data.components.filter((c) =>
                      (
                        (c.dependencies?.internal_containers || []) as Array<
                          string | { container?: string; id?: string }
                        >
                      ).some(
                        (item) =>
                          (typeof item === "string" ? item : item?.container || item?.id || "") ===
                          inspectedComponent.id
                      )
                    );
                    return (
                      <>
                        <div className="flex items-center justify-between">
                          <p className="font-semibold text-foreground">Used By</p>
                          <span className="text-[10px] text-muted-foreground">
                            {dependents.length} {dependents.length === 1 ? "consumer" : "consumers"}
                          </span>
                        </div>
                        {dependents.length > 0 ? (
                          <div className="flex flex-wrap gap-1.5 pt-1">
                            {dependents.map((dep) => {
                              const depTitle = formatDisplayTitle(dep.title, dep.id);
                              return (
                                <button
                                  key={dep.id}
                                  onClick={() => openC4Inspector(dep)}
                                  className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-[11px] font-medium bg-muted hover:bg-muted/80 text-foreground transition-colors cursor-pointer"
                                  title={depTitle}
                                >
                                  <span>{depTitle}</span>
                                  <ChevronRight className="h-3 w-3" />
                                </button>
                              );
                            })}
                          </div>
                        ) : (
                          <p className="text-muted-foreground text-[11px] italic">
                            No other containers depend on this one
                          </p>
                        )}
                      </>
                    );
                  })()}
                </div>

                {/* External Services */}
                {inspectedComponent.dependencies?.external_services &&
                  inspectedComponent.dependencies.external_services.length > 0 && (
                    <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1.5 text-xs">
                      <p className="font-semibold text-foreground">External Services</p>
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {(inspectedComponent.dependencies.external_services as Array<
                          string | { name?: string; service?: string; relationship?: string }
                        >).map((ext, idx) => {
                          const extLabel =
                            typeof ext === "string"
                              ? ext
                              : ext?.relationship
                                ? `${ext?.name || ext?.service || ""} (${ext.relationship})`
                                : ext?.name || ext?.service || "";
                          return (
                            <span
                              key={idx}
                              className="px-2 py-0.5 rounded-md text-[11px] bg-muted text-muted-foreground border border-border/40"
                            >
                              {extLabel}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  )}

                {/* Interface Contracts */}
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                    <p className="font-semibold text-foreground">Provides Interfaces</p>
                    <div className="flex flex-wrap gap-1">
                      {(inspectedComponent.contracts?.provides || []).length > 0 ? (
                        (inspectedComponent.contracts?.provides || []).map((cid, idx) => {
                          const ctrId = String(cid).split("(")[0].trim();
                          const ctrObj = (data.contracts || []).find((ct) => ct.id === ctrId);
                          const ctrTitle = ctrObj
                            ? formatDisplayTitle(ctrObj.title, ctrObj.id)
                            : resolveEntityTitle(ctrId, labelIndex);
                          return (
                            <button
                              key={idx}
                              type="button"
                              onClick={() => {
                                if (ctrObj) {
                                  closeC4Inspector();
                                  setInspectedContract(ctrObj);
                                } else {
                                  requestSpecNavigation(ctrId);
                                }
                              }}
                              className="text-[11px] font-medium px-2 py-0.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 cursor-pointer"
                            >
                              {ctrTitle}
                            </button>
                          );
                        })
                      ) : (
                        <span className="text-muted-foreground text-[11px]">None</span>
                      )}
                    </div>
                  </div>
                  <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1">
                    <p className="font-semibold text-foreground">Uses Interfaces</p>
                    <div className="flex flex-wrap gap-1">
                      {(inspectedComponent.contracts?.consumes || []).length > 0 ? (
                        (inspectedComponent.contracts?.consumes || []).map((cid, idx) => {
                          const ctrId = String(cid).split("(")[0].trim();
                          const ctrObj = (data.contracts || []).find((ct) => ct.id === ctrId);
                          const ctrTitle = ctrObj
                            ? formatDisplayTitle(ctrObj.title, ctrObj.id)
                            : resolveEntityTitle(ctrId, labelIndex);
                          return (
                            <button
                              key={idx}
                              type="button"
                              onClick={() => {
                                if (ctrObj) {
                                  closeC4Inspector();
                                  setInspectedContract(ctrObj);
                                } else {
                                  requestSpecNavigation(ctrId);
                                }
                              }}
                              className="text-[11px] font-medium px-2 py-0.5 rounded bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/20 cursor-pointer"
                            >
                              {ctrTitle}
                            </button>
                          );
                        })
                      ) : (
                        <span className="text-muted-foreground text-[11px]">None</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* Architectural Decisions */}
              {inspectedComponent.decisions && inspectedComponent.decisions.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                    <Sparkles className="h-4 w-4 text-amber-500" />
                    Architectural Decisions ({inspectedComponent.decisions.length})
                  </h4>
                  <div className="space-y-2">
                    {inspectedComponent.decisions.map((dec, i) => (
                      <div key={i} className="p-3 rounded-xl border border-border/70 bg-card text-xs space-y-1.5">
                        <span className="font-semibold text-foreground block">
                          {formatDisplayTitle(dec.title, dec.id)}
                        </span>
                        <p className="text-muted-foreground text-xs leading-relaxed">{dec.decision}</p>
                        {dec.rationale && (
                          <p className="text-[11px] text-muted-foreground/80 italic">
                            Why: {dec.rationale}
                          </p>
                        )}
                        {dec.rejected_alternatives && dec.rejected_alternatives.length > 0 && (
                          <div className="pt-1 text-[10px] text-muted-foreground">
                            <span className="font-semibold text-rose-400">Alternatives Considered: </span>
                            {dec.rejected_alternatives.map((ra) => ra.option).join(", ")}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Full Documentation */}
              {hasSpecBodyOrChunk(inspectedComponent.id, inspectedComponent.description) && (
                <div className="space-y-2.5 pt-3 border-t border-border/60">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-purple-400" />
                      <span>Full Specification</span>
                    </h4>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        closeC4Inspector();
                        requestSpecNavigation(inspectedComponent.id, { mode: "reader" });
                      }}
                      className="h-7 text-[11px] gap-1.5"
                    >
                      <ExternalLink className="h-3 w-3" />
                      <span>Open in Full Reader</span>
                    </Button>
                  </div>
                  <AsciiDocRenderer
                    measure="full"
                    specId={inspectedComponent.id}
                    content={inspectedComponent.description || ""}
                  />
                </div>
              )}

              <div className="pt-2">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    closeC4Inspector();
                    drillDownToContainer(inspectedComponent.id);
                  }}
                  className="w-full"
                >
                  Explore Internal Components
                </Button>
              </div>
            </>
          )}
        </div>
      </Sheet>

      {/* Interface Contract Inspector Sheet */}
      <Sheet
        open={Boolean(inspectedContract)}
        onOpenChange={(open) => {
          if (!open) {
            setInspectedContract(null);
            onClearSelectedComponent?.();
          }
        }}
        title={
          inspectedContract
            ? formatDisplayTitle(inspectedContract.title, inspectedContract.id)
            : "Interface Contract"
        }
        subtitle={
          inspectedContract
            ? formatProtocol(inspectedContract.protocol, inspectedContract.custom_protocol)
            : undefined
        }
        maxWidth="4xl"
      >
        {inspectedContract && (
          <div className="space-y-4 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="primary">
                {formatProtocol(inspectedContract.protocol, inspectedContract.custom_protocol)}
              </Badge>
              {inspectedContract.container && inspectedContract.container !== "N/A" && (
                <button
                  type="button"
                  onClick={() => {
                    const cnt = data.components.find((c) => c.id === inspectedContract.container);
                    setInspectedContract(null);
                    if (cnt) openC4Inspector(cnt);
                  }}
                  className="text-xs font-medium px-2.5 py-0.5 rounded-md bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 cursor-pointer"
                >
                  Provided by {resolveEntityTitle(inspectedContract.container, labelIndex)}
                </button>
              )}
            </div>
            {hasSpecBodyOrChunk(inspectedContract.id, inspectedContract.description) && (
              <AsciiDocRenderer
                measure="full"
                specId={inspectedContract.id}
                content={inspectedContract.description || ""}
                title={formatDisplayTitle(inspectedContract.title, inspectedContract.id)}
              />
            )}
          </div>
        )}
      </Sheet>
    </div>
  );
}
