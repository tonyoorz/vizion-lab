import { useState } from "react";
import { AlertTriangle, ChevronDown, ChevronUp, FileStack, Loader2, SearchCheck, Sparkles } from "lucide-react";

import type {
  TestCasePattern,
  TestCasePatternCoverageAudit,
  TestCasePatternCoverageAuditResult,
  TestCasePatternDiscoveryResult,
} from "./testCaseTypes";

interface Props {
  result: TestCasePatternDiscoveryResult;
  auditResult?: TestCasePatternCoverageAuditResult;
  onAuditCoverage?: (patternIds: string[]) => Promise<void>;
  onGenerateDrafts?: (patternIds: string[]) => Promise<void>;
}

const INITIAL_PATTERN_COUNT = 12;

function formatContext(pattern: TestCasePattern) {
  return [pattern.context.project, pattern.context.assignedEcu, pattern.context.leadModel]
    .filter(Boolean)
    .join(" · ") || "上下文待确认";
}

function PatternRow({
  pattern,
  selected,
  onSelectedChange,
  audit,
  disabled,
}: {
  pattern: TestCasePattern;
  selected: boolean;
  onSelectedChange?: (selected: boolean) => void;
  audit?: TestCasePatternCoverageAudit;
  disabled?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const memberCount = pattern.members.length || 1;

  return (
    <div className="border-b border-border last:border-b-0">
      <div className="flex items-start">
        {onSelectedChange && (
          <div className="px-4 pt-4">
            <input
              type="checkbox"
              aria-label={`选择模式 ${pattern.patternId}`}
              checked={selected}
              onChange={(event) => onSelectedChange(event.target.checked)}
              disabled={disabled}
              className="h-4 w-4 rounded border-border"
            />
          </div>
        )}
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="flex min-w-0 flex-1 items-start gap-3 px-4 py-3 text-left hover:bg-muted/30"
          aria-expanded={expanded}
        >
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <FileStack className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-mono text-xs font-medium text-primary">D{pattern.representative.defectId}</span>
              <span className="text-xs text-muted-foreground">{memberCount} 个缺陷</span>
              <span className="text-xs text-muted-foreground">置信度 {(pattern.confidence * 100).toFixed(0)}%</span>
              {pattern.requiresManualReview && (
                <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="h-3 w-3" /> 上下文待复核
                </span>
              )}
            </div>
            <p className="mt-1 line-clamp-1 text-sm font-medium text-foreground">{pattern.representative.name}</p>
            <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{formatContext(pattern)}</p>
          </div>
          {expanded ? (
            <ChevronUp className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
          )}
        </button>
      </div>
      {expanded && (
        <div className="border-t border-border bg-muted/20 px-4 py-3">
          {pattern.evidencePreview && (
            <p className="mb-3 text-xs leading-5 text-muted-foreground">{pattern.evidencePreview}</p>
          )}
          <div className="mb-3 flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
            {(pattern.commonAnchorTerms || []).map((term) => (
              <span key={`anchor-${term}`} className="rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-primary">{term}</span>
            ))}
            {pattern.parameterMatrix.softwareVersions.map((version) => (
              <span key={version} className="rounded-md border border-border bg-background px-2 py-1">{version}</span>
            ))}
            {pattern.parameterMatrix.detectedInReleases.map((release) => (
              <span key={release} className="rounded-md border border-border bg-background px-2 py-1">{release}</span>
            ))}
          </div>
          <div className="space-y-1.5">
            {pattern.members.map((member) => (
              <div key={member.defectId} className="flex items-start justify-between gap-3 text-xs">
                <div className="min-w-0">
                  <span className="font-mono text-primary">D{member.defectId}</span>
                  <span className="ml-2 text-muted-foreground">{member.name}</span>
                </div>
                <span className="shrink-0 text-muted-foreground">{(member.similarityToRepresentative * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
          {audit && (
            <div className="mt-4 border-t border-border pt-3 text-xs">
              <p className="font-medium text-foreground">
                {audit.recommendedDisposition === "requires_manual_review"
                  ? "需先复核模式上下文"
                  : audit.recommendedDisposition === "review_existing_testcase"
                    ? "发现已有 testcase 候选，需审核覆盖范围"
                    : "未发现已有 testcase 候选，可审核后生成复用用例"}
              </p>
              {audit.directLinks.map((link) => (
                <p key={link.defectId} className="mt-2 text-muted-foreground">
                  D{link.defectId} 已直接关联：{link.testcases.map((testcase) => `T${testcase.testId}`).join("、")}
                </p>
              ))}
              {audit.semanticCandidates.length > 0 && (
                <div className="mt-2 space-y-1 text-muted-foreground">
                  {audit.semanticCandidates.map((candidate) => (
                    <p key={`${candidate.source}-${candidate.testId}`}>
                      T{candidate.testId} · {candidate.name}
                      {typeof candidate.score === "number" ? ` · ${(candidate.score * 100).toFixed(0)}%` : ""}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function TestCasePatternDiscovery({ result, auditResult, onAuditCoverage, onGenerateDrafts }: Props) {
  const [visibleCount, setVisibleCount] = useState(INITIAL_PATTERN_COUNT);
  const [selectedPatternIds, setSelectedPatternIds] = useState<Set<string>>(new Set());
  const [auditing, setAuditing] = useState(false);
  const [generatingDrafts, setGeneratingDrafts] = useState(false);
  const visiblePatterns = result.patterns.slice(0, visibleCount);
  const allPatternIds = result.patterns.map((pattern) => pattern.patternId);
  const reusablePatternIds = result.patterns
    .filter((pattern) => pattern.members.length > 1 && !pattern.requiresManualReview)
    .map((pattern) => pattern.patternId);
  const reusableClusterCount = Math.max(
    0,
    result.summary.pattern_count - result.summary.singleton_pattern_count,
  );
  const manualReviewCount = result.patterns.filter((pattern) => pattern.requiresManualReview).length;
  const auditsByPatternId = new Map(auditResult?.audits.map((audit) => [audit.patternId, audit]) || []);
  const selectedIds = [...selectedPatternIds];
  const selectionBusy = auditing || generatingDrafts;
  const selectedPatternsAreAudited = selectedIds.length > 0 && selectedIds.every((patternId) => auditsByPatternId.has(patternId));
  const canGenerateDrafts = Boolean(
    onGenerateDrafts
    && selectedPatternsAreAudited
    && !auditing
    && !generatingDrafts,
  );

  const togglePattern = (patternId: string, selected: boolean) => {
    setSelectedPatternIds((current) => {
      const next = new Set(current);
      if (selected) next.add(patternId);
      else next.delete(patternId);
      return next;
    });
  };

  const setSelectedPatterns = (patternIds: string[]) => {
    setSelectedPatternIds(new Set(patternIds));
  };

  const auditSelectedPatterns = async () => {
    if (!onAuditCoverage || selectedPatternIds.size === 0) return;
    setAuditing(true);
    try {
      await onAuditCoverage([...selectedPatternIds]);
    } finally {
      setAuditing(false);
    }
  };

  const generateSelectedDrafts = async () => {
    if (!onGenerateDrafts || !canGenerateDrafts) return;
    setGeneratingDrafts(true);
    try {
      await onGenerateDrafts(selectedIds);
    } finally {
      setGeneratingDrafts(false);
    }
  };

  return (
    <section aria-label="可复用 testcase 模式" className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-start gap-3 border-b border-border bg-muted/30 px-4 py-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Sparkles className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-foreground">可复用 testcase 模式</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {result.summary.source_count.toLocaleString()} 个缺陷，发现 {result.summary.pattern_count.toLocaleString()} 个候选模式 · {reusableClusterCount.toLocaleString()} 个复用簇 · {result.summary.singleton_pattern_count.toLocaleString()} 个单条候选
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="font-medium text-primary">预计减少 {result.summary.estimated_llm_generations_avoided.toLocaleString()} 次 LLM 生成</span>
          <span className="text-muted-foreground">待补充证据 {result.summary.needs_evidence_count.toLocaleString()}</span>
          {manualReviewCount > 0 && (
            <span className="inline-flex items-center gap-1 font-medium text-amber-700 dark:text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5" /> 需人工审核
            </span>
          )}
        </div>
      </div>

      {(onAuditCoverage || onGenerateDrafts) && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-background px-4 py-2.5">
          <button
            type="button"
            onClick={() => setSelectedPatterns(reusablePatternIds)}
            disabled={selectionBusy || reusablePatternIds.length === 0}
            title="选择包含多个 defect 且无需上下文复核的模式"
            className="text-xs font-medium text-primary hover:underline disabled:opacity-50"
          >
            全选复用簇 ({reusablePatternIds.length})
          </button>
          <button
            type="button"
            onClick={() => setSelectedPatterns(allPatternIds)}
            disabled={selectionBusy || allPatternIds.length === 0}
            title="选择当前 discovery snapshot 中的全部候选模式"
            className="text-xs font-medium text-primary hover:underline disabled:opacity-50"
          >
            全选全部 ({allPatternIds.length})
          </button>
          <button
            type="button"
            onClick={() => setSelectedPatterns([])}
            disabled={selectionBusy || selectedPatternIds.size === 0}
            className="text-xs text-muted-foreground hover:underline disabled:opacity-50"
          >
            清空选择
          </button>
          <span className="ml-auto text-xs text-muted-foreground">已选 {selectedPatternIds.size}</span>
        </div>
      )}

      <div className="divide-y divide-border">
        {visiblePatterns.map((pattern) => (
          <PatternRow
            key={pattern.patternId}
            pattern={pattern}
            selected={selectedPatternIds.has(pattern.patternId)}
            onSelectedChange={onAuditCoverage || onGenerateDrafts ? (selected) => togglePattern(pattern.patternId, selected) : undefined}
            audit={auditsByPatternId.get(pattern.patternId)}
            disabled={selectionBusy}
          />
        ))}
      </div>

      {result.patterns.length > visibleCount && (
        <div className="border-t border-border bg-muted/20 px-4 py-2.5 text-center">
          <button
            type="button"
            onClick={() => setVisibleCount((count) => Math.min(count + INITIAL_PATTERN_COUNT, result.patterns.length))}
            className="text-xs font-medium text-primary hover:underline"
          >
            显示更多模式 ({result.patterns.length - visibleCount})
          </button>
        </div>
      )}

      {(onAuditCoverage || onGenerateDrafts) && (
        <div className="flex flex-wrap items-center gap-3 border-t border-border bg-muted/20 px-4 py-3">
          <span className="text-xs text-muted-foreground">已选 {selectedPatternIds.size} 个模式</span>
          <div className="ml-auto flex flex-wrap gap-2">
            {onAuditCoverage && (
              <button
                type="button"
                onClick={auditSelectedPatterns}
                disabled={auditing || generatingDrafts || selectedPatternIds.size === 0}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
              >
                {auditing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <SearchCheck className="h-3.5 w-3.5" />}
                检查已有 testcase 候选 ({selectedPatternIds.size})
              </button>
            )}
            {onGenerateDrafts && auditResult && (
              <button
                type="button"
                onClick={generateSelectedDrafts}
                disabled={!canGenerateDrafts}
                title={selectedPatternsAreAudited ? "生成已审计模式的参数化草稿" : "请先审核所选模式的已有 testcase 候选"}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
              >
                {generatingDrafts ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileStack className="h-3.5 w-3.5" />}
                生成参数化草稿 ({selectedPatternIds.size})
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}