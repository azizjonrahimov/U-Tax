// D40 §2: an empty pane says what will happen there, with a line drawing (never a bare "No … yet").
import { EMPTY_ART } from "./art";

export type EmptyArt = keyof typeof EMPTY_ART;

export function EmptyState({ art, title, line, className }: { art: EmptyArt; title: string; line: string; className?: string }) {
  const a = EMPTY_ART[art];
  return (
    <div className={`ui-empty${className ? ` ${className}` : ""}`}>
      <svg viewBox={a.vb} aria-hidden="true" focusable="false" dangerouslySetInnerHTML={{ __html: a.svg }} />
      <b>{title}</b>
      <span>{line}</span>
    </div>
  );
}
