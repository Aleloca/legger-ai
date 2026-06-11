/**
 * Splitter puro del testo assistant in segmenti testo / marker / pending.
 *
 * Specchia il contratto del backend (legger/chat/stream.py): un marker è
 * una coppia `[[...]]` nel formato stretto `[[slug|art.N]]` oppure
 * `[[slug|art.N|c.M]]` (slug minuscolo `[a-z0-9-]`). Qualunque altra
 * coppia `[[...]]` è testo e resta fusa nel run di testo circostante,
 * così il markdown la attraversa intatta.
 *
 * Streaming: il testo accumulato può terminare con un marker incompleto
 * (`[[codice-` in coda). Finché il frammento è ancora un prefisso
 * plausibile del formato, viene emesso un segmento `pending` (che il
 * renderer tiene invisibile); al parse successivo, con più testo, si
 * risolve in marker oppure decade a testo. Un `[[` che non può più
 * diventare marker (spazi, maiuscole, troppi campi, oltre il cap) decade
 * subito a testo, senza nascondere un eventuale `[[` plausibile più
 * avanti.
 */

export type Segment =
  | { type: "text"; value: string }
  | {
      type: "marker";
      /** Il marker grezzo, parentesi comprese (chiave di match con Citation.marker). */
      marker: string;
      actRef: string;
      article: string;
      comma: string | null;
    }
  | {
      type: "pending";
      /** Il frammento grezzo `[[...` in coda (reso come testo a fine stream). */
      value: string;
    };

/** Formato stretto del contratto (stesso regex del backend). */
const MARKER_RE = /^\[\[([a-z0-9-]+)\|art\.([^|[\]\s]+)(?:\|c\.([^|[\]\s]+))?\]\]$/;

/** Oltre questo, un `[[` aperto era un falso allarme (i marker reali sono <60 char). */
const MARKER_BUFFER_CAP = 200;

const OPEN = "[[";
const CLOSE = "]]";

/** Un valore di campo (dopo `art.` / `c.`) ancora valido come prefisso. */
const FIELD_VALUE_RE = /^[^|[\]\s]*$/;

/**
 * True se `partial` (che inizia con `[[` e non contiene `]]`) è ancora un
 * prefisso plausibile di un marker valido.
 */
function isMarkerPrefix(partial: string): boolean {
  if (partial.length > MARKER_BUFFER_CAP) return false;
  let body = partial.slice(OPEN.length);
  // metà della chiusura `]]` già arrivata
  if (body.endsWith("]")) body = body.slice(0, -1);
  const parts = body.split("|");
  if (parts.length > 3) return false;
  if (!/^[a-z0-9-]*$/.test(parts[0])) return false;
  const fieldOk = (part: string, keyword: string) =>
    keyword.startsWith(part) ||
    (part.startsWith(keyword) && FIELD_VALUE_RE.test(part.slice(keyword.length)));
  if (parts.length >= 2 && !fieldOk(parts[1], "art.")) return false;
  if (parts.length === 3 && !fieldOk(parts[2], "c.")) return false;
  return true;
}

/** Scompone il testo in segmenti; `pending` può comparire solo in coda. */
export function parseMarkers(text: string): Segment[] {
  const segments: Segment[] = [];
  let buf = ""; // testo accumulato (incluse coppie malformate)

  const flushText = () => {
    if (buf) {
      segments.push({ type: "text", value: buf });
      buf = "";
    }
  };

  let i = 0;
  while (i < text.length) {
    const open = text.indexOf(OPEN, i);
    if (open === -1) {
      buf += text.slice(i);
      break;
    }
    buf += text.slice(i, open);

    const close = text.indexOf(CLOSE, open + OPEN.length);
    if (close === -1) {
      // Nessuna chiusura nel testo: o pending (prefisso plausibile in
      // coda) o testo. Un `[[` implausibile viene scavalcato e la
      // scansione riprende subito dopo, per non inghiottire un `[[`
      // plausibile più avanti.
      if (isMarkerPrefix(text.slice(open))) {
        flushText();
        segments.push({ type: "pending", value: text.slice(open) });
        return segments;
      }
      buf += OPEN;
      i = open + OPEN.length;
      continue;
    }

    const candidate = text.slice(open, close + CLOSE.length);
    const match = MARKER_RE.exec(candidate);
    if (match) {
      flushText();
      segments.push({
        type: "marker",
        marker: candidate,
        actRef: match[1],
        article: match[2],
        comma: match[3] ?? null,
      });
    } else {
      // Coppia malformata: resta testo (come nel backend, l'intera
      // coppia fluisce a valle senza evento citation).
      buf += candidate;
    }
    i = close + CLOSE.length;
  }

  flushText();
  return segments;
}
