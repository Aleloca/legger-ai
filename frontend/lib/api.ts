/**
 * Client di GET /api/backend/acts/{act_ref} per la split-view (G4).
 *
 * Cache in-memory (Map per act_ref, vita = sessione della pagina): la
 * split-view apre e riapre gli stessi Codici, e il Codice civile pesa
 * ~2 MB di JSON — un solo fetch per atto. La cache tiene la *promise*
 * (due click ravvicinati sullo stesso atto condividono il fetch in
 * volo); una promise rigettata viene espulsa, così «Riprova» rifà
 * davvero la richiesta.
 *
 * Gli errori sono tipizzati (ActFetchError con status) e con messaggio
 * già in italiano, pronto per la card d'errore del pannello:
 * 404 → norma non trovata; 503 → il detail del backend (corpus in
 * aggiornamento, vedi backend/legger/api/acts.py); rete → invito a
 * riprovare.
 */

/** Un comma dell'articolo (number null = comma unico non numerato). */
export interface ActComma {
  number: string | null;
  text: string;
}

export interface ActArticle {
  number: string;
  heading: string | null;
  /** Gerarchia delle partizioni (Libro, Titolo, Capo…) che contengono l'articolo. */
  path: string[];
  commi: ActComma[];
  /** Id-frammento univoco nell'atto, es. `art-2051` (campo `anchor` dell'API). */
  anchor: string;
}

export interface ActDetail {
  act_ref: string;
  title: string | null;
  act_type: string;
  vigenza: string;
  collection: string;
  articles: ActArticle[];
}

export class ActFetchError extends Error {
  /** Status HTTP, o null per errori di rete. */
  readonly status: number | null;

  constructor(message: string, status: number | null) {
    super(message);
    this.name = "ActFetchError";
    this.status = status;
  }
}

const cache = new Map<string, Promise<ActDetail>>();

/** Il testo integrale dell'atto, dalla cache di sessione o dal backend. */
export function fetchAct(actRef: string): Promise<ActDetail> {
  const cached = cache.get(actRef);
  if (cached) return cached;

  const promise = fetchActUncached(actRef);
  cache.set(actRef, promise);
  // Niente cache degli errori: il retry deve rifare la richiesta.
  promise.catch(() => cache.delete(actRef));
  return promise;
}

async function fetchActUncached(actRef: string): Promise<ActDetail> {
  let response: Response;
  try {
    response = await fetch(`/api/backend/acts/${encodeURIComponent(actRef)}`);
  } catch {
    throw new ActFetchError(
      "Impossibile raggiungere il server. Verificare la connessione e riprovare.",
      null,
    );
  }

  if (!response.ok) {
    if (response.status === 404) {
      throw new ActFetchError("Norma non trovata nel corpus.", 404);
    }
    // Il 503 del backend porta un detail già leggibile (corpus in
    // aggiornamento); per gli altri status il detail è comunque la
    // migliore descrizione disponibile.
    const detail = await response
      .json()
      .then((body: { detail?: unknown }) =>
        typeof body.detail === "string" ? body.detail : null,
      )
      .catch(() => null);
    throw new ActFetchError(
      detail ?? `Errore del server (${response.status}). Riprovare.`,
      response.status,
    );
  }

  return (await response.json()) as ActDetail;
}
