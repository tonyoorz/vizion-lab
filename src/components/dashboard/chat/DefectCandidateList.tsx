import { useState, useEffect } from "react";
import { Search, Loader2, Upload, FileDown, Network, ChevronLeft, ChevronRight, X } from "lucide-react";
import type {
  TestCaseDefectCandidate,
  TestCaseFilteredDefect,
  BatchProgress,
} from "./testCaseTypes";

/**
 * A defect row unified from the two candidate sources. The similarity-search
 * path (TestCaseDefectCandidate) fills similarity/snippet; the topic-filter path
 * (TestCaseFilteredDefect) fills phase/hasLinkedTest. Either set is optional.
 */
type UnifiedDefect = {
  defectId: string;
  name: string;
  project: string | null;
  phase: string | null;
  statusPhase: string | null;
  similarity?: number;
  snippet?: string;
  hasLinkedTest?: boolean;
};

interface Props {
  /** Similarity-search candidates (legacy /search path). */
  candidates?: TestCaseDefectCandidate[];
  /** Topic-filter candidates with pagination metadata (new /filter-defects path). */
  filterResult?: {
    candidates: TestCaseFilteredDefect[];
    total: number;
    keyword: string;
  };
  /** Called when the user picks a subset to generate test cases from. */
  onSelect: (defectIds: string[]) => void;
  /** Fetch a specific page (1-indexed) of filter candidates. Returns the page's candidates. */
  onLoadPage?: (page: number) => Promise<TestCaseFilteredDefect[] | null>;
  /** Optional: export generated drafts to CSV (topic-filter mode). */
  onExportCsv?: (defectIds: string[]) => Promise<void>;
  /** Fetch ALL defect IDs (lightweight, no metadata) for "select all" across pages. */
  onSelectAll?: () => Promise<string[] | null>;
  /** Discover reusable testcase patterns from the selected topic-filter defects. */
  onDiscoverPatterns?: (defectIds: string[]) => Promise<void>;
  /** Real-time progress for long-running batch export (shown as a progress bar). */
  batchProgress?: BatchProgress;
  /** Cancel the in-progress batch export. */
  onCancel?: () => void;
}

const OCTANE_DEFECT_URL = "https://octane-prod.bmwgroup.net/ui/entity-navigation?p=1002/2001&entityType=work_item&id=";
const PAGE_SIZE = 50;

/**
 * Displays defect candidates in one of two modes:
 *
 *  - Similarity search (candidates prop): flat list, no pagination.
 *  - Topic filter (filterResult prop): paginated — 50 per page with
 *    prev/next + page number navigation. Selection persists across pages.
 *
 * Lets the user pick a subset, then either generate test-case drafts (onSelect)
 * or export drafts to CSV (onExportCsv — topic-filter mode only).
 */
