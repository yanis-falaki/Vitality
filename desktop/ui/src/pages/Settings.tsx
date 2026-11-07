// The Settings page (spec §7): start at login, the server address, the numbers of the rules,
// image quality, the disk warning level, and Free up space (after asking). Edits a draft;
// Save sends it to the core, which checks it (errors appear under their field) and applies it.
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
  help: string;
  /** Shown value from the stored one, and back (the change share is stored as a fraction). */
  scale?: number;
  step?: number;
}

const RULES: Field[] = [
  { key: "start_gap_s", label: "Start gap", unit: "s", help: "Input after this long without input starts an interaction (R1); the same wait ends it after the settle shot (R5)." },
  { key: "settle_s", label: "Settle", unit: "s", help: "A shot this long after the last input (R2)." },
  { key: "max_gap_s", label: "Maximum gap", unit: "s", help: "While you are continuously active, a shot at least this often (R3)." },
  { key: "change_fraction", label: "Changed area", unit: "%", scale: 100, step: 0.1, help: "A screen counts as changed when more than this share of it moved (R4)." },
  { key: "change_levels", label: "Change strength", unit: "levels", help: "How many gray levels (of 255) a part of the screen must move by to count as changed (R4)." },
  { key: "passive_every_s", label: "Passive check", unit: "s", help: "While idle, the screens are checked this often (R6)." },
  { key: "passive_quiet_checks", label: "Quiet checks", unit: "checks", help: "A passive interaction ends after this many checks in a row without change (R7)." },
  { key: "max_interaction_mib", label: "Size budget", unit: "MiB", help: "An interaction this large is split in two (R9); the server takes at most 50 MiB." },
];

const STORAGE: Field[] = [
  { key: "webp_quality", label: "Image quality", unit: "of 100", help: "WebP quality of the shots: higher is sharper and larger." },
  { key: "disk_warning_gb", label: "Disk warning", unit: "GB free", step: 0.5, help: "Status warns when the disk has less free space than this." },
];

export function Settings() {
  const view = useSettingsView();
  const stored = { data: view.data?.settings };
  const client = useQueryClient();
  const [draft, setDraft] = useState<S | null>(null);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    if (stored.data && !draft) setDraft(stored.data);
  }, [stored.data, draft]);
  const save = useMutation({
    mutationFn: core.settingsSave,
    onSuccess: (reply) => {
      setErrors(reply.errors);
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
  const known = new Set<string>(["server", "start_at_login", ...RULES.map((f) => f.key), ...STORAGE.map((f) => f.key)]);
  // Errors no field shows (the file could not be written…), and a failed call.
  const general = [
    ...errors.filter((e) => !known.has(e.field)).map((e) => (e.field === "file" ? e.message : `${e.field}: ${e.message}`)),
    ...(save.error ? [`Not saved: ${String(save.error)}`] : []),
  ];
  const fieldErrors = errors.filter((e) => known.has(e.field)).length;
  const changed = JSON.stringify(draft) !== JSON.stringify(stored.data);

  const number = (f: Field) => (
    <div className={`field${errorOf(f.key) ? " field-error" : ""}`} key={f.key}>
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
      <p className="field-help">{errorOf(f.key) ?? f.help}</p>
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
        <h2 className="section-title">Capture rules</h2>
        <div className="fields">{RULES.map(number)}</div>
      </section>

      <section className="panel">
        <h2 className="section-title">Images and disk</h2>
        <div className="fields">{STORAGE.map(number)}</div>
        <FreeUpSpace />
      </section>
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
