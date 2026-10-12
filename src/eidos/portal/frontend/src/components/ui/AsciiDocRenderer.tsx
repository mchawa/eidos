import * as React from "react";
import {
  FileText,
  Copy,
  Check,
  Code2,
  Info,
  CheckCircle2,
  AlertTriangle,
  ShieldAlert,
  Sparkles,
  ExternalLink,
  ChevronRight,
  Eye,
  Hash,
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface AsciiDocOutlineItem {
  id: string;
  text: string;
  level: 2 | 3;
}

export interface AsciiDocRendererProps {
  content: string;
  className?: string;
  title?: string;
  fontSize?: "compact" | "comfortable" | "large";
  measure?: "prose" | "full";
  hideControls?: boolean;
}

interface ParsedBlock {
  type:
    | "title"
    | "h2"
    | "h3"
    | "h4"
    | "admonition"
    | "code"
    | "table"
    | "list"
    | "paragraph";
  id?: string;
  text?: string;
  admonitionType?: "NOTE" | "TIP" | "IMPORTANT" | "WARNING" | "CAUTION";
  language?: string;
  code?: string;
  tableHeaders?: string[];
  tableRows?: string[][];
  listItems?: Array<{ level: number; text: string; ordered?: boolean }>;
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .trim();
}

/**
 * Extracts hierarchical table of contents items (H2 and H3) from raw AsciiDoc content.
 */
export function extractAsciiDocOutline(content: string): AsciiDocOutlineItem[] {
  if (!content) return [];
  const lines = content.split("\n");
  const outline: AsciiDocOutlineItem[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("== ") && !trimmed.startsWith("=== ")) {
      const text = trimmed.replace(/^==\s+/, "").trim();
      outline.push({ id: slugify(text), text, level: 2 });
    } else if (trimmed.startsWith("=== ") && !trimmed.startsWith("==== ")) {
      const text = trimmed.replace(/^===\s+/, "").trim();
      outline.push({ id: slugify(text), text, level: 3 });
    }
  }

  return outline;
}

function CodeBlockWithCopy({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-xl overflow-hidden border border-border/80 bg-zinc-900 dark:bg-zinc-950 text-zinc-100 my-4 shadow-sm">
      <div className="px-4 py-2 bg-zinc-800/80 dark:bg-zinc-900 border-b border-zinc-700/60 text-xs font-mono text-zinc-400 flex items-center justify-between">
        <span className="uppercase tracking-wider text-[11px] font-semibold text-zinc-300">
          {language && language !== "text" ? language : "code"}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-zinc-700/60 text-zinc-300 hover:text-white transition-colors cursor-pointer text-[11px]"
          title="Copy code to clipboard"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-emerald-400" />
              <span className="text-emerald-400 font-semibold">Copied</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <pre className="p-4 font-mono text-[13px] leading-relaxed overflow-x-auto text-zinc-100 selection:bg-primary/30">
        <code>{code}</code>
      </pre>
    </div>
  );
}

