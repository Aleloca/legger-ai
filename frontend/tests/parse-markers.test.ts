/**
 * parseMarkers (lib/parse-markers.ts): splitter puro del testo assistant
 * in segmenti testo / marker / pending. Specchia il contratto del backend
 * (legger/chat/stream.py): formato stretto `[[slug|art.N]]` o
 * `[[slug|art.N|c.M]]`; tutto il resto è testo. Un marker incompleto in
 * coda durante lo streaming diventa un segmento `pending` (invisibile),
 * che si risolve in marker o testo al parse successivo con più testo.
 */

import { describe, expect, it } from "vitest";

import { parseMarkers } from "@/lib/parse-markers";

describe("parseMarkers", () => {
  it("testo senza marker → un solo segmento testo", () => {
    expect(parseMarkers("La custodia è disciplinata dal codice.")).toEqual([
      { type: "text", value: "La custodia è disciplinata dal codice." },
    ]);
  });

  it("stringa vuota → nessun segmento", () => {
    expect(parseMarkers("")).toEqual([]);
  });

  it("marker a metà testo, con comma", () => {
    expect(
      parseMarkers("Vedi [[codice-civile|art.2051|c.1]] per la custodia."),
    ).toEqual([
      { type: "text", value: "Vedi " },
      {
        type: "marker",
        marker: "[[codice-civile|art.2051|c.1]]",
        actRef: "codice-civile",
        article: "2051",
        comma: "1",
      },
      { type: "text", value: " per la custodia." },
    ]);
  });

  it("marker senza comma → comma null", () => {
    expect(parseMarkers("[[dlgs-81-2008|art.18]]")).toEqual([
      {
        type: "marker",
        marker: "[[dlgs-81-2008|art.18]]",
        actRef: "dlgs-81-2008",
        article: "18",
        comma: null,
      },
    ]);
  });

  it("articoli con suffisso (2051-bis) preservati come stringa", () => {
    const segments = parseMarkers("[[codice-civile|art.2051-bis]]");
    expect(segments).toEqual([
      expect.objectContaining({ type: "marker", article: "2051-bis" }),
    ]);
  });

  it("più marker nello stesso testo", () => {
    const segments = parseMarkers(
      "Prima [[codice-civile|art.2051]], poi [[codice-penale|art.575]].",
    );
    expect(segments.map((s) => s.type)).toEqual([
      "text",
      "marker",
      "text",
      "marker",
      "text",
    ]);
  });

  it("marker adiacenti senza testo intermedio", () => {
    const segments = parseMarkers(
      "[[codice-civile|art.2051]][[codice-civile|art.2052]]",
    );
    expect(segments).toEqual([
      expect.objectContaining({ type: "marker", article: "2051" }),
      expect.objectContaining({ type: "marker", article: "2052" }),
    ]);
  });

  it("coppia [[...]] senza pipe → testo, fusa con il contesto", () => {
    expect(parseMarkers("nota [[senza pipe]] qui")).toEqual([
      { type: "text", value: "nota [[senza pipe]] qui" },
    ]);
  });

  it("coppia [[...]] con forma errata (niente art.) → testo", () => {
    expect(parseMarkers("[[codice-civile|2051]]")).toEqual([
      { type: "text", value: "[[codice-civile|2051]]" },
    ]);
  });

  it("act_ref con maiuscole o spazi → testo", () => {
    expect(parseMarkers("[[Codice-Civile|art.1]]")).toEqual([
      { type: "text", value: "[[Codice-Civile|art.1]]" },
    ]);
  });

  it("troppi campi → testo", () => {
    expect(parseMarkers("[[a|art.1|c.2|extra]]")).toEqual([
      { type: "text", value: "[[a|art.1|c.2|extra]]" },
    ]);
  });

  it("trailing `[[` in coda → pending", () => {
    expect(parseMarkers("Vedi [[")).toEqual([
      { type: "text", value: "Vedi " },
      { type: "pending" },
    ]);
  });

  it("trailing `[[codice-` in coda → pending", () => {
    expect(parseMarkers("Vedi [[codice-")).toEqual([
      { type: "text", value: "Vedi " },
      { type: "pending" },
    ]);
  });

  it("trailing `[[x|art.1` in coda → pending", () => {
    expect(parseMarkers("Vedi [[x|art.1")).toEqual([
      { type: "text", value: "Vedi " },
      { type: "pending" },
    ]);
  });

  it("trailing con metà della chiusura `]` → ancora pending", () => {
    expect(parseMarkers("Vedi [[x|art.1]")).toEqual([
      { type: "text", value: "Vedi " },
      { type: "pending" },
    ]);
  });

  it("pending risolto in marker quando arriva il resto del testo", () => {
    const partial = parseMarkers("Vedi [[codice-civile|art.20");
    expect(partial.at(-1)).toEqual({ type: "pending" });

    const full = parseMarkers("Vedi [[codice-civile|art.2051]] qui.");
    expect(full).toEqual([
      { type: "text", value: "Vedi " },
      expect.objectContaining({ type: "marker", article: "2051" }),
      { type: "text", value: " qui." },
    ]);
  });

  it("trailing `[[` non più plausibile come marker (spazi) → testo subito", () => {
    expect(parseMarkers("legenda [[vedi nota")).toEqual([
      { type: "text", value: "legenda [[vedi nota" },
    ]);
  });

  it("un `[[` implausibile non nasconde un marker pending successivo", () => {
    expect(parseMarkers("nota [[vedi sopra e [[codice-")).toEqual([
      { type: "text", value: "nota [[vedi sopra e " },
      { type: "pending" },
    ]);
  });
});
