/**
 * Smoke test delle pagine editoriali (/come-funziona, /metodologia) e
 * del footer persistente con i link silenziosi. Le pagine sono server
 * components puri: si montano direttamente con RTL.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ComeFunzionaPage from "@/app/come-funziona/page";
import MetodologiaPage from "@/app/metodologia/page";
import { SiteFooter } from "@/components/site-footer";

describe("pagina /come-funziona", () => {
  it("monta titolo, sezioni numerate e back link alla chat", () => {
    render(<ComeFunzionaPage />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Come funziona Legger" }),
    ).toBeInTheDocument();
    // back link in testa e in coda
    const backLinks = screen.getAllByRole("link", {
      name: "← Torna alla chat",
    });
    expect(backLinks.length).toBeGreaterThanOrEqual(1);
    for (const link of backLinks) expect(link).toHaveAttribute("href", "/");
    // struttura da paper: abstract + sezioni numerate
    expect(screen.getByText("Abstract")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "Il problema: perché un modello generico non basta",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Limiti dichiarati" }),
    ).toBeInTheDocument();
    // un numero chiave del corpus, mai inventato
    expect(screen.getAllByText(/181\.870/).length).toBeGreaterThanOrEqual(1);
  });
});

describe("pagina /metodologia", () => {
  it("monta titolo, tabella del benchmark e back link alla chat", () => {
    render(<MetodologiaPage />);
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Metodologia e validazione",
      }),
    ).toBeInTheDocument();
    const backLinks = screen.getAllByRole("link", {
      name: "← Torna alla chat",
    });
    expect(backLinks.length).toBeGreaterThanOrEqual(1);
    for (const link of backLinks) expect(link).toHaveAttribute("href", "/");
    // la tabella del benchmark con i numeri reali
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("voyage-4-large")).toBeInTheDocument();
    expect(screen.getAllByText("96,7%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("voyage-law-2")).toBeInTheDocument();
  });
});

describe("SiteFooter", () => {
  it("disclaimer + link silenziosi alle pagine editoriali", () => {
    render(<SiteFooter />);
    expect(
      screen.getByText(/non costituisce consulenza legale/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Come funziona" })).toHaveAttribute(
      "href",
      "/come-funziona",
    );
    expect(screen.getByRole("link", { name: "Metodologia" })).toHaveAttribute(
      "href",
      "/metodologia",
    );
  });
});
