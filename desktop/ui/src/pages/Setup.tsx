// First-run setup (spec §8), shown instead of the pages while no screen is set up or an
// essential check fails. Steps: Welcome, Input access, Server, Screens, Start at login, Done;
// each ticks as soon as its check passes (the checks refresh live). The very first start is
// paused; Done offers Start recording (or Done, stay paused). `onDone` leaves Setup.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { CaptureState, usePauseResume } from "../components/CaptureState";
import { core, type Check } from "../core";
import { CheckLine } from "../components/CheckLine";
import { Switch } from "../components/Switch";
import { check } from "../lib/setup";
import { keys, useChecks, useSettings, useStatus } from "../lib/queries";
import { Screens } from "./Screens";

const STEPS = ["Welcome", "Input access", "Server", "Screens", "Start at login", "Done"] as const;
export const USERMOD = "sudo usermod -aG input $USER";
const SERVER_PARTS = ["tailscale", "server_health", "device_accepted", "server"];

export function Setup({ onDone, initialStep = 0 }: { onDone: () => void; initialStep?: number }) {
  const [step, setStep] = useState(initialStep);
  const [skippedServer, setSkippedServer] = useState(false);
  const checks = useChecks().data;
  const status = useStatus().data;
  const passed = (id: string) => check(checks, id)?.ok ?? false;
  const done = [
    step > 0,
    passed("input_access"),
    skippedServer || check(checks, "server")?.ok === true || SERVER_PARTS.every((id) => !check(checks, id) || passed(id)),
    passed("screens_open") && (status?.screens ?? 0) > 0,
    passed("start_at_login"),
    false,
  ];
  const blocking = (checks ?? []).filter((c) => !c.ok && (c.id === "desktop_session" || c.id === "portal_available"));
  return (
    <div className="setup">
      <aside className="setup-steps">
        <span className="brand"><span className="brand-mark" aria-hidden />Vitality</span>
        <p className="setup-caption">Setup</p>
        <ol>
          {STEPS.map((name, i) => (
            <li key={name}>
              <button className={i === step ? "on" : ""} onClick={() => setStep(i)}>
                <span className={`step-mark${done[i] ? " step-done" : ""}`} aria-hidden>{done[i] ? "✓" : i + 1}</span>
                {name}
              </button>
            </li>
          ))}
        </ol>
        <span className="spacer" />
        <CaptureState />
      </aside>
      <main className="page">
        <div className="page-inner setup-page">
          {blocking.length > 0 && step > 0 && (
            <ul className="checks setup-blocking">{blocking.map((c) => <CheckLine key={c.id} c={c} />)}</ul>
          )}
          {step === 0 && <Welcome />}
          {step === 1 && <InputStep check={check(checks, "input_access")} />}
          {step === 2 && <ServerStep checks={checks} onSkip={() => { setSkippedServer(true); setStep(3); }} />}
          {step === 3 && <Screens embedded />}
          {step === 4 && <LoginStep check={check(checks, "start_at_login")} />}
          {step === 5 && <DoneStep checks={checks ?? []} onDone={onDone} />}
          {step < 5 && (
            <footer className="setup-nav">
              {step > 0 && <button className="button" onClick={() => setStep(step - 1)}>Back</button>}
              <span className="spacer" />
              <button className="button button-primary" onClick={() => setStep(step + 1)}>{step === 0 ? "Begin" : "Continue"}</button>
            </footer>
          )}
        </div>
      </main>
    </div>
  );
}

function Welcome() {
  return (
    <section>
      <h1>Welcome to Vitality</h1>
      <p className="lede">Vitality captures your screens as evidence of what you work on.</p>
      <ul className="facts">
        <li><b>What is recorded:</b> screenshots of the screens you pick, around your keyboard and mouse activity, and while you are idle when a screen changes (a video, a call). Never the sound, never your keystrokes themselves.</li>
        <li><b>Where it goes:</b> to this machine first, then to your own Vitality server over Tailscale. Nothing goes anywhere else.</li>
        <li><b>You stay in control:</b> pause at any time from the tray icon or this window; “How capture works” explains every rule.</li>
      </ul>
    </section>
  );
}

function InputStep({ check: c }: { check: Check | undefined }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(USERMOD);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <section>
      <h1>Input access</h1>
      <p className="lede">Vitality notices keyboard and mouse activity from your input devices, which the <code>input</code> group may read.</p>
      {c && <ul className="checks"><CheckLine c={c} /></ul>}
      {c && !c.ok && (
        <div className="command">
          <p>Run this in a terminal, then log out and in again:</p>
          <div className="command-line">
            <code>{USERMOD}</code>
            <button className="button" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
          </div>
          <p className="field-help">Vitality never runs sudo itself.</p>
        </div>
      )}
    </section>
  );
}

