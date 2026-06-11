"use client";

/**
 * Parametri sperimentali della chat (fase di beta testing): quale
 * modello genera le risposte, quale fa la comprensione della domanda,
 * e il livello di effort di ciascuno — per giocare con i parametri e
 * confrontare gli esiti.
 *
 * Affordance silenziosa nell'header della chat (ingranaggio +
 * «Parametri»); il pannello segue il pattern di ActPanel: popover
 * ancorato al bottone da lg in su, bottom sheet con scrim sotto. Le
 * voci dei select arrivano TUTTE dal catalogo del backend
 * (GET /api/backend/chat/models, via lib/chat-config): nessuna lista
 * duplicata qui. Il select dell'effort è disabilitato — con la nota
 * «non disponibile per Haiku» — quando il modello scelto non supporta
 * output_config.effort; la normalizzazione in useChatConfig azzera
 * comunque l'effort non supportato.
 */

import { Settings2 } from "lucide-react";
import * as React from "react";

import { effectiveModel } from "@/lib/chat-config";
import type { CatalogModel, ChatConfig, ModelsCatalog } from "@/lib/types";
import { cn } from "@/lib/utils";

const EFFORT_LABELS: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  max: "Max",
};

export function ChatSettings({
  config,
  catalog,
  onChange,
}: {
  config: ChatConfig;
  /** Null finché il catalogo non è arrivato: il bottone resta disabilitato. */
  catalog: ModelsCatalog | null;
  onChange: (next: ChatConfig) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const close = React.useCallback(() => setOpen(false), []);

  // Esc chiude il pannello (solo quando è aperto).
  React.useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={catalog === null}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium tracking-wide text-muted-foreground transition-colors [font-variant-caps:small-caps] hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
      >
        <Settings2 aria-hidden className="size-3.5" />
        Parametri
      </button>

      {open && catalog ? (
        <>
          {/* Scrim: visibile (e modale) sotto lg, trasparente da lg in su —
              in entrambi i casi il click fuori chiude. */}
          <div
            aria-hidden
            onClick={close}
            className="animate-in fade-in fixed inset-0 z-40 bg-foreground/25 duration-300 lg:bg-transparent lg:animate-none"
          />
          <div
            role="dialog"
            aria-label="Parametri sperimentali della chat"
            className={cn(
              // mobile: bottom sheet (pattern ActPanel)
              "animate-in slide-in-from-bottom fixed inset-x-0 bottom-0 z-50 flex max-h-[85dvh] flex-col rounded-t-xl border-t border-border bg-popover shadow-[0_-8px_32px_color-mix(in_srgb,var(--color-foreground)_18%,transparent)] duration-300",
              // desktop: popover ancorato al bottone
              "lg:absolute lg:inset-x-auto lg:top-full lg:right-0 lg:bottom-auto lg:mt-2 lg:w-88 lg:animate-none lg:rounded-md lg:border lg:shadow-[0_8px_24px_color-mix(in_srgb,var(--color-foreground)_12%,transparent)]",
            )}
          >
            {/* Maniglia visiva del bottom sheet */}
            <div aria-hidden className="flex justify-center pt-2 pb-1 lg:hidden">
              <span className="h-1 w-10 rounded-full bg-border" />
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 pt-2 pb-4 lg:pt-4">
              <ConfigSection
                title="Modello risposte"
                idPrefix="answer"
                section={catalog.answer}
                effortLevels={catalog.effort_levels}
                model={config.answer_model}
                effort={config.answer_effort}
                onModelChange={(model) =>
                  onChange({ ...config, answer_model: model })
                }
                onEffortChange={(effort) =>
                  onChange({ ...config, answer_effort: effort })
                }
              />
              <div className="my-4 border-t border-border" />
              <ConfigSection
                title="Comprensione della domanda"
                idPrefix="qu"
                section={catalog.qu}
                effortLevels={catalog.effort_levels}
                model={config.qu_model}
                effort={config.qu_effort}
                onModelChange={(model) => onChange({ ...config, qu_model: model })}
                onEffortChange={(effort) =>
                  onChange({ ...config, qu_effort: effort })
                }
              />
            </div>

            <footer className="border-t border-border px-5 py-3">
              <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                Impostazioni sperimentali per la fase di test. I costi indicati
                sono per milione di token.
              </p>
            </footer>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** Una sezione del pannello: select del modello (+ prezzo) e dell'effort. */
function ConfigSection({
  title,
  idPrefix,
  section,
  effortLevels,
  model,
  effort,
  onModelChange,
  onEffortChange,
}: {
  title: string;
  idPrefix: string;
  section: ModelsCatalog["answer"];
  effortLevels: string[];
  model: string | null;
  effort: string | null;
  onModelChange: (model: string | null) => void;
  onEffortChange: (effort: string | null) => void;
}) {
  const selected = effectiveModel(section, model);
  const modelId = `${idPrefix}-model`;
  const effortId = `${idPrefix}-effort`;
  return (
    <section>
      <h3 className="text-[0.6875rem] font-medium tracking-[0.08em] text-muted-foreground [font-variant-caps:small-caps]">
        {title}
      </h3>

      <div className="mt-2 space-y-2.5">
        <div>
          <div className="flex items-center justify-between gap-3">
            <label htmlFor={modelId} className="text-xs text-foreground">
              Modello
            </label>
            {selected ? <PriceBadge model={selected} /> : null}
          </div>
          <select
            id={modelId}
            value={model ?? section.default}
            onChange={(event) =>
              onModelChange(
                event.target.value === section.default
                  ? null
                  : event.target.value,
              )
            }
            className="mt-1 w-full rounded-md border border-input bg-card px-2.5 py-1.5 text-sm focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 focus-visible:outline-none"
          >
            {section.models.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
                {entry.id === section.default ? " (default)" : ""}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor={effortId} className="text-xs text-foreground">
            Effort
          </label>
          <select
            id={effortId}
            value={effort ?? ""}
            disabled={!selected?.supports_effort}
            onChange={(event) => onEffortChange(event.target.value || null)}
            className="mt-1 w-full rounded-md border border-input bg-card px-2.5 py-1.5 text-sm focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 focus-visible:outline-none disabled:opacity-50"
          >
            <option value="">Default</option>
            {effortLevels.map((level) => (
              <option key={level} value={level}>
                {EFFORT_LABELS[level] ?? level}
              </option>
            ))}
          </select>
          {!selected?.supports_effort ? (
            <p className="mt-1 text-[0.6875rem] text-muted-foreground">
              non disponibile per {selected?.label ?? "questo modello"}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}

/** Prezzo input/output del modello selezionato, in USD per Mtok. */
function PriceBadge({ model }: { model: CatalogModel }) {
  return (
    <span className="rounded-sm border border-border bg-secondary px-1.5 py-px text-[0.6875rem] tracking-wide whitespace-nowrap text-muted-foreground">
      ≈ ${formatPrice(model.input_usd_mtok)}/${formatPrice(model.output_usd_mtok)} per Mtok
    </span>
  );
}

function formatPrice(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}
