/**
 * fetchAct (lib/api.ts): un solo fetch per atto (cache di sessione),
 * errori tipizzati con messaggi in italiano, e nessuna cache degli
 * errori (il retry rifà la richiesta).
 *
 * La cache è module-level: ogni test reimporta il modulo con
 * vi.resetModules per partire da una cache vuota.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const ACT = {
  act_ref: "codice-civile",
  title: "Codice civile",
  act_type: "codice",
  vigenza: "vigente",
  collection: "codici",
  articles: [],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn();

async function importApi() {
  vi.resetModules();
  vi.stubGlobal("fetch", fetchMock);
  return import("@/lib/api");
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("fetchAct", () => {
  it("chiama il proxy /api/backend/acts/{act_ref} e restituisce l'atto", async () => {
    const { fetchAct } = await importApi();
    fetchMock.mockResolvedValueOnce(jsonResponse(ACT));
    const act = await fetchAct("codice-civile");
    expect(fetchMock).toHaveBeenCalledWith("/api/backend/acts/codice-civile");
    expect(act.title).toBe("Codice civile");
  });

  it("cache: due richieste per lo stesso atto → un solo fetch", async () => {
    const { fetchAct } = await importApi();
    fetchMock.mockResolvedValue(jsonResponse(ACT));
    const [first, second] = await Promise.all([
      fetchAct("codice-civile"),
      fetchAct("codice-civile"),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);
    // anche una terza chiamata, dopo la risoluzione, resta in cache
    await fetchAct("codice-civile");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("atti diversi → fetch distinti", async () => {
    const { fetchAct } = await importApi();
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(ACT)));
    await fetchAct("codice-civile");
    await fetchAct("dlgs-81-2008");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenLastCalledWith("/api/backend/acts/dlgs-81-2008");
  });

  it("404 → ActFetchError «Norma non trovata nel corpus.»", async () => {
    const { fetchAct, ActFetchError } = await importApi();
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Atto non trovato." }, 404));
    const error = await fetchAct("atto-inesistente").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ActFetchError);
    expect((error as InstanceType<typeof ActFetchError>).status).toBe(404);
    expect((error as Error).message).toBe("Norma non trovata nel corpus.");
  });

  it("503 → il detail del backend arriva al chiamante", async () => {
    const { fetchAct, ActFetchError } = await importApi();
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Aggiornamento del corpus in corso." }, 503),
    );
    const error = await fetchAct("codice-civile").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ActFetchError);
    expect((error as InstanceType<typeof ActFetchError>).status).toBe(503);
    expect((error as Error).message).toBe("Aggiornamento del corpus in corso.");
  });

  it("errore di rete → messaggio di riprova, status null", async () => {
    const { fetchAct, ActFetchError } = await importApi();
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const error = await fetchAct("codice-civile").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ActFetchError);
    expect((error as InstanceType<typeof ActFetchError>).status).toBeNull();
  });

  it("gli errori non restano in cache: il retry rifà il fetch", async () => {
    const { fetchAct } = await importApi();
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500))
      .mockResolvedValueOnce(jsonResponse(ACT));
    await expect(fetchAct("codice-civile")).rejects.toThrow();
    const act = await fetchAct("codice-civile");
    expect(act.act_ref).toBe("codice-civile");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
