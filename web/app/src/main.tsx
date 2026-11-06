// The app: routes to the four views inside the shell, with the query client, the selection and the
// lightbox around them. Routes: / (Reader), /graph, /cards, /day/:date?; `?sel=<id>` everywhere.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./theme.css";
import "./components/components.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { LightboxProvider } from "./components/Lightbox";
import { Shell } from "./components/Shell";
import { SelectionProvider } from "./lib/selection";
import { Cards } from "./views/Cards";
import { Day } from "./views/Day";
import { Graph } from "./views/Graph";
import { Reader } from "./views/Reader";

const queries = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      <BrowserRouter>
        <SelectionProvider>
          <LightboxProvider>
            <Routes>
              <Route element={<Shell />}>
                <Route index element={<Reader />} />
                <Route path="graph" element={<Graph />} />
                <Route path="cards" element={<Cards />} />
                <Route path="day/:date?" element={<Day />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </LightboxProvider>
        </SelectionProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
