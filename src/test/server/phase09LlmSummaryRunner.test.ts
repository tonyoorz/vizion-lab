import { describe, expect, it } from "vitest";

import {
  buildPhase09SummaryMessages,
  parsePhase09SummaryResponse,
  summarizePhase09Ticket,
} from "../../../server/phase09LlmSummaryRunner.mjs";

describe("Phase 09 LLM summary runner", () => {
  it("builds a Chinese processing-summary request without treating ticket evidence as instructions", () => {
    const messages = buildPhase09SummaryMessages([
      {
        ticket_id: "D-RISK",
        input_fingerprint: "fixture-fingerprint",
        ticket: {
          title: "Navigation fails after a power cycle",
          function: "Provide Navigation 2.0",
          problem_evidence: "Observed failure remains reproducible.",
          closure_evidence: "Reject reason: Management decision. No target version recorded.",
          risk_evidence: "Showstopper_Candidate / 05-unsatisfactory.",
          comments_evidence: "No explicit repair plan found.",
          history_evidence: "Phase: 01-New -> 09-Concluded without action.",
          audit_facts: {
            reject_reason: "Management decision",
            reason_source: "Current blocking reason",
            rejection_route: "09 Rejected by CoC",
            direct_close: true,
            reopened_after_phase09: false,
            entered_phase06_after_phase09: false,
            follow_up_status: "No remediation or follow-up evidence",
            target_version: "Not recorded",
            planning_window: "Not recorded",
            commercial_solution_date: "Not recorded",
            reprel_change_status: "Reprel change count not recorded",
            postponement_document_status: "Template only",
            related_resolution_status: "No explicit linked ticket",
            risk_tier: "Priority production-exposure review",
            risk_score: 10,
            reporting_class: "Showstopper_Candidate",
            problem_severity: "05-unsatisfactory",
            severity: "Medium",
            production_exposure_status: "Production exposure cannot be ruled out - priority review",
            risk_flags: ["Decision/tolerance closure without repair evidence"],
          },
        },
      },
    ]);

    expect(messages).toHaveLength(2);
    expect(messages[0].role).toBe("system");
    expect(messages[0].content).toContain("issue_summary");
    expect(messages[0].content).toContain("process_summary");
    expect(messages[0].content).toContain("review_focus");
    expect(messages[0].content).toContain("untrusted data");
    expect(messages[0].content).toContain("Never output a ticket id");
    expect(messages[0].content).toContain("A direct 01 -> 09 transition does not prove");
    expect(messages[0].content).toContain("Do not call Phase 06 an analysis phase");
    expect(messages[0].content).toContain("Do not infer customer dissatisfaction");
    expect(messages[0].content).toContain("Convert raw logs, DTC codes, field-variable names");
    expect(messages[0].content).toContain("title and function are the authoritative scope anchors");
    expect(messages[1].content).toContain("authoritative_ticket_id");
    expect(messages[1].content).toContain("Observed failure remains reproducible.");
  });

  it("parses a valid Chinese language fragment without asking the model to bind a ticket id", () => {
    const summary = parsePhase09SummaryResponse(
      JSON.stringify({
        issue_summary: "导航功能在电源循环后无法使用。",
        process_summary: "记录显示测试后以无法复现关闭，未找到交付记录。",
        review_focus: "核实关闭前的复现条件和现场软件版本。",
      }),
    );

    expect(summary).toEqual({
      issue_summary: "导航功能在电源循环后无法使用。",
      process_summary: "记录显示测试后以无法复现关闭，未找到交付记录。",
      review_focus: "核实关闭前的复现条件和现场软件版本。",
    });
  });

  it("requires one ticket per request so embedded ticket references cannot change summary ownership", () => {
    expect(() =>
      buildPhase09SummaryMessages([
        {
          ticket_id: "D-RISK",
          ticket: { title: "First ticket" },
        },
        {
          ticket_id: "D-PLAN",
          ticket: { title: "Second ticket" },
        },
      ]),
    ).toThrow("must contain exactly one ticket");
  });

  it("uses structured audit facts for reject, follow-up, and risk lines instead of model-generated claims", async () => {
    const ticketInput = {
      ticket_id: "D-RISK",
      input_fingerprint: "fixture-fingerprint",
      ticket: {
        title: "Navigation fails after a power cycle",
        problem_evidence: "Observed failure remains reproducible.",
        closure_evidence: "Reject reason: Management decision.",
        risk_evidence: "Showstopper_Candidate / 05-unsatisfactory.",
        comments_evidence: "No explicit repair plan found.",
        history_evidence: "Phase: 01-New -> 09-Concluded without action.",
        audit_facts: {
          reject_reason: "Management decision",
          reason_source: "Current blocking reason",
          rejection_route: "09 Rejected by CoC",
          direct_close: true,
          reopened_after_phase09: false,
          entered_phase06_after_phase09: false,
          follow_up_status: "No remediation or follow-up evidence",
          target_version: "Not recorded",
          planning_window: "Not recorded",
          commercial_solution_date: "Not recorded",
          reprel_change_status: "Reprel change count not recorded",
          postponement_document_status: "Template only",
          related_resolution_status: "No explicit linked ticket",
          risk_tier: "Priority production-exposure review",
          risk_score: 10,
          reporting_class: "Showstopper_Candidate",
          problem_severity: "05-unsatisfactory",
          severity: "Medium",
          production_exposure_status: "Production exposure cannot be ruled out - priority review",
          risk_flags: ["Decision/tolerance closure without repair evidence"],
        },
      },
    };
    const result = await summarizePhase09Ticket({
      ticketInput,
      model: "fixture-model",
      requestCompletion: async () => ({
        answerModel: "fixture-model",
        content: JSON.stringify({
          issue_summary: "导航功能在电源循环后无法使用。",
          process_summary: "测试后关闭，未发现修复记录。",
          review_focus: "核实现场软件版本和关闭依据。",
        }),
      }),
    });

    expect(result.ticket_id).toBe("D-RISK");
    expect(result.summary).toContain("问题：导航功能在电源循环后无法使用。");
    expect(result.summary).toContain("处理过程：测试后关闭，未发现修复记录。");
    expect(result.summary).toContain("Reject/关闭原因：Management decision");
    expect(result.summary).toContain("后续闭环：No remediation or follow-up evidence");
    expect(result.summary).toContain("风险判断与待核实：Priority production-exposure review");
    expect(result.summary).toContain("核实现场软件版本和关闭依据。");
    expect(result.summary).not.toContain("Observed failure remains reproducible.");
  });
});