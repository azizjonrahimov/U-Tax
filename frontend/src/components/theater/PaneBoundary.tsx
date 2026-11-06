"use client";

import { Component, type ReactNode } from "react";

/**
 * Keeps one bad render from blanking the projector: the panes show a short
 * note instead, and recover on the next change of `resetKey` (the next event).
 */
export class PaneBoundary extends Component<{ resetKey: number; children: ReactNode }, { failedAt: number | null }> {
  state = { failedAt: null as number | null };

  static getDerivedStateFromError() {
    return { failedAt: -1 };
  }

  static getDerivedStateFromProps(props: { resetKey: number }, state: { failedAt: number | null }) {
    if (state.failedAt === -1) return { failedAt: props.resetKey };
    if (state.failedAt !== null && props.resetKey !== state.failedAt) return { failedAt: null };
    return null;
  }

  componentDidCatch(error: unknown) {
    console.error("[theater] a pane failed to render; showing the next event instead", error);
  }

  render() {
    if (this.state.failedAt !== null) {
      return (
        <main>
          <div className="overlay" role="status">
            <div>
              One event couldn’t be drawn
              <small>The log keeps playing</small>
            </div>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
