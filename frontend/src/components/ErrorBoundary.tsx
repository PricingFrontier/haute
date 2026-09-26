import { Component, type ErrorInfo, type ReactNode } from "react";
import { isChunkLoadError } from "../utils/chunkLoadError";
import { ERROR_FALLBACK_BUTTON_STYLE, UpdatedNotice } from "./UpdatedNotice";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error(
      `[ErrorBoundary${this.props.name ? `: ${this.props.name}` : ""}]`,
      error,
      errorInfo.componentStack
    );
  }

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      // A retry cannot load a chunk the rebuilt server no longer has (and the
      // lazy import's rejection is cached), so only a reload recovers.
      const updated = isChunkLoadError(this.state.error);
      return (
        <div
          style={{
            padding: "16px",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border)",
            borderRadius: "8px",
            color: "var(--text-secondary)",
            fontSize: "12px",
          }}
        >
          <p style={{ margin: "0 0 8px", fontWeight: 600, color: "var(--text-primary)" }}>
            {updated ? "Haute has been updated" : "Something went wrong"}
          </p>
          {updated ? (
            <UpdatedNotice />
          ) : (
            <>
              <p style={{ margin: "0 0 12px", fontFamily: "var(--font-code)", fontSize: "11px" }}>
                {this.state.error?.message}
              </p>
              <button onClick={() => this.setState({ hasError: false, error: null })} style={ERROR_FALLBACK_BUTTON_STYLE}>
                Try again
              </button>
            </>
          )}
        </div>
      );
    }
    return this.props.children;
  }
}
