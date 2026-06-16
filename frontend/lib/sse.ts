/**
 * Client SSE basato su fetch per POST /api/backend/chat.
 *
 * EventSource non supporta POST, quindi il flusso `text/event-stream`
 * viene letto a mano dal body della risposta: framing `event:`/`data:`,
 * data multi-linea (riunite con `\n`), righe-commento (`:`) ignorate,
 * frame separati da riga vuota. Il default SSE (`event: message`) non
 * fa parte del contratto e viene scartato, come ogni frame con JSON
 * malformato.
 *
 * Terminazione: `done` chiude il flusso con successo; `error` è
 * terminale (nessun `done` segue) e la lettura si interrompe. Se la
 * connessione cade senza un evento terminale, viene invocato comunque
 * `onError`. L'abort via AbortSignal è silenzioso (nessuna callback).
 */

import type { ChatConfig, Citation, DoneData, Source, SseEvent } from "@/lib/types";

export interface StreamHandlers {
  onStatus?: (stage: string) => void;
  onSources?: (sources: Source[]) => void;
  onToken?: (text: string) => void;
  onCitation?: (citation: Citation) => void;
  onDone?: (data: DoneData) => void;
  onError?: (message: string) => void;
}

export interface StreamOptions {
  signal?: AbortSignal;
  /** Override dell'endpoint (default: il rewrite proxy di Next). */
  endpoint?: string;
  /**
   * Override per-conversazione di modello/effort (beta testing): incluso
   * come `config` nel body quando presente; assente = default backend.
   */
  config?: ChatConfig | null;
}

const DEFAULT_ENDPOINT = "/api/backend/chat";

/** Messaggio user-safe quando il problema è di rete, non del backend. */
const NETWORK_ERROR_MESSAGE =
  "Impossibile contattare il servizio. Verifica la connessione e riprova.";

/**
 * Copy UI dei rate-limit, indicizzata per `code` del backend (HTTP 429).
 * Il `message` del server è ignorato: queste stringhe sono l'unica fonte
 * di verità lato UI. Codici sconosciuti → NETWORK_ERROR_MESSAGE.
 */
const RATE_LIMIT_MESSAGES: Record<string, string> = {
  daily_limit:
    "Hai raggiunto il limite di richieste giornaliere per questa demo. Riprova domani.",
  concurrency_limit:
    "Hai già una richiesta in corso. Attendi che finisca prima di inviarne un'altra.",
  unavailable:
    "Servizio temporaneamente non disponibile. Riprova tra qualche istante.",
};

/**
 * Guardie minime sulla shape del payload, per evento: un frame con JSON
 * valido ma payload fuori contratto viene scartato come quelli malformati,
 * così le callback ricevono solo dati tipizzati davvero.
 */
const PAYLOAD_GUARDS: Record<SseEvent["event"], (data: unknown) => boolean> = {
  status: (d) => isRecord(d) && typeof d.stage === "string",
  sources: (d) => isRecord(d) && Array.isArray(d.sources),
  token: (d) => isRecord(d) && typeof d.text === "string",
  citation: (d) =>
    isRecord(d) && typeof d.marker === "string" && typeof d.verified === "boolean",
  done: (d) => isRecord(d) && typeof d.truncated === "boolean",
  error: (d) => isRecord(d) && typeof d.message === "string",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Decodifica un frame SSE accumulato in un evento tipizzato (o null se estraneo). */
function decodeFrame(eventName: string, dataLines: string[]): SseEvent | null {
  if (dataLines.length === 0) return null;
  const guard = PAYLOAD_GUARDS[eventName as SseEvent["event"]];
  if (!guard) return null; // evento fuori contratto
  try {
    const data = JSON.parse(dataLines.join("\n"));
    if (!guard(data)) return null; // payload fuori shape: frame scartato
    return { event: eventName, data } as SseEvent;
  } catch {
    return null; // JSON malformato: frame scartato
  }
}

/**
 * Parser incrementale del framing text/event-stream.
 * Esportato per i test; `feed` restituisce gli eventi completati dal chunk.
 */
export class SseParser {
  private buffer = "";
  private eventName = "message";
  private dataLines: string[] = [];

  feed(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];
    let newlineAt: number;
    while ((newlineAt = this.buffer.indexOf("\n")) !== -1) {
      let line = this.buffer.slice(0, newlineAt);
      this.buffer = this.buffer.slice(newlineAt + 1);
      if (line.endsWith("\r")) line = line.slice(0, -1);
      const event = this.processLine(line);
      if (event) events.push(event);
    }
    return events;
  }

  private processLine(line: string): SseEvent | null {
    if (line === "") {
      // Riga vuota: dispatch del frame accumulato.
      const event = decodeFrame(this.eventName, this.dataLines);
      this.eventName = "message";
      this.dataLines = [];
      return event;
    }
    if (line.startsWith(":")) return null; // commento
    const colonAt = line.indexOf(":");
    const field = colonAt === -1 ? line : line.slice(0, colonAt);
    let value = colonAt === -1 ? "" : line.slice(colonAt + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.eventName = value;
    else if (field === "data") this.dataLines.push(value);
    // altri campi (id, retry, …) ignorati
    return null;
  }
}

/**
 * Invia la conversazione a POST /chat e smista gli eventi SSE sulle
 * callback. Risolve quando il flusso termina (done, error o abort).
 */
export async function streamChat(
  messages: { role: "user" | "assistant"; content: string }[],
  handlers: StreamHandlers,
  options: StreamOptions = {},
): Promise<void> {
  const { signal, endpoint = DEFAULT_ENDPOINT, config = null } = options;

  let response: Response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config ? { messages, config } : { messages }),
      signal,
    });
  } catch (err) {
    if (isAbort(err)) return;
    handlers.onError?.(NETWORK_ERROR_MESSAGE);
    return;
  }

  if (response.status === 429) {
    const body = await response.json().catch(() => null);
    const message =
      RATE_LIMIT_MESSAGES[(body as { code?: string } | null)?.code as string] ??
      NETWORK_ERROR_MESSAGE;
    handlers.onError?.(message);
    return;
  }

  if (!response.ok || response.body === null) {
    handlers.onError?.(NETWORK_ERROR_MESSAGE);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  let terminal = false;

  const dispatch = (event: SseEvent): boolean => {
    switch (event.event) {
      case "status":
        handlers.onStatus?.(event.data.stage);
        return false;
      case "sources":
        handlers.onSources?.(event.data.sources);
        return false;
      case "token":
        handlers.onToken?.(event.data.text);
        return false;
      case "citation":
        handlers.onCitation?.(event.data);
        return false;
      case "done":
        handlers.onDone?.(event.data);
        return true;
      case "error":
        handlers.onError?.(event.data.message);
        return true;
    }
  };

  try {
    while (!terminal) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of parser.feed(decoder.decode(value, { stream: true }))) {
        if (dispatch(event)) {
          terminal = true;
          break; // error/done sono terminali: niente altri eventi
        }
      }
    }
  } catch (err) {
    if (isAbort(err)) return;
    handlers.onError?.(NETWORK_ERROR_MESSAGE);
    return;
  } finally {
    reader.cancel().catch(() => {});
  }

  if (!terminal) {
    // Flusso chiuso senza done né error: connessione caduta a metà.
    if (signal?.aborted) return;
    handlers.onError?.(NETWORK_ERROR_MESSAGE);
  }
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}
