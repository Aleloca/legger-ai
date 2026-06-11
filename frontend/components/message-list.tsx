"use client";

/**
 * La lista dei messaggi: utente come quieta scheda di carta allineata a
 * destra; assistant come testo editoriale a tutta larghezza, senza
 * bolla. Lo scroll resta agganciato al fondo finché il lettore non
 * risale a leggere.
 */

import * as React from "react";

import { renderAssistantText } from "@/lib/render";
import type { ChatMessage } from "@/lib/types";

const STICK_THRESHOLD_PX = 96;

export function MessageList({
  messages,
  searching,
}: {
  messages: ChatMessage[];
  searching: boolean;
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
          <EmptyState />
        ) : (
          messages.map((message, i) => (
            <MessageRow
              key={i}
              message={message}
              searching={searching && i === messages.length - 1}
            />
          ))
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
}: {
  message: ChatMessage;
  searching: boolean;
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
  return <AssistantMessage message={message} searching={searching} />;
});

function AssistantMessage({
  message,
  searching,
}: {
  message: ChatMessage;
  searching: boolean;
}) {
  // G4 cablerà qui l'apertura della split-view; oggi i chip sono no-op.
  const rendered = React.useMemo(
    () => renderAssistantText(message.content, message.citations ?? []),
    [message.content, message.citations],
  );

  return (
    <div className="flex flex-col gap-3">
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
        // Segnaposto: G5 renderizza l'elenco completo delle fonti.
        <p className="border-t border-border pt-2 text-xs text-muted-foreground">
          {message.sources.length}{" "}
          {message.sources.length === 1 ? "fonte consultata" : "fonti consultate"}
        </p>
      ) : null}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-start gap-4 pt-[18vh]">
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
    </div>
  );
}
