/**
 * Resa del testo normativo nel pannello-atto (norm-text).
 *
 * Il corpus Normattiva incastona nel testo dei commi (e talvolta nelle
 * rubriche) riferimenti normativi come link Markdown a URN, ad es.
 * `[articolo 1469-quater del codice civile](http://www.normattiva.it/
 * uri-res/N2Ls?urn:nir:stato:codice.civile:1942-03-16;262~art1469quater)`
 * (vedi docs/corpus-analysis.md, A8). Renderli grezzi mostra parentesi
 * quadre e URL chilometrici in mezzo alla norma.
 *
 * DELIBERATAMENTE non-markdown: il testo legale è pieno di asterischi,
 * trattini a inizio riga e righe numerate che un parser markdown
 * trasformerebbe in enfasi/liste, corrompendo il testo. Qui si riconosce
 * SOLO il pattern `[ancora](url)` con url http(s); tutto il resto passa
 * intatto come testo semplice (whitespace compreso).
 *
 * Convenzione Normattiva `(( ))` (corpus-analysis A8): il testo tra
 * doppie parentesi è quello inserito/modificato dal consolidamento.
 * I marcatori RESTANO nel testo (sono uno standard del dominio legale);
 * qui vengono solo avvolti in <span> attenuati con tooltip, per dare
 * l'informazione anche a chi non conosce la convenzione. Il contenuto
 * testuale resta byte-identico (il copia-incolla riproduce l'originale).
 */

import type { ReactNode } from "react";

/**
 * `[ancora](url)`: l'ancora non può contenere quadre (così
 * l'annidato `[a[b](url)` degrada con grazia: "[a" letterale + link "b",
 * come farebbe un parser markdown) ma può attraversare un a-capo — il
 * corpus contiene ancore multilinea reali, es. le note all'art. 5 di
 * legge-526-1999; l'url deve
 * iniziare con http(s):// e non può contenere spazi o `)` (gli URN
 * Normattiva usano `?:;~`, mai parentesi), con lunghezza limitata a 2048
 * (rende lineare anche l'unico caso di backtracking quadratico). Tutto
 * ciò che non combacia esattamente — niente parentesi di chiusura,
 * schema non-http (`javascript:` ecc.), quadre annidate — resta testo
 * letterale.
 */
const MD_LINK = /\[([^[\]]*)\]\((https?:\/\/[^\s)]{1,2048})\)/g;

/** I marcatori della novella: `((` apre, `))` chiude (mai annidati nel corpus). */
const NOVELLA_MARKER = /\(\(|\)\)/g;

/** Tooltip sui glifi `((`/`))` nel testo (Feature: affordance Normattiva). */
export const NOVELLA_TOOLTIP =
  "Testo tra (( )) = modificato da provvedimenti successivi — convenzione Normattiva";

/**
 * Avvolge i soli glifi `((`/`))` di un segmento di testo SEMPLICE in
 * <span> attenuati con tooltip. Presentazionale puro: i marcatori restano
 * nel flusso testuale (textContent invariato byte per byte). Le chiavi
 * derivano dall'offset assoluto nel testo del comma (keyBase).
 */
function renderNovellaMarkers(text: string, keyBase: number): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(NOVELLA_MARKER)) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    nodes.push(
      <span
        key={`nov-${keyBase + match.index}`}
        title={NOVELLA_TOOLTIP}
        className="text-muted-foreground"
      >
        {match[0]}
      </span>,
    );
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length || nodes.length === 0) nodes.push(text.slice(cursor));
  return nodes;
}

/**
 * Spezza il testo sui link markdown e rende le ancore come <a> esterni,
 * stilati come il link "Apri su Normattiva" del pannello (bordeaux,
 * sottolineatura discreta che si accende in hover). Il resto del testo
 * resta nodo-testo semplice, byte per byte.
 *
 * Ordine delle trasformazioni (importante): PRIMA i link markdown
 * sull'intero testo, POI i marcatori `(( ))` sui soli segmenti di testo
 * semplice rimasti. Così un link dentro un blocco `(( … ))` resta un <a>
 * (il blocco novellato attraversa i link, non li contiene mai nell'url:
 * la regex MD_LINK esclude `)` dall'url) e i marcatori intorno al link
 * vengono comunque attenuati. L'ancora di un link resta intatta: un
 * marcatore DENTRO l'ancora rimane parte del testo del link (byte-identico
 * comunque). L'ordine inverso spezzerebbe il pattern `[ancora](url)` a
 * cavallo di un marcatore, perdendo il link.
 *
 * BONUS futuro (fuori scope): gli URN che puntano ad atti che ABBIAMO
 * indicizzato (es. urn:nir:stato:codice.civile:...) potrebbero aprire la
 * nostra split-view invece di Normattiva — richiede una risoluzione
 * URN→act_ref lato client o una lookup API che oggi non esiste.
 */
