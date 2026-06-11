/**
 * Configurazione per-conversazione di modello/effort (fase di beta
 * testing): catalogo, persistenza e validazione.
 *
 * Il catalogo arriva da GET /api/backend/chat/models (un solo fetch per
 * sessione, stessa cache-di-promise di lib/api.ts) ed è la UNICA fonte
 * dei modelli ammessi: la scelta dell'utente è persistita in
 * localStorage (`legger.chatConfig`) e RIVALIDATA contro il catalogo ad
 * ogni caricamento — valori sconosciuti (modello rimosso, effort
 * inventato) vengono scartati in silenzio, e l'effort decade quando il
 * modello scelto non lo supporta (es. Haiku). I default del backend
 * sono rappresentati da `null`: una config tutta-null non viene inviata
 * affatto nel body di POST /chat.
 */

import * as React from "react";

import type { CatalogModel, ChatConfig, ModelsCatalog } from "@/lib/types";

export const STORAGE_KEY = "legger.chatConfig";

export const EMPTY_CONFIG: ChatConfig = {
  answer_model: null,
  answer_effort: null,
  qu_model: null,
  qu_effort: null,
};

/** True quando ogni campo è al default del backend (config da NON inviare). */
export function isDefaultConfig(config: ChatConfig): boolean {
  return (
    config.answer_model === null &&
    config.answer_effort === null &&
    config.qu_model === null &&
    config.qu_effort === null
  );
}

let catalogPromise: Promise<ModelsCatalog> | null = null;

/** Il catalogo modelli, dalla cache di sessione o dal backend. */
export function fetchModelsCatalog(): Promise<ModelsCatalog> {
  if (catalogPromise) return catalogPromise;
  const promise = fetch("/api/backend/chat/models").then((response) => {
    if (!response.ok) {
      throw new Error(`catalogo modelli non disponibile (${response.status})`);
    }
    return response.json() as Promise<ModelsCatalog>;
  });
  catalogPromise = promise;
  // Niente cache degli errori: il prossimo open del pannello riprova.
  promise.catch(() => {
    catalogPromise = null;
  });
  return promise;
}

/** Reset della cache del catalogo — SOLO per i test. */
export function clearCatalogCache(): void {
  catalogPromise = null;
}

function findModel(
  section: ModelsCatalog["answer"],
  id: string | null,
): CatalogModel | null {
  if (id === null) return null;
  return section.models.find((m) => m.id === id) ?? null;
}

/** La voce di catalogo del modello effettivo (override o default di sezione). */
export function effectiveModel(
  section: ModelsCatalog["answer"],
  id: string | null,
): CatalogModel | null {
  return findModel(section, id ?? section.default);
}

function normalizeSection(
  section: ModelsCatalog["answer"],
  effortLevels: string[],
  model: unknown,
  effort: unknown,
): { model: string | null; effort: string | null } {
  // Modello: deve esistere nel catalogo; il default si normalizza a null.
  let validModel: string | null = null;
  if (typeof model === "string" && model !== section.default) {
    validModel = findModel(section, model)?.id ?? null;
  }
  // Effort: livello noto E supportato dal modello effettivo (Haiku: mai).
  let validEffort: string | null = null;
  if (typeof effort === "string" && effortLevels.includes(effort)) {
    const info = effectiveModel(section, validModel);
    if (info?.supports_effort) validEffort = effort;
  }
  return { model: validModel, effort: validEffort };
}

/**
 * Valida un oggetto (lo stato della UI o il JSON da localStorage) contro
 * il catalogo: tutto ciò che non è ammesso decade a null (default).
 */
export function normalizeConfig(
  raw: unknown,
  catalog: ModelsCatalog,
): ChatConfig {
  if (typeof raw !== "object" || raw === null) return EMPTY_CONFIG;
  const record = raw as Record<string, unknown>;
  const answer = normalizeSection(
    catalog.answer,
    catalog.effort_levels,
    record.answer_model,
    record.answer_effort,
  );
  const qu = normalizeSection(
    catalog.qu,
    catalog.effort_levels,
    record.qu_model,
    record.qu_effort,
  );
  return {
    answer_model: answer.model,
    answer_effort: answer.effort,
    qu_model: qu.model,
    qu_effort: qu.effort,
  };
}

function loadStoredConfig(catalog: ModelsCatalog): ChatConfig {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return EMPTY_CONFIG;
    return normalizeConfig(JSON.parse(raw), catalog);
  } catch {
    return EMPTY_CONFIG; // storage negato o JSON corrotto: default
  }
}

function storeConfig(config: ChatConfig): void {
  try {
    if (isDefaultConfig(config)) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
    }
  } catch {
    // storage non disponibile: la config vive solo nello stato React
  }
}

/**
 * Riassunto a una riga per il chip vicino al composer — null quando la
 * config è tutta-default (nessun chip). Es.:
 * «Opus 4.8 · effort max — QU: Haiku 4.5».
 */
export function configSummary(
  config: ChatConfig,
  catalog: ModelsCatalog | null,
): string | null {
  if (catalog === null || isDefaultConfig(config)) return null;
  const answer = effectiveModel(catalog.answer, config.answer_model);
  const qu = effectiveModel(catalog.qu, config.qu_model);
  let summary = answer?.label ?? (config.answer_model ?? catalog.answer.default);
  if (config.answer_effort) summary += ` · effort ${config.answer_effort}`;
  summary += ` — QU: ${qu?.label ?? (config.qu_model ?? catalog.qu.default)}`;
  if (config.qu_effort) summary += ` · effort ${config.qu_effort}`;
  return summary;
}

/**
 * Stato della configurazione: carica il catalogo (una volta), rilegge la
 * scelta persistita validandola, e persiste ogni modifica normalizzata.
 */
export function useChatConfig(): {
  config: ChatConfig;
  setConfig: (next: ChatConfig) => void;
  catalog: ModelsCatalog | null;
} {
  const [catalog, setCatalog] = React.useState<ModelsCatalog | null>(null);
  const [config, setConfigState] = React.useState<ChatConfig>(EMPTY_CONFIG);

  React.useEffect(() => {
    let cancelled = false;
    fetchModelsCatalog().then(
      (loaded) => {
        if (cancelled) return;
        setCatalog(loaded);
        setConfigState(loadStoredConfig(loaded));
      },
      () => {
        // Catalogo non raggiungibile: si resta sui default (niente panel
        // utilizzabile, la chat funziona comunque senza config).
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const setConfig = React.useCallback(
    (next: ChatConfig) => {
      const normalized = catalog ? normalizeConfig(next, catalog) : next;
      setConfigState(normalized);
      storeConfig(normalized);
    },
    [catalog],
  );

  return { config, setConfig, catalog };
}
