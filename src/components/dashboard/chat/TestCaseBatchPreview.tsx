import { useState, useCallback } from "react";
import { FlaskConical, Loader2, Upload, AlertCircle, CheckCircle2, ExternalLink } from "lucide-react";
import type { TestCaseBatchItem, TestCaseBatchPrepareResponse } from "./testCaseTypes";
import TestCaseBatchItemCard from "./TestCaseBatchItem";

const DEFAULT_OWNER_ID = "138017";

interface Props {
  /** The initial batch prepare response (from /api/create-testcase/prepare-batch). */
  initialResponse: TestCaseBatchPrepareResponse;
  /** Called when the user edits the batch state (for external persistence if needed). */
  onItemsChange?: (items: TestCaseBatchItem[]) => void;
}

export default function TestCaseBatchPreview({ initialResponse, onItemsChange }: Props) {
  // Initialize items from the prepare response, adding batch UI state.
  const [items, setItems] = useState<TestCaseBatchItem[]>(() =>
    (initialResponse.results || []).map((r) => ({
      ...r,
      selected: !r.error,
      error: r.error ?? null,
      commitState: "idle" as const,
      commitError: "",
      octaneUrl: undefined,
    })),
  );
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);
  const [featureId, setFeatureId] = useState("");
  const [batchCommitState, setBatchCommitState] = useState<"idle" | "committing" | "done">("idle");
  const [verifyStates, setVerifyStates] = useState<Record<number, { state: "idle" | "verifying" | "done"; error: string }>>({});

  const updateItem = useCallback(
    (index: number, patch: Partial<TestCaseBatchItem>) => {
      setItems((prev) => {
        const next = prev.map((it, i) => (i === index ? { ...it, ...patch } : it));
        onItemsChange?.(next);
        return next;
      });
    },
    [onItemsChange],
  );

  const toggleSelect = useCallback((index: number) => {
    setItems((prev) => prev.map((it, i) => (i === index ? { ...it, selected: !it.selected } : it)));
  }, []);

  const handleReverify = useCallback(
    async (index: number) => {
      setVerifyStates((prev) => ({ ...prev, [index]: { state: "verifying", error: "" } }));
      const item = items[index];
      try {
        const defectInfo = {
          defect_id: item.defectId,
          name: item.defectName,
          severity: item.defectSeverity,
          software_version: item.defectSoftwareVersion || "",
          assigned_ecu: item.defectAssignedEcu || "",
        };
        const response = await fetch("/api/create-testcase/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            defect_info: defectInfo,
            description_html: item.descriptionHtml,
            steps_text: item.stepsText,
          }),
        });
        const payload = await response.json().catch(() => ({}));
        if (response.ok && payload.success) {
          updateItem(index, { verification: payload.verification });
          setVerifyStates((prev) => ({ ...prev, [index]: { state: "done", error: "" } }));
        } else {
          setVerifyStates((prev) => ({ ...prev, [index]: { state: "done", error: payload.error || "校验失败" } }));
        }
      } catch (err) {
        setVerifyStates((prev) => ({ ...prev, [index]: { state: "done", error: String(err instanceof Error ? err.message : err) } }));
      }
    },
    [items, updateItem],
  );

  const selectedCount = items.filter((it) => it.selected).length;
  const succeededCount = items.filter((it) => it.commitState === "success").length;
  const failedCount = items.filter((it) => it.commitState === "error").length;
  const allDone = items.every((it) => it.commitState === "success" || it.commitState === "error");

  const handleBatchCommit = async () => {
    const selectedItems = items.filter((it) => it.selected && it.commitState !== "success");
    if (selectedItems.length === 0) return;

    setBatchCommitState("committing");

    // Mark all selected as "submitting" up front for UI feedback.
    setItems((prev) =>
      prev.map((it) =>
        it.selected && it.commitState !== "success" ? { ...it, commitState: "submitting", commitError: "" } : it,
      ),
    );

    try {
      const response = await fetch("/api/create-testcase/commit-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: selectedItems.map((it) => ({
            test_case_data: {
              name: it.name,
              description_html: it.descriptionHtml,
              steps_text: it.stepsText,
              defectId: it.defectId,
              owner_workspace_user_id: DEFAULT_OWNER_ID,
            },
            feature_id: featureId,
          })),
          owner_workspace_user_id: DEFAULT_OWNER_ID,
        }),
      });
      const payload = await response.json().catch(() => ({}));

      if (response.ok && payload.success) {
        // Apply commit results to items by defectId.
        const resultMap = new Map<string, { success: boolean; octane_url?: string; error: string | null }>();
        for (const r of payload.results || []) {
          resultMap.set(r.defectId, { success: r.success, octane_url: r.octane_url, error: r.error });
        }
        setItems((prev) =>
          prev.map((it) => {
            const r = resultMap.get(it.defectId);
            if (!r) return it;
            return {
              ...it,
              commitState: r.success ? "success" : "error",
              commitError: r.error || "",
              octaneUrl: r.octane_url,
            } as TestCaseBatchItem;
          }),
        );
      } else {
        // Global failure — mark all selected as error.
        const errorMsg = payload.error || "批量创建失败";
        setItems((prev) =>
          prev.map((it) =>
            it.selected && it.commitState === "submitting"
              ? { ...it, commitState: "error", commitError: errorMsg }
              : it,
          ),
        );
      }
    } catch (err) {
      const msg = String(err instanceof Error ? err.message : err);
      setItems((prev) =>
        prev.map((it) =>
          it.selected && it.commitState === "submitting" ? { ...it, commitState: "error", commitError: msg } : it,
        ),
      );
    } finally {
      setBatchCommitState("done");
    }
  };

  const toggleAll = () => {
    const anyUnselected = items.some((it) => !it.selected && !it.error);
    setItems((prev) =>
      prev.map((it) => (it.error ? it : { ...it, selected: anyUnselected })),
    );
  };

  return (
    <div className="mt-3 rounded-xl border border-border bg-card overflow-hidden">
      {/* header */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 bg-muted/30">
        <FlaskConical className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">批量测试用例预览</span>
        <span className="text-xs text-muted-foreground">
          共 {items.length} 条 · 选中 {selectedCount}
        </span>
        <span className="ml-auto text-xs text-muted-foreground">
          {batchCommitState === "done" && `成功 ${succeededCount} · 失败 ${failedCount}`}
        </span>
      </div>

      <div className="space-y-2 p-4">
        {/* items */}
        {items.map((item, i) => {
          const vs = verifyStates[i] || { state: "idle" as const, error: "" };
          return (
            <TestCaseBatchItemCard
              key={item.defectId || i}
              item={item}
              index={i}
              expanded={expandedIndex === i}
              onToggleExpand={() => setExpandedIndex(expandedIndex === i ? null : i)}
              onToggleSelect={() => toggleSelect(i)}
              onEdit={(patch) => updateItem(i, patch)}
              onReverify={() => handleReverify(i)}
              verifyState={vs.state}
              verifyError={vs.error}
            />
          );
        })}

        {/* batch commit bar */}
        {allDone ? (
          <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/20 px-4 py-3">
            <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400" />
            <span className="text-sm font-medium text-green-700 dark:text-green-400">批量创建完成</span>
            <span className="text-xs text-muted-foreground">
              成功 {succeededCount} · 失败 {failedCount}
            </span>
            <div className="ml-auto flex flex-wrap gap-1">
              {items
                .filter((it) => it.commitState === "success" && it.octaneUrl)
                .map((it) => (
                  <a
                    key={it.defectId}
                    href={it.octaneUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
                  >
                    D{it.defectId} <ExternalLink className="h-3 w-3" />
                  </a>
                ))}
            </div>
          </div>
        ) : (
          <div className="space-y-2 rounded-lg border border-border bg-muted/20 p-3">
            <div className="flex items-center gap-2">
              <button
                onClick={toggleAll}
                className="text-xs text-blue-600 hover:underline dark:text-blue-400"
              >
                {items.some((it) => !it.selected && !it.error) ? "全选" : "取消全选"}
              </button>
              <input
                type="text"
                value={featureId}
                onChange={(e) => setFeatureId(e.target.value)}
                placeholder="Feature ID（可选，统一 link 到 feature）"
                className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-xs"
              />
              <button
                onClick={handleBatchCommit}
                disabled={batchCommitState === "committing" || selectedCount === 0}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
              >
                {batchCommitState === "committing" ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> 创建中…
                  </>
                ) : (
                  <>
                    <Upload className="h-3.5 w-3.5" /> 批量创建 ({selectedCount})
                  </>
                )}
              </button>
            </div>
            {batchCommitState === "committing" && (
              <p className="text-[10px] text-muted-foreground">
                正在逐条写入 Octane（每条含 test 实体 + steps 脚本），请稍候…
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
