/**
 * SourcesList (components/sources-list.tsx): «Fonti consultate (N)»
 * chiusa di default, espansione con righe umanizzate (registro di
 * lib/act-labels.ts), fonti NON citate incluse (trasparenza), pallino
 * di vigenza, click → onSourceClick con il riferimento per la
 * split-view.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SourcesList } from "@/components/sources-list";
import type { Source } from "@/lib/types";

const SOURCES: Source[] = [
  {
    act_ref: "codice-civile",
    article: "2051",
    title: "Danno cagionato da cosa in custodia",
    vigenza: "vigente",
    anchor: "art-2051",
  },
  {
    // fonte consultata ma NON citata nella risposta: deve esserci comunque
    act_ref: "codice-civile",
    article: "2043",
    title: "Risarcimento per fatto illecito",
    vigenza: "vigente",
    anchor: "art-2043",
  },
  {
    act_ref: "rd-262-1942",
    article: "1",
    title: null,
    vigenza: "abrogato",
    anchor: "art-1",
  },
];

describe("SourcesList", () => {
  it("chiusa di default: intestazione con il conteggio, nessuna riga", () => {
    render(<SourcesList sources={SOURCES} />);
    const toggle = screen.getByRole("button", {
      name: "Fonti consultate (3)",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.queryByText(/art\. 2051/)).not.toBeInTheDocument();
  });

  it("espansa: una riga per fonte, etichetta umanizzata «label — art. N»", () => {
    render(<SourcesList sources={SOURCES} />);
    fireEvent.click(screen.getByRole("button", { name: "Fonti consultate (3)" }));

    expect(
      screen.getByRole("button", { name: "Fonti consultate (3)" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("Cod. Civ. — art. 2051")).toBeInTheDocument();
    expect(screen.getByText("R.D. 262/1942 — art. 1")).toBeInTheDocument();
  });

  it("include le fonti non citate (trasparenza sul contesto)", () => {
    render(<SourcesList sources={SOURCES} />);
    fireEvent.click(screen.getByRole("button", { name: "Fonti consultate (3)" }));
    expect(screen.getByText("Cod. Civ. — art. 2043")).toBeInTheDocument();
    expect(
      screen.getByText("Risarcimento per fatto illecito"),
    ).toBeInTheDocument();
  });

  it("richiudibile: il secondo click nasconde le righe", () => {
    render(<SourcesList sources={SOURCES} />);
    const toggle = screen.getByRole("button", { name: "Fonti consultate (3)" });
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("pallino di vigenza: vigente verde, abrogato grigio", () => {
    const { container } = render(<SourcesList sources={SOURCES} />);
    fireEvent.click(screen.getByRole("button", { name: "Fonti consultate (3)" }));
    expect(container.querySelectorAll(".bg-vigente")).toHaveLength(2);
    expect(container.querySelectorAll(".bg-abrogato")).toHaveLength(1);
  });

  it("click su una riga → onSourceClick con il riferimento (comma null)", () => {
    const onSourceClick = vi.fn();
    render(<SourcesList sources={SOURCES} onSourceClick={onSourceClick} />);
    fireEvent.click(screen.getByRole("button", { name: "Fonti consultate (3)" }));
    fireEvent.click(screen.getByText("Cod. Civ. — art. 2043"));
    expect(onSourceClick).toHaveBeenCalledWith({
      actRef: "codice-civile",
      article: "2043",
      comma: null,
    });
  });

  it("conteggio al singolare e al plurale nel numero", () => {
    const { rerender } = render(<SourcesList sources={[SOURCES[0]]} />);
    expect(
      screen.getByRole("button", { name: "Fonti consultate (1)" }),
    ).toBeInTheDocument();
    rerender(<SourcesList sources={SOURCES} />);
    expect(
      screen.getByRole("button", { name: "Fonti consultate (3)" }),
    ).toBeInTheDocument();
  });
});
