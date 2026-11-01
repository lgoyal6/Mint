"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Download } from "lucide-react";

import { ToolTrace } from "@/components/ToolTrace";
import { apiBase } from "@/lib/api";
import { extractExportArtifacts, extractLeadReferences } from "@/lib/chat";
import { cn } from "@/lib/utils";
import type { ThreadMessage } from "@/lib/types";

type MessageProps = {
  message: ThreadMessage;
  index: number;
  onOpenLead: (personId: string) => void;
};

export function Message({ message, index, onOpenLead }: MessageProps) {
  const isUser = message.role === "user";
  const leadRefs = !isUser ? extractLeadReferences(message.content) : [];
  const exportArtifacts = !isUser ? extractExportArtifacts(message) : [];

  return (
    <article
      className={cn("animate-soft-in", isUser ? "ml-auto max-w-[640px]" : "mr-auto max-w-[760px]")}
      style={{ animationDelay: `${Math.min(index * 20, 120)}ms` }}
    >
      <div
        className={cn(
          "rounded-lg border px-4 py-3 text-[15px] leading-7 shadow-sm",
          isUser
            ? "border-primary/20 bg-secondary text-secondary-foreground"
            : "border-border bg-card text-card-foreground",
        )}
      >
        <div className="prose prose-stone max-w-none prose-p:my-2 prose-ul:my-2 prose-li:my-1 prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:text-[0.85em] prose-code:font-normal">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => {
                const personId = leadIdFromHref(href);
                if (personId) {
                  return (
                    <button
                      className="rounded text-primary underline decoration-primary/25 underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      type="button"
                      onClick={() => onOpenLead(personId)}
                    >
                      {children}
                    </button>
                  );
                }
                return (
                  <a href={href} target="_blank" rel="noreferrer">
                    {children}
                  </a>
                );
              },
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>

        {leadRefs.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">
            {leadRefs.map((ref) => (
              <button
                key={ref.personId}
                className="rounded-md border border-border bg-muted px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                type="button"
                onClick={() => onOpenLead(ref.personId)}
              >
                {ref.label}
              </button>
            ))}
          </div>
        ) : null}

        {exportArtifacts.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">
            {exportArtifacts.map((artifact) => (
              <a
                key={artifact.download_url}
                className="inline-flex min-h-10 items-center gap-2 rounded-md border border-primary/25 bg-primary px-3 py-2 text-sm font-semibold leading-none text-primary-foreground no-underline transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                href={artifactHref(artifact.download_url)}
                download={artifact.filename}
              >
                <Download className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>Download {artifactLabel(artifact.format)}</span>
                {typeof artifact.row_count === "number" ? (
                  <span className="text-xs font-medium opacity-80">({artifact.row_count} rows)</span>
                ) : null}
              </a>
            ))}
          </div>
        ) : null}
      </div>

      {!isUser && message.toolTrace?.length ? <ToolTrace items={message.toolTrace} /> : null}
    </article>
  );
}

function artifactLabel(format: string | null | undefined): string {
  return (format || "file").toUpperCase();
}

function artifactHref(downloadUrl: string): string {
  if (/^https?:\/\//i.test(downloadUrl)) return downloadUrl;
  return `${apiBase()}${downloadUrl.startsWith("/") ? downloadUrl : `/${downloadUrl}`}`;
}

function leadIdFromHref(href: string | undefined): string | null {
  if (!href) return null;
  const leadMatch = href.match(/^lead:([0-9a-fA-F-]{36})$/);
  if (leadMatch) return leadMatch[1];
  const pathMatch = href.match(/\/leads\/([0-9a-fA-F-]{36})$/);
  return pathMatch ? pathMatch[1] : null;
}
