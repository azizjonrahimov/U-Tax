// D40 §2: an on/off switch (role="switch"). It only draws the state; the caller's handler does the work.
// F45: `className` adds the page's own class (the /ceo prototype's `sw`); the switch is the same.
export function Switch({ on, onChange, label, disabled, className }: { on: boolean; onChange: () => void; label: string; disabled?: boolean; className?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      className={`ui-switch${on ? " on" : ""}${className ? ` ${className}` : ""}`}
      onClick={onChange}
    >
      <i aria-hidden="true" />
    </button>
  );
}
