"use client";

/**
 * La colonna chat (Task G2): stato della conversazione in React —
 * nessuna persistenza, design §3.1 — più orchestrazione del flusso SSE.
 *
 * Un turno: l'utente invia → si accodano il messaggio utente e un
 * placeholder assistant → streamChat smista gli eventi sulle callback
 * che aggiornano l'ultimo messaggio. `error` è terminale e riabilita
 * il composer; l'abort (unmount) è silenzioso.
 *
 * `config` (beta testing, da useChatConfig in page.tsx) viaggia nel
 * body di POST /chat solo quando è non-default; `configSummary` è il
 * riassunto a una riga mostrato come chip sopra il composer.
 */

import { Settings2 } from "lucide-react";
import * as React from "react";

import { Composer } from "@/components/composer";
import { MessageList } from "@/components/message-list";
import { isDefaultConfig } from "@/lib/chat-config";
import type { CitationRef } from "@/lib/render";
import { streamChat } from "@/lib/sse";
import type { ChatConfig, ChatMessage } from "@/lib/types";

export function Chat({
  onCitationClick,
  config,
  configSummary,
}: {
  /** Click su un chip-citazione: apre la split-view della norma (G4). */
  onCitationClick?: (ref: CitationRef) => void;
  /** Override modello/effort (beta testing); assente o default = niente config nel body. */
  config?: ChatConfig | null;
  /** Riassunto della config non-default per il chip sopra il composer. */
  configSummary?: string | null;
}) {
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = React.useState(false);
  // Suggerimento dall'empty state → riempie il composer (non invia).
  const [prefill, setPrefill] = React.useState<{ text: string } | null>(null);
  // "sto cercando nel corpus…": dall'invio fino al primo token. Parte
  // ottimisticamente al send (un proxy che bufferizza può consegnare
  // l'evento status insieme ai successivi) e l'evento status la conferma.
  const [searching, setSearching] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => () => abortRef.current?.abort(), []);

  /** Aggiorna l'ultimo messaggio (il placeholder assistant in streaming). */
  const patchLast = React.useCallback(
    (patch: (message: ChatMessage) => ChatMessage) => {
      setMessages((current) => [
        ...current.slice(0, -1),
        patch(current[current.length - 1]),
      ]);
    },
    [],
  );

  const send = React.useCallback(
    (text: string) => {
      const content = text.trim();
      if (!content || streaming) return;

      const turn: ChatMessage[] = [
        ...messages,
        { role: "user", content },
        { role: "assistant", content: "", citations: [] },
      ];
      setMessages(turn);
      setStreaming(true);
      setSearching(true);

      // Il payload esclude il placeholder e i turni vuoti (un assistant
      // fallito prima del primo token): il backend rifiuta content vuoto.
      const payload = turn
        .slice(0, -1)
        .filter((m) => m.content.length > 0)
        .map((m) => ({ role: m.role, content: m.content }));

      const controller = new AbortController();
      abortRef.current = controller;

      void streamChat(
        payload,
        {
          onStatus: () => setSearching(true),
          onSources: (sources) => patchLast((m) => ({ ...m, sources })),
          onToken: (text) => {
            setSearching(false);
            patchLast((m) => ({ ...m, content: m.content + text }));
          },
          onCitation: (citation) =>
            patchLast((m) => ({
              ...m,
              citations: [...(m.citations ?? []), citation],
            })),
          onDone: ({ truncated }) => {
            setSearching(false);
            setStreaming(false);
            patchLast((m) => ({ ...m, final: true, truncated }));
          },
          onError: (message) => {
            setSearching(false);
            setStreaming(false);
            patchLast((m) => ({ ...m, final: true, error: message }));
          },
        },
        {
          signal: controller.signal,
          config: config && !isDefaultConfig(config) ? config : null,
        },
      );
    },
    [messages, streaming, patchLast, config],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <MessageList
        messages={messages}
        searching={searching}
        onCitationClick={onCitationClick}
        onSuggestion={(text) => setPrefill({ text })}
      />
      {configSummary ? (
        <div className="px-6 pb-1.5">
          <div className="mx-auto w-full max-w-3xl">
            <span className="inline-flex items-center gap-1.5 rounded-sm border border-border bg-secondary px-2 py-0.5 text-[0.6875rem] tracking-wide text-muted-foreground">
              <Settings2 aria-hidden className="size-3" />
              {configSummary}
            </span>
          </div>
        </div>
      ) : null}
      <Composer onSend={send} disabled={streaming} prefill={prefill} />
    </div>
  );
}