export function AsciiDocRenderer({
  content,
  className,
  title,
  fontSize = "comfortable",
  measure = "prose",
  hideControls = false,
}: AsciiDocRendererProps) {
  const [viewMode, setViewMode] = React.useState<"rendered" | "raw">("rendered");
  const [copied, setCopied] = React.useState(false);
  const [activeFontSize, setActiveFontSize] = React.useState<"compact" | "comfortable" | "large">(fontSize);
  const [activeMeasure, setActiveMeasure] = React.useState<"prose" | "full">(measure);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Parse AsciiDoc content into structured blocks
  const blocks = React.useMemo(() => {
    if (!content) return [];
    const lines = content.split("\n");
    const parsed: ParsedBlock[] = [];

    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      // Skip document attributes like :toc: left, :doctype: book, etc.
      if (trimmed.startsWith(":") && trimmed.endsWith(":") && trimmed.length > 2) {
        i++;
        continue;
      }
      if (trimmed.startsWith(":") && trimmed.includes(": ")) {
        i++;
        continue;
      }

      // Empty lines
      if (!trimmed) {
        i++;
        continue;
      }

      // Document Title: = Title
      if (trimmed.startsWith("= ") && !trimmed.startsWith("== ")) {
        const text = trimmed.replace(/^=\s+/, "").trim();
        parsed.push({
          type: "title",
          text,
          id: slugify(text),
        });
        i++;
        continue;
      }

      // Section H2: == Title
      if (trimmed.startsWith("== ") && !trimmed.startsWith("=== ")) {
        const text = trimmed.replace(/^==\s+/, "").trim();
        parsed.push({
          type: "h2",
          text,
          id: slugify(text),
        });
        i++;
        continue;
      }

      // Subsection H3: === Title
      if (trimmed.startsWith("=== ") && !trimmed.startsWith("==== ")) {
        const text = trimmed.replace(/^===\s+/, "").trim();
        parsed.push({
          type: "h3",
          text,
          id: slugify(text),
        });
        i++;
        continue;
      }

      // Sub-subsection H4: ==== Title
      if (trimmed.startsWith("==== ")) {
        const text = trimmed.replace(/^====\s+/, "").trim();
        parsed.push({
          type: "h4",
          text,
          id: slugify(text),
        });
        i++;
        continue;
      }

      // Block Admonitions: [NOTE], [TIP], [IMPORTANT], [WARNING], [CAUTION]
      const admonitionMatch = trimmed.match(/^\[(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]$/i);
      if (admonitionMatch) {
        const admType = admonitionMatch[1].toUpperCase() as any;
        i++; // skip [NOTE]
        let admText = "";
        const isDelimited = lines[i] && lines[i].trim().startsWith("====");
        if (isDelimited) {
          i++; // skip opening ====
          const bodyLines: string[] = [];
          while (i < lines.length && !lines[i].trim().startsWith("====")) {
            bodyLines.push(lines[i]);
            i++;
          }
          if (i < lines.length) i++; // skip closing ====
          admText = bodyLines.join("\n").trim();
        } else {
          // single line or next paragraph
          const bodyLines: string[] = [];
          while (i < lines.length && lines[i].trim()) {
            bodyLines.push(lines[i]);
            i++;
          }
          admText = bodyLines.join("\n").trim();
        }
        parsed.push({
          type: "admonition",
          admonitionType: admType,
          text: admText,
        });
        continue;
      }

      // Single line inline admonitions: NOTE: ...
      const singleAdmMatch = trimmed.match(/^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s+(.+)$/i);
      if (singleAdmMatch) {
        parsed.push({
          type: "admonition",
          admonitionType: singleAdmMatch[1].toUpperCase() as any,
          text: singleAdmMatch[2],
        });
        i++;
        continue;
      }

      // Source code block: [source,yaml] or [source,bash]
      let codeLang = "";
      const sourceMatch = trimmed.match(/^\[source(?:,\s*([a-zA-Z0-9_-]+))?\]$/i);
      if (sourceMatch) {
        codeLang = sourceMatch[1] || "";
        i++; // advance past [source,...]
      }

      // Delimited Code or Literal Blocks: ---- or ....
      if (
        (lines[i] && lines[i].trim().startsWith("----")) ||
        (lines[i] && lines[i].trim().startsWith("...."))
      ) {
        const delimiter = lines[i].trim().slice(0, 4);
        i++; // skip opening delimiter
        const codeLines: string[] = [];
        while (i < lines.length && !lines[i].trim().startsWith(delimiter)) {
          codeLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) i++; // skip closing delimiter
        parsed.push({
          type: "code",
          language: codeLang || "text",
          code: codeLines.join("\n"),
        });
        continue;
      }

      // AsciiDoc Tables: |===
      if (trimmed.startsWith("|===")) {
        i++; // skip opening |===
        const tableLines: string[] = [];
        while (i < lines.length && !lines[i].trim().startsWith("|===")) {
          tableLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) i++; // skip closing |===

        // Parse cells inside table
        const rows: string[][] = [];
        tableLines.forEach((tLine) => {
          const tTrim = tLine.trim();
          if (!tTrim.startsWith("|")) return;
          const cells = tTrim
            .slice(1)
            .split("|")
            .map((c) => c.trim())
            .filter((c) => c.length > 0);
          if (cells.length > 0) {
            rows.push(cells);
          }
        });

        if (rows.length > 0) {
          const headers = rows[0];
          const bodyRows = rows.slice(1);
          parsed.push({
            type: "table",
            tableHeaders: headers,
            tableRows: bodyRows,
          });
        }
        continue;
      }

      // Bullet Lists: * or **
      if (trimmed.startsWith("* ") || trimmed.startsWith("** ") || trimmed.startsWith("*** ")) {
        const listItems: Array<{ level: number; text: string; ordered?: boolean }> = [];
        while (
          i < lines.length &&
          (lines[i].trim().startsWith("* ") ||
            lines[i].trim().startsWith("** ") ||
            lines[i].trim().startsWith("*** "))
        ) {
          const curTrim = lines[i].trim();
          let level = 1;
          let text = "";
          if (curTrim.startsWith("*** ")) {
            level = 3;
            text = curTrim.slice(4).trim();
          } else if (curTrim.startsWith("** ")) {
            level = 2;
            text = curTrim.slice(3).trim();
          } else {
            level = 1;
            text = curTrim.slice(2).trim();
          }
          listItems.push({ level, text, ordered: false });
          i++;
        }
        parsed.push({
          type: "list",
          listItems,
        });
        continue;
      }

      // Numbered Lists: 1. or . or ..
      if (/^(\d+\.|\.{1,2})\s+/.test(trimmed)) {
        const listItems: Array<{ level: number; text: string; ordered?: boolean }> = [];
        while (i < lines.length && /^(\d+\.|\.{1,2})\s+/.test(lines[i].trim())) {
          const curTrim = lines[i].trim();
          let level = 1;
          let text = "";
          if (curTrim.startsWith(".. ")) {
            level = 2;
            text = curTrim.slice(3).trim();
          } else if (curTrim.startsWith(". ")) {
            level = 1;
            text = curTrim.slice(2).trim();
          } else {
            level = 1;
            text = curTrim.replace(/^\d+\.\s+/, "").trim();
          }
          listItems.push({ level, text, ordered: true });
          i++;
        }
        parsed.push({
          type: "list",
          listItems,
        });
        continue;
      }

      // Standard Paragraph
      const pLines: string[] = [trimmed];
      i++;
      while (
        i < lines.length &&
        lines[i].trim() &&
        !lines[i].trim().startsWith("=") &&
        !lines[i].trim().startsWith("[") &&
        !lines[i].trim().startsWith("|") &&
        !lines[i].trim().startsWith("* ") &&
        !/^(\d+\.|\.{1,2})\s+/.test(lines[i].trim())
      ) {
        pLines.push(lines[i].trim());
        i++;
      }
      parsed.push({
        type: "paragraph",
        text: pLines.join(" "),
      });
    }

    return parsed;
  }, [content]);

  // Helper to render inline formatting: bold, italic, code, links
  const renderInline = (rawText: string) => {
    if (!rawText) return null;

    const parts: React.ReactNode[] = [];
    const regex =
      /(https?:\/\/[^\s\[]+)\[([^\]]*)\]|(?:\*\*|\*)([^\*]+)(?:\*\*|\*)|(?:`|\+)([^`\+]+)(?:`|\+)|(?:_)([^_]+)(?:_)/g;

    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = regex.exec(rawText)) !== null) {
      if (match.index > lastIndex) {
        parts.push(rawText.slice(lastIndex, match.index));
      }

      if (match[1]) {
        // Link: https://...[label]
        parts.push(
          <a
            key={match.index}
            href={match[1]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline inline-flex items-center gap-1 font-medium"
          >
            {match[2] || match[1]}
            <ExternalLink className="h-3 w-3 inline text-primary/70" />
          </a>
        );
      } else if (match[3]) {
        // Bold: *text*
        parts.push(
          <strong key={match.index} className="font-bold text-foreground">
            {match[3]}
          </strong>
        );
      } else if (match[4]) {
        // Monospace: `code`
        parts.push(
          <code
            key={match.index}
            className="font-mono text-[12.5px] px-1.5 py-0.5 rounded bg-muted/80 text-primary border border-border/60 font-medium"
          >
            {match[4]}
          </code>
        );
      } else if (match[5]) {
        // Italic: _text_
        parts.push(
          <em key={match.index} className="italic text-foreground/90">
            {match[5]}
          </em>
        );
      }

      lastIndex = match.index + match[0].length;
    }

    if (lastIndex < rawText.length) {
      parts.push(rawText.slice(lastIndex));
    }

    return parts;
  };

  // Typography sizing classes
  const fontClass =
    activeFontSize === "compact"
      ? "text-sm leading-relaxed"
      : activeFontSize === "large"
      ? "text-[17px] leading-[1.8]"
      : "text-[15px] sm:text-base leading-[1.75]";

  const measureClass =
    activeMeasure === "prose" ? "max-w-3xl mx-auto" : "max-w-full";

  return (
    <div className={cn("space-y-4", className)}>
      {/* Top Controls Bar (if not hidden) */}
      {!hideControls && (
        <div className="flex flex-wrap items-center justify-between gap-2 p-2 rounded-xl bg-muted/40 border border-border/80 text-xs">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setViewMode("rendered")}
              className={cn(
                "px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer flex items-center gap-1.5",
                viewMode === "rendered"
                  ? "bg-card text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <Eye className="h-3.5 w-3.5 text-primary" />
              <span>Rendered View</span>
            </button>
            <button
              type="button"
              onClick={() => setViewMode("raw")}
              className={cn(
                "px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer flex items-center gap-1.5",
                viewMode === "raw"
                  ? "bg-card text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <Code2 className="h-3.5 w-3.5" />
              <span>Raw AsciiDoc</span>
            </button>
          </div>

          <div className="flex items-center gap-2">
            {/* Font Scaler */}
            <div className="flex items-center gap-0.5 bg-muted/80 rounded-lg p-0.5 border border-border/60">
              <button
                type="button"
                onClick={() => setActiveFontSize("compact")}
                className={cn(
                  "px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer",
                  activeFontSize === "compact" ? "bg-card text-foreground shadow-xs font-bold" : "text-muted-foreground"
                )}
                title="Compact text (14px)"
              >
                A-
              </button>
              <button
                type="button"
                onClick={() => setActiveFontSize("comfortable")}
                className={cn(
                  "px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer",
                  activeFontSize === "comfortable" ? "bg-card text-foreground shadow-xs font-bold" : "text-muted-foreground"
                )}
                title="Comfortable text (16px)"
              >
                A
              </button>
              <button
                type="button"
                onClick={() => setActiveFontSize("large")}
                className={cn(
                  "px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer",
                  activeFontSize === "large" ? "bg-card text-foreground shadow-xs font-bold" : "text-muted-foreground"
                )}
                title="Large text (18px)"
              >
                A+
              </button>
            </div>

            {/* Reading Measure Toggle */}
            <button
              type="button"
              onClick={() => setActiveMeasure((prev) => (prev === "prose" ? "full" : "prose"))}
              className={cn(
                "px-2 py-1 rounded-lg text-[11px] font-medium transition-colors cursor-pointer border border-border/60",
                activeMeasure === "prose" ? "bg-card text-foreground shadow-xs font-semibold" : "text-muted-foreground hover:text-foreground"
              )}
              title="Toggle reading measure constraint"
            >
              {activeMeasure === "prose" ? "72ch Measure" : "Full Width"}
            </button>

            {/* Copy Source */}
            <button
              type="button"
              onClick={handleCopy}
              className="px-2.5 py-1 rounded-lg text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer flex items-center gap-1.5 border border-border/60"
            >
              {copied ? (
                <>
                  <Check className="h-3.5 w-3.5 text-emerald-500" />
                  <span className="text-emerald-500 font-semibold">Copied!</span>
                </>
              ) : (
                <>
                  <Copy className="h-3.5 w-3.5" />
                  <span>Copy</span>
                </>
              )}
            </button>
          </div>
        </div>
      )}

      {/* Rendered Document View */}
      {viewMode === "rendered" ? (
        <div className={cn("p-6 sm:p-8 rounded-2xl bg-card border border-border/80 md-elevation-1 space-y-6 text-foreground/90 overflow-x-hidden", fontClass)}>
          <div className={cn("w-full transition-all duration-200", measureClass)}>
            {blocks.length === 0 ? (
              <p className="text-muted-foreground italic text-center py-10">
                No specification body declared.
              </p>
            ) : (
              blocks.map((block, idx) => {
                switch (block.type) {
                  case "title":
                    return (
                      <div
                        key={idx}
                        id={block.id}
                        className="pb-4 mb-6 border-b border-border/80 flex items-center gap-3 spec-heading"
                      >
                        <FileText className="h-6 w-6 text-primary shrink-0" />
                        <h2 className="text-2xl sm:text-3xl font-extrabold text-foreground tracking-tight">
                          {block.text}
                        </h2>
                      </div>
                    );

                  case "h2":
                    return (
                      <h3
                        key={idx}
                        id={block.id}
                        className="text-xl sm:text-2xl font-bold text-foreground tracking-tight pt-8 pb-2 border-b border-border/60 flex items-center justify-between group spec-heading mt-6"
                      >
                        <div className="flex items-center gap-2.5">
                          <span className="h-2 w-2 rounded-full bg-primary shrink-0" />
                          <span>{block.text}</span>
                        </div>
                        {block.id && (
                          <a
                            href={`#${block.id}`}
                            className="heading-anchor-link text-muted-foreground/60 hover:text-primary p-1 rounded"
                            title="Copy link to section"
                          >
                            <Hash className="h-4 w-4" />
                          </a>
                        )}
                      </h3>
                    );

                  case "h3":
                    return (
                      <h4
                        key={idx}
                        id={block.id}
                        className="text-base sm:text-lg font-bold text-primary pt-5 pb-1 flex items-center justify-between group spec-heading mt-3"
                      >
                        <div className="flex items-center gap-2">
                          <ChevronRight className="h-4 w-4 text-primary shrink-0" />
                          <span>{block.text}</span>
                        </div>
                        {block.id && (
                          <a
                            href={`#${block.id}`}
                            className="heading-anchor-link text-muted-foreground/60 hover:text-primary p-1 rounded"
                            title="Copy link to subsection"
                          >
                            <Hash className="h-3.5 w-3.5" />
                          </a>
                        )}
                      </h4>
                    );

                  case "h4":
                    return (
                      <h5
                        key={idx}
                        id={block.id}
                        className="text-xs sm:text-sm font-bold text-muted-foreground uppercase tracking-wider pt-3"
                      >
                        {block.text}
                      </h5>
                    );

                  case "admonition": {
                    const type = block.admonitionType || "NOTE";
                    let borderClass = "border-l-4 border-l-blue-500 bg-blue-500/10 text-blue-500";
                    let textClass = "text-foreground/90";
                    let Icon = Info;

                    if (type === "TIP") {
                      borderClass = "border-l-4 border-l-emerald-500 bg-emerald-500/10 text-emerald-500";
                      Icon = Sparkles;
                    } else if (type === "IMPORTANT") {
                      borderClass = "border-l-4 border-l-purple-500 bg-purple-500/10 text-purple-400";
                      Icon = CheckCircle2;
                    } else if (type === "WARNING") {
                      borderClass = "border-l-4 border-l-amber-500 bg-amber-500/10 text-amber-500";
                      Icon = AlertTriangle;
                    } else if (type === "CAUTION") {
                      borderClass = "border-l-4 border-l-rose-500 bg-rose-500/10 text-rose-500";
                      Icon = ShieldAlert;
                    }

                    return (
                      <div
                        key={idx}
                        className={cn(
                          "p-4 sm:p-5 rounded-r-2xl border border-border/60 flex items-start gap-3.5 my-5 shadow-xs",
                          borderClass
                        )}
                      >
                        <Icon className="h-5 w-5 shrink-0 mt-0.5" />
                        <div className="space-y-1.5 flex-1">
                          <span className="font-bold font-mono text-[11px] uppercase tracking-wider block">
                            {type}
                          </span>
                          <div className={cn("text-sm sm:text-[15px] font-medium leading-relaxed whitespace-pre-wrap", textClass)}>
                            {block.text}
                          </div>
                        </div>
                      </div>
                    );
                  }

                  case "code":
                    return (
                      <CodeBlockWithCopy
                        key={idx}
                        code={block.code || ""}
                        language={block.language}
                      />
                    );

                  case "table":
                    return (
                      <div
                        key={idx}
                        className="rounded-xl border border-border/80 overflow-hidden my-5 overflow-x-auto shadow-xs"
                      >
                        <table className="w-full text-left text-sm border-collapse">
                          {block.tableHeaders && (
                            <thead className="bg-muted/70 border-b border-border text-foreground font-semibold">
                              <tr>
                                {block.tableHeaders.map((h, hIdx) => (
                                  <th key={hIdx} className="p-3 px-4 font-semibold tracking-tight">
                                    {renderInline(h)}
                                  </th>
                                ))}
                              </tr>
                            </thead>
                          )}
                          <tbody className="divide-y divide-border/60 bg-card">
                            {block.tableRows?.map((row, rIdx) => (
                              <tr
                                key={rIdx}
                                className="hover:bg-muted/30 transition-colors"
                              >
                                {row.map((cell, cIdx) => (
                                  <td key={cIdx} className="p-3 px-4 leading-relaxed">
                                    {renderInline(cell)}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    );

                  case "list":
                    return (
                      <ul key={idx} className="space-y-2 my-4">
                        {block.listItems?.map((item, itemIdx) => {
                          const indentClass =
                            item.level === 3
                              ? "pl-8"
                              : item.level === 2
                              ? "pl-4"
                              : "pl-0";
                          return (
                            <li
                              key={itemIdx}
                              className={cn(
                                "flex items-start gap-2.5 leading-relaxed",
                                indentClass
                              )}
                            >
                              <span className="h-1.5 w-1.5 rounded-full bg-primary mt-2.5 shrink-0" />
                              <div className="flex-1">{renderInline(item.text)}</div>
                            </li>
                          );
                        })}
                      </ul>
                    );

                  case "paragraph":
                  default:
                    return (
                      <p key={idx} className="leading-relaxed my-3.5 text-foreground/90">
                        {renderInline(block.text || "")}
                      </p>
                    );
                }
              })
            )}
          </div>
        </div>
      ) : (
        /* Raw Source View */
        <pre className="p-6 rounded-2xl bg-zinc-900 dark:bg-zinc-950 border border-border/80 font-mono text-xs text-zinc-100 whitespace-pre-wrap leading-relaxed max-h-[70vh] overflow-y-auto">
          {content}
        </pre>
      )}
    </div>
  );
}
