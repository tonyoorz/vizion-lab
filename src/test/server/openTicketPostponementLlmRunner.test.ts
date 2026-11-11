import { describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as postponementRunner from "../../../server/openTicketPostponementLlmRunner.mjs";

import {
  buildOpenTicketPostponementMessages,
  parseOpenTicketPostponementResponse,
  summarizeOpenTicketPostponement,
} from "../../../server/openTicketPostponementLlmRunner.mjs";

const ticketInput = {
  ticket_id: "D-OPEN-1",
  input_fingerprint: "fixture-fingerprint",
  input_version: "open-shift-ticket-plan-llm-v3",
  ticket: {
    title: "Navigation screen stays blank",
    phase: "04-In Progress",
    problem_evidence: "Screen remains blank after restart.",
    comments_evidence: "Verification remains open.",
    source_software_version: "idcevo-pu2707-27w01.1-1",
    source_release: "27-07",
    target_i_step: "NA05-27-11-460",
    target_release: "27-11",
    target_evidence_field: "target_i_step_udf",
    target_recorded_at: "2026-10-10T08:00:00Z",
    shift_to_set_pu: "27-11",
    first_use_sop_pu: "27-11",
    supplier_confirmed_i_step: "",
    predicted_target_i_step: "",
    target_change_trail: "2026-10-10 | NA05-27-07-460 -> NA05-27-11-460",
    target_week: "CW47",
    target_set: "",
    supplier_confirmed_target_week: "",
    commercial_solution_date: "",
    postponement_decision_date: "2026-10-09",
    postponement_document_evidence: "Delivery moved because verification remains open.",
    reprel_changes: "1",
    closed_in_version: "",
    sw_hrl_closed_version: "",
    app_version: "",
    phase07_fix_versions: "",
    phase07_fix_version_quality: "Not a Phase 07 ticket",
    plan_evidence_signals: "Target I-Step: NA05-27-11-460\nShift-to-Set PU: 27-11",
    classification: "confirmed_postponed",
    postponed_release_steps: 1,
    history_evidence: "target_i_step_udf changed to NA05-27-11-460.",
  },
};

describe("open-ticket postponement LLM runner", () => {
  it("requires dated current-state evidence and rejects missing or invented citations", () => {
    const currentInput = {
      ...ticketInput,
      input_version: "shift-current-evidence-v2",
      ticket: {
        ...ticketInput.ticket,
        evidence: [{ id: "C-1", date: "2026-10-10", source: "comment", text: "Fix reported; retest pending." }],
      },
    };
    const messages = buildOpenTicketPostponementMessages([currentInput]);
    expect(messages[0].content).toContain("historical_shift_status");
    expect(messages[0].content).toContain("retracted");
    expect(messages[0].content).toContain("repair_status");
    expect(messages[0].content).toContain("database_snapshot");
    expect(() => parseOpenTicketPostponementResponse(JSON.stringify({
      issue_summary: "Observed symptom.",
      postponement_reason_analysis: "Historical plan only.",
      resolution_timing_analysis: "No timing recorded.",
      resolution_version_analysis: "No version recorded.",
      review_focus: "Retest required.",
    }), currentInput)).toThrow();
    const response = {
      issue_summary: "Observed symptom.",
      postponement_reason_analysis: "Historical plan only.",
      resolution_timing_analysis: "No timing recorded.",
      resolution_version_analysis: "No version recorded.",
      review_focus: "Retest required.",
      current_state_summary: "2026-10-10: fix reported, retest pending.",
      root_cause_analysis: "No verified root cause recorded.",
      recovery_analysis: "2026-10-10: fix reported, not verified.",
      historical_shift_status: "uncertain",
      current_postponement_status: "unclear",
      repair_status: "verification_pending",
      delivery_status: "unknown",
      status_evidence: {
        historical_shift_status: [], current_postponement_status: [],
        repair_status: ["C-1"], delivery_status: [],
      },
    };
    expect(parseOpenTicketPostponementResponse(JSON.stringify(response), currentInput).repair_status).toBe("verification_pending");
    expect(typeof postponementRunner.canReuseOpenTicketPostponementSummary).toBe("function");
    const cached = {
      ...response, ticket_id: currentInput.ticket_id,
      input_version: currentInput.input_version, input_fingerprint: currentInput.input_fingerprint,
      source: "llm", model: "deepseek-v4-pro", postponement_assessment: "Recorded plan and retest pending.",
    };
    expect(postponementRunner.canReuseOpenTicketPostponementSummary(currentInput, cached, "deepseek-v4-pro")).toBe(true);
    expect(postponementRunner.canReuseOpenTicketPostponementSummary(currentInput, cached, "glm-5.1")).toBe(false);
    expect(postponementRunner.canReuseOpenTicketPostponementSummary(currentInput, { ...cached, input_fingerprint: "old" }, "deepseek-v4-pro")).toBe(false);
    expect(postponementRunner.canReuseOpenTicketPostponementSummary(currentInput, { ...cached, root_cause_analysis: "" }, "deepseek-v4-pro")).toBe(false);
    expect(postponementRunner.canReuseOpenTicketPostponementSummary(currentInput, { ...cached, status_evidence: { ...cached.status_evidence, repair_status: ["invented"] } }, "deepseek-v4-pro")).toBe(false);
    const directory = mkdtempSync(join(tmpdir(), "shift-summary-cache-"));
    try {
      const inputPath = join(directory, "new-run-inputs.jsonl");
      const cachePath = join(directory, "deepseek-v4-pro-cache.jsonl");
      const originalCache = JSON.stringify(cached) + "\n";
      writeFileSync(inputPath, JSON.stringify(currentInput) + "\n");
      writeFileSync(cachePath, originalCache);
      const preload = 'globalThis.fetch = async () => new Response("MODEL_REQUEST_FORBIDDEN_IN_CACHE_TEST", { status: 400 });';
      const argumentsList = [
        "--import", "data:text/javascript," + encodeURIComponent(preload),
        "scripts/summarizeOpenTicketPostponementInputs.mjs",
        "--input", inputPath, "--output", cachePath,
        "--model", "deepseek-v4-pro", "--max-retries", "1",
      ];
      const executionOptions = {
        encoding: "utf8" as const, stdio: "pipe" as const, timeout: 20000,
        env: { ...process.env, GLM_PROXY_API_KEY: "fixture-no-network", DUPSEARCH_GLM_PROXY_API_BASE: "http://127.0.0.1:1/v1" },
      };
      const output = execFileSync(process.execPath, argumentsList, executionOptions);
      expect(output).toContain("1 cached, 0 pending");
      expect(output).toContain("0 completed, 0 failed");
      expect(readFileSync(cachePath, "utf8")).toBe(originalCache);
      writeFileSync(cachePath, originalCache + JSON.stringify({ ...cached, issue_summary: "" }) + "\n");
      let rejectedOutput = "";
      try {
        execFileSync(process.execPath, argumentsList, executionOptions);
      } catch (error) {
        rejectedOutput = String((error as { stdout?: string }).stdout || "");
      }
      expect(rejectedOutput).toContain("0 cached, 1 pending");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
    const uncited = { ...response, current_postponement_status: "not_pending" };
    const guarded = parseOpenTicketPostponementResponse(JSON.stringify(uncited), currentInput);
    expect(guarded.current_postponement_status).toBe("unclear");
    expect(guarded.assessment_warnings).toHaveLength(1);
    const diagnostic = parseOpenTicketPostponementResponse(JSON.stringify({ ...response, issue_summary: "Warning 0x1234; recordedSignalName=failed." }), currentInput);
    expect(diagnostic.issue_summary).not.toContain("0x1234");
    expect(diagnostic.issue_summary).not.toContain("recordedSignalName=");
    expect(diagnostic.assessment_warnings.join(" ")).toContain("redacted");
    response.status_evidence.repair_status = ["not-in-input"];
    expect(() => parseOpenTicketPostponementResponse(JSON.stringify(response), currentInput)).toThrow(/evidence/i);
  });

  it("builds an evidence-bounded Chinese request", () => {
    const messages = buildOpenTicketPostponementMessages([ticketInput]);

    expect(messages).toHaveLength(2);
    expect(messages[0].content).toContain("untrusted data");
    expect(messages[0].content).toContain("postponement_reason_analysis");
    expect(messages[0].content).toContain("resolution_timing_analysis");
    expect(messages[0].content).toContain("resolution_version_analysis");
    expect(messages[0].content).toContain("Shift-to-Set");
    expect(messages[0].content).toContain("Backendfix");
    expect(messages[0].content).toContain("Do not invent a root cause");
    expect(messages[0].content).toContain("Never output a ticket id");
    expect(messages[1].content).toContain("NA05-27-11-460");
  });

  it("parses the required Chinese fields and keeps audited delivery facts separate", async () => {
    const parsed = parseOpenTicketPostponementResponse(
      JSON.stringify({
        issue_summary: "重启后导航屏幕仍为空白。",
        postponement_assessment: "记录显示目标从 27-07 调整为 27-11；文档仅说明验证仍未完成。",
        postponement_reason_analysis: "记录仅说明验证尚未完成，未记录更具体的延期原因。",
        resolution_timing_analysis: "延期决定日期为 2026-10-09，目标周为 CW47。",
        resolution_version_analysis: "记录的目标 I-Step 为 NA05-27-11-460，属于后续 27-11 软件计划。",
        review_focus: "核实验证未完成的具体项目和交付准入条件。",
      }),
    );

    expect(parsed.issue_summary).toContain("导航");
    expect(parsed.postponement_reason_analysis).toContain("验证");
    expect(parsed.resolution_timing_analysis).toContain("CW47");
    expect(parsed.resolution_version_analysis).toContain("NA05-27-11-460");
    let requestOptions;
    const result = await summarizeOpenTicketPostponement({
      ticketInput,
      model: "fixture-model",
      requestCompletion: async (options) => {
        requestOptions = options;
        return {
          answerModel: "fixture-model",
          content: JSON.stringify(parsed),
        };
      },
    });

    expect(result.ticket_id).toBe("D-OPEN-1");
    expect(result.summary).toContain("问题：重启后导航屏幕仍为空白。");
    expect(result.summary).toContain("目标软件：NA05-27-11-460");
    expect(result.summary).toContain("Shift-to-Set：27-11");
    expect(result.summary).toContain("延期原因：记录仅说明验证尚未完成");
    expect(result.summary).toContain("解决时间：延期决定日期为 2026-10-09");
    expect(result.summary).toContain("解决版本：记录的目标 I-Step 为 NA05-27-11-460");
    expect(requestOptions.responseFormat).toEqual({ type: "json_object" });
    expect(result.source).toBe("llm");
  });
});