# 本地 DuckDB 接入 AI Agent · 实施方案

## 目标
让用户把本地 `.duckdb` 文件（或 CSV/Parquet）直接拖进 DTSV Chat，AI 能用 SQL 探查、自动生成 insights、识别测试过程中的风险与异常，全程数据不出浏览器。

---

## 技术选型（对标 ChatGPT Advanced Data Analysis / Hex Magic / Julius AI）

| 关注点 | 选择 | 理由 |
|---|---|---|
| 本地数据库引擎 | **DuckDB-WASM** (`@duckdb/duckdb-wasm`) | 浏览器原生跑 .duckdb / CSV / Parquet，零后端、数据不出端 |
| SQL 生成 | **Lovable AI Gateway + Tool Calling** (`query_sql` / `profile_table` / `detect_anomaly`) | OpenAI 兼容，多步 agent loop |
| 自动洞察 | **schema profiling + 启发式风险规则 + LLM 总结** | 业界共识：先 profile 再让 LLM 看摘要而非原始数据 |
| 图表 | 复用现有 `<chart>` Recharts 渲染 | 一致性 |
| 大文件加载 | OPFS（Origin Private File System）持久化 | Hex/Mode 同款方案 |
| 安全 | **只读连接 + SQL AST 校验**（禁止 ATTACH/INSTALL/COPY TO/写操作） | 防止 prompt injection 改本地数据 |

---

## 架构
```
浏览器
 ├─ DuckDBProvider (单例 Worker)
 │   ├─ 加载 .duckdb / .csv / .parquet → 注册为表
 │   └─ 暴露 runQuery(sql) / listTables() / describeTable()
 ├─ AIChat
 │   ├─ 文件区识别 .duckdb/.csv/.parquet → 走 DuckDB 而非 extract-file
 │   ├─ 把 schema 摘要注入 system prompt
 │   └─ 收到 <tool name="query_sql"> → 本地执行 → 结果回灌 → 继续流
 └─ Insights 面板（可选）：右侧 Canvas 展示 profiling 卡片
Edge Function `chat`
 └─ 新增工具协议（XML 标签）：
    <tool name="query_sql">SELECT ...</tool>
    <tool name="profile_table">table_name</tool>
    <tool name="detect_anomaly" col="x" table="y" />
```

> 说明：保持现有 SSE + XML 标签风格（不引入 AI SDK 重构），把"工具调用"做成模型输出 `<tool .../>` → 前端拦截执行 → 把 `<tool_result name="..." id="...">JSON</tool_result>` 追加到 messages → 再发一轮。最小侵入。

---

## 实施步骤（一轮 PR）

### Step 1 · DuckDB-WASM 接入
- `bun add @duckdb/duckdb-wasm apache-arrow`
- 新文件 `src/lib/duckdb/client.ts`：单例初始化 Worker、CDN bundle、`registerFileBuffer` 加载用户文件、`runQuery` 返回 Arrow → JSON。
- 新文件 `src/lib/duckdb/safety.ts`：SQL 白名单校验（只允许 SELECT/WITH/PRAGMA show_tables、describe；拒绝 ATTACH/INSTALL/LOAD/COPY/INSERT/UPDATE/DELETE/CREATE/DROP）。

### Step 2 · 文件上传识别
- `AIChat.tsx` `handleFiles` 分流：
  - `.duckdb` → `db.registerDatabase(file)`（用 `ATTACH ... (READ_ONLY)` 内部受控调用）
  - `.csv/.parquet` → `db.registerTable(file)`
  - 其他保持现有 `extract-file`
- Attachment chip 显示表数 + 总行数。

### Step 3 · Schema 摘要 + 风险 profiling
- `src/lib/duckdb/profile.ts`：对每张表跑 `SUMMARIZE`、`SELECT COUNT(*)、null率、distinct率、min/max/p50/p95`；对时间列做月度聚合；对枚举列 top-K。
- 摘要 → markdown 注入 system prompt（替代/追加 DATASET_SCHEMA）。

### Step 4 · Tool calling 协议
- `agentParser.ts` 增加 `tool` 段类型：`<tool name="query_sql" id="t1">SQL</tool>`。
- `MessageRenderer.tsx` 渲染工具卡片（运行中 / 结果摘要 / 可展开原始表）。
- `AIChat.tsx` 在流结束（或检测到 `<tool .../>` 闭合）后：
  1. 本地执行（DuckDB / profile / anomaly）
  2. 把 `<tool_result id="t1">{rows, cols, truncated}</tool_result>` 作为 user/system 消息追加
  3. 重新调用 chat edge function 让模型继续
  - 用 `stopWhen` 等价的循环上限：最多 5 轮工具调用。

### Step 5 · 风险发现内置规则
`detect_anomaly` 工具的内置策略：
- 时间序列：z-score > 3 / IQR 离群 / 突变点（PELT 简版）
- 类别失衡：某 status 占比突增 > 30%
- 缺陷长尾：`age_days > P95` 数量环比涨
- 覆盖回退：覆盖率环比降 > 5pp
返回结构化 finding 让 LLM 转成自然语言。

### Step 6 · System prompt 升级（`chat/index.ts`）
新增段落：
- 描述新工具集 + JSON schema
- 强制"先 `<tool name="profile_table">` 再写 SQL"
- 明确"只生成 DuckDB 方言 SQL，禁止写操作"
- 引用规则：`<cite source="duckdb:tableName">`

### Step 7 · UI 细节
- 顶部状态条：🟢 已连接 DuckDB · 3 表 · 12.4 万行
- Slash 命令新增：`/explore <table>` `/risk-scan` `/sql ...`
- 工具卡片：DuckDB SVG 图标 + 行数/耗时

---

## 安全 & 隐私
- 所有数据停留在浏览器 OPFS，不上传后端
- SQL 校验在执行前 + DuckDB 端 `SET enable_external_access=false; SET lock_configuration=true`
- 工具结果回传给 LLM 时**采样**（默认 top 200 行 + 全量聚合统计），避免泄漏明细 & 控 token

---

## 不在本轮范围（建议下轮）
- Canvas/Artifact 模式（右侧可编辑 SQL/图表）
- Python 沙箱（Pyodide）做预测 / Prophet
- 持久化会话到 Lovable Cloud（要先做登录）
- 多文件 JOIN 向导 UI

---

## 交付物（本轮 PR 改动）
- 新增 `src/lib/duckdb/{client,safety,profile}.ts`
- 修改 `AIChat.tsx`、`MessageRenderer.tsx`、`agentParser.ts`、`SlashMenu.tsx`
- 修改 `supabase/functions/chat/index.ts`（system prompt + 工具协议说明）
- `package.json` 新增 2 个依赖

确认后我直接开干。