// A screen's name, renamed in place: click it (or Rename), type, Enter saves, Escape cancels.
// `onSave(name)` gets the trimmed name; blank names are not sent.
import { useEffect, useRef, useState } from "react";

export function NameEdit({ name, onSave }: { name: string; onSave: (name: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);
  const save = () => {
    const next = draft.trim();
    setEditing(false);
    if (next && next !== name) onSave(next);
    else setDraft(name);
  };
  if (!editing)
    return (
      <button className="name" title="Rename" onClick={() => { setDraft(name); setEditing(true); }}>
        {name}
      </button>
    );
  return (
    <input
      ref={input}
      className="name-input"
      value={draft}
      maxLength={60}
      aria-label="Screen name"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter") save();
        if (e.key === "Escape") { setDraft(name); setEditing(false); }
      }}
    />
  );
}
