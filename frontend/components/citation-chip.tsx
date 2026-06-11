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
 * onClick è il seam per il G4 (split-view della norma): oggi nessuno
 * lo cabla e il chip è un no-op.
 */

import type { Citation } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Codici e testi unici noti: lo slug È l'identità del prodotto
 * (vedi backend legger/corpus/refs.py, _KNOWN_CODICI).
 */
const KNOWN_ACTS: Record<string, string> = {
  "codice-civile": "Cod. Civ.",
  "codice-penale": "Cod. Pen.",
  "codice-procedura-civile": "C.p.c.",
  "codice-procedura-penale": "C.p.p.",
  // il c.p.p. è veicolato dal d.P.R. 447/1988: stessa abbreviazione
  "dpr-447-1988": "C.p.p.",
  costituzione: "Cost.",
};

/** Tipi di atto nel pattern `<tipo>-<numero>-<anno>`. */
const ACT_TYPES: Record<string, string> = {
  dlgs: "D.Lgs.",
  dl: "D.L.",
  legge: "L.",
  dpr: "D.P.R.",
  rd: "R.D.",
  dm: "D.M.",
  dpcm: "D.P.C.M.",
};

const ACT_PATTERN = /^([a-z]+)-(\d+[a-z]*)-(\d{4})$/;

/** Umanizza un act_ref: registro noto → pattern tipo-numero-anno → verbatim. */
export function actRefLabel(actRef: string): string {
  const known = KNOWN_ACTS[actRef];
  if (known) return known;
  const match = ACT_PATTERN.exec(actRef);
  if (match && ACT_TYPES[match[1]]) {
    return `${ACT_TYPES[match[1]]} ${match[2]}/${match[3]}`;
  }
  return actRef;
}

/** L'etichetta completa del chip: atto, articolo e (se noto) comma. */
export function citationLabel(
  actRef: string,
  article: string,
  comma: string | null,
): string {
  const base = `${actRefLabel(actRef)}, art. ${article}`;
  return comma ? `${base}, c. ${comma}` : base;
}

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
