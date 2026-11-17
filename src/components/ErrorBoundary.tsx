import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCcw } from "lucide-react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * React Error Boundary — catches render errors in children and shows
 * a graceful fallback instead of a blank screen.
 */
export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex min-h-[200px] flex-col items-center justify-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-8 text-center">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10">
            <AlertTriangle className="h-5 w-5 text-destructive" />
          </div>
          <div>
            <p className="text-sm font-semibold text-foreground">渲染出错</p>
            <p className="mt-1 max-w-md text-xs text-muted-foreground">
              {this.state.error?.message || "组件渲染时发生了意外错误。"}
            </p>
          </div>
          <button
            onClick={this.handleRetry}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <RefreshCcw className="h-3 w-3" /> 重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
