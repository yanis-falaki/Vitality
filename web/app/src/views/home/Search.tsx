// Search across the model, at the top of Home: typing filters the strivings, activities and actions by
// title or summary (every word must appear), a simple match over the model already loaded. Enter or a
// click jumps: a striving or activity to its Reader page, an action to its day, selected. The "/" key
// focuses the box from anywhere on Home; Escape clears it.
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Dot } from "../../components/bits";
import { clock, day, dayLabel } from "../../lib/format";
import type { Kind, Model } from "../../lib/model";

const SHOWN = 8;

interface Hit { id: string; kind: Kind; title: string; detail: string; href: string }

export function Search({ model }: { model: Model }) {
  const navigate = useNavigate();
  const box = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const hits = useMemo(() => search(model, query), [model, query]);
  useEffect(() => setActive(0), [query]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.altKey || e.ctrlKey || e.metaKey) return;
      if ((e.target as HTMLElement).closest("input, textarea, select, [contenteditable]")) return;
      e.preventDefault();
      box.current?.focus();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  const go = (hit: Hit | undefined) => { if (hit) navigate(hit.href); };
  return (
    <div className="home-search" role="search">
      <input ref={box} type="search" value={query} placeholder="Search strivings, activities, actions"
        aria-label="Search the model (Enter jumps to the first match; / focuses)" aria-controls="home-hits"
        aria-activedescendant={hits[active] ? `hit-${active}` : undefined} autoComplete="off" spellCheck={false}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter") go(hits[active]);
          else if (e.key === "Escape") { setQuery(""); box.current?.blur(); }
        }} />
      <kbd className="home-search-key" aria-hidden>/</kbd>
      {query.trim() && (
        <ul className="home-hits" id="home-hits" role="listbox" data-testid="home-hits">
          {hits.map((h, i) => (
            <li key={h.id} id={`hit-${i}`} role="option" aria-selected={i === active}>
              <button type="button" className={`home-hit${i === active ? " on" : ""}`} onClick={() => go(h)}
                onMouseEnter={() => setActive(i)}>
                <Dot kind={h.kind} />
                <span className="home-hit-text">
                  <span className="home-hit-title">{h.title}</span>
                  <span className="home-hit-detail">{h.detail}</span>
                </span>
              </button>
            </li>
          ))}
          {hits.length === 0 && <li className="home-hit-none">Nothing in the model matches.</li>}
        </ul>
      )}
    </div>
  );
}

/** The first SHOWN matches: strivings, then activities, then actions, newest first. */
function search(model: Model, query: string): Hit[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const matches = (text: string) => { const t = text.toLowerCase(); return words.every((w) => t.includes(w)); };
  const hits: Hit[] = [];
  for (const s of model.strivings) {
    if (matches(`${s.title} ${s.id}`)) hits.push({ id: s.id, kind: "striving", title: s.title, detail: "Striving", href: page(s.id) });
  }
  for (const a of model.activities) {
    if (!matches(`${a.title} ${a.id}`)) continue;
    const serves = a.part_of.map((s) => model.title(s)).join(", ");
    hits.push({ id: a.id, kind: "activity", title: a.title, detail: serves ? `Activity · ${serves}` : "Activity", href: page(a.id) });
  }
  for (let i = model.actions.length - 1; i >= 0 && hits.length < SHOWN; i--) {
    const a = model.actions[i];
    if (!matches(a.summary)) continue;
    hits.push({ id: a.id, kind: "action", title: a.summary, detail: `${dayLabel(day(a.start))}, ${clock(a.start)}`,
      href: `/day/${day(a.start)}?sel=${encodeURIComponent(a.id)}` });
  }
  return hits.slice(0, SHOWN);
}

const page = (id: string) => `/reader?page=${encodeURIComponent(id)}`;
