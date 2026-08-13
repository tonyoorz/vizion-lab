// Task memory: every completed mission (智能问数 / Test Agent) leaves a trace
// so it can be reviewed, diffed and re-run later.

import { idbClear, idbDelete, idbGetAll, idbPut } from "./db";

export type MissionType = "ask-data" | "test-agent" | "chat";

export interface MissionStep {
  name: string;
  status: string;
  summary?: string;
  ms?: number;
}

export interface MissionRecord {
  id: string;
  type: MissionType;
  goal: string;
  filters?: Record<string, unknown>;
  steps: MissionStep[];
  sql?: string[];
  result?: string;
  score?: number;
  ts: number;
}

export const listMissions = async (): Promise<MissionRecord[]> =>
  (await idbGetAll<MissionRecord>("missions")).sort((a, b) => b.ts - a.ts);

export async function recordMission(m: Omit<MissionRecord, "id" | "ts"> & Partial<Pick<MissionRecord, "id" | "ts">>) {
  const rec: MissionRecord = {
    id: m.id ?? crypto.randomUUID(),
    ts: m.ts ?? Date.now(),
    type: m.type,
    goal: m.goal,
    filters: m.filters,
    steps: m.steps ?? [],
    sql: m.sql,
    result: m.result,
    score: m.score,
  };
  await idbPut("missions", rec);
  return rec;
}

export const deleteMission = (id: string) => idbDelete("missions", id);
export const clearMissions = () => idbClear("missions");

/** Compact recap of the most relevant past missions, for prompt injection. */
export function serializeMissions(missions: MissionRecord[], max = 3): string {
  if (!missions.length) return "";
  const body = missions
    .slice(0, max)
    .map((m) => {
      const when = new Date(m.ts).toLocaleDateString("zh-CN");
      const sql = m.sql?.[0] ? `\n  SQL: ${m.sql[0].slice(0, 220)}` : "";
      const res = m.result ? `\n  结论: ${m.result.slice(0, 220)}` : "";
      return `- [${when}] ${m.goal}${sql}${res}`;
    })
    .join("\n");
  return `以下是用户近期的相关分析任务，可作为口径与做法参考：\n${body}`;
}
