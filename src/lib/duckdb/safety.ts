// Lightweight SQL safety check for browser-side DuckDB.
// Only allow read statements: SELECT / WITH / DESCRIBE / SHOW / PRAGMA / EXPLAIN.
// Reject anything that could mutate state, load extensions, or touch the network.

const BANNED = [
  "ATTACH",
  "DETACH",
  "INSTALL",
  "LOAD",
  "COPY",
  "EXPORT",
  "IMPORT",
  "INSERT",
  "UPDATE",
  "DELETE",
  "MERGE",
  "CREATE",
  "DROP",
  "ALTER",
  "TRUNCATE",
  "GRANT",
  "REVOKE",
  "SET",
  "CALL",
  "VACUUM",
  "CHECKPOINT",
  "USE",
  "BEGIN",
  "COMMIT",
  "ROLLBACK",
];

const ALLOWED_LEADING = ["SELECT", "WITH", "DESCRIBE", "DESC", "SHOW", "PRAGMA", "EXPLAIN", "SUMMARIZE"];

export function isSafeReadOnlySql(raw: string): boolean {
  // Strip /* */ and -- comments
  const stripped = raw
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/--[^\n]*/g, " ")
    .trim();
  if (!stripped) return false;
  // Only one statement
  if (stripped.replace(/;+\s*$/, "").includes(";")) return false;

  const upper = stripped.toUpperCase();
  const firstWord = upper.match(/^[A-Z]+/)?.[0];
  if (!firstWord || !ALLOWED_LEADING.includes(firstWord)) return false;

  // Reject banned keywords as standalone tokens
  const tokenRe = new RegExp(`\\b(${BANNED.join("|")})\\b`);
  if (tokenRe.test(upper)) return false;

  return true;
}
