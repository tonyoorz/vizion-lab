# 方案：缺陷 + 测试用例向量存储到 PostgreSQL + pgvector

## 目标
把 qgate_raw.db 中的 10.9万缺陷 + 1.7万测试用例向量化存到 PostgreSQL pgvector，
原 SQLite 完全不动，用 pgvector 做 similarity search 看效果。

## 数据源
- **缺陷**: `database/source/qgate_raw.db` → `octane_defects` 表 (109,509 行)
  - 文本拼接字段: name + description + project + pu + ecu + top_aida + fv + team + problem_severity
  - embedding 模型: BAAI/bge-small-zh-v1.5 (512维 float32)
  - 现有 embedding cache: `backend/database/ticket_embeddings.db` (8,880 行已有向量)
- **测试用例**: `database/source/qgate_raw.db` → `octane_testcases` 表 (16,842 行)
  - 文本拼接字段: test_name + test_subtype + scope_team + scope_release
  - 现有 embedding cache: `backend/database/test_case_embeddings.db` (16,842 行已有向量)

## 执行步骤

### 1. 建表 (PostgreSQL)
```sql
-- 缺陷向量表
CREATE TABLE defect_vectors (
    defect_id    TEXT PRIMARY KEY,
    name         TEXT,
    project      TEXT,
    problem_severity TEXT,
    text_hash    TEXT,
    embedding    vector(512),
    model_name   TEXT,
    updated_at   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX ON defect_vectors USING hnsw (embedding vector_cosine_ops);

-- 测试用例向量表
CREATE TABLE testcase_vectors (
    test_id      TEXT PRIMARY KEY,
    test_name    TEXT,
    scope_team   TEXT,
    scope_release TEXT,
    text_hash    TEXT,
    embedding    vector(512),
    model_name   TEXT,
    updated_at   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX ON testcase_vectors USING hnsw (embedding vector_cosine_ops);
```

### 2. 数据导入脚本
- 从 SQLite 读缺陷/测试用例文本字段
- 复用现有 ticket_embeddings.db / test_case_embeddings.db 中的 8,880 + 16,842 条已计算 embedding (不重复计算)
- 对缺失 embedding 的记录用 BAAI/bge-small-zh-v1.5 补算
- float32 bytes → pgvector 格式写入 PostgreSQL

### 3. 对比测试
- 同一个查询，分别在 SQLite (暴力全扫) 和 pgvector (HNSW索引) 上执行
- 对比: 查询速度、召回结果质量、Top-K 一致性
