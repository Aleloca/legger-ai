/**
 * renderNormText (lib/norm-text.tsx): resa dei soli link markdown del
 * corpus Normattiva come <a> esterni — tutto il resto resta testo
 * letterale, deliberatamente NON markdown.
 * dedupPartitionLabel: collasso dell'etichetta di partizione duplicata
 * dal corpus ("CAPO II CAPO II …"), conservativo.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { dedupPartitionLabel, renderNormText } from "@/lib/norm-text";

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
    for (const text of [
      "Vedi [art. 1](javascript:alert(1)) per i dettagli.",
      "Vedi [art. 1](urn:nir:stato:legge:1999;526) per i dettagli.",
    ]) {
      expect(renderNormText(text)).toEqual([text]);
    }
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
