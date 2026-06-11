"use client";

/**
 * Feedback 👍/👎 a piè di ogni risposta COMPLETATA (mai in streaming,
 * mai sui turni in errore): due ghost button silenziosi, in tono muto
 * che vira al bordeaux al passaggio, coerenti con il registro
 * editoriale di sources-list.
 *
 * Sul 👎 si apre un input a riga singola «Cosa non andava? (opzionale)»;
 * l'invio (con o senza motivo) fa POST /api/backend/feedback con
 * {rating, reason?, question, answer, citations, config} — la config è
 * quella EFFETTIVA del turno (evento `done`, vedi chat.tsx). Un solo
 * feedback per messaggio (stato locale): al successo i bottoni lasciano
 * il posto a «Grazie per il feedback.»; al fallimento un testo d'errore
 * discreto e i bottoni si riabilitano.
 *
 * La nota privacy compare in tono minuscolo accanto ai bottoni solo la
 * PRIMA volta per sessione (flag in sessionStorage).
 */

import { ThumbsDown, ThumbsUp } from "lucide-react";
import * as React from "react";

import type { Citation, EffectiveConfig } from "@/lib/types";
import { cn } from "@/lib/utils";

export const PRIVACY_NOTICE_KEY = "legger.feedbackNoticeShown";

const PRIVACY_NOTICE =
  "Il feedback salva domanda e risposta per migliorare il servizio.";

const ERROR_TEXT = "Invio non riuscito. Riprova.";

/** Reso visibile al posto dei bottoni dopo l'invio riuscito. */
const THANKS_TEXT = "Grazie per il feedback.";

type Phase = "idle" | "reason" | "sending" | "sent";

/**
 * True (e marca la sessione) solo per la prima istanza che lo chiede:
 * la nota privacy si mostra una volta per sessione, poi tace.
 */
function claimPrivacyNotice(): boolean {
  try {
    if (window.sessionStorage.getItem(PRIVACY_NOTICE_KEY)) return false;
    window.sessionStorage.setItem(PRIVACY_NOTICE_KEY, "1");
    return true;
  } catch {
    return false; // storage negato: meglio nessuna nota che una a ogni messaggio
  }
}

export function FeedbackButtons({
  question,
  answer,
  citations,
  config,
}: {
  /** Il turno utente che ha prodotto questa risposta. */
  question: string;
  /** Trascrizione grezza della risposta (marker inclusi). */
  answer: string;
  /** Le citazioni del messaggio (eventi `citation`). */
  citations?: Citation[];
  /** La config EFFETTIVA del turno (evento `done`). */
  config?: EffectiveConfig;
}) {
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [reason, setReason] = React.useState("");
  const [failed, setFailed] = React.useState(false);
  // Lazy initializer: deciso una volta per messaggio, al primo render.
  const [showNotice] = React.useState(claimPrivacyNotice);

  const send = async (rating: 1 | -1, reasonText?: string) => {
    setPhase("sending");
    setFailed(false);
    const trimmed = reasonText?.trim();
    const body: Record<string, unknown> = {
      rating,
      question,
      answer,
      citations: citations ?? [],
    };
    if (trimmed) body.reason = trimmed;
    if (config) body.config = config;
    try {
      const response = await fetch("/api/backend/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(`feedback ${response.status}`);
      setPhase("sent");
    } catch {
      // Bottoni riabilitati: il 👎 torna sull'input col motivo intatto.
      setFailed(true);
      setPhase(rating === -1 ? "reason" : "idle");
    }
  };

  if (phase === "sent") {
    return <p className="text-xs text-muted-foreground">{THANKS_TEXT}</p>;
  }

  const disabled = phase === "sending";

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="Risposta utile"
          disabled={disabled}
          onClick={() => void send(1)}
          className={cn(
            "rounded-sm p-1 text-muted-foreground transition-colors",
            "hover:bg-accent hover:text-primary disabled:opacity-50",
          )}
        >
          <ThumbsUp aria-hidden className="size-3.5" />
        </button>
        <button
          type="button"
          aria-label="Risposta non utile"
          aria-expanded={phase === "reason"}
          disabled={disabled}
          onClick={() => setPhase(phase === "reason" ? "idle" : "reason")}
          className={cn(
            "rounded-sm p-1 text-muted-foreground transition-colors",
            "hover:bg-accent hover:text-primary disabled:opacity-50",
            phase === "reason" && "bg-accent text-primary",
          )}
        >
          <ThumbsDown aria-hidden className="size-3.5" />
        </button>
        {showNotice ? (
          <span className="ml-1.5 text-[0.6875rem] text-muted-foreground/80">
            {PRIVACY_NOTICE}
          </span>
        ) : null}
        {failed ? (
          <span className="ml-1.5 text-[0.6875rem] text-destructive">
            {ERROR_TEXT}
          </span>
        ) : null}
      </div>

      {phase === "reason" || (phase === "sending" && reason) ? (
        <form
          className="flex max-w-md items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send(-1, reason);
          }}
        >
          <input
            type="text"
            value={reason}
            disabled={disabled}
            autoFocus
            maxLength={2000}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Cosa non andava? (opzionale)"
            aria-label="Cosa non andava? (opzionale)"
            className={cn(
              "flex-1 rounded-sm border border-border bg-card px-2 py-1 text-xs",
              "placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1",
              "focus:ring-ring/40 disabled:opacity-50",
            )}
          />
          <button
            type="submit"
            // Distinto dal bottone «Invia» del composer per gli screen reader.
            aria-label="Invia il feedback"
            disabled={disabled}
            className={cn(
              "rounded-sm border border-border px-2 py-1 text-xs text-muted-foreground",
              "transition-colors hover:border-primary/30 hover:text-primary",
              "disabled:opacity-50",
            )}
          >
            Invia
          </button>
        </form>
      ) : null}
    </div>
  );
}
