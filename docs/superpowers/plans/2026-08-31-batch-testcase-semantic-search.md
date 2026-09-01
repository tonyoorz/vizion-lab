# 批量测试用例创建：语义查询 → 批量审核 → 批量提交

**状态**: 待 review
**日期**: 2026-08-31
**分支**: feature/2026-08-28-multiuser-auth (基于当前 HEAD)

---

## 1. 背景与问题

### 现状：单条 defect → 单条 testcase 的硬绑定

整条创建链路每一层都是单条设计：

| 层 | 文件 | 单条证据 |
|---|---|---|
| HTTP API | `server/index.mjs:646-655` | `POST /api/create-testcase` 只接收一个 `defect_id` 字符串 |
| Bridge | `scripts/testcase_bridge.py:129-149` | `_do_prepare()` 一次只读一个 defect |
| 写入 | `scripts/testcase_bridge.py:177-215` | `_do_commit()` 一次只建一个 test |
| Skill | `.claude/skills/create-octane-testcase/SKILL.md:71-166` | procedure 全程假设一对一 |
| 前端 | `TestCasePreviewCard.tsx` | 只渲染单条 `TestCaseResult` |

**用户给 5 个 defect 号**（如 `2743423,2812109,2783932,2620842,2610279`），当前只能串行处理，没有批量编排、没有统一审核界面。

### 目标

1. **意图 A — 批量创建**：每个 defect 各生成一个 testcase，可批量审核、批量提交。
2. **意图 B — 语义查询**：不写 defect 号，用户说"基于历史的 RSU 的 defect"，先语义检索匹配的 defect 列表，展示给用户确认，再进入批量创建流程。

### 设计原则

- **复用现有基建**：语义检索复用 `duplicate_search_bridge.py` 的 16K+ defect embedding 索引（同一个 `DuplicateIssueIndex` 引擎），不新建索引。
- **两阶段分离**：阶段 1（查询 + 生成草稿）不写 Octane，安全可迭代；阶段 2（批量提交）才写，需用户确认。
- **逐条审核 + 批量提交**：用户逐条查看/编辑/勾选草稿，确认后一次性串行提交选中的。
- **最小侵入**：不改动现有单条 `/api/create-testcase` 端点（保持向后兼容），新增端点。

---

## 2. 整体流程

```
用户输入（两种入口）
  ├─ A. 显式 defect 号列表: "2743423, 2812109, ..."
  └─ B. 语义查询: "基于历史的 RSU 的 defect"
        │
        ▼
┌─────────────────────────────────────────────┐
│ 阶段 1: 查询 + 生成草稿（不写 Octane）       │
│                                             │
│  1a. 解析输入                                │
│      - A: 直接得到 defect_id 列表            │
│      - B: 调语义检索 → 返回 N 个候选 defect  │
│           (复用 duplicate_search 索引)        │
│                                             │
│  1b. 展示候选 defect 列表给用户              │
│      用户勾选要生成 test 的 defect 子集       │
│                                             │
│  1c. 对选中的 defect 并行生成草稿            │
│      每个 defect:                            │
│        prepare (读 defect + RAG)             │
│        → LLM 生成 description + steps       │
│        → verify (LLM-as-judge)              │
│      限并发 2-3，避免 LLM rate limit         │
│                                             │
│  1d. 展示 N 份草稿（批量审核 UI）            │
│      表格/卡片列表，每条可编辑、可勾选       │
└─────────────────────────────────────────────┘
        │ 用户确认要提交的子集
        ▼
┌─────────────────────────────────────────────┐
│ 阶段 2: 批量提交（写 Octane，需确认）        │
│                                             │
│  2a. 串行 commit 选中的草稿                 │
│      每条: create_test_case + write_steps   │
│      单条失败不影响其他，继续下一条          │
│                                             │
│  2b. 汇总结果                                │
│      成功 X 条（带 Octane 链接）             │
│      失败 Y 条（带错误原因）                 │
└─────────────────────────────────────────────┘
```

---

## 3. 改动清单

### 3.1 Python Bridge — `scripts/testcase_bridge.py`

新增 2 个 action，现有 4 个 action 不变。

#### Action: `search_defects`（语义查询）

复用 `duplicate_search_bridge.py` 的 `_search()`，但只返回 defect 候选列表，不带 reranker feedback。

