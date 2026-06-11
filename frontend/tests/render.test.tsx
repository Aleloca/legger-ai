/**
 * Il seam di rendering (lib/render.tsx): oggi i marker restano testo
 * grezzo; G3 li sostituirà con chip-citazione.
 */

import { describe, expect, it } from "vitest";

import { renderAssistantText } from "@/lib/render";
import type { Citation } from "@/lib/types";

const CITATION: Citation = {
  marker: "[[codice.civile|art.2051|c.1]]",
  act_ref: "codice.civile",
  article: "2051",
  comma: "1",
  title: "Danno cagionato da cosa in custodia",
  vigenza: "vigente",
  verified: true,
  reason: "ok",
};

describe("renderAssistantText", () => {
  it("restituisce il testo invariato, marker compresi", () => {
    const text =
      "La responsabilità è disciplinata da [[codice.civile|art.2051|c.1]].";
    expect(renderAssistantText(text, [CITATION])).toBe(text);
  });

  it("restituisce la stringa vuota per testo vuoto", () => {
    expect(renderAssistantText("", [])).toBe("");
  });
});
