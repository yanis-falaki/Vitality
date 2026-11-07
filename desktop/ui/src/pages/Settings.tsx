// The Settings page (spec §7), in three groups: General (start at login, the server),
// Screenshots (image quality, the disk warning level, Free up space after asking) and, folded
// away under Advanced, Capture timing in plain words (no rule numbers here; "How these are
// used" links to How capture works). Edits a draft; Save sends it to the core, which checks
// each field (errors appear under it, and Advanced opens when one is there) and applies it.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { core, type FieldError, type Freed, type Settings as S } from "../core";
import { Switch } from "../components/Switch";
import { bytes, plural } from "../lib/format";
import { keys, useSettingsView } from "../lib/queries";

type NumberKey = { [K in keyof S]: S[K] extends number ? K : never }[keyof S];

interface Field {
  key: NumberKey;
  label: string;
  unit: string;
  /** Shown value from the stored one, and back (the changed share is stored as a fraction). */
  scale?: number;
  step?: number;
}

const TIMING: Field[] = [
  { key: "end_after_idle_s", label: "End an interaction after you've been idle for", unit: "s", step: 0.5 },
  { key: "max_gap_s", label: "Screenshot at least every", unit: "s while you're busy" },
  { key: "change_fraction", label: "Count a screen as changed when more than", unit: "% of it changes", scale: 100, step: 0.1 },
  { key: "change_levels", label: "…by more than", unit: "brightness levels (of 255)" },
  { key: "passive_every_s", label: "Check idle screens every", unit: "s" },
  { key: "passive_quiet_checks", label: "End a passive interaction after", unit: "quiet checks" },
  { key: "max_interaction_mib", label: "Split an interaction when it reaches", unit: "MiB" },
];

const SCREENSHOTS: Field[] = [
  { key: "webp_quality", label: "Image quality", unit: "of 100" },
  { key: "disk_warning_gb", label: "Warn when free disk space is under", unit: "GB", step: 0.5 },
];