```python
def _do_search_defects(payload: Dict[str, Any]) -> Dict[str, Any]:
    """语义检索 defect，返回候选列表供用户选择。

    复用 duplicate_search 的索引引擎，但：
    - 不写 feedback（只读检索）
    - 返回字段精简为 testcase 流程需要的子集
    """
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"success": False, "error": "query is required"}

    top_k = int(payload.get("top_k") or 10)
    top_k = max(1, min(30, top_k))

    from duplicate_search_bridge import _get_cached_defect_df, _build_cache_key
    from duplicate_issue_finder import get_or_build_index_with_metadata
    from progressive_reranker import get_progressive_reranker

    df, cache_key, _ = _get_cached_defect_df(REPO_ROOT)
    index, _ = get_or_build_index_with_metadata(cache_key=cache_key, df=df)

    hints = extract_hints(query)
    reranker = get_progressive_reranker()
    candidates, _ = index.search_with_metadata(
        query, hints=hints, top_k=top_k, reranker=reranker,
        feedback_db_path=str(BACKEND_ROOT / 'database' / 'duplicate_feedback.db'),
    )

    return {
        "success": True,
        "candidates": [
            {
                "defectId": str(getattr(c, 'ticket_id', '') or ''),
                "name": str(getattr(c, 'name', '') or ''),
                "project": getattr(c, 'project', None),
                "pu": getattr(c, 'pu', None),
                "statusPhase": getattr(c, 'status_phase', None),
                "similarity": float(getattr(c, 'similarity', 0.0) or 0.0),
                "snippet": str(getattr(c, 'snippet', '') or '')[:200],
            }
            for c in candidates
        ],
    }
```

#### Action: `prepare_batch`（批量生成草稿）

输入 defect_id 列表，对每个 defect 跑 prepare + LLM 生成 + verify，返回 N 份草稿。

**关键设计**：LLM 生成仍由 Node 层（`generateTestCaseContent`）负责，Python bridge 只做 `prepare`（读 defect + RAG）。批量生成在 Node 层用 `Promise` 限并发编排，Python bridge 保持无状态单条 `prepare`。

