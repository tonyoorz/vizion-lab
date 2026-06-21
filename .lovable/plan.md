## 目标

为 DTSV 项目构建工程化的智能问数 Agent，核心是 **Ontology 语义层**（消除 LLM 幻觉）+ **显式多 Agent 编排面板**（过程可见可纠错），覆盖 Defect / TestCase / TestRun 三大业务实体。

---

## 一、Ontology 语义层（Phase 1）

### 1.1 文件结构

```text
src/lib/ontology/
├── schema.ts              Zod 定义：Entity / Attribute / Relation / Metric
├── loader.ts              加载 YAML → 内存图，提供查询 API
├── resolver.ts            自然语言 → 本体概念（同义词、模糊匹配）
├── graph.ts               关系图遍历（BFS 找 JOIN 路径）
└── definitions/
    ├── defect.yaml        缺陷实体 + 严重度/状态/模块
    ├── testcase.yaml      用例实体 + 类型/优先级/覆盖
    ├── testrun.yaml       执行实体 + 通过率/耗时/环境
    └── metrics.yaml       业务指标：高频缺陷率、回归覆盖率、长尾率等
```

### 1.2 Ontology DSL 示例

```yaml
entity: Defect
table: defects
synonyms: [缺陷, bug, 问题, 故障]
attributes:
  severity:
    type: enum
    values: [P0, P1, P2, P3]
    synonyms: [严重度, 优先级, 等级]
  status:
    type: enum
    values: [open, fixed, closed, reopened]
relations:
  - name: belongs_to_module
    target: Module
    via: module_id
  - name: found_in_run
    target: TestRun
    via: run_id

metrics:
  - name: 高频缺陷率
    synonyms: [复发率, 重复缺陷比例]
    formula: |
      COUNT(DISTINCT CASE WHEN occur_count >= 3 THEN id END) * 1.0
      / NULLIF(COUNT(DISTINCT id), 0)
    dimensions: [module, time_range, severity]
```

### 1.3 关键能力

- **概念解析**：用户问「上周 P0 的高频缺陷」→ 命中 `severity=P0` + metric「高频缺陷率」+ 时间维度
- **JOIN 路径自动推导**：BFS 在关系图上找 Defect→Module→TestCase 的最短路径
- **字段白名单**：SQL 生成器只能引用 Ontology 中声明的字段，杜绝幻觉字段
- **指标复用**：业务公式集中维护，AI 直接调用，不重写聚合逻辑

---

## 二、多 Agent 编排（Phase 2）

### 2.1 Agent 角色（Orchestrator-Worker 模式）

| Agent | 职责 | 模型 |
|---|---|---|
| **Planner** | 拆解用户问题为子任务，决定调用哪些 Worker | `google/gemini-3-flash-preview` |
| **Ontologist** | 把自然语言映射到 Ontology 概念/指标/维度 | `google/gemini-3-flash-preview` |
| **SQL Writer** | 基于 Ontology 上下文生成受约束的 DuckDB SQL | `google/gemini-3.5-flash` |
| **Executor** | 在 DuckDB-WASM 跑 SQL，返回结果 + Schema | （无模型，纯工具）|
| **Critic** | 校验结果合理性，必要时回炉重写 | `google/gemini-3-flash-preview` |
| **Presenter** | 生成图表配置 + 自然语言摘要 | `google/gemini-3-flash-preview` |

通过 AI SDK 的 `tool` + `stepCountIs(50)` 实现，Planner 作为 Orchestrator 调用其他 Agent 作为 tools。

### 2.2 后端

新增 `supabase/functions/data-agent/index.ts`：
- 接收 `{ question, conversationId, ontologyVersion }`
- Planner Agent 流式输出步骤 → 调用 Worker tools → 通过 `toUIMessageStreamResponse` 把每个 Agent 的状态/输出作为 `tool` parts 推到前端

### 2.3 前端：显式多 Agent 面板

