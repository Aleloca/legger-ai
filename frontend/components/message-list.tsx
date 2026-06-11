"use client";

/**
 * La lista dei messaggi: utente come quieta scheda di carta allineata a
 * destra; assistant come testo editoriale a tutta larghezza, senza
 * bolla. Lo scroll resta agganciato al fondo finché il lettore non
 * risale a leggere.
 */

import * as React from "react";

import { SourcesList } from "@/components/sources-list";
import { renderAssistantText, type CitationRef } from "@/lib/render";
import type { ChatMessage } from "@/lib/types";
import { cn } from "@/lib/utils";

const STICK_THRESHOLD_PX = 96;

export function MessageList({
  messages,
  searching,
  onCitationClick,
  onSuggestion,
}: {
  messages: ChatMessage[];
  searching: boolean;
  onCitationClick?: (ref: CitationRef) => void;
  /** Click su un suggerimento dell'empty state: riempie il composer. */
  onSuggestion?: (text: string) => void;
}) {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const stickToBottom = React.useRef(true);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottom.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
  };

  React.useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages, searching]);

  return (
    <div
      ref={scrollRef}
      onScroll={onScroll}
      className="min-h-0 flex-1 overflow-y-auto px-6"
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 py-10">
        {messages.length === 0 ? (
          <EmptyState onSuggestion={onSuggestion} />
        ) : (
          <>
            {/* h1 della pagina a conversazione attiva (l'empty state ha il suo). */}
            <h1 className="sr-only">Conversazione sulla normativa</h1>
            {messages.map((message, i) => (
              <MessageRow
                key={i}
                message={message}
                searching={searching && i === messages.length - 1}
                onCitationClick={onCitationClick}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Memoizzato: i messaggi completati mantengono la stessa identità tra un
 * token e l'altro (chat.tsx riscrive solo l'ultimo), quindi durante lo
 * streaming si ri-renderizza soltanto il messaggio in corso.
 */
const MessageRow = React.memo(function MessageRow({
  message,
  searching,
  onCitationClick,
}: {
  message: ChatMessage;
  searching: boolean;
  onCitationClick?: (ref: CitationRef) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-md border border-border bg-card px-4 py-3 text-[0.9375rem] leading-relaxed whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }
  return (
    <AssistantMessage
      message={message}
      searching={searching}
      onCitationClick={onCitationClick}
    />
  );
});

function AssistantMessage({
  message,
  searching,
  onCitationClick,
}: {
  message: ChatMessage;
  searching: boolean;
  onCitationClick?: (ref: CitationRef) => void;
}) {
  const rendered = React.useMemo(
    () =>
      renderAssistantText(
        message.content,
        message.citations ?? [],
        onCitationClick,
        message.final ?? false,
      ),
    [message.content, message.citations, message.final, onCitationClick],
  );

  return (
    // aria-live polite: gli screen reader annunciano la risposta mentre
    // arriva in streaming, senza interrompere la lettura in corso.
    <div aria-live="polite" className="flex flex-col gap-3">
      {searching && message.content.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">
          sto cercando nel corpus
          <span aria-hidden className="searching-ellipsis" />
        </p>
      ) : null}

      {message.content.length > 0 ? (
        <div className="text-[0.9375rem] leading-7">{rendered}</div>
      ) : null}

      {message.error ? (
        <div className="rounded-md border border-primary/20 bg-accent px-4 py-3 text-sm leading-relaxed text-accent-foreground">
          {message.error}
        </div>
      ) : null}

      {message.truncated ? (
        <p className="text-xs text-muted-foreground italic">
          risposta troncata
        </p>
      ) : null}

      {message.sources && message.sources.length > 0 ? (
        <SourcesList sources={message.sources} onSourceClick={onCitationClick} />
      ) : null}
    </div>
  );
}

/** Domande d'esempio: il click riempie il composer (non invia). */
const SUGGESTIONS = [
  "Cosa prevede l'art. 2051 c.c.?",
  "Obblighi del datore di lavoro nel D.Lgs. 81/2008",
  "Differenza tra dolo eventuale e colpa cosciente",
];

function EmptyState({ onSuggestion }: { onSuggestion?: (text: string) => void }) {
  return (
    <div className="flex flex-col items-start gap-4 pt-[14vh]">
      <span
        aria-hidden
        className="animate-in fade-in slide-in-from-bottom-2 block h-px w-12 bg-primary/60 duration-700 fill-mode-both"
      />
      <h1 className="animate-in fade-in slide-in-from-bottom-2 font-display text-3xl font-medium tracking-tight delay-100 duration-700 fill-mode-both">
        La normativa italiana, letta con rigore
      </h1>
      <p className="animate-in fade-in slide-in-from-bottom-2 max-w-prose text-sm leading-relaxed text-muted-foreground delay-200 duration-700 fill-mode-both">
        Poni una domanda sulla legislazione italiana: la risposta è fondata
        sui testi vigenti, con citazioni puntuali agli articoli consultati.
      </p>
      <div className="mt-4 flex w-full max-w-prose flex-col gap-2">
        {SUGGESTIONS.map((text, i) => (
          <button
            key={text}
            type="button"
            onClick={onSuggestion && (() => onSuggestion(text))}
            className={cn(
              "animate-in fade-in slide-in-from-bottom-2 rounded-md border border-border bg-card px-4 py-3 text-left text-sm leading-relaxed duration-700 fill-mode-both transition-colors hover:border-primary/30 hover:bg-accent",
              ["delay-300", "delay-[450ms]", "delay-[600ms]"][i],
            )}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}
