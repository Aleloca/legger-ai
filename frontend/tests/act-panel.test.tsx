/**
 * ActPanel (components/act-panel.tsx): rendering editoriale dell'atto
 * (articoli, commi, partizioni), targeting dell'articolo citato (anchor,
 * impulso, comma evidenziato), breadcrumb, chiusura con Esc, re-target
 * senza refetch nello stesso atto, errore con retry.
 *
 * fetchAct è finto (vi.mock): qui si testa il pannello, non il client.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActPanel, type ActTarget } from "@/components/act-panel";
import type { ActDetail } from "@/lib/api";

const fetchActMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchAct: fetchActMock,
}));

const CODICE_CIVILE: ActDetail = {
  act_ref: "codice-civile",
  title: "Codice civile",
  act_type: "codice",
  vigenza: "vigente",
  collection: "codici",
  articles: [
    {
      number: "2050",
      heading: "Responsabilità per l'esercizio di attività pericolose",
      path: ["LIBRO QUARTO - Delle obbligazioni", "Titolo IX - Dei fatti illeciti"],
      commi: [
        {
          number: null,
          text: "Chiunque cagiona danno ad altri nello svolgimento di un'attività pericolosa è tenuto al risarcimento.",
        },
      ],
      anchor: "art-2050",
    },
    {
      number: "2051",
      heading: "Danno cagionato da cosa in custodia",
      path: ["LIBRO QUARTO - Delle obbligazioni", "Titolo IX - Dei fatti illeciti"],
      commi: [
        {
          number: "1",
          text: "Ciascuno è responsabile del danno cagionato dalle cose che ha in custodia.",
        },
        { number: "2", text: "Salvo che provi il caso fortuito." },
      ],
      anchor: "art-2051",
    },
    {
      number: "2643",
      heading: "Atti soggetti a trascrizione",
      path: ["LIBRO SESTO - Della tutela dei diritti", "Titolo I - Della trascrizione"],
      commi: [{ number: "1", text: "Si devono rendere pubblici col mezzo della trascrizione." }],
      anchor: "art-2643",
    },
  ],
};

const TARGET: ActTarget = { actRef: "codice-civile", article: "2051", comma: "2" };

beforeEach(() => {
  fetchActMock.mockReset();
  fetchActMock.mockResolvedValue(CODICE_CIVILE);
  // jsdom non implementa scrollIntoView
  Element.prototype.scrollIntoView = vi.fn();
});

async function renderPanel(target: ActTarget = TARGET, onClose = vi.fn()) {
  const view = render(<ActPanel target={target} onClose={onClose} />);
  // lascia risolvere la promise di fetchAct
  await act(async () => {});
  return { ...view, onClose };
}

describe("ActPanel", () => {
  it("renderizza articoli e commi dell'atto, in ordine", async () => {
    await renderPanel();
    expect(screen.getByText("art. 2050")).toBeInTheDocument();
    expect(
      screen.getByText("Danno cagionato da cosa in custodia"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Ciascuno è responsabile del danno cagionato/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Salvo che provi il caso fortuito/)).toBeInTheDocument();
  });

  it("ogni articolo porta il suo anchor id (bersaglio dello scroll)", async () => {
    const { container } = await renderPanel();
    expect(container.querySelector("#art-2051")).not.toBeNull();
    expect(container.querySelector("#art-2050")).not.toBeNull();
  });

  it("scrolla all'articolo citato e gli applica l'impulso", async () => {
    const { container } = await renderPanel();
    const article = container.querySelector("#art-2051")!;
    expect(article.scrollIntoView).toHaveBeenCalled();
    expect(article).toHaveClass("pulse-articolo");
  });

  it("il comma citato resta evidenziato; gli altri no", async () => {
    const { container } = await renderPanel();
    const highlighted = container.querySelectorAll("[data-evidenziato]");
    expect(highlighted).toHaveLength(1);
    expect(highlighted[0]).toHaveTextContent("Salvo che provi il caso fortuito.");
  });

  it("intestazioni di partizione al cambio di path, non ripetute", async () => {
    await renderPanel();
    // due partizioni distinte → due separatori (2050 e 2051 condividono il path)
    expect(
      screen.getByText(/LIBRO QUARTO - Delle obbligazioni · Titolo IX/),
    ).toBeInTheDocument();
    expect(screen.getByText(/LIBRO SESTO - Della tutela dei diritti/)).toBeInTheDocument();
  });

  it("breadcrumb: titolo → partizioni dell'articolo citato → art. N", async () => {
    await renderPanel();
    const breadcrumb = screen.getByRole("navigation", {
      name: "Posizione dell'articolo citato",
    });
    expect(breadcrumb).toHaveTextContent(
      "Codice civile › LIBRO QUARTO - Delle obbligazioni › Titolo IX - Dei fatti illeciti › art. 2051",
    );
  });

  it("header: titolo, badge di vigenza e link Normattiva", async () => {
    await renderPanel();
    expect(
      screen.getByRole("heading", { name: "Codice civile" }),
    ).toBeInTheDocument();
    expect(screen.getByText("vigente")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Apri su Normattiva" })).toHaveAttribute(
      "href",
      "https://www.normattiva.it/ricerca/semplice",
    );
  });

  it("Esc chiude il pannello", async () => {
    const { onClose } = await renderPanel();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("la X chiude il pannello", async () => {
    const { onClose } = await renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Chiudi il pannello" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("re-target nello stesso atto: nessun refetch, evidenziato aggiornato", async () => {
    const { container, rerender } = await renderPanel();
    expect(fetchActMock).toHaveBeenCalledTimes(1);

    rerender(
      <ActPanel
        target={{ actRef: "codice-civile", article: "2643", comma: "1" }}
        onClose={vi.fn()}
      />,
    );
    await act(async () => {});

    expect(fetchActMock).toHaveBeenCalledTimes(1);
    const highlighted = container.querySelectorAll("[data-evidenziato]");
    expect(highlighted).toHaveLength(1);
    expect(highlighted[0]).toHaveTextContent(/trascrizione/);
    expect(container.querySelector("#art-2643")).toHaveClass("pulse-articolo");
  });

  it("re-target su un altro atto: refetch e nuovo contenuto", async () => {
    const { rerender } = await renderPanel();
    const tusl: ActDetail = {
      act_ref: "dlgs-81-2008",
      title: "Testo unico sulla sicurezza sul lavoro",
      act_type: "decreto legislativo",
      vigenza: "vigente",
      collection: "leggi",
      articles: [
        {
          number: "18",
          heading: "Obblighi del datore di lavoro",
          path: ["Titolo I"],
          commi: [{ number: "1", text: "Il datore di lavoro deve…" }],
          anchor: "art-18",
        },
      ],
    };
    fetchActMock.mockResolvedValue(tusl);

    rerender(
      <ActPanel
        target={{ actRef: "dlgs-81-2008", article: "18", comma: null }}
        onClose={vi.fn()}
      />,
    );
    await act(async () => {});

    expect(fetchActMock).toHaveBeenCalledTimes(2);
    expect(fetchActMock).toHaveBeenLastCalledWith("dlgs-81-2008");
    expect(
      screen.getByRole("heading", { name: "Testo unico sulla sicurezza sul lavoro" }),
    ).toBeInTheDocument();
  });

  it("skeleton durante il caricamento, poi il testo", async () => {
    let resolveAct!: (a: ActDetail) => void;
    fetchActMock.mockReturnValue(
      new Promise<ActDetail>((resolve) => {
        resolveAct = resolve;
      }),
    );
    const { container } = render(<ActPanel target={TARGET} onClose={vi.fn()} />);
    expect(container.querySelector(".animate-pulse")).not.toBeNull();

    await act(async () => resolveAct(CODICE_CIVILE));
    expect(container.querySelector(".animate-pulse")).toBeNull();
    expect(container.querySelector("#art-2051")).not.toBeNull();
  });

  it("errore → card con messaggio e Riprova che rifà il fetch", async () => {
    fetchActMock.mockRejectedValueOnce(new Error("Norma non trovata nel corpus."));
    const { container } = await renderPanel();
    expect(screen.getByText("Norma non trovata nel corpus.")).toBeInTheDocument();

    fetchActMock.mockResolvedValue(CODICE_CIVILE);
    fireEvent.click(screen.getByRole("button", { name: "Riprova" }));
    await act(async () => {});

    expect(fetchActMock).toHaveBeenCalledTimes(2);
    expect(container.querySelector("#art-2051")).not.toBeNull();
  });

  it("abrogato → badge grigio (testo della vigenza)", async () => {
    fetchActMock.mockResolvedValue({
      ...CODICE_CIVILE,
      act_ref: "rd-1234-1900",
      title: "Regio decreto abrogato",
      vigenza: "abrogato",
    });
    await renderPanel({ actRef: "rd-1234-1900", article: "2051", comma: null });
    expect(screen.getByText("abrogato")).toBeInTheDocument();
  });

  it("all'apertura il focus entra nel pannello (tabIndex -1)", async () => {
    await renderPanel();
    const panel = screen.getByRole("dialog", { name: "Testo della norma" });
    expect(panel).toHaveAttribute("tabindex", "-1");
    expect(panel).toHaveFocus();
  });

  it("alla chiusura il focus torna all'elemento che ha aperto il pannello", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    expect(opener).toHaveFocus();

    const { unmount } = await renderPanel();
    expect(opener).not.toHaveFocus();

    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it("sotto lg (jsdom) il bottom sheet è un dialog modale", async () => {
    await renderPanel();
    const panel = screen.getByRole("dialog", { name: "Testo della norma" });
    expect(panel).toHaveAttribute("aria-modal", "true");
  });

  it("articolo citato assente dall'atto → avviso, nessun impulso", async () => {
    const { container } = await renderPanel({
      actRef: "codice-civile",
      article: "9999",
      comma: null,
    });
    expect(
      screen.getByText(/art\. 9999: articolo non indicato nell'atto/),
    ).toBeInTheDocument();
    expect(container.querySelector(".pulse-articolo")).toBeNull();
  });

  it("titolo amichevole dal registro; estremi grezzi nel sottotitolo", async () => {
    fetchActMock.mockResolvedValue({
      ...CODICE_CIVILE,
      title: "Regio Decreto 16 marzo 1942, n. 262",
    });
    await renderPanel();
    expect(
      screen.getByRole("heading", { name: "Codice civile" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Regio Decreto 16 marzo 1942, n\. 262/),
    ).toBeInTheDocument();
  });

  it("atto fuori registro: il titolo resta quello dell'API", async () => {
    fetchActMock.mockResolvedValue({
      ...CODICE_CIVILE,
      act_ref: "dlgs-81-2008",
      title: "Attuazione dell'articolo 1 della legge 3 agosto 2007, n. 123",
    });
    await renderPanel({ actRef: "dlgs-81-2008", article: "2051", comma: null });
    expect(
      screen.getByRole("heading", {
        name: "Attuazione dell'articolo 1 della legge 3 agosto 2007, n. 123",
      }),
    ).toBeInTheDocument();
  });
});
