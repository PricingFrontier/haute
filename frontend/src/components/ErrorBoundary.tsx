import { Component, type ErrorInfo, type ReactNode } from "react";
import { chunkLoadVerdict, isChunkLoadError, type ChunkLoadVerdict } from "../utils/chunkLoadError";
import { ERROR_FALLBACK_BUTTON_STYLE, ReloadNotice } from "./ReloadNotice";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
  /** For a part of the page that could not be loaded, why, once the server is asked. */
  chunk: ChunkLoadVerdict | "asking" | null;
}

/** What the fallback says for a part of the page that could not be loaded, by why. */
const CHUNK_NOTICES: Record<ChunkLoadVerdict, { title: string; reason: string }> = {
  gone: {
    title: "Haute has been updated",
    reason: "This page was opened before the update and cannot load this part of it. Reload to use the new version.",
  },
  unanswered: {
    title: "Haute's server did not answer",
    reason: "This part of the page could not be loaded while the server was away. Reload to load it once the server answers.",
  },
};

export class ErrorBoundary extends Component<Props, State> {
  private unmounted = false;

  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, chunk: null };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error, chunk: isChunkLoadError(error) ? "asking" : null };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error(
      `[ErrorBoundary${this.props.name ? `: ${this.props.name}` : ""}]`,
      error,
      errorInfo.componentStack
    );
    if (!isChunkLoadError(error)) return;
    void chunkLoadVerdict(error).then((chunk) => {
      if (!this.unmounted && this.state.error === error) this.setState({ chunk });
    });
  }

  componentWillUnmount(): void {
    this.unmounted = true;
  }

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      // A part of the page that could not be loaded stays failed in this page (the browser
      // keeps the failure of the chunk and of what it preloaded), so only a reload recovers.
      const { chunk } = this.state;
      const notice = chunk === null || chunk === "asking" ? null : CHUNK_NOTICES[chunk];
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
            {notice ? notice.title : chunk === "asking" ? "This part of the page could not be loaded" : "Something went wrong"}
          </p>
          {notice ? (
            <ReloadNotice reason={notice.reason} />
          ) : chunk === "asking" ? null : (
            <>
              <p style={{ margin: "0 0 12px", fontFamily: "var(--font-code)", fontSize: "11px" }}>
                {this.state.error?.message}
              </p>
              <button onClick={() => this.setState({ hasError: false, error: null, chunk: null })} style={ERROR_FALLBACK_BUTTON_STYLE}>
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