function ServerStep({ checks, onSkip }: { checks: Check[] | null | undefined; onSkip: () => void }) {
  const settings = useSettings();
  const client = useQueryClient();
  const [address, setAddress] = useState("");
  const filled = useRef(false);
  const [result, setResult] = useState<Check[] | null>(null);
  const [skipping, setSkipping] = useState(false);
  useEffect(() => {
    // Once: an address the owner cleared stays empty.
    if (settings.data && !filled.current) {
      filled.current = true;
      setAddress(settings.data.server);
    }
  }, [settings.data]);
  const test = useMutation({
    mutationFn: () => core.serverTest(address),
    onSuccess: (c) => {
      setResult(c.filter((x) => SERVER_PARTS.includes(x.id)));
      client.setQueryData(keys.checks, c);
      void client.invalidateQueries({ queryKey: keys.settings });
    },
  });
  // Skip keeps captures here for real: the server is cleared, uploads stop.
  const skip = useMutation({
    mutationFn: () => core.settingsSave({ ...settings.data!, server: "" }),
    onSuccess: (reply) => {
      if (reply.errors.length) return;
      void client.invalidateQueries({ queryKey: keys.settings });
      void client.invalidateQueries({ queryKey: keys.status });
      onSkip();
    },
  });
  const shown = result ?? checks?.filter((x) => SERVER_PARTS.includes(x.id));
  return (
    <section>
      <h1>Server</h1>
      <p className="lede">Finished interactions are uploaded to your Vitality server. Each part is tested on its own.</p>
      <div className="field field-wide">
        <label htmlFor="setup-server">Server address</label>
        <div className="command-line">
          <input id="setup-server" type="text" value={address} spellCheck={false} onChange={(e) => setAddress(e.target.value)} />
          <button className="button button-primary" disabled={test.isPending} onClick={() => test.mutate()}>{test.isPending ? "Testing…" : "Test"}</button>
        </div>
        {test.error && <p className="field-help field-help-error">{String(test.error)}</p>}
      </div>
      {shown && shown.length > 0 && <ul className="checks">{shown.map((c) => <CheckLine key={c.id} c={c} />)}</ul>}
      {skipping ? (
        <div className="command">
          <p>Keep captures on this machine? No server is set and nothing is uploaded until you set one in Settings.</p>
          <div className="command-line">
            <button className="button button-primary" disabled={skip.isPending} onClick={() => skip.mutate()}>Keep them here</button>
            <button className="button" onClick={() => setSkipping(false)}>Back</button>
          </div>
        </div>
      ) : (
        <p className="field-help">
          No server yet? <button className="link" onClick={() => setSkipping(true)}>Skip this step</button>: captures then stay on this machine.
        </p>
      )}
    </section>
  );
}

function LoginStep({ check: c }: { check: Check | undefined }) {
  const settings = useSettings();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (on: boolean) => core.settingsSave({ ...settings.data!, start_at_login: on }),
    onSuccess: (reply) => {
      void client.invalidateQueries({ queryKey: keys.settings });
      setError(reply.login_error ?? reply.errors[0]?.message ?? null);
      void client.invalidateQueries({ queryKey: keys.checks });
    },
    onError: (e) => setError(String(e)),
  });
  return (
    <section>
      <h1>Start at login</h1>
      <p className="lede">Vitality runs in the background from login, with its icon in the tray. It restarts by itself after a crash.</p>
      {settings.data && (
        <Switch checked={settings.data.start_at_login} disabled={save.isPending} onChange={(v) => save.mutate(v)} label="Start Vitality when you log in" />
      )}
      {error && <p className="field-help field-help-error">{error}</p>}
      {c && <ul className="checks">{<CheckLine c={c} />}</ul>}
    </section>
  );
}

function DoneStep({ checks, onDone }: { checks: Check[]; onDone: () => void }) {
  const client = useQueryClient();
  const again = useMutation({ mutationFn: core.checksRun, onSuccess: (c) => client.setQueryData(keys.checks, c) });
  const status = useStatus();
  const start = usePauseResume();
  const essentialMissing = checks.some((c) => c.essential && !c.ok);
  const paused = status.data?.pause.paused ?? false;
  return (
    <section>
      <h1>{essentialMissing ? "Almost there" : "All set"}</h1>
      <p className="lede">
        {essentialMissing
          ? "Something essential still needs attention. Fix it, then check again."
          : paused
            ? "Vitality is paused: nothing is captured until you start recording. You can pause again at any time from the tray icon or this window."
            : "Capture is running. You can pause it at any time from the tray icon or this window."}
      </p>
      <ul className="checks">{checks.map((c) => <CheckLine key={c.id} c={c} />)}</ul>
      <footer className="setup-nav">
        <button className="button" disabled={again.isPending} onClick={() => again.mutate()}>{again.isPending ? "Checking…" : "Check again"}</button>
        <span className="spacer" />
        {paused ? (
          <>
            <button className="button" disabled={essentialMissing} onClick={onDone}>Done, stay paused</button>
            <button className="button button-primary" disabled={essentialMissing || start.isPending}
              onClick={() => start.mutate("resume", { onSuccess: onDone })}>Start recording</button>
          </>
        ) : (
          <button className="button button-primary" disabled={essentialMissing} onClick={onDone}>Done</button>
        )}
      </footer>
    </section>
  );
}
