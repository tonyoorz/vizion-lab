import { Skeleton } from "@/components/ui/skeleton";

/**
 * Reusable skeleton layouts for common loading states.
 */
export function KPISkeleton() {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="rounded-xl border border-border bg-card p-4">
          <div className="flex items-center justify-between">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-8 w-8 rounded-lg" />
          </div>
          <Skeleton className="mt-3 h-7 w-24" />
          <Skeleton className="mt-2 h-3 w-16" />
        </div>
      ))}
    </div>
  );
}

export function ChartSkeleton() {
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center justify-between">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-7 w-7 rounded-md" />
      </div>
      <Skeleton className="mt-4 h-48 w-full rounded-lg" />
    </div>
  );
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center justify-between">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-7 w-24 rounded-md" />
      </div>
      <div className="mt-4 space-y-2">
        <div className="flex gap-4 border-b border-border pb-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-3 flex-1" />
          ))}
        </div>
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex gap-4 py-2">
            {Array.from({ length: 5 }).map((_, j) => (
              <Skeleton key={j} className="h-3 flex-1" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="space-y-5">
      <KPISkeleton />
      <div className="grid gap-5 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <ChartSkeleton />
        </div>
        <div className="lg:col-span-2">
          <ChartSkeleton />
        </div>
      </div>
      <TableSkeleton />
    </div>
  );
}
