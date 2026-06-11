/**
 * Test del feedback 👍/👎: visibilità solo a risposta completata,
 * shape del payload (👍 e 👎+motivo), lock un-feedback-per-messaggio,
 * stato «Grazie», errore con riabilitazione, nota privacy una volta
 * per sessione. fetch è finto; il flusso SSE è pilotato a mano come
 * negli altri test di Chat.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Chat } from "@/components/chat";
import {
  FeedbackButtons,
  PRIVACY_NOTICE_KEY,
} from "@/components/feedback-buttons";
import type { StreamHandlers } from "@/lib/sse";
import type { Citation, EffectiveConfig } from "@/lib/types";

const streamChatMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/sse", () => ({ streamChat: streamChatMock }));

const fetchMock = vi.fn();

const CITATION: Citation = {
  marker: "[[codice-civile|art.2051|c.1]]",
  act_ref: "codice-civile",
  article: "2051",
  comma: "1",
  title: "Codice civile",
  vigenza: "vigente",
  verified: true,
  reason: "ok",
};

const CONFIG: EffectiveConfig = {
  answer_model: "claude-haiku-4-5",
  answer_effort: null,
  qu_model: "claude-haiku-4-5",
  qu_effort: null,
};

function lastHandlers(): StreamHandlers {
  return streamChatMock.mock.calls.at(-1)![1] as StreamHandlers;
}

function sendMessage(text: string) {
  const textarea = screen.getByRole("textbox", {
    name: "Domanda sulla normativa",
  });
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.keyDown(textarea, { key: "Enter" });
}

function ok204() {
  return Promise.resolve(new Response(null, { status: 204 }));
}

function sentBody(callIndex = 0): Record<string, unknown> {
  const [url, init] = fetchMock.mock.calls[callIndex];
  expect(url).toBe("/api/backend/feedback");
  expect(init.method).toBe("POST");
  return JSON.parse(init.body as string);
}

beforeEach(() => {
  streamChatMock.mockReset();
  streamChatMock.mockResolvedValue(undefined);
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  window.sessionStorage.clear();
  // Nota privacy già vista: i test dedicati la riabilitano da soli.
  window.sessionStorage.setItem(PRIVACY_NOTICE_KEY, "1");
});

describe("FeedbackButtons in Chat", () => {
  it("assenti durante lo streaming, presenti solo al done", () => {
    render(<Chat />);
    sendMessage("domanda");
    act(() => lastHandlers().onToken?.("Risposta in corso"));
    expect(
      screen.queryByRole("button", { name: "Risposta utile" }),
    ).not.toBeInTheDocument();

    act(() =>
      lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false }),
    );
    expect(
      screen.getByRole("button", { name: "Risposta utile" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Risposta non utile" }),
    ).toBeInTheDocument();
  });

  it("assenti sui messaggi in errore", () => {
    render(<Chat />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onToken?.("inizio ");
      lastHandlers().onError?.("Si è verificato un errore.");
    });
    expect(
      screen.queryByRole("button", { name: "Risposta utile" }),
    ).not.toBeInTheDocument();
  });

  it("👍 → POST con question/answer/citations/config e stato «Grazie»", async () => {
    fetchMock.mockImplementation(ok204);
    render(<Chat />);
    sendMessage("art. 2051 c.c.");
    act(() => {
      lastHandlers().onToken?.("Vedi [[codice-civile|art.2051|c.1]].");
      lastHandlers().onCitation?.(CITATION);
      lastHandlers().onDone?.({
        stop_reason: "end_turn",
        truncated: false,
        config: CONFIG,
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "Risposta utile" }));
    expect(
      await screen.findByText("Grazie per il feedback."),
    ).toBeInTheDocument();

    expect(sentBody()).toEqual({
      rating: 1,
      question: "art. 2051 c.c.",
      answer: "Vedi [[codice-civile|art.2051|c.1]].",
      citations: [CITATION],
      config: CONFIG,
    });
    // Bottoni spariti: un solo feedback per messaggio.
    expect(
      screen.queryByRole("button", { name: "Risposta utile" }),
    ).not.toBeInTheDocument();
  });

  it("👎 → input motivo, invio → POST con rating -1 e reason", async () => {
    fetchMock.mockImplementation(ok204);
    render(<Chat />);
    sendMessage("art. 2051 c.c.");
    act(() => {
      lastHandlers().onToken?.("Risposta.");
      lastHandlers().onDone?.({
        stop_reason: "end_turn",
        truncated: false,
        config: CONFIG,
      });
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Risposta non utile" }),
    );
    const input = screen.getByRole("textbox", {
      name: "Cosa non andava? (opzionale)",
    });
    fireEvent.change(input, { target: { value: "Cita male l'articolo." } });
    fireEvent.click(screen.getByRole("button", { name: "Invia il feedback" }));

    expect(
      await screen.findByText("Grazie per il feedback."),
    ).toBeInTheDocument();
    expect(sentBody()).toMatchObject({
      rating: -1,
      reason: "Cita male l'articolo.",
      question: "art. 2051 c.c.",
      answer: "Risposta.",
    });
  });

  it("👎 senza motivo → POST senza chiave reason", async () => {
    fetchMock.mockImplementation(ok204);
    render(<Chat />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onToken?.("Risposta.");
      lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false });
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Risposta non utile" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Invia il feedback" }));
    await screen.findByText("Grazie per il feedback.");

    const body = sentBody();
    expect(body.rating).toBe(-1);
    expect(body).not.toHaveProperty("reason");
    expect(body).not.toHaveProperty("config"); // done senza config
  });

  it("fallimento → errore discreto e bottoni riabilitati", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 503 }));
    render(<Chat />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onToken?.("Risposta.");
      lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false });
    });

    fireEvent.click(screen.getByRole("button", { name: "Risposta utile" }));
    expect(
      await screen.findByText("Invio non riuscito. Riprova."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Risposta utile" }),
    ).not.toBeDisabled();

    // Il retry può riuscire.
    fetchMock.mockImplementation(ok204);
    fireEvent.click(screen.getByRole("button", { name: "Risposta utile" }));
    expect(
      await screen.findByText("Grazie per il feedback."),
    ).toBeInTheDocument();
  });
});

describe("nota privacy", () => {
  const NOTICE =
    "Il feedback salva domanda e risposta per migliorare il servizio.";

  it("compare solo la prima volta per sessione", () => {
    window.sessionStorage.clear();
    const { unmount } = render(
      <FeedbackButtons question="q" answer="a" />,
    );
    expect(screen.getByText(NOTICE)).toBeInTheDocument();
    unmount();

    // Seconda risposta nella stessa sessione: nessuna nota.
    render(<FeedbackButtons question="q2" answer="a2" />);
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  });

  it("non compare quando la sessione l'ha già mostrata", () => {
    render(<FeedbackButtons question="q" answer="a" />);
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  });
});
