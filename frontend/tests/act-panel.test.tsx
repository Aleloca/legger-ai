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

/**
 * Atto con la convenzione Normattiva (( )): l'art. 2-septies rispecchia
 * la forma reale del codice-privacy (comma-marcatore `((` standalone in
 * testa, `))` in coda, commi auto-numerati «1. …»); l'art. 3 è novellato
 * solo in parte; l'art. 4 è pulito.
 */
const CODICE_PRIVACY: ActDetail = {
  act_ref: "codice-privacy",
  title: "Codice in materia di protezione dei dati personali",
  act_type: "decreto legislativo",
  vigenza: "vigente",
  collection: "codici",
  articles: [
    {
      number: "2-septies",
      heading: "Misure di garanzia per il trattamento dei dati genetici",
      path: [],
      commi: [
        { number: null, text: "((" },
        {
          number: "1",
          text: "1. In attuazione di quanto previsto dal regolamento, i dati genetici possono essere oggetto di trattamento.",
        },
        {
          number: "1-bis",
          text: "1-bis. Le misure di garanzia sono adottate con provvedimento del Garante.",
        },
        {
          number: "2",
          text: "Lo schema di provvedimento è sottoposto a consultazione pubblica.))",
        },
      ],
      anchor: "art-2-septies",
    },
    {
      number: "3",
      heading: "Principio di minimizzazione",
      path: [],
      commi: [
        {
          number: "1",
          text: "3. I sistemi informativi sono configurati riducendo l'utilizzazione di dati personali. ((Le parole sono sostituite dal presente periodo.))",
        },
      ],
      anchor: "art-3",
    },
    {
      number: "4",
      heading: "Definizioni",
      path: [],
      commi: [{ number: "1", text: "Ai fini del presente codice si intende per:" }],
      anchor: "art-4",
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
    // il primo bersaglio è stato scrollato all'apertura
    expect(container.querySelector("#art-2051")!.scrollIntoView).toHaveBeenCalled();
    (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mockClear();

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
    // il re-target nello stesso atto DEVE riscrollare al nuovo articolo
    // (contexts = i `this` delle chiamate: lo scroll è partito da #art-2643)
    const scrollSpy = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scrollSpy.mock.contexts).toContain(container.querySelector("#art-2643"));
  });

  it("re-target sulla stessa citazione (oggetto target nuovo): riscrolla", async () => {
    const { container, rerender } = await renderPanel();
    const scrollSpy = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    scrollSpy.mockClear();

    // page.tsx crea un oggetto target NUOVO ad ogni click: anche a parità
    // di atto+articolo lo scroll (e l'impulso) devono ripartire.
    rerender(<ActPanel target={{ ...TARGET }} onClose={vi.fn()} />);
    await act(async () => {});

    expect(scrollSpy.mock.contexts).toContain(container.querySelector("#art-2051"));
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

describe("ActPanel — convenzione Normattiva (( ))", () => {
  const PRIVACY_TARGET: ActTarget = {
    actRef: "codice-privacy",
    article: "2-septies",
    comma: null,
  };

  async function renderPrivacy() {
    fetchActMock.mockResolvedValue(CODICE_PRIVACY);
    return renderPanel(PRIVACY_TARGET);
  }

  it("badge «testo novellato» sull'articolo interamente racchiuso tra (( ))", async () => {
    const { container } = await renderPrivacy();
    const badge = container.querySelector("#art-2-septies h3 span[title]");
    expect(badge).toHaveTextContent("testo novellato");
    expect(badge).toHaveAttribute(
      "title",
      "Articolo inserito o modificato da provvedimenti successivi rispetto al testo originale (convenzione Normattiva: doppie parentesi)",
    );
  });

  it("nessun badge sull'articolo parzialmente novellato né su quello pulito", async () => {
    const { container } = await renderPrivacy();
    for (const anchor of ["#art-3", "#art-4"]) {
      const article = container.querySelector(anchor)!;
      expect(article.textContent).not.toContain("testo novellato");
    }
  });

  it("i marcatori (( )) restano nel testo, attenuati e con tooltip", async () => {
    const { container } = await renderPrivacy();
    const article = container.querySelector("#art-3")!;
    // il testo del comma resta byte-identico, marcatori compresi
    expect(article.textContent).toContain(
      "((Le parole sono sostituite dal presente periodo.))",
    );
    const markers = article.querySelectorAll(
      'p span[title="Testo tra (( )) = modificato da provvedimenti successivi — convenzione Normattiva"]',
    );
    expect(markers).toHaveLength(2);
    expect(markers[0].textContent).toBe("((");
    expect(markers[1].textContent).toBe("))");
  });

  it("legenda in fondo al pannello quando l'atto contiene marcatori", async () => {
    await renderPrivacy();
    expect(
      screen.getByText(
        /\(\( \)\) — testo inserito o modificato da provvedimenti successivi \(convenzione Normattiva\)/,
      ),
    ).toBeInTheDocument();
  });

  it("nessuna legenda per un atto senza marcatori", async () => {
    await renderPanel(); // CODICE_CIVILE, pulito
    expect(
      screen.queryByText(/convenzione Normattiva/),
    ).not.toBeInTheDocument();
  });

  it("comma auto-numerato («1. …», «1-bis. …»): niente esponente duplicato", async () => {
    const { container } = await renderPrivacy();
    const article = container.querySelector("#art-2-septies")!;
    expect(article.querySelector('p[data-comma="1"] sup')).toBeNull();
    expect(article.querySelector('p[data-comma="1-bis"] sup')).toBeNull();
  });

  it("l'esponente resta quando il testo non si auto-numera o diverge", async () => {
    const { container } = await renderPrivacy();
    // testo senza numero in testa («Lo schema…»): esponente visibile
    const comma2 = container.querySelector('#art-2-septies p[data-comma="2"]')!;
    expect(comma2.querySelector("sup")).toHaveTextContent("2");
    // number "1" ma il testo apre con «3.»: divergente, esponente visibile
    const divergente = container.querySelector('#art-3 p[data-comma="1"]')!;
    expect(divergente.querySelector("sup")).toHaveTextContent("1");
    // e il Codice civile pulito conserva i suoi esponenti
  });
});
