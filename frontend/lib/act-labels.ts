/**
 * Registro delle etichette degli act_ref — condiviso da chip-citazione
 * (citation-chip.tsx), elenco fonti (sources-list.tsx) e intestazione
 * del pannello norma (act-panel.tsx). Estratto dal G3 nel G5.
 *
 * Due registri:
 * - KNOWN_ACTS: abbreviazione editoriale ("Cod. Civ.") per chip e righe
 *   compatte;
 * - ACT_NAMES: denominazione corrente per esteso ("Codice civile") per
 *   l'intestazione del pannello, dove gli estremi grezzi dell'atto
 *   scendono nel sottotitolo.
 */

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

/** Denominazioni correnti per esteso (intestazione del pannello norma). */
const ACT_NAMES: Record<string, string> = {
  "codice-civile": "Codice civile",
  "codice-penale": "Codice penale",
  "codice-procedura-civile": "Codice di procedura civile",
  "codice-procedura-penale": "Codice di procedura penale",
  "dpr-447-1988": "Codice di procedura penale",
  costituzione: "Costituzione della Repubblica",
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

/**
 * Denominazione per esteso dell'atto, o null se lo slug non è nel
 * registro (in tal caso il chiamante usa il titolo dell'API).
 */
export function actRefName(actRef: string): string | null {
  return ACT_NAMES[actRef] ?? null;
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