export function Settings({ onHowItWorks }: { onHowItWorks?: () => void }) {
  const view = useSettingsView();
  const stored = { data: view.data?.settings };
  const client = useQueryClient();
  const [draft, setDraft] = useState<S | null>(null);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => {
    if (stored.data && !draft) setDraft(stored.data);
  }, [stored.data, draft]);
  const save = useMutation({
    mutationFn: core.settingsSave,
    onSuccess: (reply) => {
      setErrors(reply.errors);
      if (reply.errors.some((e) => TIMING.some((f) => f.key === e.field))) setAdvanced(true);
      if (reply.errors.length) return setNote(null);
      void client.invalidateQueries({ queryKey: keys.settings });
      setDraft(reply.settings);
      void client.invalidateQueries({ queryKey: keys.rules });
      void client.invalidateQueries({ queryKey: keys.checks });
      setNote(reply.login_error ? `Saved, but start at login could not change: ${reply.login_error}` : "Saved.");
    },
  });
  if (!draft) return <div className="page-inner" />;
  const set = <K extends keyof S>(k: K, v: S[K]) => {
    setDraft({ ...draft, [k]: v });
    setNote(null);
  };
  const errorOf = (k: string) => errors.find((e) => e.field === k)?.message;
  const known = new Set<string>(["server", "start_at_login", ...TIMING.map((f) => f.key), ...SCREENSHOTS.map((f) => f.key)]);
  // Errors no field shows (the file could not be written…), and a failed call.
  const general = [
    ...errors.filter((e) => !known.has(e.field)).map((e) => (e.field === "file" ? e.message : `${e.field}: ${e.message}`)),
    ...(save.error ? [`Not saved: ${String(save.error)}`] : []),
  ];
  const fieldErrors = errors.filter((e) => known.has(e.field)).length;
  const changed = JSON.stringify(draft) !== JSON.stringify(stored.data);

  const number = (f: Field) => (
    <div className={`row-field${errorOf(f.key) ? " field-error" : ""}`} key={f.key}>
      <label htmlFor={f.key}>{f.label}</label>
      <div className="field-input">
        <input
          id={f.key}
          type="number"
          step={f.step ?? 1}
          value={Number(((draft[f.key] as number) * (f.scale ?? 1)).toFixed(4))}
          onChange={(e) => set(f.key, (e.target.valueAsNumber / (f.scale ?? 1)) as never)}
        />
        <span className="unit">{f.unit}</span>
      </div>
      {errorOf(f.key) && <p className="field-help">{errorOf(f.key)}</p>}
    </div>
  );

  return (
    <div className="page-inner">
      <header className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="lede">Saved to <code>~/.config/vitality/client.toml</code> and applied at once.</p>
        </div>
        <button className="button button-primary" disabled={!changed || save.isPending} onClick={() => save.mutate(draft)}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
      </header>
      {note && <p className="note" role="status">{note}</p>}
      {fieldErrors > 0 && <p className="note note-error" role="alert">Not saved: {fieldErrors === 1 ? "one value needs" : `${fieldErrors} values need`} fixing below.</p>}
      {general.map((g) => <p key={g} className="note note-error" role="alert">{g}</p>)}
      {(view.data?.problems.length ?? 0) > 0 && (
        <div className="banner" role="status">
          <strong>Some values in client.toml were not valid and use their defaults:</strong>
          <ul className="problems">{view.data!.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          Saving keeps them as written unless you change them here.
        </div>
      )}

      <section className="panel">
        <h2 className="section-title">General</h2>
        <Switch checked={draft.start_at_login} onChange={(v) => set("start_at_login", v)} label="Start Vitality when you log in" />
        <div className={`field field-wide${errorOf("server") ? " field-error" : ""}`}>
          <label htmlFor="server">Server</label>
          <input id="server" type="text" value={draft.server} spellCheck={false} onChange={(e) => set("server", e.target.value)} />
          <p className="field-help">{errorOf("server") ?? "Where finished interactions are uploaded. Leave it empty to keep captures on this machine."}</p>
        </div>
      </section>

      <section className="panel">
        <h2 className="section-title">Screenshots</h2>
        <div className="rows-fields">{SCREENSHOTS.map(number)}</div>
        <FreeUpSpace />
      </section>

      <details className="panel advanced" open={advanced} onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}>
        <summary>
          <span className="section-title">Advanced · Capture timing</span>
        </summary>
        <div className="rows-fields">{TIMING.map(number)}</div>
        {onHowItWorks && (
          <button className="link link-quiet" onClick={onHowItWorks}>How these are used →</button>
        )}
      </details>
    </div>
  );
}

function FreeUpSpace() {
  const [asking, setAsking] = useState(false);
  const [freed, setFreed] = useState<Freed | null>(null);
  const client = useQueryClient();
  const free = useMutation({
    mutationFn: core.freeUpSpace,
    onSuccess: (f) => {
      setFreed(f);
      setAsking(false);
      void client.invalidateQueries({ queryKey: keys.statusPage });
    },
  });
  return (
    <div className="free">
      <div>
        <p className="check-title">Free up space</p>
        <p className="field-help">
          {freed
            ? `Deleted ${plural(freed.interactions, "uploaded interaction")}, ${bytes(freed.bytes)}.`
            : "Deletes the local copies of interactions the server already has. Nothing pending is touched."}
        </p>
      </div>
      {asking ? (
        <div className="free-ask">
          <span className="confirm">Delete the uploaded copies?</span>
          <button className="button button-danger" disabled={free.isPending} onClick={() => free.mutate()}>Delete</button>
          <button className="button" onClick={() => setAsking(false)}>Keep</button>
        </div>
      ) : (
        <button className="button" onClick={() => setAsking(true)}>Free up space…</button>
      )}
    </div>
  );
}