export function renderNormText(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(MD_LINK)) {
    if (match.index > cursor)
      nodes.push(...renderNovellaMarkers(text.slice(cursor, match.index), cursor));
    nodes.push(
      <a
        key={`link-${match.index}`}
        href={match[2]}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline decoration-primary/35 underline-offset-2 transition-colors hover:decoration-primary"
      >
        {match[1]}
      </a>,
    );
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length || nodes.length === 0)
    nodes.push(...renderNovellaMarkers(text.slice(cursor), cursor));
  return nodes;
}

/**
 * Rileva l'articolo interamente novellato (badge «testo novellato»).
 *
 * Criterio strutturale e CONSERVATIVO, sul solo corpo dell'articolo
 * (i commi, come arrivano da /acts): il primo contenuto non vuoto apre
 * con `((` — o come comma-marcatore a sé stante (testo trimmato
 * esattamente `((`, la forma dell'art. 2-septies del codice-privacy),
 * o come `((` all'inizio assoluto del primo comma — E l'ultimo comma
 * non vuoto chiude con `))`. Gli articoli novellati solo in parte
 * (blocco `(( ))` interno) NON contano: lì parlano i marcatori inline.
 */
export function isNovellatoArticle(commi: { text: string }[]): boolean {
  const first = commi.find((c) => c.text.trim() !== "");
  if (!first) return false;
  if (first.text.trim() !== "((" && !first.text.startsWith("((")) return false;
  const last = commi.findLast((c) => c.text.trim() !== "");
  return last !== undefined && last.text.trim().endsWith("))");
}

/**
 * True se da qualche parte nell'atto compare `((`: governa la legenda
 * della convenzione in fondo al pannello (calcolata una volta per atto).
 */
export function actHasNovellaMarkers(act: {
  articles: { heading: string | null; commi: { text: string }[] }[];
}): boolean {
  return act.articles.some(
    (a) =>
      (a.heading !== null && a.heading.includes("((")) ||
      a.commi.some((c) => c.text.includes("((")),
  );
}

/**
 * Numero di comma in testa al testo: dopo whitespace (e un eventuale
 * marcatore `((` di apertura della novella) un numero — anche nelle
 * forme ordinali latine «1-bis», «2-septies» — seguito da `.` o `)`.
 */
const COMMA_SELF_NUMBER = /^\s*(?:\(\(\s*)?(\d+(?:-[a-zà-ú]+)*)\s*[.)]/iu;

/**
 * True quando il testo del comma si auto-numera con LO STESSO numero del
 * campo `comma.number` («1. In attuazione…» con number "1"): in quel caso
 * il numero in esponente del pannello duplicherebbe e va nascosto.
 * Confronto normalizzato (minuscole); numeri divergenti («3.» con number
 * "2") o testo non numerato lasciano l'esponente al suo posto.
 */
export function commaSelfNumbered(number: string, text: string): boolean {
  const match = COMMA_SELF_NUMBER.exec(text);
  return match !== null && match[1].toLowerCase() === number.trim().toLowerCase();
}

/**
 * Etichette di partizione (LIBRO/PARTE/TITOLO/CAPO/SEZIONE) seguite dal
 * loro "numero" (romano, ordinale, suffisso).
 */
const PARTITION_LABEL = /^((?:LIBRO|PARTE|TITOLO|CAPO|SEZIONE)\s+[^\s.-]+)\s*(?:-\s*)?/iu;

/**
 * Dedup conservativo dell'etichetta di partizione duplicata dal corpus.
 *
 * Negli atti recenti il convertitore Normattiva antepone un contatore
 * artificiale `CAPO N` alla partizione reale (corpus-analysis A6); quando
 * la partizione reale è essa stessa un CAPO con lo stesso numero, il
 * testo della heading duplica l'etichetta: il corpus di legge-526-1999
 * contiene letteralmente `CAPO II CAPO II DISPOSIZIONI PARTICOLARI ...`.
 *
 * La correzione è SOLO di display: il parser/chunker passa la heading
 * verbatim e cambiarlo lì altererebbe gli header dei chunk già
 * indicizzati (richiederebbe una re-indicizzazione completa). Qui si
 * collassa unicamente la ripetizione ESATTA dell'etichetta iniziale
 * (`CAPO II [- ]CAPO II ...` → `CAPO II ...`, anche con `.` dopo la
 * ripetizione); i casi in cui contatore e partizione reale differiscono
 * (`CAPO III CAPO II ...`) o non c'è ripetizione restano intatti.
 */
export function dedupPartitionLabel(label: string): string {
  const match = PARTITION_LABEL.exec(label);
  if (!match) return label;
  const prefix = match[1];
  const rest = label.slice(match[0].length);
  const repeated =
    rest.slice(0, prefix.length).toLowerCase() === prefix.toLowerCase() &&
    (rest.length === prefix.length || /[\s.-]/.test(rest[prefix.length]));
  return repeated ? rest : label;
}
