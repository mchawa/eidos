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
  Table as TableIcon,
  ChevronRight,
  Eye,
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface AsciiDocRendererProps {
  content: string;
  className?: string;
  title?: string;
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
  text?: string;
  admonitionType?: "NOTE" | "TIP" | "IMPORTANT" | "WARNING" | "CAUTION";
  language?: string;
  code?: string;
  tableHeaders?: string[];
  tableRows?: string[][];
  listItems?: Array<{ level: number; text: string; ordered?: boolean }>;
}

export function AsciiDocRenderer({
  content,
  className,
  title,
}: AsciiDocRendererProps) {
  const [viewMode, setViewMode] = React.useState<"rendered" | "raw">("rendered");
  const [copied, setCopied] = React.useState(false);

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
        parsed.push({
          type: "title",
          text: trimmed.replace(/^=\s+/, "").trim(),
        });
        i++;
        continue;
      }

      // Section H2: == Title
      if (trimmed.startsWith("== ") && !trimmed.startsWith("=== ")) {
        parsed.push({
          type: "h2",
          text: trimmed.replace(/^==\s+/, "").trim(),
        });
        i++;
        continue;
      }

      // Subsection H3: === Title
      if (trimmed.startsWith("=== ") && !trimmed.startsWith("==== ")) {
        parsed.push({
          type: "h3",
          text: trimmed.replace(/^===\s+/, "").trim(),
        });
        i++;
        continue;
      }

      // Sub-subsection H4: ==== Title
      if (trimmed.startsWith("==== ")) {
        parsed.push({
          type: "h4",
          text: trimmed.replace(/^====\s+/, "").trim(),
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

    // Tokenize inline markdown / asciidoc
    // Pattern matches:
    // 1. URLs with label: https://url[label] or http://url[label]
    // 2. Bold: *text* or **text**
    // 3. Monospace: `text` or +text+
    // 4. Italic: _text_
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
            className="text-primary hover:underline inline-flex items-center gap-0.5 font-medium"
          >
            {match[2] || match[1]}
            <ExternalLink className="h-2.5 w-2.5 inline" />
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
            className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-muted/60 text-primary border border-border/50"
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

  return (
    <div className={cn("space-y-3", className)}>
      {/* Top Controls Bar */}
      <div className="flex items-center justify-between gap-2 p-1.5 rounded-xl bg-muted/30 border border-border/70 text-xs">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setViewMode("rendered")}
            className={cn(
              "px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors cursor-pointer flex items-center gap-1.5",
              viewMode === "rendered"
                ? "bg-card text-foreground shadow-xs font-semibold"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            <Eye className="h-3 w-3 text-primary" />
            <span>Rendered Document</span>
          </button>
          <button
            type="button"
            onClick={() => setViewMode("raw")}
            className={cn(
              "px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors cursor-pointer flex items-center gap-1.5",
              viewMode === "raw"
                ? "bg-card text-foreground shadow-xs font-semibold"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            <Code2 className="h-3 w-3" />
            <span>Raw AsciiDoc</span>
          </button>
        </div>

        <button
          type="button"
          onClick={handleCopy}
          className="px-2.5 py-1 rounded-lg text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer flex items-center gap-1"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-emerald-500" />
              <span className="text-emerald-500 font-semibold">Copied!</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>Copy Source</span>
            </>
          )}
        </button>
      </div>

      {/* Rendered Document View */}
      {viewMode === "rendered" ? (
        <div className="p-4 sm:p-5 rounded-2xl bg-card border border-border/80 md-elevation-1 space-y-4 text-xs text-foreground/90 leading-relaxed overflow-x-hidden">
          {blocks.length === 0 ? (
            <p className="text-muted-foreground italic text-center py-6">
              No specification body declared.
            </p>
          ) : (
            blocks.map((block, idx) => {
              switch (block.type) {
                case "title":
                  return (
                    <div
                      key={idx}
                      className="pb-3 border-b border-border/80 flex items-center gap-2"
                    >
                      <FileText className="h-5 w-5 text-primary shrink-0" />
                      <h2 className="text-base sm:text-lg font-extrabold text-foreground tracking-tight">
                        {block.text}
                      </h2>
                    </div>
                  );

                case "h2":
                  return (
                    <h3
                      key={idx}
                      className="text-sm font-bold text-foreground tracking-tight pt-3 pb-1 border-b border-border/60 flex items-center gap-2"
                    >
                      <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                      <span>{block.text}</span>
                    </h3>
                  );

                case "h3":
                  return (
                    <h4
                      key={idx}
                      className="text-xs font-bold text-primary pt-2 flex items-center gap-1.5"
                    >
                      <ChevronRight className="h-3 w-3 text-primary" />
                      <span>{block.text}</span>
                    </h4>
                  );

                case "h4":
                  return (
                    <h5
                      key={idx}
                      className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider pt-1.5"
                    >
                      {block.text}
                    </h5>
                  );

                case "admonition": {
                  const type = block.admonitionType || "NOTE";
                  let bg = "bg-blue-500/10 border-blue-500/30 text-blue-500";
                  let Icon = Info;

                  if (type === "TIP") {
                    bg = "bg-emerald-500/10 border-emerald-500/30 text-emerald-500";
                    Icon = Sparkles;
                  } else if (type === "IMPORTANT") {
                    bg = "bg-purple-500/10 border-purple-500/30 text-purple-400";
                    Icon = CheckCircle2;
                  } else if (type === "WARNING") {
                    bg = "bg-amber-500/10 border-amber-500/30 text-amber-500";
                    Icon = AlertTriangle;
                  } else if (type === "CAUTION") {
                    bg = "bg-rose-500/10 border-rose-500/30 text-rose-500";
                    Icon = ShieldAlert;
                  }

                  return (
                    <div
                      key={idx}
                      className={cn(
                        "p-3.5 rounded-xl border flex items-start gap-2.5 my-2",
                        bg
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0 mt-0.5" />
                      <div className="space-y-1 flex-1">
                        <span className="font-bold font-mono text-[10px] uppercase tracking-wider block">
                          {type}
                        </span>
                        <div className="text-xs text-foreground/90 font-medium leading-relaxed whitespace-pre-wrap">
                          {block.text}
                        </div>
                      </div>
                    </div>
                  );
                }

                case "code":
                  return (
                    <div
                      key={idx}
                      className="rounded-xl overflow-hidden border border-border/80 bg-muted/40 my-2"
                    >
                      {block.language && block.language !== "text" && (
                        <div className="px-3 py-1 bg-muted/60 border-b border-border/60 text-[10px] font-mono text-muted-foreground flex items-center justify-between">
                          <span className="uppercase">{block.language}</span>
                          <Code2 className="h-3 w-3" />
                        </div>
                      )}
                      <pre className="p-3.5 font-mono text-[11px] overflow-x-auto text-foreground leading-relaxed">
                        {block.code}
                      </pre>
                    </div>
                  );

                case "table":
                  return (
                    <div
                      key={idx}
                      className="rounded-xl border border-border/80 overflow-hidden my-3 overflow-x-auto"
                    >
                      <table className="w-full text-left text-xs border-collapse">
                        {block.tableHeaders && (
                          <thead className="bg-muted/60 border-b border-border text-foreground font-bold">
                            <tr>
                              {block.tableHeaders.map((h, hIdx) => (
                                <th key={hIdx} className="p-2.5 px-3">
                                  {renderInline(h)}
                                </th>
                              ))}
                            </tr>
                          </thead>
                        )}
                        <tbody className="divide-y divide-border/60">
                          {block.tableRows?.map((row, rIdx) => (
                            <tr
                              key={rIdx}
                              className="hover:bg-muted/30 transition-colors"
                            >
                              {row.map((cell, cIdx) => (
                                <td key={cIdx} className="p-2.5 px-3">
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
                    <ul key={idx} className="space-y-1.5 my-2">
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
                              "flex items-start gap-2 leading-relaxed",
                              indentClass
                            )}
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-primary mt-1.5 shrink-0" />
                            <div className="flex-1">{renderInline(item.text)}</div>
                          </li>
                        );
                      })}
                    </ul>
                  );

                case "paragraph":
                default:
                  return (
                    <p key={idx} className="leading-relaxed">
                      {renderInline(block.text || "")}
                    </p>
                  );
              }
            })
          )}
        </div>
      ) : (
        /* Raw Source View */
        <pre className="p-4 rounded-2xl bg-card border border-border/80 font-mono text-xs text-foreground/90 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
          {content}
        </pre>
      )}
    </div>
  );
}
