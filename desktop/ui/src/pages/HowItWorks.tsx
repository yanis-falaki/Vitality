// "How capture works": the rules R1–R6 in plain words with the current numbers, as the core
// writes them (`how_it_works`); this page only lays them out.
import { useRules } from "../lib/queries";

export function HowItWorks() {
  const rules = useRules();
  return (
    <div className="page-inner">
      <header className="page-head">
        <div>
          <h1>How capture works</h1>
          <p className="lede">Vitality follows these rules, with the numbers set in Settings.</p>
        </div>
      </header>
      <ol className="rules">
        {rules.data?.map((r) => (
          <li key={r.id} className="rule">
            <span className="rule-id">{r.id}</span>
            <div>
              <h2>{r.title}</h2>
              <p>{r.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
