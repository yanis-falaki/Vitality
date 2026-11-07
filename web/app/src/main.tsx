// The app: routes to Home and the four views inside the shell, with the query client, the selection and
// the lightbox around them. Routes: / (Home), /reader, /graph, /cards, /day/:date?; `?sel=<id>` in the
// views. The Reader was once at /: an old link there with `?page=` or `?sel=` goes on to /reader.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./theme.css";
import "./components/components.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { LightboxProvider } from "./components/Lightbox";
import { Shell } from "./components/Shell";
import { SelectionProvider } from "./lib/selection";
import { Cards } from "./views/Cards";
import { Day } from "./views/Day";
import { GraphView } from "./views/Graph";
import { Home } from "./views/Home";
import { Reader } from "./views/Reader";

const queries = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

/** Home, unless the link is an old one to the Reader (which was at `/`). */
function HomeOrReader() {
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  if (params.has("page") || params.has("sel")) return <Navigate to={{ pathname: "/reader", search }} replace />;
  return <Home />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      <BrowserRouter>
        <SelectionProvider>
          <LightboxProvider>
            <Routes>
              <Route element={<Shell />}>
                <Route index element={<HomeOrReader />} />
                <Route path="reader" element={<Reader />} />
                <Route path="graph" element={<GraphView />} />
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
