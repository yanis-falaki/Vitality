// The core's data as TanStack queries, refreshed whenever the core says its state changed.
// `useStatus()`, `useScreens()`, `usePreview(id)` (about once a second, only while the window is
// visible), and `useCoreEvents()` (call once, in App) that turns `state-changed` into refetches.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { core, onStateChanged, type ScreenId } from "../core";

export const keys = {
  status: ["status"],
  statusPage: ["status-page"],
  screens: ["screens"],
  checks: ["checks"],
  settings: ["settings"],
  rules: ["rules"],
  preview: (id: ScreenId) => ["preview", id],
};

export const useStatus = () => useQuery({ queryKey: keys.status, queryFn: core.status, refetchInterval: 5000 });

export const useStatusPage = () => useQuery({ queryKey: keys.statusPage, queryFn: core.statusPage, refetchInterval: 3000 });

export const useChecks = () => useQuery({ queryKey: keys.checks, queryFn: core.checks, refetchInterval: 10000 });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: core.settingsGet });
export const useRules = () => useQuery({ queryKey: keys.rules, queryFn: core.howItWorks });

export const useScreens = () => useQuery({ queryKey: keys.screens, queryFn: core.screensList, refetchInterval: 2000 });

export const usePreview = (id: ScreenId) =>
  useQuery({
    queryKey: keys.preview(id),
    queryFn: () => core.screenPreview(id),
    refetchInterval: 1000, // paused by TanStack while the window is hidden
    placeholderData: (previous) => previous,
  });

export function useCoreEvents() {
  const client = useQueryClient();
  useEffect(() => {
    const stop = onStateChanged(() => {
      void client.invalidateQueries({ queryKey: keys.status });
      void client.invalidateQueries({ queryKey: keys.statusPage });
      void client.invalidateQueries({ queryKey: keys.screens });
      void client.invalidateQueries({ queryKey: keys.checks });
    });
    return () => void stop.then((unlisten) => unlisten());
  }, [client]);
}
