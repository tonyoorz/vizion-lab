import { useState } from "react";
import { CheckCircle2, AlertCircle, Loader2, FlaskConical, ExternalLink, RefreshCw } from "lucide-react";
import type { TestCaseResult, TestCaseVerification } from "./testCaseTypes";

interface Props {
  result: TestCaseResult;
}

const OCTANE_BASE = "https://octane-prod.bmwgroup.net/ui/entity-navigation?p=1002/2001&entityType=test&id=";

export default function TestCasePreviewCard({ result }: Props) {
  const DEFAULT_OWNER_ID = "138017";

  // Editable copies of the generated content — user can modify before committing.
  const [editedName, setEditedName] = useState(result.name);
  const [editedDescription, setEditedDescription] = useState(result.descriptionHtml);
  const [editedSteps, setEditedSteps] = useState(result.stepsText);
  const [descMode, setDescMode] = useState<"preview" | "edit">("preview");

  // Verification state — refreshed when user clicks "重新校验" after editing.
  const [verification, setVerification] = useState<TestCaseVerification>(result.verification);
  const [verifyState, setVerifyState] = useState<"idle" | "verifying" | "done">("idle");
  const [verifyError, setVerifyError] = useState("");

  const [commitState, setCommitState] = useState<"idle" | "submitting" | "success" | "error">("idle");
  const [commitError, setCommitError] = useState("");
  const [createdUrl, setCreatedUrl] = useState("");
  const [featureId, setFeatureId] = useState("");

  const handleReverify = async () => {
    setVerifyState("verifying");
    setVerifyError("");
    try {
      const defectInfo = {
        defect_id: result.defectId,
        name: result.defectName,
        severity: result.defectSeverity,
        software_version: result.defectSoftwareVersion || "",
        assigned_ecu: result.defectAssignedEcu || "",
      };
      const response = await fetch("/api/create-testcase/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          defect_info: defectInfo,
          description_html: editedDescription,
          steps_text: editedSteps,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (response.ok && payload.success) {
        setVerification(payload.verification);
      } else {
        setVerifyError(payload.error || "校验失败");
      }
    } catch (err) {
      setVerifyError(String(err?.message || err));
    } finally {
      setVerifyState("done");
    }
  };

  const handleCommit = async () => {
    setCommitState("submitting");
    setCommitError("");
    try {
      const response = await fetch("/api/create-testcase/commit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          test_case_data: {
            name: editedName,
            description_html: editedDescription,
            steps_text: editedSteps,
            defectId: result.defectId,
            owner_workspace_user_id: DEFAULT_OWNER_ID,
          },
          feature_id: featureId,
          owner_workspace_user_id: DEFAULT_OWNER_ID,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (response.ok && payload.success) {
        setCreatedUrl(payload.octane_url || "");
        setCommitState("success");
      } else {
        setCommitError(payload.error || "创建失败");
        setCommitState("error");
      }
    } catch (err) {
      setCommitError(String(err?.message || err));
      setCommitState("error");
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-border bg-card overflow-hidden">
      {/* header */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 bg-muted/30">
        <FlaskConical className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">测试用例预览</span>
        <span className="ml-auto text-xs text-muted-foreground">
          {new Date(result.generatedAt).toLocaleString("zh-CN")}
        </span>
      </div>

      <div className="space-y-4 p-4">
        {/* defect info (read-only) */}
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">缺陷信息</p>
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge label={result.defectId} />
            <Badge label={result.defectSeverity} variant={result.defectSeverity.includes("High") || result.defectSeverity.includes("Very") ? "danger" : "default"} />
            {result.defectSoftwareVersion && <Badge label={result.defectSoftwareVersion} />}
            {result.defectAssignedEcu && <Badge label={result.defectAssignedEcu} />}
            {result.defectLeadModel && <Badge label={result.defectLeadModel} />}
          </div>
          <p className="text-sm text-foreground mt-1">{result.defectName}</p>
        </div>

        {/* test case name (editable) */}
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">用例名称</p>
          <input
            type="text"
            value={editedName}
            onChange={(e) => setEditedName(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>

        {/* similar cases (RAG, read-only) */}
        {result.similarCases.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              RAG 检索参考用例 ({result.similarCases.length})
            </p>
            <div className="space-y-1">
              {result.similarCases.slice(0, 3).map((c) => (
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

        {/* description (editable, preview/edit toggle) */}
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">描述 (Description)</p>
            <button
              onClick={() => setDescMode(descMode === "preview" ? "edit" : "preview")}
              className="ml-auto text-xs text-blue-600 hover:underline"
            >
              {descMode === "preview" ? "编辑" : "预览"}
            </button>
          </div>
          {descMode === "preview" ? (
            <div
              className="rounded-lg border border-border bg-muted/20 p-3 text-xs prose prose-sm max-w-none"
              dangerouslySetInnerHTML={{ __html: editedDescription }}
            />
          ) : (
            <textarea
              value={editedDescription}
              onChange={(e) => setEditedDescription(e.target.value)}
              className="w-full h-64 rounded-lg border border-border bg-background p-3 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary resize-y"
              placeholder="HTML 源码 — 编辑后切回预览查看渲染效果"
            />
          )}
        </div>

        {/* steps (editable textarea) */}
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">步骤 (Steps)</p>
          <textarea
            value={editedSteps}
            onChange={(e) => setEditedSteps(e.target.value)}
            className="w-full h-48 rounded-lg border border-border bg-background p-3 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary resize-y whitespace-pre-wrap"
            placeholder={"每行一步：\n- [PreCon] 前置条件\n- 操作步骤\n- ? 预期结果"}
          />
          <p className="text-[10px] text-muted-foreground">
            格式：<span className="text-blue-600">- [PreCon]</span> 前置条件 · <span className="text-foreground">-</span> 操作步骤 · <span className="text-green-600">- ?</span> 预期结果
          </p>
        </div>

        {/* verification badges + re-verify button */}
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              质量验证 ({verification.passed ? "全部通过" : `${verification.criteria.filter((c) => c.passed).length}/${verification.criteria.length} 通过`})
            </p>
            <button
              onClick={handleReverify}
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
          {verifyError && (
            <p className="text-xs text-red-600 dark:text-red-400">{verifyError}</p>
          )}
          {verifyState === "done" && !verifyError && (
            <p className="text-[10px] text-muted-foreground">已重新校验编辑后的内容</p>
          )}
        </div>

        {/* commit section */}
        {commitState === "success" ? (
          <div className="flex items-center gap-2 rounded-lg border border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-900/20 px-4 py-3">
            <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400" />
            <span className="text-sm font-medium text-green-700 dark:text-green-400">测试用例已创建</span>
            <a
              href={createdUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
            >
              在 Octane 中打开 <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        ) : (
          <div className="space-y-2 rounded-lg border border-border bg-muted/20 p-3">
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={featureId}
                onChange={(e) => setFeatureId(e.target.value)}
                placeholder="Feature ID（可选，link 到 feature）"
                className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-xs"
              />
              <button
                onClick={handleCommit}
                disabled={commitState === "submitting"}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
              >
                {commitState === "submitting" ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> 创建中…
                  </>
                ) : (
                  "确认创建"
                )}
              </button>
            </div>
            {commitState === "error" && (
              <p className="text-xs text-red-600 dark:text-red-400">{commitError}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Badge({ label, variant = "default" }: { label: string; variant?: "default" | "danger" }) {
  const color =
    variant === "danger"
      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
      : "bg-muted text-muted-foreground";
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ${color}`}>{label}</span>;
}
