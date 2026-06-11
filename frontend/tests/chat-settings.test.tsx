/**
 * Test del pannello «Parametri» (beta testing) e di useChatConfig:
 * rendering DAL catalogo finto (fetch stubbato), effort disabilitato
 * per Haiku, persistenza in localStorage con rivalidazione al
 * caricamento (valori sconosciuti scartati).
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatSettings } from "@/components/chat-settings";
import {
  clearCatalogCache,
  configSummary,
  STORAGE_KEY,
  useChatConfig,
} from "@/lib/chat-config";
import type { ModelsCatalog } from "@/lib/types";

const CATALOG: ModelsCatalog = {
  answer: {
    default: "claude-sonnet-4-6",
    models: [
      {
        id: "claude-haiku-4-5",
        label: "Haiku 4.5",
        input_usd_mtok: 1,
        output_usd_mtok: 5,
        supports_effort: false,
      },
      {
        id: "claude-sonnet-4-6",
        label: "Sonnet 4.6",
        input_usd_mtok: 3,
        output_usd_mtok: 15,
        supports_effort: true,
      },
      {
        id: "claude-opus-4-8",
        label: "Opus 4.8",
        input_usd_mtok: 5,
        output_usd_mtok: 25,
        supports_effort: true,
      },
    ],
  },
  qu: {
    default: "claude-haiku-4-5",
    models: [
      {
        id: "claude-haiku-4-5",
        label: "Haiku 4.5",
        input_usd_mtok: 1,
        output_usd_mtok: 5,
        supports_effort: false,
      },
      {
        id: "claude-sonnet-4-6",
        label: "Sonnet 4.6",
        input_usd_mtok: 3,
        output_usd_mtok: 15,
        supports_effort: true,
      },
    ],
  },
  effort_levels: ["low", "medium", "high", "max"],
};

/** Harness con lo stato reale (useChatConfig) attorno al pannello. */
function Harness() {
  const { config, setConfig, catalog } = useChatConfig();
  return (
    <>
      <ChatSettings config={config} catalog={catalog} onChange={setConfig} />
      <output data-testid="summary">{configSummary(config, catalog) ?? ""}</output>
    </>
  );
}

async function openPanel() {
  const trigger = screen.getByRole("button", { name: "Parametri" });
  await waitFor(() => expect(trigger).not.toBeDisabled());
  fireEvent.click(trigger);
  return trigger;
}

