/**
 * CitationChip: il marker `[[act_ref|art.N|c.M]]` reso come riferimento
 * editoriale cliccabile — maiuscoletto, bordo a capello, carta, testo
 * bordeaux — con pallino di vigenza (verde salvia = vigente, grigio
 * caldo = abrogato).
 *
 * Una citazione è "non verificata" quando l'evento citation del
 * guardrail ha verified=false (atto o articolo fuori contesto) oppure
 * quando NESSUN evento citation corrisponde al marker: stile ambra +
 * tooltip. `reason: comma_not_in_context` è solo advisory (verified
 * resta true) e il chip resta normale.
 *
 * onClick apre la split-view della norma (cablato dal G4).
 *
 * Le etichette (registro degli act_ref) vivono in lib/act-labels.ts,
 * condivise con l'elenco fonti e l'intestazione del pannello.
 */

import { citationLabel } from "@/lib/act-labels";
import type { Citation } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface CitationChipProps {
  actRef: string;
  article: string;
  comma: string | null;
  /** L'evento citation del guardrail che corrisponde al marker, se c'è. */
  citation?: Citation;
  onClick?: () => void;
}

export function CitationChip({
  actRef,
  article,
  comma,
  citation,
  onClick,
}: CitationChipProps) {
  const verified = citation?.verified === true;
  const vigente = citation?.vigenza === "vigente";

  return (
    <button
      type="button"
      onClick={onClick}
      data-verified={verified}
      title={
        verified
          ? (citation?.title ?? undefined)
          : "citazione non verificata"
      }
      className={cn(
        "mx-px inline-flex items-baseline gap-1.5 rounded-sm border px-1.5 align-baseline text-[0.8125rem] leading-6 font-medium tracking-wide [font-variant-caps:small-caps] transition-colors",
        verified
          ? "border-border bg-card text-primary hover:bg-accent hover:underline hover:underline-offset-2"
          : "border-non-verificato/40 bg-non-verificato-muted text-non-verificato hover:underline hover:underline-offset-2",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 self-center rounded-full",
          !verified
            ? "bg-non-verificato"
            : vigente
              ? "bg-vigente"
              : "bg-abrogato",
        )}
      />
      {citationLabel(actRef, article, comma)}
    </button>
  );
}
