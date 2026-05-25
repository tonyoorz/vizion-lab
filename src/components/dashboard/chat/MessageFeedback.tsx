import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ThumbsUp,
  ThumbsDown,
  X,
  AlertCircle,
  Zap,
  FileQuestion,
  Ban,
  Sparkles,
} from "lucide-react";

interface FeedbackOption {
  id: string;
  label: string;
  icon: typeof AlertCircle;
}

const NEGATIVE_OPTIONS: FeedbackOption[] = [
  { id: "inaccurate", label: "信息不准确", icon: AlertCircle },
  { id: "incomplete", label: "回答不完整", icon: FileQuestion },
  { id: "slow", label: "响应太慢", icon: Zap },
  { id: "irrelevant", label: "与问题无关", icon: Ban },
];

interface Props {
  messageId: string;
  onFeedback?: (messageId: string, feedback: {
    type: "positive" | "negative";
    reasons?: string[];
    comment?: string;
  }) => void;
}

export default function MessageFeedback({ messageId, onFeedback }: Props) {
  const [feedback, setFeedback] = useState<"positive" | "negative" | null>(null);
  const [showPanel, setShowPanel] = useState(false);
  const [selectedReasons, setSelectedReasons] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handlePositive = () => {
    setFeedback("positive");
    setSubmitted(true);
    onFeedback?.(messageId, { type: "positive" });
    setTimeout(() => setSubmitted(false), 2000);
  };

  const handleNegative = () => {
    setFeedback("negative");
    setShowPanel(true);
  };

  const submitNegative = () => {
    onFeedback?.(messageId, {
      type: "negative",
      reasons: selectedReasons,
      comment: comment.trim() || undefined,
    });
    setShowPanel(false);
    setSubmitted(true);
    setTimeout(() => setSubmitted(false), 2000);
  };

  const toggleReason = (id: string) => {
    setSelectedReasons((prev) =>
      prev.includes(id) ? prev.filter((r) => r !== id) : [...prev, id]
    );
  };

  if (submitted) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        className="flex items-center gap-1.5 text-xs text-primary"
      >
        <Sparkles className="h-3 w-3" />
        <span>感谢反馈</span>
      </motion.div>
    );
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-0.5">
        <button
          onClick={handlePositive}
          className={`flex h-6 w-6 items-center justify-center rounded-md transition-all ${
            feedback === "positive"
              ? "bg-emerald-500/20 text-emerald-500"
              : "text-muted-foreground hover:bg-muted hover:text-foreground"
          }`}
          title="有帮助"
        >
          <ThumbsUp className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={handleNegative}
          className={`flex h-6 w-6 items-center justify-center rounded-md transition-all ${
            feedback === "negative"
              ? "bg-rose-500/20 text-rose-500"
              : "text-muted-foreground hover:bg-muted hover:text-foreground"
          }`}
          title="需要改进"
        >
          <ThumbsDown className="h-3.5 w-3.5" />
        </button>
      </div>

      <AnimatePresence>
        {showPanel && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setShowPanel(false)}
              className="fixed inset-0 z-40"
            />
            {/* Panel */}
            <motion.div
              initial={{ opacity: 0, y: 8, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.95 }}
              className="absolute bottom-full left-0 z-50 mb-2 w-72 overflow-hidden rounded-xl border border-border bg-popover p-4 shadow-xl"
            >
              <div className="mb-3 flex items-center justify-between">
                <h4 className="text-sm font-semibold text-foreground">
                  告诉我们哪里可以改进
                </h4>
                <button
                  onClick={() => setShowPanel(false)}
                  className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="mb-3 grid grid-cols-2 gap-2">
                {NEGATIVE_OPTIONS.map((opt) => {
                  const selected = selectedReasons.includes(opt.id);
                  return (
                    <button
                      key={opt.id}
                      onClick={() => toggleReason(opt.id)}
                      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-all ${
                        selected
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border bg-card text-muted-foreground hover:border-primary/40 hover:text-foreground"
                      }`}
                    >
                      <opt.icon className="h-3.5 w-3.5 shrink-0" />
                      <span>{opt.label}</span>
                    </button>
                  );
                })}
              </div>

              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="补充说明（可选）"
                rows={2}
                className="mb-3 w-full resize-none rounded-lg border border-border bg-card px-3 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-primary/50"
              />

              <button
                onClick={submitNegative}
                disabled={selectedReasons.length === 0}
                className="w-full rounded-lg bg-primary px-4 py-2 text-xs font-medium text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
              >
                提交反馈
              </button>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
