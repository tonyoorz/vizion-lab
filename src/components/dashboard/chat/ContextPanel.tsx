import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronRight,
  Database,
  FileText,
  Image,
  X,
  Paperclip,
  Link2,
  Sparkles,
} from "lucide-react";

interface Attachment {
  id: string;
  name: string;
  kind: "image" | "file";
  dataUrl?: string;
  size: number;
}

interface DataSource {
  id: string;
  name: string;
  type: "database" | "api" | "file";
}

interface Props {
  isOpen: boolean;
  onToggle: () => void;
  attachments: Attachment[];
  dataSources?: DataSource[];
  moduleLabel?: string;
  onRemoveAttachment?: (id: string) => void;
}

const DEFAULT_SOURCES: DataSource[] = [
  { id: "defects", name: "缺陷数据库", type: "database" },
  { id: "projects", name: "项目数据库", type: "database" },
  { id: "metrics", name: "质量指标 API", type: "api" },
];

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

export default function ContextPanel({
  isOpen,
  onToggle,
  attachments,
  dataSources = DEFAULT_SOURCES,
  moduleLabel,
  onRemoveAttachment,
}: Props) {
  return (
    <>
      {/* Toggle button when closed */}
      {!isOpen && (
        <motion.button
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 20 }}
          onClick={onToggle}
          className="fixed right-0 top-1/2 z-30 flex -translate-y-1/2 items-center gap-1 rounded-l-lg border border-r-0 border-border bg-card px-2 py-3 text-muted-foreground shadow-lg transition-colors hover:bg-secondary hover:text-foreground"
        >
          <ChevronRight className="h-4 w-4 rotate-180" />
          <span className="text-[10px] font-medium [writing-mode:vertical-lr]">
            上下文
          </span>
          {(attachments.length > 0 || moduleLabel) && (
            <span className="flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[9px] font-bold text-primary-foreground">
              {attachments.length + (moduleLabel ? 1 : 0)}
            </span>
          )}
        </motion.button>
      )}

      {/* Panel */}
      <AnimatePresence>
        {isOpen && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 280, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed right-0 top-0 z-40 flex h-full flex-col border-l border-border bg-card shadow-xl lg:relative lg:h-auto lg:shadow-none"
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-primary" />
                <span className="text-sm font-semibold text-foreground">
                  上下文
                </span>
              </div>
              <button
                onClick={onToggle}
                className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto">
              {/* Active module */}
              {moduleLabel && (
                <div className="border-b border-border p-4">
                  <h4 className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    <Link2 className="h-3 w-3" />
                    当前模块
                  </h4>
                  <div className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2">
                    <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-primary">
                      <Sparkles className="h-3.5 w-3.5" />
                    </div>
                    <span className="text-sm font-medium text-foreground">
                      {moduleLabel}
                    </span>
                  </div>
                </div>
              )}

              {/* Data sources */}
              <div className="border-b border-border p-4">
                <h4 className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  <Database className="h-3 w-3" />
                  可用数据源
                </h4>
                <div className="space-y-1.5">
                  {dataSources.map((source) => (
                    <div
                      key={source.id}
                      className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2"
                    >
                      <Database className="h-3.5 w-3.5 text-emerald-500" />
                      <span className="text-xs text-foreground">{source.name}</span>
                      <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[9px] text-muted-foreground">
                        {source.type === "database" ? "DB" : source.type === "api" ? "API" : "FILE"}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Attachments */}
              <div className="p-4">
                <h4 className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  <Paperclip className="h-3 w-3" />
                  附件
                  {attachments.length > 0 && (
                    <span className="rounded-full bg-muted px-1.5 py-0.5 text-[9px]">
                      {attachments.length}
                    </span>
                  )}
                </h4>
                {attachments.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-border bg-muted/20 py-6 text-center text-xs text-muted-foreground">
                    暂无附件
                    <br />
                    <span className="text-[10px]">粘贴或拖放文件到输入框</span>
                  </p>
                ) : (
                  <div className="space-y-2">
                    {attachments.map((att) => (
                      <div
                        key={att.id}
                        className="group flex items-center gap-2 rounded-lg border border-border bg-card p-2"
                      >
                        {att.kind === "image" && att.dataUrl ? (
                          <img
                            src={att.dataUrl}
                            alt={att.name}
                            className="h-10 w-10 rounded-md object-cover"
                          />
                        ) : (
                          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-muted">
                            {att.kind === "image" ? (
                              <Image className="h-4 w-4 text-muted-foreground" />
                            ) : (
                              <FileText className="h-4 w-4 text-muted-foreground" />
                            )}
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-foreground">
                            {att.name}
                          </p>
                          <p className="text-[10px] text-muted-foreground">
                            {formatSize(att.size)}
                          </p>
                        </div>
                        {onRemoveAttachment && (
                          <button
                            onClick={() => onRemoveAttachment(att.id)}
                            className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-all hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Footer */}
            <div className="border-t border-border p-4">
              <p className="text-center text-[10px] text-muted-foreground">
                AI 将结合上下文信息进行分析
              </p>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* Mobile backdrop */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onToggle}
            className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          />
        )}
      </AnimatePresence>
    </>
  );
}
