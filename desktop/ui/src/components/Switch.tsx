// An on/off switch with its label: `checked`, `onChange(next)`.
export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch-track" aria-hidden><span className="switch-knob" /></span>
      <span>{label}</span>
    </label>
  );
}
