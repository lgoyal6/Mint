"use client";

import { Check, Paperclip, SendHorizontal, X } from "lucide-react";
import { ChangeEvent, KeyboardEvent, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { testEnrichmentIntegrations } from "@/lib/api";
import type { JobProgress } from "@/lib/types";

type ComposerProps = {
  disabled: boolean;
  onSend: (content: string) => void;
  onUploadFile?: (
    input: { file: File; eventName?: string; classify?: boolean; workEmailsOnly?: boolean },
    onProgress?: (progress: JobProgress | null) => void,
  ) => Promise<void>;
};

export function Composer({ disabled, onSend, onUploadFile }: ComposerProps) {
  const [value, setValue] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [eventName, setEventName] = useState("");
  const [classify, setClassify] = useState(false);
  const [workEmailsOnly, setWorkEmailsOnly] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [testingIntegrations, setTestingIntegrations] = useState(false);
  const [integrationTest, setIntegrationTest] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function send() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    setValue("");
    onSend(trimmed);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      textarea.style.height = "";
      textarea.focus();
    });
  }

  function handleInput(event: ChangeEvent<HTMLTextAreaElement>) {
    setValue(event.target.value);
    const textarea = event.target;
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setSelectedFile(file);
    setUploadError(null);
    if (!eventName.trim()) {
      setEventName(file.name.replace(/\.(csv|tsv|xlsx)$/i, "").replace(/[-_]+/g, " "));
    }
  }

  async function uploadFile() {
    if (!selectedFile || !onUploadFile || uploading) return;
    setUploading(true);
    setUploadError(null);
    try {
      await onUploadFile(
        {
          file: selectedFile,
          eventName: eventName.trim() || undefined,
          classify,
          workEmailsOnly,
        },
        (progress) => setUploadProgress(progress?.message ?? null),
      );
      clearUpload();
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setUploading(false);
      setUploadProgress(null);
    }
  }

  async function testIntegrations() {
    if (testingIntegrations || uploading) return;
    setTestingIntegrations(true);
    setUploadError(null);
    setIntegrationTest("Testing Apollo and one capped Exa search…");
    try {
      const result = await testEnrichmentIntegrations();
      if (result.status !== "ok") {
        const exaStatus = result.exa.http_status ? ` (HTTP ${result.exa.http_status})` : "";
        throw new Error(`Integration test failed: Exa ${result.exa.status}${exaStatus}.`);
      }
      setIntegrationTest(`Apollo and Exa verified. Estimated test cost: $${result.estimated_cost_usd.toFixed(3)}.`);
    } catch (error) {
      setIntegrationTest(null);
      setUploadError(error instanceof Error ? error.message : "Integration test failed.");
    } finally {
      setTestingIntegrations(false);
    }
  }

  function clearUpload() {
    setSelectedFile(null);
    setEventName("");
    setClassify(false);
    setWorkEmailsOnly(false);
    setUploadError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div className="shrink-0 border-t border-stone-200/80 bg-canvas px-5 py-4">
      {selectedFile ? (
        <div className="mx-auto mb-3 max-w-[860px] rounded-xl border border-stone-200 bg-stone-50/80 p-3 shadow-hairline">
          <div className="flex flex-col gap-3 md:flex-row md:items-center">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-stone-950">{selectedFile.name}</p>
              <p className="text-xs text-stone-500">This will ingest, enrich, and resolve uploaded rows.</p>
              <p className="mt-1 text-xs text-stone-400">
                Best results: email, name, employer, company website, LinkedIn, role, what they build, production stage, compute provider, and budget band.
              </p>
            </div>
            <input
              className="h-9 min-w-0 rounded-lg border border-stone-200 bg-white px-3 text-sm text-stone-900 outline-none focus:border-accent-line focus:ring-2 focus:ring-accent-soft md:w-56"
              value={eventName}
              onChange={(event) => setEventName(event.target.value)}
              placeholder="Event name"
              disabled={uploading}
            />
            <label className="flex items-center gap-2 text-sm text-stone-600">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-stone-300 text-accent focus:ring-accent"
                checked={classify}
                onChange={(event) => setClassify(event.target.checked)}
                disabled={uploading}
              />
              Classify
            </label>
            <label className="flex items-center gap-2 text-sm text-stone-600">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-stone-300 text-accent focus:ring-accent"
                checked={workEmailsOnly}
                onChange={(event) => setWorkEmailsOnly(event.target.checked)}
                disabled={uploading}
              />
              Work emails only
            </label>
            <Button
              variant="secondary"
              className="h-9 whitespace-nowrap"
              onClick={testIntegrations}
              disabled={uploading || testingIntegrations || disabled}
            >
              {testingIntegrations ? "Testing…" : "Test APIs (~$0.01)"}
            </Button>
            <div className="flex items-center gap-2">
              <Button
                variant="primary"
                className="h-9"
                onClick={uploadFile}
                disabled={uploading || disabled}
              >
                <Check size={15} />
                {uploading ? "Enriching..." : "Upload"}
              </Button>
              <Button
                variant="ghost"
                className="h-9 w-9 px-0"
                onClick={clearUpload}
                disabled={uploading}
                aria-label="Cancel upload"
              >
                <X size={15} />
              </Button>
            </div>
          </div>
          {uploading ? (
            <p className="mt-2 text-xs text-stone-500">
              {uploadProgress ?? "Enriching uploaded leads. This can take a while for larger files."}
            </p>
          ) : null}
          {uploadError ? <p className="mt-2 text-xs text-red-700">{uploadError}</p> : null}
          {integrationTest ? <p className="mt-2 text-xs text-emerald-700">{integrationTest}</p> : null}
        </div>
      ) : null}
      <div className="mx-auto flex max-w-[860px] items-end gap-3 rounded-xl border border-stone-300 bg-stone-50 p-2 shadow-hairline focus-within:border-accent-line focus-within:ring-2 focus-within:ring-accent-soft">
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept=".csv,.tsv,.xlsx"
          onChange={handleFileChange}
          disabled={disabled || uploading || !onUploadFile}
        />
        <Button
          variant="ghost"
          className="mt-1 h-10 w-10 shrink-0 px-0"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || uploading || !onUploadFile}
          aria-label="Upload CSV or XLSX"
        >
          <Paperclip size={17} />
        </Button>
        <textarea
          ref={textareaRef}
          className="max-h-40 min-h-[52px] flex-1 resize-none bg-transparent px-3 py-3 text-[15px] leading-6 text-stone-950 placeholder:text-stone-400 focus:outline-none"
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder="Ask about event leads, review queues, or pipeline health..."
          disabled={disabled}
          rows={1}
        />
        <Button
          variant="primary"
          className="h-10 w-10 shrink-0 px-0"
          onClick={send}
          disabled={disabled || !value.trim()}
          aria-label="Send message"
        >
          <SendHorizontal size={17} />
        </Button>
      </div>
      <p className="mx-auto mt-2 max-w-[860px] text-xs text-stone-400">Enter sends. Shift+Enter adds a line.</p>
    </div>
  );
}
