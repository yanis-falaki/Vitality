// Home: the page the owner opens each day (web spec, §11). Top to bottom: today's date and whether the
// pipeline is working; a search across the model; today so far (Day's lanes, compact, and what is
// probably happening now); where the week's time went, by striving; and what the modeler changed, newest
// first. On a desktop the changes are a column of their own beside the day and the week.
//
// Room is left for a section of opportunities, from an ambient agent: it would sit first in the main
// column, above "Today so far". Nothing stands in for it until it exists.
import { usePipeline } from "../api/client";
import { WithModel } from "../components/Loading";
import { dayLong } from "../lib/format";
import { useLayout } from "../lib/media";
import type { Model } from "../lib/model";
import { useNow } from "../lib/now";
import { useLastVisit } from "../lib/visit";
import { Changes } from "./home/Changes";
import { PipelineLine } from "./home/Pipeline";
import { Search } from "./home/Search";
import { Today } from "./home/Today";
import { Week } from "./home/Week";
import "./home.css";

export function Home() {
  return <WithModel>{(model) => <HomeView model={model} />}</WithModel>;
}

function HomeView({ model }: { model: Model }) {
  const now = useNow();
  const layout = useLayout();
  const lastVisit = useLastVisit(now?.ms);
  const lastCapture = usePipeline().data?.last_capture ?? null;
  if (!now) return <div className="notice">Loading…</div>;
  return (
    <div className={`home home-${layout}`} data-testid="home">
      <div className="home-page">
        <header className="home-head">
          <div className="home-title">
            <h1>{dayLong(now.today)}</h1>
            <PipelineLine now={now} />
          </div>
          <Search model={model} />
        </header>
        <div className="home-columns">
          <div className="home-main">
            <Today model={model} now={now} lastCapture={lastCapture} />
            <Week model={model} now={now} />
          </div>
          <Changes model={model} lastVisit={lastVisit} />
        </div>
      </div>
    </div>
  );
}