新增 `src/components/dashboard/chat/AgentOrchestrator.tsx`，UI 形态：

```text
┌─────────────────────────────────────────────────────────┐
│ 💬 问：上周 P0 高频缺陷集中在哪个模块？                  │
├─────────────────────────────────────────────────────────┤
│ ┌─ Agent 工作流 ────────────────────────────────────┐  │
│ │ ✅ Planner       识别为「指标查询 + 维度下钻」      │  │
│ │ ✅ Ontologist    匹配: 高频缺陷率 / severity=P0    │  │
│ │ ✅ SQL Writer    生成 SQL [查看] [编辑]            │  │
│ │ ⏳ Executor      执行中... (DuckDB)                │  │
│ │ ⚪ Critic        待启动                             │  │
│ │ ⚪ Presenter     待启动                             │  │
│ └────────────────────────────────────────────────────┘  │
│ ┌─ 答案 ────────────────────────────────────────────┐  │
│ │ 📊 [图表]  上周 P0 高频缺陷共 18 个，集中在...    │  │
│ │ 🔍 数据来源：3 张表，置信度 92%                   │  │
│ └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

- 每个 Agent 节点：状态图标 + 名称 + 一句话输出 + 「展开/编辑」按钮
- 用户可在任意节点暂停、修改 Ontology 映射或 SQL，再继续
- 折叠态显示进度条，展开态显示完整链路（避免插件感，与现有 Stripe/Linear 极简风一致）

### 2.4 UI 组件清单

- `AgentOrchestrator.tsx` — 主面板
- `AgentStepCard.tsx` — 单个 Agent 步骤卡片
- `OntologyMatchPanel.tsx` — Ontologist 输出（命中的实体/指标/同义词）
- `SQLPreview.tsx` — 带语法高亮 + Ontology 字段着色
- 集成到现有 `AIChat.tsx`：增加「智能问数」模式切换按钮

---

## 三、技术细节（给工程参考）

- **Ontology 加载**：构建期用 Vite `import.meta.glob` 把 YAML 全部预编译为 JSON 注入 bundle，前端零运行时解析开销
- **同义词匹配**：先精确 → 再编辑距离（Levenshtein）→ 最后 LLM 兜底
- **SQL 安全**：复用 `src/lib/duckdb/safety.ts`，新增 Ontology 字段白名单校验
- **流式协议**：AI SDK UIMessage `parts`，每个 Agent 用 `tool-call` + `tool-result` 表达，前端按 `toolName` 渲染对应卡片
- **可观测**：每次问答记录 `{question, ontology_matches, sql, latency, agent_steps}` 到 localStorage（Phase 4 再上 Supabase）

---

## 四、本次交付范围（Phase 1+2）

1. ✅ Ontology 骨架：schema / loader / resolver / graph + 3 个种子 YAML（defect/testcase/testrun）+ metrics.yaml
2. ✅ 后端 Edge Function `data-agent`：6 个 Agent 角色，AI SDK tool 编排
3. ✅ 前端显式多 Agent 面板：`AgentOrchestrator` + 4 个子组件
4. ✅ 集成进 `AIChat.tsx`：保留现有 Mission Launcher + Slash 命令，新增「问数」入口
5. ⏭️ Phase 3（下个 PR）：pgvector + Hybrid RAG
6. ⏭️ Phase 4（下个 PR）：本体可视化编辑器

---

## 五、风险与取舍

| 风险 | 缓解 |
|---|---|
| YAML 维护成本 | Phase 4 提供可视化编辑器；当前先用代码 + Git review |
| 多 Agent 延迟叠加 | Planner 决定是否跳过 Critic（简单查询直出） |
| DuckDB 数据未到位 | Executor 在数据缺失时降级为 Mock，并提示用户上传 |
| UI 信息密度过高 | 默认折叠 Agent 面板，仅显示进度条；点击展开 |