export default function DefectCandidateList({
  candidates,
  filterResult,
  onSelect,
  onLoadPage,
  onExportCsv,
  onSelectAll,
  onDiscoverPatterns,
  batchProgress,
  onCancel,
}: Props) {
  const isFilterMode = Boolean(filterResult);
  const initialDefects: UnifiedDefect[] = isFilterMode
    ? (filterResult!.candidates.map((c) => ({
        defectId: c.defectId,
        name: c.name,
        project: c.project,
        phase: c.phase,
        statusPhase: c.statusPhase,
        hasLinkedTest: c.hasLinkedTest,
      })) as UnifiedDefect[])
    : (candidates || []).map((c) => ({
        defectId: c.defectId,
        name: c.name,
        project: c.project,
        phase: null,
        statusPhase: c.statusPhase,
        similarity: c.similarity,
        snippet: c.snippet,
      }));

  const [pageDefects, setPageDefects] = useState<UnifiedDefect[]>(initialDefects);
  const [selected, setSelected] = useState<Set<string>>(
    new Set(initialDefects.map((c) => c.defectId))
  );
  const [submitting, setSubmitting] = useState(false);
  const [loadingPage, setLoadingPage] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);

  const total = filterResult?.total ?? pageDefects.length;
  const totalPages = isFilterMode ? Math.max(1, Math.ceil(total / PAGE_SIZE)) : 1;
  const [currentPage, setCurrentPage] = useState(1);

  // Keep selection state synced when page data changes (select all on new page
  // if "select all" was the prior state for that page — simplest: just reflect
  // what the user toggles. Selection persists across pages via the Set.)
  useEffect(() => {
    setPageDefects(initialDefects);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterResult]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const togglePage = () => {
    // Toggle all on the current page.
    const pageIds = pageDefects.map((c) => c.defectId);
    const allSelected = pageIds.every((id) => selected.has(id));
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        pageIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const handleGenerate = async () => {
    const ids = [...selected];
    if (ids.length === 0) return;
    setSubmitting(true);
    onSelect(ids);
  };

  const handleExportCsv = async () => {
    if (!onExportCsv) return;
    const ids = [...selected];
    if (ids.length === 0) return;
    setExporting(true);
    try {
      await onExportCsv(ids);
    } finally {
      setExporting(false);
    }
  };

  const [selectingAll, setSelectingAll] = useState(false);
  const [selectAllError, setSelectAllError] = useState<string | null>(null);
  const [discoveringPatterns, setDiscoveringPatterns] = useState(false);

  const handleSelectAll = async () => {
    if (!onSelectAll) return;
    setSelectAllError(null);
    // If already all-selected, clear. Otherwise fetch all IDs and select them.
    if (selected.size >= total) {
      setSelected(new Set());
      return;
    }
    setSelectingAll(true);
    try {
      const allIds = await onSelectAll();
      if (allIds && allIds.length > 0) {
        setSelected(new Set(allIds));
      } else {
        setSelectAllError("获取全部 ID 失败，请检查 server 是否已重启");
      }
    } catch (err) {
      setSelectAllError(err instanceof Error ? err.message : String(err));
    } finally {
      setSelectingAll(false);
    }
  };

  const handleDiscoverPatterns = async () => {
    if (!onDiscoverPatterns || selected.size === 0) return;
    setDiscoveringPatterns(true);
    try {
      await onDiscoverPatterns([...selected]);
    } finally {
      setDiscoveringPatterns(false);
    }
  };

  const goToPage = async (page: number) => {
    if (!onLoadPage || page < 1 || page > totalPages || page === currentPage) return;
    setLoadingPage(true);
    try {
      const newPage = await onLoadPage(page);
      if (newPage && newPage.length > 0) {
        const unified: UnifiedDefect[] = newPage.map((c) => ({
          defectId: c.defectId,
          name: c.name,
          project: c.project,
          phase: c.phase,
          statusPhase: c.statusPhase,
          hasLinkedTest: c.hasLinkedTest,
        }));
        setPageDefects(unified);
        setCurrentPage(page);
      }
    } finally {
      setLoadingPage(false);
    }
  };

  const busy = submitting || exporting || loadingPage || selectingAll || discoveringPatterns;
  const allOnPageSelected = pageDefects.length > 0 && pageDefects.every((c) => selected.has(c.defectId));

  // Page number buttons: show current ± 2, plus first and last.
  const pageNumbers: number[] = [];
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pageNumbers.push(i);
  } else {
    pageNumbers.push(1);
    const start = Math.max(2, currentPage - 1);
    const end = Math.min(totalPages - 1, currentPage + 1);
    if (start > 2) pageNumbers.push(-1); // ellipsis
    for (let i = start; i <= end; i++) pageNumbers.push(i);
    if (end < totalPages - 1) pageNumbers.push(-1); // ellipsis
    pageNumbers.push(totalPages);
  }

  return (
    <div className="mt-3 rounded-xl border border-border bg-card overflow-hidden">
      {/* header */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 bg-muted/30">
        <Search className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">
          {isFilterMode ? "匹配缺陷列表（已排除关联 testcase）" : "匹配缺陷列表"}
        </span>
        <span className="text-xs text-muted-foreground">
          {isFilterMode
            ? `共 ${total} 个 · 已选 ${selected.size}`
            : `${pageDefects.length} 条 · 已选 ${selected.size}`}
        </span>
        <div className="ml-auto flex items-center gap-3">
          {isFilterMode && onSelectAll && (
            <button
              onClick={handleSelectAll}
              disabled={busy}
              className="text-xs text-blue-600 hover:underline dark:text-blue-400 disabled:opacity-50"
            >
              {selectingAll ? (
                <span className="inline-flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" /> 加载中…
                </span>
              ) : selected.size >= total ? (
                "取消全选所有"
              ) : (
                `全选所有 (${total})`
              )}
            </button>
          )}
          <button
            onClick={togglePage}
            disabled={busy}
            className="text-xs text-blue-600 hover:underline dark:text-blue-400 disabled:opacity-50"
          >
            {allOnPageSelected ? "取消本页" : "全选本页"}
          </button>
        </div>
      </div>

      {/* progress / status line */}
      {progress && !batchProgress && (
        <div className="border-b border-border bg-primary/5 px-4 py-2 text-xs text-primary">
          {progress}
        </div>
      )}

      {/* select-all error — shown when fetching all IDs fails (e.g. server not restarted) */}
      {selectAllError && (
        <div className="border-b border-border bg-destructive/10 px-4 py-2 text-xs text-destructive">
          ⚠ {selectAllError}
        </div>
      )}

      {/* batch progress bar — shown during long-running export */}
      {batchProgress && batchProgress.phase !== "done" && (
        <div className="border-b border-border bg-primary/5 px-4 py-3">
          <div className="flex items-center justify-between text-xs text-primary mb-1.5">
            <span className="font-medium">
              {batchProgress.phase === "generating" && "生成测试用例草稿中…"}
              {batchProgress.phase === "clustering" && "聚类 + 导出 CSV 中…"}
              {batchProgress.phase === "error" && "导出失败"}
              {batchProgress.phase === "cancelled" && "已取消"}
            </span>
            {onCancel && (batchProgress.phase === "generating" || batchProgress.phase === "clustering") && (
              <button
                onClick={onCancel}
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-destructive"
              >
                <X className="h-3 w-3" /> 取消
              </button>
            )}
          </div>
          {batchProgress.total > 0 && (
            <>
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-primary transition-all duration-500"
                  style={{ width: `${Math.round((batchProgress.current / batchProgress.total) * 100)}%` }}
                />
              </div>
              <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
                <span>
                  {batchProgress.current} / {batchProgress.total}
                  {batchProgress.totalBatches > 1 && batchProgress.phase === "generating" && (
                    <span className="ml-1">（第 {batchProgress.batchIndex}/{batchProgress.totalBatches} 批）</span>
                  )}
                </span>
                <span>{Math.round((batchProgress.current / batchProgress.total) * 100)}%</span>
              </div>
            </>
          )}
        </div>
      )}

      {/* candidate list */}
      <div className="divide-y divide-border max-h-[480px] overflow-y-auto">
        {pageDefects.map((c) => (
          <label
            key={c.defectId}
            className={`flex items-start gap-3 px-4 py-2.5 cursor-pointer transition-colors hover:bg-muted/20 ${
              selected.has(c.defectId) ? "bg-primary/5" : ""
            }`}
          >
            <input
              type="checkbox"
              checked={selected.has(c.defectId)}
              onChange={() => toggle(c.defectId)}
              disabled={busy}
              className="mt-0.5 h-4 w-4 shrink-0 rounded border-border"
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <a
                  href={`${OCTANE_DEFECT_URL}${c.defectId}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="shrink-0 text-xs font-mono text-blue-600 hover:underline dark:text-blue-400"
                  onClick={(e) => e.stopPropagation()}
                >
                  D{c.defectId}
                </a>
                {c.project && (
                  <span className="shrink-0 inline-flex rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                    {c.project}
                  </span>
                )}
                {c.statusPhase && (
                  <span className="shrink-0 text-[10px] text-muted-foreground">{c.statusPhase}</span>
                )}
                {typeof c.similarity === "number" && (
                  <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                    {(c.similarity * 100).toFixed(0)}% match
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-sm text-foreground line-clamp-2">{c.name}</p>
              {c.snippet && (
                <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{c.snippet}</p>
              )}
            </div>
          </label>
        ))}

        {loadingPage && (
          <div className="flex items-center justify-center gap-2 px-4 py-4 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> 加载中…
          </div>
        )}
      </div>

      {/* pagination bar — only in filter mode */}
      {isFilterMode && totalPages > 1 && (
        <div className="flex items-center justify-center gap-1 border-t border-border px-4 py-2 bg-muted/20">
          <button
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage <= 1 || busy}
            className="rounded p-1.5 text-muted-foreground hover:bg-muted disabled:opacity-30"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          {pageNumbers.map((pn, idx) =>
            pn === -1 ? (
              <span key={`ellipsis-${idx}`} className="px-1 text-xs text-muted-foreground">…</span>
            ) : (
              <button
                key={pn}
                onClick={() => goToPage(pn)}
                disabled={busy}
                className={`min-w-[28px] rounded px-2 py-1 text-xs ${
                  pn === currentPage
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted"
                } disabled:opacity-50`}
              >
                {pn}
              </button>
            )
          )}
          <button
            onClick={() => goToPage(currentPage + 1)}
            disabled={currentPage >= totalPages || busy}
            className="rounded p-1.5 text-muted-foreground hover:bg-muted disabled:opacity-30"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* action bar */}
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3 bg-muted/20">
        <span className="text-xs text-muted-foreground">
          {isFilterMode
            ? `已选 ${selected.size} 个 defect，可生成测试用例或导出 CSV`
            : "勾选后点击「生成测试用例」将批量创建草稿"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {isFilterMode && onDiscoverPatterns && (
            <button
              onClick={handleDiscoverPatterns}
              disabled={busy || selected.size === 0}
              className="inline-flex items-center gap-1.5 rounded-md border border-primary/30 bg-primary/5 px-4 py-1.5 text-xs font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
            >
              {discoveringPatterns ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> 发现中…
                </>
              ) : (
                <>
                  <Network className="h-3.5 w-3.5" /> 发现复用模式 ({selected.size})
                </>
              )}
            </button>
          )}
          {isFilterMode && onExportCsv && (
            <button
              onClick={handleExportCsv}
              disabled={busy || selected.size === 0}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-4 py-1.5 text-xs font-medium text-foreground hover:bg-muted/50 disabled:opacity-50"
            >
              {exporting ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> 导出中…
                </>
              ) : (
                <>
                  <FileDown className="h-3.5 w-3.5" /> 导出 CSV ({selected.size})
                </>
              )}
            </button>
          )}
          <button
            onClick={handleGenerate}
            disabled={busy || selected.size === 0}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
          >
            {submitting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> 生成中…
              </>
            ) : (
              <>
                <Upload className="h-3.5 w-3.5" /> 生成测试用例 ({selected.size})
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
