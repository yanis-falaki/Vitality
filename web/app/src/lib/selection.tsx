// What is selected, shared by a view and the inspector. The selected id is in the URL (`?sel=<id>`),
// so a view can be shared and reloaded; a view may keep more of its state there (`useParam`). On a
// phone or tablet the inspector is opened by a selection the person made and closed with its close
// button; a view's own default choice (`quiet`) leaves it closed.
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

type Changes = Record<string, string | null>;
interface Options { quiet?: boolean }

interface Selection {
  sel: string | null;
  /** Select `id`; `quiet` for a view's default choice (no history entry, inspector stays closed). */
  select(id: string | null, options?: Options): void;
  /** Change several URL parameters at once (`sel` among them, if given). */
  update(changes: Changes, options?: Options): void;
  /** Whether the inspector is open (on a phone or tablet). */
  open: boolean;
  close(): void;
}

const SelectionContext = createContext<Selection | null>(null);

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState(() => params.has("sel"));
  const update = useCallback((changes: Changes, options?: Options) => {
    setParams((p) => {
      const next = new URLSearchParams(p);
      for (const [name, value] of Object.entries(changes)) {
        if (value) next.set(name, value);
        else next.delete(name);
      }
      return next;
    }, { replace: options?.quiet ?? false });
    if ("sel" in changes && !options?.quiet) setOpen(changes.sel !== null);
  }, [setParams]);
  const select = useCallback((id: string | null, options?: Options) => update({ sel: id }, options), [update]);
  const close = useCallback(() => setOpen(false), []);
  return (
    <SelectionContext.Provider value={{ sel: params.get("sel"), select, update, open, close }}>
      {children}
    </SelectionContext.Provider>
  );
}

export function useSelection(): Selection {
  const value = useContext(SelectionContext);
  if (!value) throw new Error("useSelection outside SelectionProvider");
  return value;
}

/** A URL parameter other than the selection (e.g. the Reader's page). */
export function useParam(name: string): string | null {
  const [params] = useSearchParams();
  return params.get(name);
}
