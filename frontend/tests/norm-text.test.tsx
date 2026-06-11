/**
 * renderNormText (lib/norm-text.tsx): resa dei soli link markdown del
 * corpus Normattiva come <a> esterni — tutto il resto resta testo
 * letterale, deliberatamente NON markdown.
 * dedupPartitionLabel: collasso dell'etichetta di partizione duplicata
 * dal corpus ("CAPO II CAPO II …"), conservativo.
 * Convenzione (( )) (corpus-analysis A8): marcatori attenuati con
 * tooltip ma testualmente intatti; rilevamento dell'articolo novellato;
 * auto-numerazione del comma (dedup dell'esponente).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  actHasNovellaMarkers,
  commaSelfNumbered,
  dedupPartitionLabel,
  isNovellatoArticle,
  NOVELLA_TOOLTIP,
  renderNormText,
} from "@/lib/norm-text";

const URN =
  "http://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:codice.civile:1942-03-16;262~art1469bis-com1";

describe("renderNormText", () => {
  it("lascia intatto il testo semplice (whitespace compreso)", () => {
    const text = "1. Le parole: \",che ha per oggetto\" sono soppresse.\n  Seguito.";
    expect(renderNormText(text)).toEqual([text]);
  });

  it("rende un singolo link come <a> esterno con il testo intorno intatto", () => {
    const { container } = render(
      <p>
        {renderNormText(
          `Al [primo comma dell'articolo 1469-bis del codice civile](${URN}) le parole sono soppresse.`,
        )}
      </p>,
    );
    const link = screen.getByRole("link", {
      name: "primo comma dell'articolo 1469-bis del codice civile",
    });
    expect(link).toHaveAttribute("href", URN);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(container.textContent).toBe(
      "Al primo comma dell'articolo 1469-bis del codice civile le parole sono soppresse.",
    );
  });

  it("rende più link nello stesso comma", () => {
    render(
      <p>
        {renderNormText(
          "Visto l'[art. 1](https://a.example/1) e l'[art. 2](https://a.example/2), si dispone.",
        )}
      </p>,
    );
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", "https://a.example/1");
    expect(links[1]).toHaveAttribute("href", "https://a.example/2");
  });

  it("lascia letterale un link senza parentesi di chiusura", () => {
    const text = "Vedi [art. 1](http://www.normattiva.it/uri-res/N2Ls?urn senza chiusura";
    expect(renderNormText(text)).toEqual([text]);
  });

  it("lascia letterale un url non http(s)", () => {
    expect(
      renderNormText("Vedi [art. 1](urn:nir:stato:legge:1999;526) per i dettagli."),
    ).toEqual(["Vedi [art. 1](urn:nir:stato:legge:1999;526) per i dettagli."]);
    // `javascript:alert(1))` contiene per caso il glifo `))`: niente <a>,
    // testo byte-identico (il glifo viene solo avvolto nello span-marcatore)
    const text = "Vedi [art. 1](javascript:alert(1)) per i dettagli.";
    const { container } = render(<p>{renderNormText(text)}</p>);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toBe(text);
  });

  it("rende come UN'unica <a> l'ancora multilinea reale delle note all'art. 5 di legge-526-1999", () => {
    // Ancora che attraversa un a-capo, presa verbatim dal corpus: 7 dei
    // 193 link di legge-526-1999 hanno questa forma.
    const anchor = "Art. 5:  \n- La legge 24 novembre 1981, n. 689";
    const urn =
      "http://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:1981-11-24;689~art5";
    const { container } = render(<p>{renderNormText(`Note all'[${anchor}](${urn})`)}</p>);
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", urn);
    // textContent preservato byte per byte, a-capo compreso
    expect(links[0].textContent).toBe(anchor);
    expect(container.textContent).toBe(`Note all'${anchor}`);
  });

  it("lascia letterale un url oltre i 2048 caratteri", () => {
    const text = `Vedi [art. 1](https://a.example/${"x".repeat(2048)}) per i dettagli.`;
    expect(renderNormText(text)).toEqual([text]);
  });

  it("gestisce con grazia le quadre annidate `[a[b](u)`", () => {
    // url non-http: nulla combacia, tutto letterale
    expect(renderNormText("[a[b](u)")).toEqual(["[a[b](u)"]);
    // url http: combacia solo `[b](…)`, la quadra orfana resta testo
    const { container } = render(<p>{renderNormText("[a[b](https://a.example/x)")}</p>);
    expect(screen.getByRole("link", { name: "b" })).toHaveAttribute(
      "href",
      "https://a.example/x",
    );
    expect(container.textContent).toBe("[ab");
  });

  it("rende il testo vuoto come un singolo nodo vuoto", () => {
    expect(renderNormText("")).toEqual([""]);
  });

  it("avvolge i marcatori (( )) in span attenuati con tooltip, testo byte-identico", () => {
    const text =
      "1. ((In attuazione della direttiva)) restano ferme le disposizioni.";
    const { container } = render(<p>{renderNormText(text)}</p>);
    // contenuto testuale invariato byte per byte (copia-incolla = originale)
    expect(container.textContent).toBe(text);
    const markers = container.querySelectorAll("span[title]");
    expect(markers).toHaveLength(2);
    expect(markers[0].textContent).toBe("((");
    expect(markers[1].textContent).toBe("))");
    expect(markers[0]).toHaveAttribute("title", NOVELLA_TOOLTIP);
    expect(markers[0].className).toContain("text-muted-foreground");
  });

  it("un link DENTRO un blocco (( )) resta un <a>; i marcatori intorno sono attenuati", () => {
    const text =
      "((Vedi l'[art. 1](https://a.example/1) per i dettagli.))";
    const { container } = render(<p>{renderNormText(text)}</p>);
    expect(screen.getByRole("link", { name: "art. 1" })).toHaveAttribute(
      "href",
      "https://a.example/1",
    );
    expect(container.textContent).toBe("((Vedi l'art. 1 per i dettagli.))");
    const markers = container.querySelectorAll("span[title]");
    expect(markers).toHaveLength(2);
    expect(markers[0].textContent).toBe("((");
    expect(markers[1].textContent).toBe("))");
  });
});

describe("isNovellatoArticle", () => {
  // forma reale dell'art. 2-septies del codice-privacy: comma-marcatore
  // `((` a sé stante in testa, `))` in coda all'ultimo comma
  const FULLY_WRAPPED = [
    { text: "((" },
    { text: "1. In attuazione di quanto previsto dal regolamento." },
    { text: "2. Lo schema di provvedimento è sottoposto a consultazione.))" },
  ];

  it("articolo interamente novellato (marcatore standalone in testa) → true", () => {
    expect(isNovellatoArticle(FULLY_WRAPPED)).toBe(true);
  });

  it("variante con (( all'inizio assoluto del primo comma → true", () => {
    expect(
      isNovellatoArticle([
        { text: "((1. In attuazione di quanto previsto." },
        { text: "2. Lo schema è sottoposto a consultazione.))" },
      ]),
    ).toBe(true);
  });

  it("articolo solo parzialmente novellato → false", () => {
    expect(
      isNovellatoArticle([
        { text: "1. Il testo originale resta fermo." },
        { text: "2. ((Le parole sono sostituite)) dal presente comma." },
      ]),
    ).toBe(false);
    // apre con (( ma non chiude in coda: conservativo, niente badge
    expect(
      isNovellatoArticle([
        { text: "((" },
        { text: "1. Solo il primo comma)) è novellato." },
        { text: "2. Il secondo è originale." },
      ]),
    ).toBe(false);
  });

  it("articolo pulito (o vuoto) → false", () => {
    expect(
      isNovellatoArticle([{ text: "1. Chiunque cagiona danno ad altri." }]),
    ).toBe(false);
    expect(isNovellatoArticle([])).toBe(false);
    expect(isNovellatoArticle([{ text: "   " }])).toBe(false);
  });
});

describe("actHasNovellaMarkers", () => {
  it("true se un comma o una rubrica contiene ((, false altrimenti", () => {
    const clean = {
      articles: [
        { heading: "Rubrica", commi: [{ text: "1. Testo pulito." }] },
      ],
    };
    expect(actHasNovellaMarkers(clean)).toBe(false);
    expect(
      actHasNovellaMarkers({
        articles: [
          { heading: null, commi: [{ text: "1. ((Testo novellato))." }] },
        ],
      }),
    ).toBe(true);
    expect(
      actHasNovellaMarkers({
        articles: [{ heading: "((Rubrica novellata))", commi: [] }],
      }),
    ).toBe(true);
  });
});

describe("commaSelfNumbered", () => {
  it("«1.» con number \"1\" → true (anche dietro marcatore di novella)", () => {
    expect(commaSelfNumbered("1", "1. In attuazione di quanto previsto.")).toBe(true);
    expect(commaSelfNumbered("1", "  1) In attuazione.")).toBe(true);
    expect(commaSelfNumbered("1", "((1. In attuazione.")).toBe(true);
  });

  it("«1-bis.» con number \"1-bis\" → true (ordinali latini, case-insensitive)", () => {
    expect(commaSelfNumbered("1-bis", "1-bis. Le misure di garanzia.")).toBe(true);
    expect(commaSelfNumbered("2-septies", "2-septies. I dati genetici.")).toBe(true);
    expect(commaSelfNumbered("1-BIS", "1-bis. Le misure.")).toBe(true);
  });

  it("testo non auto-numerato → false (l'esponente resta)", () => {
    expect(commaSelfNumbered("1", "In attuazione di quanto previsto.")).toBe(false);
    expect(commaSelfNumbered("1", "")).toBe(false);
  });

  it("numero divergente («3.» con number \"2\") → false", () => {
    expect(commaSelfNumbered("2", "3. In attuazione.")).toBe(false);
    expect(commaSelfNumbered("1", "1-bis. Le misure.")).toBe(false);
    expect(commaSelfNumbered("1-bis", "1. Le misure.")).toBe(false);
  });
});

describe("dedupPartitionLabel", () => {
  it("collassa la duplicazione reale di legge-526-1999", () => {
    expect(
      dedupPartitionLabel(
        "CAPO II CAPO II DISPOSIZIONI PARTICOLARI DI ADEMPIMENTO DIRETTO, CRITERI SPECIALI DI DELEGA LEGISLATIVA",
      ),
    ).toBe(
      "CAPO II DISPOSIZIONI PARTICOLARI DI ADEMPIMENTO DIRETTO, CRITERI SPECIALI DI DELEGA LEGISLATIVA",
    );
  });

  it("collassa anche con separatore ' - ' e con punto dopo la ripetizione", () => {
    expect(dedupPartitionLabel("CAPO II - CAPO II DISPOSIZIONI")).toBe(
      "CAPO II DISPOSIZIONI",
    );
    expect(dedupPartitionLabel("CAPO I CAPO I. DISPOSIZIONI GENERALI")).toBe(
      "CAPO I. DISPOSIZIONI GENERALI",
    );
  });

  it("lascia intatte le etichette non duplicate", () => {
    for (const label of [
      "LIBRO QUARTO - Delle obbligazioni",
      "Titolo IX - Dei fatti illeciti",
      "CAPO III - Sezione II Organizzazione del Servizio nazionale",
      // contatore artificiale ≠ partizione reale: NON è una ripetizione
      "CAPO III CAPO II AUTORIZZAZIONE DI OPERAZIONI DI PAGAMENTO",
      // "CAPO II" non è prefisso a confine di parola di "CAPO III"
      "CAPO II CAPO III ABROGAZIONI",
      "DISPOSIZIONI FINALI",
    ]) {
      expect(dedupPartitionLabel(label)).toBe(label);
    }
  });
});
