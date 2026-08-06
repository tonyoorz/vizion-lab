# 记忆系统（本地优先，四层记忆）

给 Agent 加一套完整记忆：会话可续、偏好可学、任务可追溯、经验可复用。全部存在浏览器本地（IndexedDB），不需要登录，数据不出端；已有的 pgvector 经验记忆继续保留作为云端语义层。

## 四层记忆

| 层 | 存什么 | 何时写 | 何时用 |
|---|---|---|---|
| 会话记忆 | 会话列表、消息、附件摘要、工具调用结果摘要 | 每条消息落库 | 刷新/切回自动恢复 |
| 用户长期记忆 | 常用模块、口径偏好、语言风格、默认时间窗、图表偏好 | 显式「记住这条」+ 自动抽取 | 每次请求注入 system prompt |
| 任务记忆 | 每次 Mission（智能问数 / Test Agent）的目标、步骤、SQL、结论、评分 | Mission 结束时 | Mission 复用、Diff、「上次怎么做的」 |
| 经验记忆 | 成功/失败问答对（qa_history / qa_negative） | 反馈 👍/👎 时 | Retriever 混合检索召回 |

## 存储设计（IndexedDB，库名 `dtsv-memory`）

```text
conversations  { id, title, createdAt, updatedAt, mode, pinned }
messages       { id, conversationId, role, content, toolCalls, artifacts, ts }
user_memory    { id, kind: preference|fact|glossary, text, source: manual|auto,
                 confidence, createdAt, lastUsedAt, hits }
missions       { id, type, goal, filters, steps[], sql[], result, score, ts }
```
索引：`messages.conversationId`、`missions.ts`、`user_memory.kind`。
容量策略：消息超过 N 条自动做「滚动摘要」压缩为一条 summary 记忆，避免 IndexedDB 与 prompt 双膨胀。

## 记忆检索与注入

每次请求组装 prompt 的顺序：

```text
system 提示
 + 用户长期记忆（Top-K，按 kind 分组，最多 ~800 tokens）
 + 当前会话滚动摘要 + 最近 N 轮原文
 + Retriever 召回的 knowledge_chunks（已有）
 + 本次问题
```
本地检索用轻量方案：关键词 + 最近使用度打分（不引入本地向量模型），命中即更新 `hits/lastUsedAt`，长期不用的自动降权。

## 自动抽取记忆

在一轮对话结束后，用一次低成本模型调用抽取候选记忆（偏好/口径/术语），返回结构化列表；候选不直接生效，而是进入「待确认」，用户在记忆面板一键采纳或忽略。避免记忆污染。

## 前端改动

1. `src/lib/memory/db.ts`：IndexedDB 封装（idb），四张表的 CRUD。
2. `src/lib/memory/store.ts`：会话状态管理、滚动摘要、消息落库与恢复。
3. `src/lib/memory/user-memory.ts`：长期记忆读写、打分检索、prompt 序列化。
4. `src/lib/memory/extract.ts`：调用现有 `chat` Edge Function 做记忆抽取。
5. `src/components/dashboard/chat/ConversationSidebar.tsx`：会话列表（新建/重命名/置顶/删除/搜索）。
6. `src/components/dashboard/chat/MemoryPanel.tsx`：长期记忆管理（查看/编辑/删除/采纳候选/一键清空）+ 任务记忆历史列表。
7. `AIChat.tsx` / `AgentOrchestrator.tsx` / `TestAgent.tsx`：接入落库、恢复与 prompt 注入；Mission 结束写 `missions`。

## 分期落地

- 第 1 步：IndexedDB 基础 + 会话记忆（侧栏、恢复、滚动摘要）
- 第 2 步：用户长期记忆 + prompt 注入 + 记忆面板 + 「记住这条」
- 第 3 步：任务记忆（Mission 历史与复用）
- 第 4 步：自动抽取候选记忆 + 采纳流

## 说明与取舍

- 本地存储意味着换设备/清缓存会丢；记忆面板提供 JSON 导出/导入作为兜底，后续加登录时可直接迁到云端同表结构。
- 已有的 pgvector 经验记忆保持不变，与本地记忆互补：本地管「你是谁、聊过什么」，云端管「怎么答对过」。
