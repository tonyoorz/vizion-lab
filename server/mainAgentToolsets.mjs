/**
 * Toolset selection — model-driven.
 *
 * Per-intent tool allowlists have been removed.  Non-direct queries receive
 * the full governed tool set; the model chooses which tools to call.
 * Governance is enforced at the execution layer (schema enums,
 * additionalProperties: false, actor-scope checks, evidence gates).
 */

/**
 * Returns all tool names from the provided tool set.
 * Used by isToolAllowed as the default allow-list.
 */
export function toolNamesForIntent(_intent, allTools) {
  return (Array.isArray(allTools) ? allTools : [])
    .map((tool) => tool.function?.name)
    .filter(Boolean);
}

/**
 * Returns the full governed tool set for any intent.
 */
export function toolsForIntent(_intent, allTools) {
  return Array.isArray(allTools) ? allTools : [];
}

/**
 * Policy gate: allows any tool that is in the selected toolset.
 * Since the toolset is now the full set, all governed tools are allowed.
 * Actor-scope enforcement happens at the executor level, not here.
 */
export function isToolAllowed(toolName, selectedToolset) {
  return Array.isArray(selectedToolset?.toolNames) && selectedToolset.toolNames.includes(toolName);
}
