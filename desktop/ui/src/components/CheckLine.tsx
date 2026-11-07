// One health check as a line: ✓ or ✗, its title and what was found, and the fix when it fails.
import type { Check } from "../core";

/** Text with `backticks` shown as code. */
export function WithCode({ text }: { text: string }) {
  return <>{text.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part))}</>;
}

export function CheckLine({ c }: { c: Check }) {
  return (
    <li className={`check${c.ok ? " check-ok" : ""}`}>
      <span className={`tick ${c.ok ? "tick-ok" : "tick-bad"}`} aria-label={c.ok ? "passed" : "failed"}>{c.ok ? "✓" : "✗"}</span>
      <div>
        <p className="check-title">{c.title} <span className="check-detail">· {c.detail}</span></p>
        {c.fix && <p className="check-fix"><WithCode text={c.fix} /></p>}
      </div>
    </li>
  );
}
