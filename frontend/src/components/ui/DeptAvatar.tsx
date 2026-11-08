// D40 §2: a department's avatar (squircle in its seat color, line glyph), inline SVG from static art.
import { AVATAR_ART } from "./art";

export function DeptAvatar({ seat, size = 28, className, title }: { seat: string; size?: number; className?: string; title?: string }) {
  const art = AVATAR_ART[seat] ?? AVATAR_ART.ceo;
  return (
    <svg
      className={`ui-av${className ? ` ${className}` : ""}`}
      viewBox={art.vb}
      width={size}
      height={size}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      aria-label={title}
      focusable="false"
      // Static art from ./art (never log data).
      dangerouslySetInnerHTML={{ __html: art.svg }}
    />
  );
}
