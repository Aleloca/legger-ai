"use client";

/**
 * La pagina: chat + split-view della norma (G4). Lo stato del pannello
 * (atto/articolo/comma bersaglio) vive qui; il click su un chip
 * citazione lo apre o lo ri-punta. Ogni click produce un oggetto target
 * NUOVO, così il pannello fa ripartire scroll e impulso anche quando si
 * clicca due volte la stessa citazione (il refetch resta governato dal
 * solo act_ref, più la cache di lib/api.ts).
 *
 * A pannello aperto, da lg in su il layout diventa un grid a due
 * colonne (chat ~55% / norma ~45%); sotto lg il pannello è un bottom
 * sheet sopra la chat, quindi il grid non serve.
 */

import * as React from "react";

import { ActPanel, type ActTarget } from "@/components/act-panel";
import { Chat } from "@/components/chat";
import { cn } from "@/lib/utils";

export default function Home() {
  const [target, setTarget] = React.useState<ActTarget | null>(null);
  const closePanel = React.useCallback(() => setTarget(null), []);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-b border-border px-6 py-3">
        <span className="font-display text-xl font-medium tracking-tight">
          legger<span className="text-primary">.</span>
        </span>
      </header>
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col",
          target &&
            "lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,45%)] lg:grid-rows-[minmax(0,1fr)]",
        )}
      >
        <Chat onCitationClick={setTarget} />
        {target ? <ActPanel target={target} onClose={closePanel} /> : null}
      </div>
    </div>
  );
}
