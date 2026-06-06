# 本地真实数据库自动接入

把你 `testagent/database/` 里的文件**复制 / 软链**到此目录，DTSV 启动时会自动加载到浏览器内的 DuckDB（只读 + 数据不出端）。

## 支持格式
- `.duckdb` — 直接 ATTACH（推荐）
- `.csv` / `.tsv` / `.parquet` / `.json` / `.ndjson` — 注册为表

> SQLite (`.sqlite` / `.db`) 暂不支持自动加载，请用 DuckDB CLI 一次性转换：
> `duckdb out.duckdb -c "INSTALL sqlite; LOAD sqlite; ATTACH 'src.sqlite' AS s (TYPE SQLITE); CREATE TABLE t AS SELECT * FROM s.t;"`

## 启用方式
方式 A — 自动清单（推荐）：编辑 `manifest.json`，列出要加载的文件：

```json
{
  "files": [
    "testagent.duckdb",
    "defects.parquet",
    "cases.csv"
  ]
}
```

方式 B — 手动：在 Chat 里把文件拖进输入框即可（已有功能）。

## 工作原理
1. 启动时 `AIChat` fetch `/database/manifest.json`
2. 逐个 fetch 文件 → `duckdbManager.registerDuckdbFile/registerTabular`
3. Schema 通过 `summarizeSchemaForPrompt()` 自动注入到 system prompt
4. Agent 通过 `<tool name="query_sql">` 受限只读执行（500 行截断，SQL AST 白名单）

## 软链示例（macOS / Linux）
```bash
cd public/database
ln -s ~/code/testagent/database/test.duckdb test.duckdb
echo '{"files":["test.duckdb"]}' > manifest.json
```

⚠️ `public/` 下的文件会被打包到生产环境。生产部署前请确认无敏感数据，或把 manifest 改为空。
