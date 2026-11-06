// The model's own prose: Markdown, sanitized, with its references turned into links that select.
// Dated references (`2026-10-24/22-56-09`) point at an interaction or an action; `act-`/`str-` ids at
// an activity or a striving. Anything that is not known stays plain text. Raw HTML in the text (an
// observation may transcribe a page's markup) is removed, never run.
import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo, type MouseEvent } from "react";
import { clock, dayLabel } from "../lib/format";
import type { Model } from "../lib/model";
import { useSelection } from "../lib/selection";

const REF = /\b(\d{4}-\d\d-\d\d\/\d\d-\d\d-\d\d(?:-\d+)?)\b|\b((?:act|str)-\d{4,})\b/g;

export function RichText({ model, text, small = false }: { model: Model; text: string; small?: boolean }) {
  const { select } = useSelection();
  const html = useMemo(() => render(model, text), [model, text]);
  const onClick = (e: MouseEvent) => {
    const ref = (e.target as HTMLElement).closest<HTMLElement>("[data-ref]");
    if (ref) {
      e.preventDefault();
      select(ref.dataset.ref!);
    }
  };
  return <div className={small ? "prose prose-small" : "prose"} onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />;
}

function render(model: Model, text: string): string {
  const clean = DOMPurify.sanitize(marked.parse(text, { async: false }) as string, { FORBID_TAGS: ["img", "style"] });
  const doc = new DOMParser().parseFromString(`<div>${clean}</div>`, "text/html");
  const root = doc.body.firstElementChild!;
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (!node.parentElement?.closest("a, pre")) texts.push(node);
  }
  for (const node of texts) linkRefs(doc, model, node);
  root.querySelectorAll("a[href]").forEach((a) => {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  });
  return root.innerHTML;
}

function linkRefs(doc: Document, model: Model, node: Text) {
  const value = node.nodeValue ?? "";
  REF.lastIndex = 0;
  if (!REF.test(value)) return;
  REF.lastIndex = 0;
  const out = doc.createDocumentFragment();
  let last = 0;
  for (const match of value.matchAll(REF)) {
    const id = match[1] ? model.resolveRef(match[1]) : model.kind(match[2]) ? match[2] : undefined;
    if (!id) continue;
    out.append(value.slice(last, match.index));
    const a = doc.createElement("a");
    const kind = model.kind(id)!;
    a.className = `ref ref-${kind}`;
    a.dataset.ref = id;
    a.href = "#";
    a.title = model.title(id);
    if (match[1]) {
      const start = kind === "action" ? model.action(id)!.start : model.interaction(id)!.start;
      a.textContent = `${dayLabel(start.slice(0, 10)).replace(/^\w+, /, "")} · ${clock(start)}`;
    } else a.textContent = id;
    out.append(a);
    last = match.index! + match[0].length;
  }
  out.append(value.slice(last));
  node.replaceWith(out);
}