理由：`generateTestCaseContent` 在 [server/index.mjs:340-396](server/index.mjs#L340-L396) 是 Node 代码（调 LLM API），不应下沉到 Python。所以 `prepare_batch` 实际上不需要新增 —— Node 层循环调用现有 `prepare` action 即可。

**结论：Python bridge 只新增 `search_defects` 一个 action。**

### 3.2 Node Server — `server/index.mjs`

新增 2 个 HTTP 端点，现有 4 个端点不变。

#### 端点: `POST /api/create-testcase/search`（语义查询）

```
请求: { "query": "RSU 历史缺陷", "top_k": 10 }
响应: {
  "success": true,
  "candidates": [
    { "defectId": "2743423", "name": "...", "project": "...", "pu": "...",
      "statusPhase": "...", "similarity": 0.87, "snippet": "..." },
    ...
  ]
}
```

实现：调 `runTestCaseBridge({ action: "search_defects", query, top_k })`。

#### 端点: `POST /api/create-testcase/prepare-batch`（批量生成草稿）

```
请求: { "defect_ids": ["2743423", "2812109", ...], "intent": "可选意图描述" }
响应: {
  "success": true,
  "results": [
    {
      "defectId": "2743423",
      "defectName": "...",
      "defectSeverity": "...",
      "defectSoftwareVersion": "...",
      "defectAssignedEcu": "...",
      "defectLeadModel": "...",
      "name": "[IDCEVO][PU2707] Regression - ... (D2743423)",
      "descriptionHtml": "<html>...",
      "stepsText": "- [PreCon] ...",
      "verification": { "passed": true, "criteria": [...], "feedback": "" },
      "similarCases": [{ "testId": "...", "name": "...", "score": 0.85 }],
      "generatedAt": "2026-08-31T...",
      "error": null  // 单条失败时填错误信息，不阻断其他
    },
    ...
  ],
  "summary": { "total": 5, "succeeded": 4, "failed": 1 }
}
```

实现（核心逻辑）：

```javascript
// server/index.mjs — 新增端点
if (request.method === "POST" && url.pathname === "/api/create-testcase/prepare-batch") {
  if (!await requireInternalAuxiliaryActor(request, response)) return;
  const body = await readJsonBody(request);
  const defectIds = Array.isArray(body?.defect_ids) ? body.defect_ids : [];
  const intent = typeof body?.intent === "string" ? body.intent.trim() : "";

  if (defectIds.length === 0) {
    sendJson(response, 400, { success: false, error: "defect_ids[] is required" });
    return;
  }
  if (defectIds.length > 20) {
    sendJson(response, 400, { success: false, error: "max 20 defects per batch" });
    return;
  }

  const chatConfig = resolveChatModelConfig(undefined, process.env);
  const BATCH_CONCURRENCY = 3;  // 限并发，避免 LLM rate limit

  const results = await mapWithConcurrency(defectIds, BATCH_CONCURRENCY, async (defectId) => {
    try {
      // Step 1: Python bridge — prepare (读 defect + RAG)
      const prepared = await runTestCaseBridge({ action: "prepare", defect_id: defectId });
      if (!prepared?.success || !prepared?.defect_info) {
        return makeBatchError(defectId, prepared?.error || "failed to prepare defect");
      }
      const defectInfo = prepared.defect_info;
      const similarCases = prepared.similar_cases || [];
      const fewShotText = prepared.few_shot_text || "";

      // Step 2: LLM 生成
      const llmResult = await generateTestCaseContent(defectInfo, fewShotText, chatConfig, intent);

      // Step 3: verify
      const verified = await runTestCaseBridge({
        action: "verify",
        defect_info: defectInfo,
        description_html: llmResult.descriptionHtml,
        steps_text: llmResult.stepsText,
      });

      return {
        defectId: defectInfo.defect_id,
        defectName: defectInfo.name,
        defectSeverity: defectInfo.severity,
        defectSoftwareVersion: defectInfo.software_version,
        defectAssignedEcu: defectInfo.assigned_ecu,
        defectLeadModel: defectInfo.lead_model,
        name: llmResult.testName,
        descriptionHtml: llmResult.descriptionHtml,
        stepsText: llmResult.stepsText,
        verification: verified?.verification || { passed: false, criteria: [], feedback: "verification skipped" },
        similarCases,
        generatedAt: new Date().toISOString(),
        error: null,
      };
    } catch (err) {
      return makeBatchError(defectId, String(err?.message || err));
    }
  });

  const succeeded = results.filter((r) => !r.error).length;
  sendJson(response, 200, {
    success: true,
    results,
    summary: { total: results.length, succeeded, failed: results.length - succeeded },
  });
  return;
}
```

`mapWithConcurrency` 是一个简单的限并发 helper（项目里可能已有，没有的话在内联实现，约 15 行）。

#### 端点: `POST /api/create-testcase/commit-batch`（批量提交）

```
请求: {
  "items": [
    {
      "test_case_data": { "name", "description_html", "steps_text", "defectId" },
      "feature_id": "可选",
    },
    ...
  ],
  "owner_workspace_user_id": "138017"
}
响应: {
  "success": true,
  "results": [
    { "defectId": "2743423", "success": true, "test_id": "...", "octane_url": "..." },
    { "defectId": "2812109", "success": false, "error": "403 Forbidden" },
  ],
  "summary": { "total": 5, "succeeded": 4, "failed": 1 }
}
```

实现：串行循环调 `runTestCaseBridge({ action: "commit", ... })`（逐条，因为 `write_test_steps` 按 test_id 逐条），单条失败不阻断。

### 3.3 前端 — 批量审核 UI

#### 新增组件: `TestCaseBatchPreview.tsx`

放在 `src/components/dashboard/chat/`。

**结构**：
- 顶部：批量操作栏（全选/取消、统一填 feature ID、批量提交按钮 + 进度条）
- 列表：每条是一个可折叠/展开的卡片，复用 `TestCasePreviewCard` 的内部结构（但提取为受控组件，状态由父组件管理）
- 底部：汇总结果（成功 X 条 + Octane 链接、失败 Y 条 + 错误原因）

**复用策略**：
现有 `TestCasePreviewCard` 是自管理状态的组件（`useState` 持有 editedName/editedDescription/editedSteps）。批量场景下需要父组件统一管理选中状态和提交，所以需要把 `TestCasePreviewCard` 重构为**受控组件**（props 传入 value + onChange），或新建 `TestCaseBatchItem` 子组件。

**推荐**：新建 `TestCaseBatchItem.tsx`（复用 `TestCasePreviewCard` 的 JSX 结构，但改为受控），避免破坏现有单条卡片。

#### 类型扩展: `testCaseTypes.ts`

```typescript
export interface TestCaseBatchItem extends TestCaseResult {
  selected: boolean;       // 是否选中提交
  error: string | null;    // 生成/提交错误
  commitState: "idle" | "submitting" | "success" | "error";
  octaneUrl?: string;       // 提交成功后的链接
}

export interface TestCaseBatchResult {
  results: TestCaseBatchItem[];
  summary: { total: number; succeeded: number; failed: number };
}
```

### 3.4 Skill — `.claude/skills/create-octane-testcase/SKILL.md`

在现有 procedure 之前新增一节"批量创建与语义查询"，说明：
1. 当用户给多个 defect 号时，调 `/api/create-testcase/prepare-batch`。
2. 当用户用语义描述（"基于 RSU 的 defect"）时，先调 `/api/create-testcase/search` 查 defect，展示给用户确认，再进入批量流程。
3. 提交时调 `/api/create-testcase/commit-batch`。

---

## 4. 数据流详解

### 4.1 语义查询的 defect 来源

复用 `duplicate_search_bridge.py` 的索引：
- `_get_cached_defect_df(REPO_ROOT)` → 返回 defect DataFrame（从 SQLite 或 JSON）
- `get_or_build_index_with_metadata(cache_key, df)` → 返回 `DuplicateIssueIndex`
- `index.search_with_metadata(query, hints, top_k, reranker)` → 返回候选

**关键**：`testcase_bridge.py` 和 `duplicate_search_bridge.py` 共用 `REPO_ROOT`、同一套索引缓存。`search_defects` action 直接 import `duplicate_search_bridge` 的函数即可，不重复建索引。

### 4.2 批量生成的并发控制

- Python bridge 的 `prepare` 是无状态单条调用，可以并行。
- LLM 生成（`generateTestCaseContent`）是 Node 层 HTTP 调用，限并发 3。
- 用 `mapWithConcurrency` helper 实现限并发 Promise 池。

### 4.3 批量提交的串行性

Octane 的 `write_test_steps` 是 `PUT /tests/{id}/script`，按 test_id 逐条，无法批量。所以 `commit-batch` 串行循环调 `commit`。单条失败不阻断后续。

---

## 5. 错误处理

| 场景 | 处理 |
|---|---|
| 语义查询无结果 | 返回空 candidates，前端提示"未找到匹配 defect，请换关键词" |
| 某个 defect prepare 失败（defect 不存在） | 该条 `error` 填错误信息，其他继续 |
| 某个 defect LLM 生成失败（rate limit / 超时） | 该条 `error` 填错误信息，其他继续 |
| 某个 defect verify 失败 | 该条 verification 标记未通过，但仍展示草稿，用户可手动编辑后重新校验 |
| 批量提交某条 403（无权限） | 该条标记失败，其他继续；若全部 403，提示用户加载 privileged cookie |
| 批量提交某条 400/409（字段错误） | 该条标记失败 + 错误原因，其他继续 |

---

## 6. 改动文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `scripts/testcase_bridge.py` | 新增 action | `_do_search_defects` + 在 `_handle_payload` 注册 |
| `server/index.mjs` | 新增端点 | `/search`、`/prepare-batch`、`/commit-batch` + `mapWithConcurrency` helper |
| `src/components/dashboard/chat/TestCaseBatchPreview.tsx` | 新建 | 批量审核 UI |
| `src/components/dashboard/chat/TestCaseBatchItem.tsx` | 新建 | 受控版单条卡片（复用 PreviewCard 结构） |
| `src/components/dashboard/chat/testCaseTypes.ts` | 扩展 | `TestCaseBatchItem`、`TestCaseBatchResult` 类型 |
| `.claude/skills/create-octane-testcase/SKILL.md` | 更新 | 新增批量 + 语义查询流程说明 |

**不改动**：
- `server/index.mjs` 现有 4 个端点（`/warmup`、`/create-testcase`、`/verify`、`/commit`）
- `scripts/testcase_bridge.py` 现有 4 个 action（`warmup`、`prepare`、`verify`、`commit`）
- `backend/analytics/ingest/client.py`（`create_test_case`、`write_test_steps` 保持单条）
- `TestCasePreviewCard.tsx`（保持现有单条卡片不变）

---

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| LLM rate limit（5 个 defect 同时生成） | 限并发 3，每个生成 4096 tokens |
| 批量提交耗时（5 条 × create + write_steps） | 串行约 10-15s，前端显示进度条 |
| 语义查询结果不准（"RSU"太宽泛） | top_k 默认 10，用户勾选子集，可调整关键词重查 |
| 索引未 warmup（首次查询慢 3 min） | 前端先调 `/warmup`，或复用 duplicate_search 的 prepared index manifest |
| Octane cookie 失效（提交时 401） | 复用现有 cookie 自愈链（`create_entity` 已有 401 重试） |

---

## 8. 验收标准

1. 用户输入 5 个 defect 号 → `prepare-batch` 返回 5 份草稿，每份有 description + steps + verification。
2. 用户输入"RSU 历史缺陷" → `search` 返回 ≤10 个候选 defect，用户勾选 3 个 → 生成 3 份草稿。
3. 批量审核 UI 可逐条编辑、勾选、重新校验。
4. 确认提交后，选中的逐条写入 Octane，返回每条的 test_id + octane_url。
5. 单条失败不阻断其他，汇总显示成功/失败数。
