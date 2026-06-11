"use client";

/**
 * SourcesList (G5): «Fonti consultate (N)» a piè di ogni risposta —
 * TUTTE le provvigioni passate al modello (evento `sources`), incluse
 * quelle non citate nella risposta: è trasparenza sul contesto, non un
 * indice delle citazioni.
 *
 * Chiusa di default (sezione a capello sopra); aperta: righe compatte
 * `etichetta — art. N` (registro di lib/act-labels.ts), pallino di
 * vigenza, rubrica in tono muto. Il click apre la split-view sulla
 * fonte, stessa via dei chip (onSourceClick → setTarget in page.tsx).
 */

import { ChevronRight } from "lucide-react";
import * as React from "react";

import { actRefLabel } from "@/lib/act-labels";
import type { CitationRef } from "@/lib/render";
import type { Source } from "@/lib/types";
import { cn } from "@/lib/utils";

export function SourcesList({
  sources,
  onSourceClick,
}: {
  sources: Source[];
  /** Apre la split-view sulla fonte (stesso seam dei chip-citazione). */
  onSourceClick?: (ref: CitationRef) => void;
}) {
  const [open, setOpen] = React.useState(false);

  return (
    <section
      aria-label="Fonti consultate"
      className="border-t border-border pt-2"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight
          aria-hidden
          className={cn("size-3 transition-transform", open && "rotate-90")}
        />
        Fonti consultate ({sources.length})
      </button>

      {open ? (
        <ul className="mt-1.5 flex flex-col">
          {sources.map((source, i) => (
            <li key={`${source.act_ref}-${source.anchor}-${i}`}>
              <button
                type="button"
                onClick={
                  onSourceClick &&
                  (() =>
                    onSourceClick({
                      actRef: source.act_ref,
                      article: source.article,
                      comma: null,
                    }))
                }
                className="flex w-full items-baseline gap-2 rounded-sm px-1.5 py-1 text-left text-xs transition-colors hover:bg-accent"
              >
                <span
                  aria-hidden
                  className={cn(
                    "size-1.5 shrink-0 self-center rounded-full",
                    source.vigenza === "vigente"
                      ? "bg-vigente"
                      : source.vigenza === "abrogato"
                        ? "bg-abrogato"
                        : "bg-border",
                  )}
                />
                <span className="shrink-0 font-medium tracking-wide text-primary [font-variant-caps:small-caps]">
                  {actRefLabel(source.act_ref)} — art. {source.article}
                </span>
                {source.title ? (
                  <span className="truncate text-muted-foreground italic">
                    {source.title}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
