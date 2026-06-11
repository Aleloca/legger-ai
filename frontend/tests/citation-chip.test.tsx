/**
 * CitationChip (components/citation-chip.tsx): etichetta umanizzata dal
 * registro degli act_ref (lib/act-labels.ts, testato a parte), pallino
 * di vigenza, stile ambra + tooltip per citazioni non verificate,
 * onClick (cablato dal G4).
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CitationChip } from "@/components/citation-chip";
import type { Citation } from "@/lib/types";

const VERIFIED: Citation = {
  marker: "[[codice-civile|art.2051|c.1]]",
  act_ref: "codice-civile",
  article: "2051",
  comma: "1",
  title: "Danno cagionato da cosa in custodia",
  vigenza: "vigente",
  verified: true,
  reason: "ok",
};

describe("CitationChip", () => {
  it("citazione verificata: etichetta umanizzata, nessun tooltip d'allerta", () => {
    render(
      <CitationChip
        actRef="codice-civile"
        article="2051"
        comma="1"
        citation={VERIFIED}
      />,
    );
    const chip = screen.getByRole("button", {
      name: "Cod. Civ., art. 2051, c. 1",
    });
    expect(chip).not.toHaveAttribute("title", "citazione non verificata");
    expect(chip).toHaveAttribute("data-verified", "true");
  });

  it("vigenza vigente → pallino verde", () => {
    const { container } = render(
      <CitationChip
        actRef="codice-civile"
        article="2051"
        comma={null}
        citation={VERIFIED}
      />,
    );
    expect(container.querySelector(".bg-vigente")).toBeInTheDocument();
  });

  it("vigenza abrogato → pallino grigio", () => {
    const { container } = render(
      <CitationChip
        actRef="rd-262-1942"
        article="1"
        comma={null}
        citation={{ ...VERIFIED, vigenza: "abrogato" }}
      />,
    );
    expect(container.querySelector(".bg-abrogato")).toBeInTheDocument();
  });

  it("citation event con verified=false → stile ambra + tooltip", () => {
    render(
      <CitationChip
        actRef="codice-civile"
        article="9999"
        comma={null}
        citation={{
          ...VERIFIED,
          article: "9999",
          verified: false,
          reason: "article_not_in_context",
          title: null,
          vigenza: null,
        }}
      />,
    );
    const chip = screen.getByRole("button");
    expect(chip).toHaveAttribute("title", "citazione non verificata");
    expect(chip).toHaveAttribute("data-verified", "false");
    expect(chip.className).toContain("non-verificato");
  });

  it("nessun citation event corrispondente → trattata come non verificata", () => {
    render(<CitationChip actRef="codice-civile" article="2051" comma={null} />);
    const chip = screen.getByRole("button");
    expect(chip).toHaveAttribute("title", "citazione non verificata");
    expect(chip).toHaveAttribute("data-verified", "false");
  });

  it("reason comma_not_in_context (advisory) → chip normale verificato", () => {
    render(
      <CitationChip
        actRef="codice-civile"
        article="2051"
        comma="9"
        citation={{ ...VERIFIED, comma: "9", reason: "comma_not_in_context" }}
      />,
    );
    const chip = screen.getByRole("button");
    expect(chip).toHaveAttribute("data-verified", "true");
    expect(chip).not.toHaveAttribute("title", "citazione non verificata");
    expect(chip.className).not.toContain("non-verificato");
  });

  it("onClick viene invocato", () => {
    const onClick = vi.fn();
    render(
      <CitationChip
        actRef="codice-civile"
        article="2051"
        comma={null}
        citation={VERIFIED}
        onClick={onClick}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
