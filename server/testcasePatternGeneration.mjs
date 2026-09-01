function displayValues(values) {
  return Array.isArray(values)
    ? values.map((value) => String(value || "").trim()).filter(Boolean).join(", ")
    : "";
}

export function buildReusablePatternPromptContext(patternContext) {
  const memberDefectIds = Array.isArray(patternContext?.memberDefectIds)
    ? patternContext.memberDefectIds.map((id) => String(id || "").trim()).filter(Boolean)
    : [];
  const parameterMatrix = patternContext?.parameterMatrix || {};
  const representative = patternContext?.representative || {};
  const memberIdsPreview = memberDefectIds.slice(0, 50).map((id) => `D${id}`).join(", ");
  const omittedMemberCount = Math.max(memberDefectIds.length - 50, 0);

  return {
    systemInstructions: [
      "This request represents a reviewed defect pattern, not one isolated defect.",
      "Generate exactly one parameterized reusable manual testcase for the shared failure mode.",
      "Keep common steps shared and add branches only for the explicitly supplied parameter variations.",
      "Use only the supplied evidence, ECU/model context, and parameter values. Do not invent variants or thresholds.",
      "Include the pattern ID and representative defect in the Reference section.",
      "Do not claim that every member defect is covered; the member mapping remains subject to human review.",
    ].join("\n"),
    userContext: [
      "Reusable pattern context:",
      `Pattern ID: ${String(patternContext?.patternId || "")}`,
      `Representative defect: D${String(representative.defectId || "")}${representative.name ? ` - ${representative.name}` : ""}`,
      `Pattern members (${Number(patternContext?.memberCount || memberDefectIds.length)}): ${memberIdsPreview}${omittedMemberCount ? ` (+${omittedMemberCount} in the reviewed mapping)` : ""}`,
      `Common failure-mode anchors: ${displayValues(patternContext?.commonAnchorTerms) || "none recorded"}`,
      `Allowed software versions: ${displayValues(parameterMatrix.softwareVersions) || "none recorded"}`,
      `Allowed detected releases: ${displayValues(parameterMatrix.detectedInReleases) || "none recorded"}`,
      patternContext?.evidencePreview ? `Representative evidence: ${String(patternContext.evidencePreview)}` : "",
    ].filter(Boolean).join("\n"),
  };
}

export function buildReusablePatternVerificationPayload({ defectInfo, patternContext, generated }) {
  return {
    action: "verify_reusable_testcase_pattern",
    defect_info: defectInfo,
    pattern_id: patternContext?.patternId,
    member_defect_ids: patternContext?.memberDefectIds || [],
    parameter_matrix: patternContext?.parameterMatrix || {},
    description_html: generated?.descriptionHtml || "",
    steps_text: generated?.stepsText || "",
  };
}

export function buildReusablePatternDraftExportPayload({ runId, drafts }) {
  return {
    action: "export_reusable_pattern_drafts",
    run_id: runId,
    drafts,
  };
}