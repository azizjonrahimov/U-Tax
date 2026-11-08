// D40 §2: an on/off switch (role="switch"). It only draws the state; the caller's handler does the work.
export function Switch({ on, onChange, label, disabled }: { on: boolean; onChange: () => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} className={`ui-switch${on ? " on" : ""}`} onClick={onChange}>
      <i aria-hidden="true" />
    </button>
  );
}
