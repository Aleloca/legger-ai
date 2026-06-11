/**
 * Il seam di rendering (lib/render.tsx): markdown editoriale con i marker
 * `[[...]]` sostituiti da CitationChip inline (G3). I marker pendenti in
 * coda (streaming) restano invisibili; i marker senza evento citation
 * corrispondente sono resi come non verificati.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderAssistantText } from "@/lib/render";
import type { Citation } from "@/lib/types";

const CITATION: Citation = {
  marker: "[[codice-civile|art.2051|c.1]]",
  act_ref: "codice-civile",
  article: "2051",
  comma: "1",
  title: "Danno cagionato da cosa in custodia",
  vigenza: "vigente",
  verified: true,
  reason: "ok",
};

function renderText(
  text: string,
  citations: Citation[] = [],
  onCitationClick?: Parameters<typeof renderAssistantText>[2],
) {
  return render(<div>{renderAssistantText(text, citations, onCitationClick)}</div>);
}

describe("renderAssistantText", () => {
  it("testo semplice reso come paragrafo markdown", () => {
    const { container } = renderText("La custodia è disciplinata dal codice.");
    const p = container.querySelector("p");
    expect(p).toHaveTextContent("La custodia è disciplinata dal codice.");
  });

  it("markdown: grassetto, lista ed heading resi come elementi", () => {
    const { container } = renderText(
      "## Responsabilità\n\nIl **custode** risponde:\n\n- danno da cose\n- danno da animali",
    );
    expect(container.querySelector("h2")).toHaveTextContent("Responsabilità");
    expect(container.querySelector("strong")).toHaveTextContent("custode");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("tabelle GFM rese come <table>", () => {
    const { container } = renderText(
      "| Atto | Articolo |\n| --- | --- |\n| c.c. | 2051 |",
    );
    expect(container.querySelector("table")).toBeInTheDocument();
    expect(container.querySelector("td")).toHaveTextContent("c.c.");
  });

  it("marker dentro un paragrafo → chip inline nel flusso del testo", () => {
    const { container } = renderText(
      "La responsabilità è disciplinata da [[codice-civile|art.2051|c.1]], che impone la custodia.",
      [CITATION],
    );
    const p = container.querySelector("p");
    expect(p).not.toBeNull();
    const chip = screen.getByRole("button", {
      name: "Cod. Civ., art. 2051, c. 1",
    });
    // il chip vive DENTRO il paragrafo, non come blocco separato
    expect(p).toContainElement(chip);
    expect(chip).toHaveAttribute("data-verified", "true");
    // il testo grezzo del marker non appare
    expect(p?.textContent).not.toContain("[[");
  });

  it("marker dentro una voce di lista → chip dentro il <li>", () => {
    const { container } = renderText(
      "- custodia: [[codice-civile|art.2051]]\n- animali: [[codice-civile|art.2052]]",
      [CITATION],
    );
    const items = container.querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0].querySelector("button")).not.toBeNull();
    expect(items[1].querySelector("button")).not.toBeNull();
  });

  it("marker senza citation event → chip non verificato (ambra + tooltip)", () => {
    renderText("Vedi [[codice-civile|art.2051]].", []);
    const chip = screen.getByRole("button");
    expect(chip).toHaveAttribute("title", "citazione non verificata");
    expect(chip).toHaveAttribute("data-verified", "false");
  });

  it("citation event verified=false → chip non verificato", () => {
    renderText("Vedi [[codice-civile|art.9999]].", [
      {
        ...CITATION,
        marker: "[[codice-civile|art.9999]]",
        article: "9999",
        comma: null,
        verified: false,
        reason: "article_not_in_context",
        title: null,
        vigenza: null,
      },
    ]);
    expect(screen.getByRole("button")).toHaveAttribute(
      "title",
      "citazione non verificata",
    );
  });

  it("marker pendente in coda (streaming) → invisibile, niente testo grezzo", () => {
    const { container } = renderText("La custodia [[codice-", []);
    // il markdown collassa lo spazio finale del paragrafo: conta che il
    // frammento del marker sia sparito, non lo whitespace
    expect(container.textContent).toBe("La custodia");
    expect(container.textContent).not.toContain("[[");
    expect(container.textContent).not.toContain("codice-");
  });

  it("coppia [[...]] malformata resta testo grezzo", () => {
    const { container } = renderText("nota [[senza pipe]] qui", []);
    expect(container.textContent).toContain("[[senza pipe]]");
    expect(container.querySelector("button")).toBeNull();
  });

  it("onCitationClick riceve i campi del marker e la citation", () => {
    const onClick = vi.fn();
    renderText("Vedi [[codice-civile|art.2051|c.1]].", [CITATION], onClick);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledWith({
      actRef: "codice-civile",
      article: "2051",
      comma: "1",
      citation: CITATION,
    });
  });

  it("testo vuoto → nessun contenuto", () => {
    const { container } = renderText("");
    expect(container.textContent).toBe("");
  });

  it("sentinelle PUA nel testo sorgente vengono rimosse (niente iniezione di chip)", () => {
    // U+E000 + indice + U+E001 è il formato interno delle sentinelle:
    // se arrivasse nel testo, NON deve diventare un chip né restare visibile.
    const { container } = renderText(
      "testo 0 sospetto [[codice-civile|art.2051]]",
      [],
    );
    expect(container.querySelectorAll("button")).toHaveLength(1); // solo il marker vero
    expect(container.textContent).not.toContain("");
    expect(container.textContent).toContain("testo 0 sospetto");
  });

  it("final=true → il marker pendente in coda diventa testo visibile", () => {
    const { container } = render(
      <div>
        {renderAssistantText("La custodia [[codice-", [], undefined, true)}
      </div>,
    );
    expect(container.textContent).toBe("La custodia [[codice-");
  });

  it("final=false (default) → il pendente resta invisibile", () => {
    const { container } = render(
      <div>
        {renderAssistantText("La custodia [[codice-", [], undefined, false)}
      </div>,
    );
    expect(container.textContent).toBe("La custodia");
  });
});
