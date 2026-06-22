
# Phase 3：pgvector + Hybrid RAG

为本体多 Agent 系统接入「向量检索 + 关键词检索」融合的知识层，让 Agent 能在生成 SQL / 解释结果之前，先召回相似的缺陷、用例、历史问答、本体说明，显著提升复杂业务问题的准确率。

## 目标

1. **语义召回**：用 Gemini Embedding 把缺陷标题/描述、用例步骤、本体说明、历史问答向量化，存入 Lovable Cloud 的 pgvector。
2. **混合检索**：向量相似度 + Postgres 全文检索 (`tsvector` / BM25 风格) → RRF (Reciprocal Rank Fusion) 融合。
3. **接入多 Agent**：在 Planner 之后、Ontologist 之前插入一个 **Retriever Agent**，召回的 chunk 作为上下文喂给后续 Agent。
4. **UI 可观测**：在 `AgentOrchestrator` 显示 Retriever 步骤卡，展示 Top-K 片段、来源、向量分/关键词分/RRF 分。

## 技术架构

```text
用户问句
   │
   ▼
[Planner] ──► [Retriever (Hybrid RAG)] ──► [Ontologist] ──► [SQL Writer]
                     │  ▲                        │
                     │  │                        ▼
                     │  └── pgvector + tsvector  [Executor → Critic → Presenter]
                     │
                     └── 召回: 本体说明 / 历史问答 / 缺陷样本 / 用例样本
```

## 数据库设计（Lovable Cloud / pgvector）

`knowledge_chunks` 表：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| source_type | text | `ontology` / `defect` / `testcase` / `qa_history` |
| source_id | text | 业务侧 ID（缺陷号/用例号/本体 entity 名） |
| title | text | 短标题，用于展示 |
| content | text | 入库正文 |
| content_tsv | tsvector | 由 content 自动生成 (GIN 索引) |
| embedding | vector(1536) | Gemini Embedding，显式 dimensions=1536（HNSW 友好） |
| metadata | jsonb | 模块、优先级、时间等过滤维度 |
| model_version | text | embedding 模型标记，方便未来重嵌入 |

索引：
- `HNSW (embedding vector_cosine_ops)`
- `GIN (content_tsv)`
- `btree (source_type)`, `GIN (metadata jsonb_path_ops)`

RPC：
- `match_knowledge(query_embedding, query_text, match_count, filter jsonb)` → 返回 `id, content, metadata, vec_score, kw_score, rrf_score`，服务端做 RRF 融合。

RLS：当前应用单租户使用，先 `authenticated` 可读写；后续若多租户再细化。

## 后端 Edge Functions

1. **`embed-knowledge`**（POST）
   - 入参：`{ items: [{ source_type, source_id, title, content, metadata }] }`
   - 逻辑：批量调用 `https://ai.gateway.lovable.dev/v1/embeddings`（`google/gemini-embedding-001`, `dimensions: 1536`），upsert 到 `knowledge_chunks`。
   - 自动分块：>1200 字符按段落切分，附 `chunk_index`。

2. **`hybrid-search`**（POST）
   - 入参：`{ query: string, top_k?: number=8, filter?: jsonb, source_types?: string[] }`
   - 逻辑：embed query → 调用 `match_knowledge` RPC → 返回融合后的 Top-K + 分数。
   - 同时返回 `latency_ms` 与各路得分，便于 UI 调试。

3. **`seed-ontology-knowledge`**（POST，一次性）
   - 把 `src/lib/ontology/definitions.ts` 中的 entity/metric 文档+同义词序列化为 chunk 灌入。

## 前端改动

1. **`src/lib/rag/client.ts`**（新建）
   - `searchKnowledge(query, opts)`, `ingestKnowledge(items)` 简单封装。

2. **`AgentOrchestrator.tsx`**（编辑）
   - 在 Planner 之后插入 Retriever 步骤：调用 `hybrid-search`，把 Top-K 注入后续 Agent 的 prompt 上下文。
   - 失败优雅降级：检索为空时继续走原链路并提示「无召回」。

3. **`RetrievalStepCard.tsx`**（新建）
   - 展示召回片段：来源类型 chip、标题、metadata、三栏分数条（vector / keyword / rrf）、原文 expand。

4. **`KnowledgeIngestionPanel.tsx`**（新建，挂在「智能问数」侧栏次级入口）
   - 三个按钮：「灌入本体说明」「从当前 DuckDB 缺陷表抽样灌入」「从历史问答灌入」。
   - 显示已入库 chunk 数 / 模型版本。

## 风险与降级

- **Embedding 配额**：批量入库限流 50/秒，UI 显示进度。
- **dims 不匹配**：固定 `dimensions: 1536`；若以后换模型，`model_version` 字段允许并存与渐进重嵌入。
- **召回噪声**：Retriever 输出由 Critic 校验，且 SQL Writer 仍受本体字段白名单约束，幻觉风险可控。
- **冷启动**：未灌数据时 Retriever 步骤显示「知识库为空，跳过」并不阻塞主链路。

## 交付清单

- [ ] DB migration：pgvector + `knowledge_chunks` + 索引 + `match_knowledge` RPC + RLS + GRANT
- [ ] `supabase/functions/embed-knowledge/index.ts`
- [ ] `supabase/functions/hybrid-search/index.ts`
- [ ] `supabase/functions/seed-ontology-knowledge/index.ts`
- [ ] `src/lib/rag/client.ts`
- [ ] `AgentOrchestrator.tsx` 集成 Retriever
- [ ] `RetrievalStepCard.tsx`
- [ ] `KnowledgeIngestionPanel.tsx`
- [ ] 更新 `.lovable/plan.md`

确认后我会一次性提交 migration 并并行落地三个 Edge Function 与前端组件。
