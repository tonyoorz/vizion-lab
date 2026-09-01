import { useState } from "react";
import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, FileStack } from "lucide-react";

import type { TestCasePatternDraft, TestCasePatternDraftResponse } from "./testCaseTypes";

interface Props {
  response: TestCasePatternDraftResponse;
}

function asPlainText(value: string) {
  return value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function PatternDraftRow({ draft }: { draft: TestCasePatternDraft }) {
  const [expanded, setExpanded] = useState(false);
  const passedCount = draft.verification.criteria.filter((criterion) => criterion.passed).length;

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-muted/30"
        aria-expanded={expanded}
      >
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <FileStack className="h-3.5 w-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            <span className="font-mono font-medium text-primary">D{draft.representativeDefectId}</span>
            <span className="text-muted-foreground">{draft.memberCount} 个缺陷</span>
            {!draft.error && (
              <span className={draft.verification.passed ? "inline-flex items-center gap-1 text-green-700 dark:text-green-400" : "inline-flex items-center gap-1 text-amber-700 dark:text-amber-400"}>
                {draft.verification.passed ? <CheckCircle2 className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
                {passedCount}/{draft.verification.criteria.length}
              </span>
            )}
          </div>
          <p className="mt-1 line-clamp-1 text-sm font-medium text-foreground">{draft.name || "草稿生成失败"}</p>
          {draft.error && <p className="mt-1 text-xs text-destructive">{draft.error}</p>}
        </div>
        {expanded ? <ChevronUp className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" /> : <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />}
      </button>
      {expanded && !draft.error && (
        <div className="space-y-3 border-t border-border bg-muted/20 px-4 py-3">
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {draft.commonAnchorTerms.map((term) => (
              <span key={term} className="rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-primary">{term}</span>
            ))}
            {(draft.parameterMatrix.softwareVersions || []).map((version) => (
              <span key={version} className="rounded-md border border-border bg-background px-2 py-1 text-muted-foreground">{version}</span>
            ))}
            {(draft.parameterMatrix.detectedInReleases || []).map((release) => (
              <span key={release} className="rounded-md border border-border bg-background px-2 py-1 text-muted-foreground">{release}</span>
            ))}
          </div>
          <p className="text-xs leading-5 text-muted-foreground">{asPlainText(draft.descriptionHtml)}</p>
          <pre className="whitespace-pre-wrap rounded-md border border-border bg-background p-3 font-mono text-xs leading-5 text-foreground">{draft.stepsText}</pre>
          <div className="flex flex-wrap gap-1.5">
            {draft.verification.criteria.map((criterion) => (
              <span
                key={criterion.name}
                title={criterion.evidence}
                className={criterion.passed
                  ? "rounded-md bg-green-100 px-2 py-1 text-[10px] font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400"
                  : "rounded-md bg-red-100 px-2 py-1 text-[10px] font-medium text-red-700 dark:bg-red-900/30 dark:text-red-400"}
              >
                {criterion.name}
              </span>
            ))}
          </div>
          {!draft.verification.passed && draft.verification.feedback && (
            <p className="text-xs text-amber-700 dark:text-amber-400">{draft.verification.feedback}</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function TestCasePatternDraftPreview({ response }: Props) {
  return (
    <section aria-label="参数化 testcase 草稿" className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center gap-3 border-b border-border bg-muted/30 px-4 py-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
          <FileStack className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-foreground">参数化 testcase 草稿</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">共 {response.summary.total} 个模式 · 通过 {response.summary.succeeded} · 失败 {response.summary.failed}</p>
        </div>
      </div>
      <div className="divide-y divide-border">
        {response.results.map((draft) => <PatternDraftRow key={draft.patternId} draft={draft} />)}
      </div>
      {response.artifacts && (
        <div className="space-y-1 border-t border-border bg-muted/20 px-4 py-3 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">审核文件</p>
          <p className="break-all">草稿 CSV: {response.artifacts.patternDraftsCsvPath}</p>
          <p className="break-all">缺陷映射 CSV: {response.artifacts.defectMappingCsvPath}</p>
          <p className="break-all">资产库 XLSX: {response.artifacts.testcaseLibraryXlsxPath}</p>
        </div>
      )}
      {response.artifactError && (
        <div className="border-t border-border bg-destructive/10 px-4 py-3 text-xs text-destructive">
          审核文件导出失败：{response.artifactError}
        </div>
      )}
    </section>
  );
}