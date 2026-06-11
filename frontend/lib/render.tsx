/**
 * Seam di rendering del testo dell'assistant (G3).
 *
 * Il testo è markdown (GFM) con marker-citazione `[[act_ref|art.N|c.M]]`
 * incastonati nel flusso (dentro paragrafi, voci di lista, celle…). La
 * strategia per tenere i chip inline:
 *
 *   1. `parseMarkers` scompone il testo grezzo: i marker validi diventano
 *      sentinelle `<indice>` (caratteri Private Use Area: il
 *      markdown li attraversa come testo qualunque, e non possono
 *      comparire in un testo normativo); i `pending` (marker incompleti
 *      in coda durante lo streaming) vengono omessi (invisibili).
 *   2. react-markdown + remark-gfm parsano il markdown; un piccolo plugin
 *      remark spezza i nodi-testo sulle sentinelle e li sostituisce con
 *      elementi `citation-chip` (via `data.hName`), ovunque si trovino
 *      nell'albero.
 *   3. La mappa `components` rende `citation-chip` come <CitationChip>,
 *      risolvendo l'indice nel marker parsato e nell'evento `citation`
 *      del guardrail che gli corrisponde (match sul marker grezzo).
 *
 * Gli altri elementi markdown sono stilati sul tema editoriale: misurati,
 * compatti, bordi a capello.
 */

import type { Root, RootContent } from "mdast";
import type { ComponentType, ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { visit } from "unist-util-visit";

import { CitationChip } from "@/components/citation-chip";
import { parseMarkers, type Segment } from "@/lib/parse-markers";
import type { Citation } from "@/lib/types";

type MarkerSegment = Extract<Segment, { type: "marker" }>;

/** Il riferimento consegnato a onCitationClick (G4: apre la split-view). */
export interface CitationRef {
  actRef: string;
  article: string;
  comma: string | null;
  /** L'evento citation corrispondente, se il guardrail l'ha emesso. */
  citation?: Citation;
}

const SENTINEL_OPEN = "";
const SENTINEL_CLOSE = "";
const SENTINEL_RE = /(\d+)/g;

/** Plugin remark: sostituisce le sentinelle nei nodi-testo con elementi `citation-chip`. */
function remarkCitationChips() {
  return (tree: Root) => {
    visit(tree, "text", (node, index, parent) => {
      if (!parent || index === undefined) return undefined;
      if (!node.value.includes(SENTINEL_OPEN)) return undefined;

      const replacement: RootContent[] = [];
      let cursor = 0;
      for (const match of node.value.matchAll(SENTINEL_RE)) {
        if (match.index > cursor) {
          replacement.push({
            type: "text",
            value: node.value.slice(cursor, match.index),
          });
        }
        replacement.push({
          // Nodo fittizio: mdast-util-to-hast onora data.hName/hProperties
          // anche per tipi sconosciuti, producendo <citation-chip index=…>.
          type: "text",
          value: "",
          data: {
            hName: "citation-chip",
            hProperties: { index: match[1] },
          },
        });
        cursor = match.index + match[0].length;
      }
      if (cursor < node.value.length) {
        replacement.push({ type: "text", value: node.value.slice(cursor) });
      }
      parent.children.splice(index, 1, ...replacement);
      return index + replacement.length; // salta i nodi appena inseriti
    });
  };
}

/** Elementi markdown stilati sul tema editoriale (misurato, compatto). */
const MARKDOWN_COMPONENTS: Components = {
  p: (props) => <p className="my-3 first:mt-0 last:mb-0" {...props} />,
  h1: (props) => (
    <h1
      className="mt-6 mb-3 font-display text-xl font-medium tracking-tight first:mt-0"
      {...props}
    />
  ),
  h2: (props) => (
    <h2
      className="mt-6 mb-3 font-display text-lg font-medium tracking-tight first:mt-0"
      {...props}
    />
  ),
  h3: (props) => (
    <h3 className="mt-5 mb-2 font-display text-base font-semibold first:mt-0" {...props} />
  ),
  h4: (props) => (
    <h4 className="mt-4 mb-2 text-[0.9375rem] font-semibold first:mt-0" {...props} />
  ),
  ul: (props) => (
    <ul className="my-3 list-disc space-y-1.5 pl-5 marker:text-muted-foreground" {...props} />
  ),
  ol: (props) => (
    <ol className="my-3 list-decimal space-y-1.5 pl-5 marker:text-muted-foreground" {...props} />
  ),
  blockquote: (props) => (
    <blockquote
      className="my-3 border-l-2 border-primary/30 pl-4 text-muted-foreground italic"
      {...props}
    />
  ),
  table: (props) => (
    <div className="my-4 overflow-x-auto">
      <table className="w-full border-collapse text-sm" {...props} />
    </div>
  ),
  th: (props) => (
    <th
      className="border-b border-foreground/25 px-3 py-1.5 text-left font-semibold"
      {...props}
    />
  ),
  td: (props) => (
    <td className="border-b border-border px-3 py-1.5 align-top" {...props} />
  ),
  code: (props) => (
    <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[0.85em]" {...props} />
  ),
  pre: (props) => (
    <pre
      className="my-3 overflow-x-auto rounded-md border border-border bg-muted p-3 text-sm"
      {...props}
    />
  ),
  a: (props) => (
    <a className="text-primary underline underline-offset-2" {...props} />
  ),
  hr: () => <hr className="my-6 border-border" />,
};

export function renderAssistantText(
  text: string,
  citations: Citation[],
  onCitationClick?: (ref: CitationRef) => void,
): ReactNode {
  const segments = parseMarkers(text);
  const markers: MarkerSegment[] = [];

  const source = segments
    .map((segment) => {
      switch (segment.type) {
        case "text":
          return segment.value;
        case "marker":
          markers.push(segment);
          return `${SENTINEL_OPEN}${markers.length - 1}${SENTINEL_CLOSE}`;
        case "pending":
          return ""; // invisibile finché lo streaming non lo risolve
      }
    })
    .join("");

  const Chip = ({ index }: { index?: string }) => {
    const marker = markers[Number(index)];
    if (!marker) return null;
    const citation = citations.find((c) => c.marker === marker.marker);
    return (
      <CitationChip
        actRef={marker.actRef}
        article={marker.article}
        comma={marker.comma}
        citation={citation}
        onClick={
          onCitationClick &&
          (() =>
            onCitationClick({
              actRef: marker.actRef,
              article: marker.article,
              comma: marker.comma,
              citation,
            }))
        }
      />
    );
  };

  const components = {
    ...MARKDOWN_COMPONENTS,
    "citation-chip": Chip as ComponentType<{ index?: string }>,
  } as Components;

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkCitationChips]}
      components={components}
    >
      {source}
    </ReactMarkdown>
  );
}
