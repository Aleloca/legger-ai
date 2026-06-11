/**
 * Tipi che rispecchiano il contratto SSE di POST /chat
 * (vedi backend/legger/api/chat.py, docstring del modulo).
 *
 * Gli eventi arrivono in quest'ordine: status → sources → token*
 * (con citation subito dopo i token-marker) → done. `error` è
 * terminale: nessun `done` lo segue.
 */

/** Una provvigione consultata dal retrieval (evento `sources`). */
export interface Source {
  act_ref: string;
  article: string;
  title: string | null;
  vigenza: string | null;
  /** Frammento deep-link per la split-view (G4), es. `art-2051`. */
  anchor: string;
}

/** Esito del guardrail per un marker `[[act_ref|art.N|c.M]]` (evento `citation`). */
export interface Citation {
  marker: string;
  act_ref: string;
  article: string;
  comma: string | null;
  title: string | null;
  vigenza: string | null;
  verified: boolean;
  reason:
    | "ok"
    | "act_not_in_context"
    | "article_not_in_context"
    | "comma_not_in_context";
}

export interface DoneData {
  stop_reason: string | null;
  /** True quando il modello ha raggiunto il tetto di token (risposta a metà). */
  truncated: boolean;
  /**
   * Configurazione EFFETTIVA del turno (default riempiti; effort null se
   * omesso o non supportato dal modello) — trasparenza per i beta tester.
   */
  config?: EffectiveConfig;
}

/** Il blocco `config` dell'evento `done` (valori effettivi, mai null sui modelli). */
export interface EffectiveConfig {
  answer_model: string;
  answer_effort: string | null;
  qu_model: string;
  qu_effort: string | null;
}

/**
 * Override per-conversazione di modello/effort (fase di beta testing).
 * `null` = default del backend; inviato come `config` nel body di POST
 * /chat solo quando almeno un campo è non-default.
 */
export interface ChatConfig {
  answer_model: string | null;
  answer_effort: string | null;
  qu_model: string | null;
  qu_effort: string | null;
}

/** Una voce del catalogo modelli (GET /api/backend/chat/models). */
export interface CatalogModel {
  id: string;
  label: string;
  /** Prezzo input, USD per milione di token. */
  input_usd_mtok: number;
  /** Prezzo output, USD per milione di token. */
  output_usd_mtok: number;
  /** False (es. Haiku): il backend NON invia output_config.effort. */
  supports_effort: boolean;
}

/** Il catalogo modelli: la UI delle impostazioni si disegna DA QUI. */
export interface ModelsCatalog {
  answer: { default: string; models: CatalogModel[] };
  qu: { default: string; models: CatalogModel[] };
  effort_levels: string[];
}

/** Unione discriminata degli eventi del flusso SSE. */
export type SseEvent =
  | { event: "status"; data: { stage: string } }
  | { event: "sources"; data: { sources: Source[] } }
  | { event: "token"; data: { text: string } }
  | { event: "citation"; data: Citation }
  | { event: "done"; data: DoneData }
  | { event: "error"; data: { message: string } };

/** Un turno di conversazione, com'è tenuto nello stato React (nessuna persistenza). */
export interface ChatMessage {
  role: "user" | "assistant";
  /** Trascrizione grezza: i marker `[[...]]` restano nel testo. */
  content: string;
  /** Solo assistant: presenti dopo l'evento `sources`. */
  sources?: Source[];
  /** Solo assistant: una voce per ogni evento `citation` ricevuto. */
  citations?: Citation[];
  /** Solo assistant: true se `done` ha riportato truncated. */
  truncated?: boolean;
  /**
   * Solo assistant: true quando lo stream è concluso (done o error).
   * Il renderer mostra come testo un eventuale marker pendente in coda.
   */
  final?: boolean;
  /** Solo assistant: messaggio dell'evento `error` (terminale). */
  error?: string | null;
}
