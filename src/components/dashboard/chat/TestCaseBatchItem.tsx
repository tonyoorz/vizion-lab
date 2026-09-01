import { CheckCircle2, AlertCircle, Loader2, ExternalLink, RefreshCw } from "lucide-react";
import type { TestCaseBatchItem, TestCaseVerification } from "./testCaseTypes";

interface Props {
  item: TestCaseBatchItem;
  index: number;
  expanded: boolean;
  onToggleExpand: () => void;
  onToggleSelect: () => void;
  onEdit: (patch: Partial<TestCaseBatchItem>) => void;
  onReverify: () => Promise<void>;
  verifyState: "idle" | "verifying" | "done";
  verifyError: string;
}

const OCTANE_BASE = "https://octane-prod.bmwgroup.net/ui/entity-navigation?p=1002/2001&entityType=test&id=";

export default function TestCaseBatchItemCard({
  item,
  index,
  expanded,
  onToggleExpand,
  onToggleSelect,
  onEdit,
  onReverify,
  verifyState,
  verifyError,
}: Props) {
  const verification: TestCaseVerification = item.verification;
  const hasError = Boolean(item.error);
  const passedCount = verification.criteria.filter((c) => c.passed).length;

  return (
    <div
      className={`rounded-xl border overflow-hidden transition-colors ${
        item.commitState === "success"
          ? "border-green-300 dark:border-green-800"
          : hasError
            ? "border-red-300 dark:border-red-800"
            : item.selected
              ? "border-primary/50 bg-primary/5"
              : "border-border bg-card"
      }`}
    >
      {/* collapsed header row — always visible */}
      <div className="flex items-center gap-3 px-4 py-3">
        <input
          type="checkbox"
          checked={item.selected}
          onChange={onToggleSelect}
          disabled={hasError || item.commitState === "success" || item.commitState === "submitting"}
          className="h-4 w-4 shrink-0 rounded border-border"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="shrink-0 text-xs font-mono text-muted-foreground">#{index + 1}</span>
            <span className="shrink-0 text-xs font-mono text-blue-600 dark:text-blue-400">D{item.defectId}</span>
            {item.defectSeverity && (
              <span
                className={`shrink-0 inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ${
                  item.defectSeverity.includes("High") || item.defectSeverity.includes("Very")
                    ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                {item.defectSeverity}
              </span>
            )}
            <span className="truncate text-sm font-medium text-foreground">{item.name || "(未生成)"}</span>
          </div>
          {hasError ? (
            <p className="mt-0.5 text-xs text-red-600 dark:text-red-400">{item.error}</p>
          ) : (
            <p className="mt-0.5 text-xs text-muted-foreground truncate">{item.defectName}</p>
          )}
        </div>

        {/* status indicator */}
        <div className="shrink-0">
          {item.commitState === "submitting" ? (
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
          ) : item.commitState === "success" ? (
            <a
              href={item.octaneUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-green-600 hover:underline dark:text-green-400"
            >
              <CheckCircle2 className="h-4 w-4" />
              <ExternalLink className="h-3 w-3" />
            </a>
          ) : item.commitState === "error" ? (
            <span title={item.commitError}>
              <AlertCircle className="h-4 w-4 text-red-500" />
            </span>
          ) : !hasError ? (
            <span
              className={`inline-flex items-center gap-1 text-[10px] font-medium ${
                verification.passed
                  ? "text-green-600 dark:text-green-400"
                  : "text-amber-600 dark:text-amber-400"
              }`}
            >
              {verification.passed ? <CheckCircle2 className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
              {passedCount}/{verification.criteria.length}
            </span>
          ) : null}
        </div>

        {/* expand toggle */}
        {!hasError && (
          <button
            onClick={onToggleExpand}
            className="shrink-0 text-xs text-blue-600 hover:underline dark:text-blue-400"
          >
            {expanded ? "收起" : "展开"}
          </button>
        )}
      </div>

      {/* expanded detail — editable content */}
      {expanded && !hasError && (
        <div className="space-y-4 border-t border-border p-4">
          {/* test case name (editable) */}
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">用例名称</p>
            <input
              type="text"
              value={item.name}
              onChange={(e) => onEdit({ name: e.target.value })}
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>

          {/* defect info badges */}
          <div className="flex flex-wrap gap-2 text-xs">
            {item.defectSoftwareVersion && <Badge label={item.defectSoftwareVersion} />}
            {item.defectAssignedEcu && <Badge label={item.defectAssignedEcu} />}
            {item.defectLeadModel && <Badge label={item.defectLeadModel} />}
          </div>

          {/* similar cases */}
          {item.similarCases.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                RAG 参考用例 ({item.similarCases.length})
              </p>
              <div className="space-y-1">
                {item.similarCases.slice(0, 3).map((c) => (
                  <a
                    key={c.testId}
                    href={`${OCTANE_BASE}${c.testId}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 text-xs text-blue-600 hover:underline"
                  >
                    <span className="font-mono">T{c.testId}</span>
                    <span className="truncate text-muted-foreground">{c.name}</span>
                    <span className="ml-auto shrink-0 text-muted-foreground">score: {c.score}</span>
                  </a>
                ))}
              </div>
            </div>
          )}

          {/* description (editable) */}
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">描述 (Description)</p>
            <textarea
              value={item.descriptionHtml}
              onChange={(e) => onEdit({ descriptionHtml: e.target.value })}
              className="w-full h-32 rounded-lg border border-border bg-background p-3 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary resize-y"
            />
          </div>

          {/* steps (editable) */}
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">步骤 (Steps)</p>
            <textarea
              value={item.stepsText}
              onChange={(e) => onEdit({ stepsText: e.target.value })}
              className="w-full h-40 rounded-lg border border-border bg-background p-3 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary resize-y whitespace-pre-wrap"
              placeholder={"每行一步：\n- [PreCon] 前置条件\n- 操作步骤\n- ? 预期结果"}
            />
          </div>

          {/* verification */}
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                质量验证 ({verification.passed ? "全部通过" : `${passedCount}/${verification.criteria.length} 通过`})
              </p>
              <button
                onClick={onReverify}
                disabled={verifyState === "verifying"}
                className="ml-auto inline-flex items-center gap-1 text-xs text-blue-600 hover:underline disabled:opacity-50"
              >
                {verifyState === "verifying" ? (
                  <>
                    <Loader2 className="h-3 w-3 animate-spin" /> 校验中…
                  </>
                ) : (
                  <>
                    <RefreshCw className="h-3 w-3" /> 重新校验
                  </>
                )}
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {verification.criteria.map((c) => (
                <span
                  key={c.name}
                  title={c.evidence}
                  className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    c.passed
                      ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                      : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                  }`}
                >
                  {c.passed ? <CheckCircle2 className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
                  {c.name}
                </span>
              ))}
            </div>
            {verifyError && <p className="text-xs text-red-600 dark:text-red-400">{verifyError}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

function Badge({ label }: { label: string }) {
  return (
    <span className="inline-flex rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
      {label}
    </span>
  );
}
