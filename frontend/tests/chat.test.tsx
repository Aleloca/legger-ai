/**
 * Smoke test del componente Chat: rendering, invio dal composer,
 * composer disabilitato durante lo streaming, errore inline.
 * streamChat è finto: i test pilotano le callback a mano.
 */

import { fireEvent, render, screen, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Chat } from "@/components/chat";
import type { StreamHandlers } from "@/lib/sse";

const streamChatMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/sse", () => ({ streamChat: streamChatMock }));

function lastHandlers(): StreamHandlers {
  return streamChatMock.mock.calls.at(-1)![1] as StreamHandlers;
}

function sendMessage(text: string) {
  const textarea = screen.getByRole("textbox", {
    name: "Domanda sulla normativa",
  });
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.keyDown(textarea, { key: "Enter" });
  return textarea as HTMLTextAreaElement;
}

beforeEach(() => {
  streamChatMock.mockReset();
  streamChatMock.mockResolvedValue(undefined);
});

describe("Chat", () => {
  it("monta composer e stato vuoto", () => {
    render(<Chat />);
    expect(
      screen.getByRole("textbox", { name: "Domanda sulla normativa" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("La normativa italiana, letta con rigore"),
    ).toBeInTheDocument();
  });

  it("Invio invia il turno e chiama streamChat con la conversazione", () => {
    render(<Chat />);
    sendMessage("art. 2051 c.c.");
    expect(screen.getByText("art. 2051 c.c.")).toBeInTheDocument();
    expect(streamChatMock).toHaveBeenCalledOnce();
    expect(streamChatMock.mock.calls[0][0]).toEqual([
      { role: "user", content: "art. 2051 c.c." },
    ]);
  });

  it("disabilita il composer durante lo streaming e lo riabilita al done", () => {
    render(<Chat />);
    const textarea = sendMessage("domanda");
    expect(textarea).toBeDisabled();

    act(() => {
      lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false });
    });
    expect(textarea).not.toBeDisabled();
  });

  it("mostra l'indicatore di ricerca dall'invio fino al primo token", () => {
    render(<Chat />);
    sendMessage("domanda");

    // Ottimistico: visibile già prima dell'evento status…
    expect(screen.getByText(/sto cercando nel corpus/)).toBeInTheDocument();

    // …confermato dallo status…
    act(() => lastHandlers().onStatus?.("searching"));
    expect(screen.getByText(/sto cercando nel corpus/)).toBeInTheDocument();

    // …e rimosso al primo token.
    act(() => lastHandlers().onToken?.("La risposta"));
    expect(screen.queryByText(/sto cercando nel corpus/)).not.toBeInTheDocument();
    expect(screen.getByText("La risposta")).toBeInTheDocument();
  });

  it("accumula i token nel testo dell'assistant (marker grezzi inclusi)", () => {
    render(<Chat />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onToken?.("Vedi ");
      lastHandlers().onToken?.("[[codice.civile|art.2051|c.1]]");
      lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false });
    });
    expect(
      screen.getByText("Vedi [[codice.civile|art.2051|c.1]]"),
    ).toBeInTheDocument();
  });

  it("errore → bolla inline e composer riabilitato", () => {
    render(<Chat />);
    const textarea = sendMessage("domanda");
    act(() => lastHandlers().onError?.("Si è verificato un errore."));
    expect(screen.getByText("Si è verificato un errore.")).toBeInTheDocument();
    expect(textarea).not.toBeDisabled();
  });

  it("done con truncated → nota «risposta troncata»", () => {
    render(<Chat />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onToken?.("Risposta a metà");
      lastHandlers().onDone?.({ stop_reason: "max_tokens", truncated: true });
    });
    expect(screen.getByText("risposta troncata")).toBeInTheDocument();
  });

  it("sources → sezione «Fonti consultate (N)», chiusa di default", () => {
    render(<Chat />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onSources?.([
        {
          act_ref: "codice-civile",
          article: "2051",
          title: "Danno cagionato da cosa in custodia",
          vigenza: "vigente",
          anchor: "art-2051",
        },
        {
          act_ref: "codice-civile",
          article: "2043",
          title: "Risarcimento per fatto illecito",
          vigenza: "vigente",
          anchor: "art-2043",
        },
      ]);
      lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false });
    });
    const toggle = screen.getByRole("button", { name: "Fonti consultate (2)" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/art\. 2043/)).not.toBeInTheDocument();
  });

  it("fonte NON citata: espandi → click → onCitationClick apre la split-view", () => {
    const onCitationClick = vi.fn();
    render(<Chat onCitationClick={onCitationClick} />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onSources?.([
        {
          act_ref: "codice-civile",
          article: "2051",
          title: "Danno cagionato da cosa in custodia",
          vigenza: "vigente",
          anchor: "art-2051",
        },
        {
          // consultata ma mai citata nella risposta
          act_ref: "codice-civile",
          article: "2043",
          title: "Risarcimento per fatto illecito",
          vigenza: "vigente",
          anchor: "art-2043",
        },
      ]);
      lastHandlers().onToken?.("Vedi [[codice-civile|art.2051|c.1]].");
      lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false });
    });
    fireEvent.click(screen.getByRole("button", { name: "Fonti consultate (2)" }));
    fireEvent.click(screen.getByText("Cod. Civ. — art. 2043"));
    expect(onCitationClick).toHaveBeenCalledWith({
      actRef: "codice-civile",
      article: "2043",
      comma: null,
    });
  });

  it("empty state: il click su un suggerimento riempie il composer senza inviare", () => {
    render(<Chat />);
    const textarea = screen.getByRole("textbox", {
      name: "Domanda sulla normativa",
    }) as HTMLTextAreaElement;
    fireEvent.click(
      screen.getByRole("button", { name: "Cosa prevede l'art. 2051 c.c.?" }),
    );
    expect(textarea.value).toBe("Cosa prevede l'art. 2051 c.c.?");
    expect(streamChatMock).not.toHaveBeenCalled();
  });

  it("la regione del messaggio in streaming è aria-live polite", () => {
    const { container } = render(<Chat />);
    sendMessage("domanda");
    act(() => lastHandlers().onToken?.("La risposta"));
    const live = container.querySelector('[aria-live="polite"]');
    expect(live).not.toBeNull();
    expect(live).toHaveTextContent("La risposta");
  });

  it("a conversazione attiva resta un h1 (visually hidden)", () => {
    render(<Chat />);
    sendMessage("domanda");
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Conversazione sulla normativa",
      }),
    ).toBeInTheDocument();
  });

  it("done → final: un marker pendente in coda diventa testo visibile", () => {
    render(<Chat />);
    sendMessage("domanda");
    act(() => lastHandlers().onToken?.("Vedi [[codice-"));
    // durante lo streaming il pendente è invisibile…
    expect(screen.queryByText(/\[\[codice-/)).not.toBeInTheDocument();

    act(() => lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false }));
    // …al done non può più risolversi: resta come testo
    expect(screen.getByText(/Vedi \[\[codice-/)).toBeInTheDocument();
  });

  it("error → final: il pendente diventa testo anche sul turno fallito", () => {
    render(<Chat />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onToken?.("Vedi [[codice-");
      lastHandlers().onError?.("Errore.");
    });
    expect(screen.getByText(/Vedi \[\[codice-/)).toBeInTheDocument();
  });

  it("click su un chip → onCitationClick con il riferimento", () => {
    const onCitationClick = vi.fn();
    render(<Chat onCitationClick={onCitationClick} />);
    sendMessage("domanda");
    act(() => {
      lastHandlers().onToken?.("Vedi [[codice-civile|art.2051|c.1]].");
      lastHandlers().onDone?.({ stop_reason: "end_turn", truncated: false });
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Cod. Civ., art. 2051, c. 1" }),
    );
    expect(onCitationClick).toHaveBeenCalledWith({
      actRef: "codice-civile",
      article: "2051",
      comma: "1",
      citation: undefined,
    });
  });

  it("il turno fallito (assistant vuoto) non entra nel payload successivo", () => {
    render(<Chat />);
    sendMessage("prima domanda");
    act(() => lastHandlers().onError?.("Errore."));

    sendMessage("seconda domanda");
    expect(streamChatMock).toHaveBeenCalledTimes(2);
    expect(streamChatMock.mock.calls[1][0]).toEqual([
      { role: "user", content: "prima domanda" },
      { role: "user", content: "seconda domanda" },
    ]);
  });
});
