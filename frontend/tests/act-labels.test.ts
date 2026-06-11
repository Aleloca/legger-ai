/**
 * lib/act-labels.ts: registro condiviso delle etichette degli act_ref
 * (chip-citazione, elenco fonti, intestazione del pannello norma).
 * I casi di citationLabel sono migrati qui dal test del chip (G5:
 * estrazione del modulo condiviso).
 */

import { describe, expect, it } from "vitest";

import { actRefLabel, actRefName, citationLabel } from "@/lib/act-labels";

describe("actRefLabel", () => {
  it("registro noto: codici e testi unici", () => {
    expect(actRefLabel("codice-civile")).toBe("Cod. Civ.");
    expect(actRefLabel("codice-penale")).toBe("Cod. Pen.");
    expect(actRefLabel("codice-procedura-civile")).toBe("C.p.c.");
    expect(actRefLabel("costituzione")).toBe("Cost.");
  });

  it("caso speciale dpr-447-1988 → C.p.p.", () => {
    expect(actRefLabel("dpr-447-1988")).toBe("C.p.p.");
  });

  it("pattern tipo-numero-anno", () => {
    expect(actRefLabel("dlgs-81-2008")).toBe("D.Lgs. 81/2008");
    expect(actRefLabel("legge-241-1990")).toBe("L. 241/1990");
    expect(actRefLabel("dpr-380-2001")).toBe("D.P.R. 380/2001");
    expect(actRefLabel("dl-18-2020")).toBe("D.L. 18/2020");
    expect(actRefLabel("rd-262-1942")).toBe("R.D. 262/1942");
    expect(actRefLabel("dm-37-2008")).toBe("D.M. 37/2008");
    expect(actRefLabel("dpcm-3-2021")).toBe("D.P.C.M. 3/2021");
  });

  it("slug sconosciuto → verbatim", () => {
    expect(actRefLabel("gu-12a3456")).toBe("gu-12a3456");
  });
});

describe("actRefName", () => {
  it("denominazione per esteso per gli atti del registro", () => {
    expect(actRefName("codice-civile")).toBe("Codice civile");
    expect(actRefName("codice-penale")).toBe("Codice penale");
    expect(actRefName("codice-procedura-civile")).toBe(
      "Codice di procedura civile",
    );
    expect(actRefName("dpr-447-1988")).toBe("Codice di procedura penale");
    expect(actRefName("costituzione")).toBe("Costituzione della Repubblica");
  });

  it("slug fuori registro → null (si usa il titolo dell'API)", () => {
    expect(actRefName("dlgs-81-2008")).toBeNull();
    expect(actRefName("gu-12a3456")).toBeNull();
  });
});

describe("citationLabel", () => {
  it("codice-civile → Cod. Civ.", () => {
    expect(citationLabel("codice-civile", "2051", "1")).toBe(
      "Cod. Civ., art. 2051, c. 1",
    );
  });

  it("comma null → niente segmento c.", () => {
    expect(citationLabel("codice-penale", "575", null)).toBe(
      "Cod. Pen., art. 575",
    );
  });

  it("pattern dlgs-N-YYYY → D.Lgs. N/YYYY", () => {
    expect(citationLabel("dlgs-81-2008", "18", "1")).toBe(
      "D.Lgs. 81/2008, art. 18, c. 1",
    );
  });

  it("slug sconosciuto → verbatim", () => {
    expect(citationLabel("gu-12a3456", "5", null)).toBe("gu-12a3456, art. 5");
  });
});
