export function buildFilterDefectBridgePayload({ keyword, project, limit, offset, idsOnly }) {
  return {
    action: "search_defects_by_filter",
    keyword,
    project,
    limit,
    offset,
    ids_only: idsOnly,
  };
}