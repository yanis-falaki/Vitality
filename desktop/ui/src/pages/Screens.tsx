// The Screens page (spec §5): one card per screen with a live preview, the duplicate warning
// when two screens look the same, and Add screen (the portal's picker; the new screen is named
// "Screen N" until renamed). With no screen yet, a first-screen invitation instead of the grid.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { core, type ScreensView } from "../core";
import { ScreenCard } from "../components/ScreenCard";
import { keys, useScreens } from "../lib/queries";

export function Screens() {
  const screens = useScreens();
  const client = useQueryClient();
  const done = (view: ScreensView) => {
    client.setQueryData(keys.screens, view);
    void client.invalidateQueries({ queryKey: keys.status });
  };
  const add = useMutation({ mutationFn: core.screensAdd, onSuccess: done });
  const repick = useMutation({ mutationFn: core.screensRepick, onSuccess: done });
  const rename = useMutation({ mutationFn: (v: { id: string; name: string }) => core.screensRename(v.id, v.name), onSuccess: done });
  const remove = useMutation({ mutationFn: core.screensRemove, onSuccess: done });
  const view = screens.data;
  const nameOf = (id: string) => view?.screens.find((s) => s.id === id)?.name ?? id;
  const error = add.error ?? repick.error ?? rename.error ?? remove.error;

  return (
    <div className="page-inner">
      <header className="page-head">
        <div>
          <h1>Screens</h1>
          <p className="lede">Each screen is one screen share, named by you. Vitality captures what is on them.</p>
          <p className="hint">
            When your desktop asks which screen to share, tick <b>Allow a restore token</b> (or “Remember”) if it offers
            it: Vitality then reopens the screen at login without asking again.
          </p>
        </div>
        {view && view.screens.length > 0 && (
          <button className="button button-primary" disabled={add.isPending} onClick={() => add.mutate()}>
            {add.isPending ? "Choose a screen…" : "Add screen"}
          </button>
        )}
      </header>

      {view?.duplicates.map(([a, b]) => (
        <div className="banner" role="status" key={`${a}-${b}`}>
          <strong>{nameOf(a)} and {nameOf(b)} look the same.</strong> They are probably one monitor shared twice:
          re-pick one of them, or remove it.
        </div>
      ))}
      {error && <p className="note" role="alert">{pickMessage(String(error))}</p>}

      {view && view.screens.length === 0 ? (
        <section className="first">
          <h2>Add your first screen</h2>
          <p>Your desktop will ask which monitor to share. Pick one; add the others one at a time.</p>
          <button className="button button-primary" disabled={add.isPending} onClick={() => add.mutate()}>
            {add.isPending ? "Choose a screen in the dialog…" : "Add screen"}
          </button>
        </section>
      ) : (
        <div className="grid">
          {view?.screens.map((s) => (
            <ScreenCard
              key={s.id}
              screen={s}
              busy={repick.isPending && repick.variables === s.id}
              onRename={(name) => rename.mutate({ id: s.id, name })}
              onRepick={() => repick.mutate(s.id)}
              onRemove={() => remove.mutate(s.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** The core's error, said for the owner. */
function pickMessage(error: string): string {
  if (error.includes("refused or cancelled")) return "No screen was picked.";
  if (error.includes("not available")) return "Your desktop has no screen-share portal running, so no screen can be added.";
  return error;
}