beforeEach(() => {
  clearCatalogCache();
  window.localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(CATALOG), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatSettings", () => {
  it("si disegna DAL catalogo: modelli, default, prezzo, effort, nota footer", async () => {
    render(<Harness />);
    await openPanel();

    expect(
      screen.getByRole("dialog", { name: "Parametri sperimentali della chat" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Modello risposte")).toBeInTheDocument();
    expect(screen.getByText("Comprensione della domanda")).toBeInTheDocument();

    // Le voci del select risposte vengono dal catalogo, default marcato.
    const answerModel = screen.getByLabelText("Modello", {
      selector: "#answer-model",
    }) as HTMLSelectElement;
    expect(answerModel.value).toBe("claude-sonnet-4-6");
    const labels = Array.from(answerModel.options).map((o) => o.text);
    expect(labels).toEqual(["Haiku 4.5", "Sonnet 4.6 (default)", "Opus 4.8"]);

    // Prezzo del modello selezionato (USD per Mtok), stile muted badge.
    expect(screen.getByText("≈ $3/$15 per Mtok")).toBeInTheDocument();

    // QU: default Haiku, effort quindi disabilitato fin da subito.
    const quModel = screen.getByLabelText("Modello", {
      selector: "#qu-model",
    }) as HTMLSelectElement;
    expect(quModel.value).toBe("claude-haiku-4-5");
    expect(screen.getByLabelText("Effort", { selector: "#qu-effort" })).toBeDisabled();

    // Nota sperimentale nel footer del pannello.
    expect(
      screen.getByText(/Impostazioni sperimentali per la fase di test/),
    ).toBeInTheDocument();
  });

  it("effort disabilitato (con nota) quando il modello non lo supporta", async () => {
    render(<Harness />);
    await openPanel();

    const answerEffort = screen.getByLabelText("Effort", {
      selector: "#answer-effort",
    });
    expect(answerEffort).not.toBeDisabled(); // Sonnet (default) lo supporta

    fireEvent.change(
      screen.getByLabelText("Modello", { selector: "#answer-model" }),
      { target: { value: "claude-haiku-4-5" } },
    );
    expect(answerEffort).toBeDisabled();
    // Due note: la sezione risposte (appena passata a Haiku) e la QU
    // (Haiku è il suo default).
    expect(screen.getAllByText("non disponibile per Haiku 4.5")).toHaveLength(2);
  });

  it("passare a un modello senza effort azzera l'effort scelto", async () => {
    render(<Harness />);
    await openPanel();

    fireEvent.change(
      screen.getByLabelText("Effort", { selector: "#answer-effort" }),
      { target: { value: "max" } },
    );
    fireEvent.change(
      screen.getByLabelText("Modello", { selector: "#answer-model" }),
      { target: { value: "claude-haiku-4-5" } },
    );
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY)!);
    expect(stored.answer_effort).toBeNull();
  });

  it("roundtrip localStorage: la scelta sopravvive al remount", async () => {
    const first = render(<Harness />);
    await openPanel();

    fireEvent.change(
      screen.getByLabelText("Modello", { selector: "#answer-model" }),
      { target: { value: "claude-opus-4-8" } },
    );
    fireEvent.change(
      screen.getByLabelText("Effort", { selector: "#answer-effort" }),
      { target: { value: "max" } },
    );
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual({
      answer_model: "claude-opus-4-8",
      answer_effort: "max",
      qu_model: null,
      qu_effort: null,
    });
    expect(screen.getByTestId("summary").textContent).toBe(
      "Opus 4.8 · effort max — QU: Haiku 4.5",
    );

    first.unmount();
    render(<Harness />);
    await openPanel();
    expect(
      (
        screen.getByLabelText("Modello", {
          selector: "#answer-model",
        }) as HTMLSelectElement
      ).value,
    ).toBe("claude-opus-4-8");
    expect(screen.getByTestId("summary").textContent).toBe(
      "Opus 4.8 · effort max — QU: Haiku 4.5",
    );
  });

  it("valori sconosciuti in localStorage vengono scartati al caricamento", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        answer_model: "claude-modello-rimosso",
        answer_effort: "xhigh",
        qu_model: "claude-opus-4-8", // valido per le risposte, NON per la QU
        qu_effort: "low",
      }),
    );
    render(<Harness />);
    await openPanel();

    // Tutto decade ai default: modello sconosciuto, effort fuori lista,
    // modello fuori dall'allowlist QU — e l'effort QU decade con Haiku.
    expect(
      (
        screen.getByLabelText("Modello", {
          selector: "#answer-model",
        }) as HTMLSelectElement
      ).value,
    ).toBe("claude-sonnet-4-6");
    expect(
      (
        screen.getByLabelText("Modello", { selector: "#qu-model" }) as HTMLSelectElement
      ).value,
    ).toBe("claude-haiku-4-5");
    expect(screen.getByTestId("summary").textContent).toBe(""); // tutta-default
  });

  it("effort persistito ma non più supportato dal modello → scartato", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ answer_model: "claude-haiku-4-5", answer_effort: "max" }),
    );
    render(<Harness />);
    await openPanel();
    expect(
      (
        screen.getByLabelText("Effort", {
          selector: "#answer-effort",
        }) as HTMLSelectElement
      ).value,
    ).toBe("");
    expect(screen.getByTestId("summary").textContent).toBe(
      "Haiku 4.5 — QU: Haiku 4.5",
    );
  });

  it("Esc e click sullo scrim chiudono il pannello", async () => {
    const { container } = render(<Harness />);
    await openPanel();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await openPanel();
    fireEvent.click(container.querySelector(".fixed.inset-0")!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
