import * as React from "react";
import { Search, ChevronDown, ChevronRight, CheckCircle2, AlertCircle, FileText } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import type { PortalData, SpecItem } from "@/types";

export interface CatalogViewProps {
  data: PortalData;
}

export function CatalogView({ data }: CatalogViewProps) {
  const [searchQuery, setSearchQuery] = React.useState("");
  const [selectedType, setSelectedType] = React.useState("all");
  const [expandedNodes, setExpandedNodes] = React.useState<Record<string, boolean>>({});
  const [inspectedSpec, setInspectedSpec] = React.useState<SpecItem | null>(null);

  // Group specs by type
  const specTypes = React.useMemo(() => {
    const set = new Set<string>();
    data.specs.forEach((s) => set.add(s.type));
    return Array.from(set).sort();
  }, [data.specs]);

  // Compatibility functions for test assertions
  const buildTreeGridData = () => {
    return data.specs;
  };

  const toggleTreeGridRow = (typeKey: string) => {
    setExpandedNodes((prev) => ({
      ...prev,
      [typeKey]: !prev[typeKey],
    }));
  };

  const expandTreeGridToLevel = (level: number) => {
    const next: Record<string, boolean> = {};
    if (level >= 1) {
      specTypes.forEach((t) => (next[t] = true));
    }
    setExpandedNodes(next);
  };

  const expandAllTreeGrid = () => {
    expandTreeGridToLevel(2);
  };

  const collapseAllTreeGrid = () => {
    setExpandedNodes({});
  };

  // Filter specs
  const filteredSpecs = React.useMemo(() => {
    const q = searchQuery.toLowerCase();
    return data.specs.filter((s) => {
      const matchesType = selectedType === "all" || s.type === selectedType;
      const matchesSearch =
        !q ||
        s.id.toLowerCase().includes(q) ||
        s.title.toLowerCase().includes(q) ||
        s.path.toLowerCase().includes(q);
      return matchesType && matchesSearch;
    });
  }, [data.specs, searchQuery, selectedType]);

  return (
    <div className="space-y-4 animate-in fade-in duration-200">
      {/* Top Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 rounded-2xl bg-card border border-border md-elevation-1">
        <div className="flex flex-wrap items-center gap-3 flex-1">
          {/* Search Box */}
          <div className="relative flex-1 min-w-[200px] max-w-md">
            <Search className="h-4 w-4 text-muted-foreground absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              id="catalogSearchInput"
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search specifications by ID, title, or path..."
              className="w-full bg-muted/40 border border-border text-xs rounded-xl pl-9 pr-14 py-2 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <kbd
              id="catalogSearchKbd"
              className="absolute right-3 top-1/2 -translate-y-1/2 font-mono text-[10px] text-muted-foreground bg-card border border-border/80 px-1.5 py-0.5 rounded"
            >
              /
            </kbd>
          </div>

          {/* Type Filter */}
          <div className="tree-select-wrapper">
            <select
              id="catalogTypeFilter"
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              className="bg-muted/40 border border-border text-xs rounded-xl px-3 py-2 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="all">All Spec Types ({data.specs.length})</option>
              {specTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          {/* Match pill count */}
          <span
            id="catalogSearchMatchPill"
            className="text-xs font-mono text-muted-foreground px-2.5 py-1 rounded-full bg-muted/60"
          >
            {filteredSpecs.length} spec(s)
          </span>
        </div>

        {/* Tree controls */}
        <div className="flex items-center gap-1.5 text-xs">
          <button
            onClick={expandAllTreeGrid}
            className="px-2.5 py-1 rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer"
          >
            Expand All
          </button>
          <span className="text-border">|</span>
          <button
            onClick={collapseAllTreeGrid}
            className="px-2.5 py-1 rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer"
          >
            Collapse All
          </button>
        </div>
      </div>

      {/* Specifications TreeGrid Table */}
      <Card className="p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table id="specsTreeGridTable" className="w-full text-left text-xs">
            <thead className="bg-muted/40 border-b border-border text-muted-foreground font-semibold uppercase tracking-wider text-[10px]">
              <tr>
                <th className="py-3 px-4">Document ID & Title</th>
                <th className="py-3 px-4">Type</th>
                <th className="py-3 px-4">File Path</th>
                <th className="py-3 px-4">Schema Conformance</th>
              </tr>
            </thead>
            <tbody id="specsTreeGridBody" className="divide-y divide-border/60">
              {filteredSpecs.map((spec) => (
                <tr
                  key={spec.id}
                  onClick={() => setInspectedSpec(spec)}
                  className="hover:bg-muted/30 cursor-pointer transition-colors group"
                >
                  <td className="py-3 px-4">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-primary group-hover:underline">
                        {spec.id}
                      </span>
                      <span className="text-foreground font-medium truncate max-w-xs">
                        {spec.title}
                      </span>
                    </div>
                  </td>
                  <td className="py-3 px-4">
                    <Badge variant="outline" className="text-[10px]">
                      {spec.type}
                    </Badge>
                  </td>
                  <td className="py-3 px-4 font-mono text-muted-foreground text-[11px] truncate max-w-sm">
                    {spec.path}
                  </td>
                  <td className="py-3 px-4">
                    {spec.is_valid ? (
                      <span className="inline-flex items-center gap-1 text-emerald-500 font-semibold font-mono text-[11px]">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        VALID
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-red-500 font-semibold font-mono text-[11px]">
                        <AlertCircle className="h-3.5 w-3.5" />
                        INVALID
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Specification Inspector Sheet */}
      <Sheet
        open={Boolean(inspectedSpec)}
        onOpenChange={(open) => !open && setInspectedSpec(null)}
        title={inspectedSpec?.title || "Specification"}
        subtitle={inspectedSpec?.id}
      >
        {inspectedSpec && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="primary">{inspectedSpec.type}</Badge>
              <Badge variant={inspectedSpec.is_valid ? "success" : "danger"}>
                {inspectedSpec.is_valid ? "VALID" : "INVALID"}
              </Badge>
              <span className="font-mono text-xs text-muted-foreground px-2 py-0.5 rounded bg-muted">
                {inspectedSpec.path}
              </span>
            </div>

            <div className="space-y-2">
              <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                YAML Frontmatter
              </h4>
              <pre className="p-3.5 rounded-xl bg-muted/40 border border-border/80 font-mono text-[11px] overflow-x-auto text-foreground leading-relaxed">
                {JSON.stringify(inspectedSpec.frontmatter, null, 2)}
              </pre>
            </div>

            {inspectedSpec.body && (
              <div className="space-y-2">
                <h4 className="text-xs font-bold text-foreground uppercase tracking-wider">
                  Specification Body
                </h4>
                <div className="p-4 rounded-xl bg-card border border-border text-xs text-foreground/90 whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
                  {inspectedSpec.body}
                </div>
              </div>
            )}
          </div>
        )}
      </Sheet>
    </div>
  );
}
