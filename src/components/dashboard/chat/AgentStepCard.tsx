import { Check, ChevronDown, Loader2, AlertCircle, Circle } from "lucide-react";
import { useState } from "react";

export type StepStatus = "pending" | "running" | "done" | "error" | "skipped";

export interface AgentStep {
  key: string;
  name: string;
  hint: string;
  status: StepStatus;
  summary?: string;
  detail?: React.ReactNode;
  error?: string;
  durationMs?: number;
}

const statusIcon = (s: StepStatus) => {
  switch (s) {
    case "running":
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />;
    case "done":
      return <Check className="h-3.5 w-3.5 text-emerald-500" />;
    case "error":
      return <AlertCircle className="h-3.5 w-3.5 text-rose-500" />;
    case "skipped":
      return <Circle className="h-3.5 w-3.5 text-muted-foreground/40" />;
    default:
      return <Circle className="h-3.5 w-3.5 text-muted-foreground/40" />;
  }
};

const AgentStepCard = ({ step }: { step: AgentStep }) => {
  const [open, setOpen] = useState(false);
  const expandable = !!step.detail || !!step.error;
  return (
    <div className="rounded-lg border border-border/60 bg-card/40 transition-colors hover:bg-card/70">
      <button
        type="button"
        onClick={() => expandable && setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left"
      >
        <span className="flex h-5 w-5 items-center justify-center">{statusIcon(step.status)}</span>
        <div className="flex min-w-0 flex-1 items-baseline gap-2">
          <span className="text-[13px] font-medium text-foreground">{step.name}</span>
          <span className="text-[11px] text-muted-foreground">{step.hint}</span>
        </div>
        {step.summary && (
          <span className="max-w-[55%] truncate text-[12px] text-muted-foreground">
            {step.summary}
          </span>
        )}
        {typeof step.durationMs === "number" && step.status === "done" && (
          <span className="text-[10px] tabular-nums text-muted-foreground/70">
            {step.durationMs}ms
          </span>
        )}
        {expandable && (
          <ChevronDown
            className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${
              open ? "rotate-180" : ""
            }`}
          />
        )}
      </button>
      {open && expandable && (
        <div className="border-t border-border/60 px-3 py-2 text-[12px]">
          {step.error ? (
            <div className="text-rose-500">{step.error}</div>
          ) : (
            step.detail
          )}
        </div>
      )}
    </div>
  );
};

export default AgentStepCard;
