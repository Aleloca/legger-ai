/**
 * Test del client SSE (lib/sse.ts) con fetch + ReadableStream finti:
 * framing (data multi-linea, commenti, frame spezzati tra chunk),
 * dispatch tipizzato, abort silenzioso, error terminale.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChat, type StreamHandlers } from "@/lib/sse";

const USER_TURN = [{ role: "user" as const, content: "art. 2051 c.c." }];

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function mockFetch(chunks: string[]) {
  const spy = vi.fn().mockResolvedValue(streamResponse(chunks));
  vi.stubGlobal("fetch", spy);
  return spy;
}

function recordingHandlers() {
  const calls: { name: string; payload: unknown }[] = [];
  const handlers: StreamHandlers = {
    onStatus: (stage) => calls.push({ name: "status", payload: stage }),
    onSources: (sources) => calls.push({ name: "sources", payload: sources }),
    onToken: (text) => calls.push({ name: "token", payload: text }),
    onCitation: (c) => calls.push({ name: "citation", payload: c }),
    onDone: (data) => calls.push({ name: "done", payload: data }),
    onError: (message) => calls.push({ name: "error", payload: message }),
  };
  return { calls, handlers };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamChat", () => {
  it("invia POST con il body del contratto", async () => {
    const spy = mockFetch([
      'event: done\ndata: {"stop_reason": "end_turn", "truncated": false}\n\n',
    ]);
    await streamChat(USER_TURN, {});
    expect(spy).toHaveBeenCalledOnce();
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/backend/chat");
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ messages: USER_TURN });
  });

  it("include `config` nel body quando passata nelle opzioni", async () => {
    const spy = mockFetch([
      'event: done\ndata: {"stop_reason": "end_turn", "truncated": false}\n\n',
    ]);
    const config = {
      answer_model: "claude-opus-4-8",
      answer_effort: "max",
      qu_model: null,
      qu_effort: null,
    };
    await streamChat(USER_TURN, {}, { config });
    expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({
      messages: USER_TURN,
      config,
    });
  });

  it("config null/assente → body senza la chiave config", async () => {
    const spy = mockFetch([
      'event: done\ndata: {"stop_reason": "end_turn", "truncated": false}\n\n',
    ]);
    await streamChat(USER_TURN, {}, { config: null });
    expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({ messages: USER_TURN });
  });

  it("smista la sequenza completa di eventi nell'ordine del contratto", async () => {
    mockFetch([
      'event: status\ndata: {"stage": "searching"}\n\n' +
        'event: sources\ndata: {"sources": [{"act_ref": "codice.civile", "article": "2051", "title": "Danno cagionato da cosa in custodia", "vigenza": "vigente", "anchor": "art-2051"}]}\n\n' +
        'event: token\ndata: {"text": "La custodia "}\n\n' +
        'event: token\ndata: {"text": "[[codice.civile|art.2051|c.1]]"}\n\n' +
        'event: citation\ndata: {"marker": "[[codice.civile|art.2051|c.1]]", "act_ref": "codice.civile", "article": "2051", "comma": "1", "title": "Danno cagionato da cosa in custodia", "vigenza": "vigente", "verified": true, "reason": "ok"}\n\n' +
        'event: done\ndata: {"stop_reason": "end_turn", "truncated": false}\n\n',
    ]);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);

    expect(calls.map((c) => c.name)).toEqual([
      "status",
      "sources",
      "token",
      "token",
      "citation",
      "done",
    ]);
    expect(calls[0].payload).toBe("searching");
    expect(calls[1].payload).toEqual([
      expect.objectContaining({ act_ref: "codice.civile", anchor: "art-2051" }),
    ]);
    expect(calls[3].payload).toBe("[[codice.civile|art.2051|c.1]]");
    expect(calls[4].payload).toMatchObject({ verified: true, reason: "ok" });
    expect(calls[5].payload).toEqual({ stop_reason: "end_turn", truncated: false });
  });

  it("riunisce le data multi-linea con \\n prima del parse JSON", async () => {
    mockFetch([
      'event: token\ndata: {"text":\ndata: "ciao"}\n\n' +
        'event: done\ndata: {"stop_reason": null, "truncated": false}\n\n',
    ]);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls[0]).toEqual({ name: "token", payload: "ciao" });
  });

  it("ignora le righe-commento e i campi sconosciuti", async () => {
    mockFetch([
      ": keep-alive\n" +
        "id: 7\n" +
        'event: token\ndata: {"text": "ok"}\n\n' +
        ": another comment\n" +
        'event: done\ndata: {"stop_reason": null, "truncated": false}\n\n',
    ]);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls.map((c) => c.name)).toEqual(["token", "done"]);
  });

  it("ricompone gli eventi spezzati tra chunk (anche a metà riga)", async () => {
    mockFetch([
      "event: tok",
      'en\ndata: {"text": "spez',
      'zato"}\n',
      "\nevent: done\n",
      'data: {"stop_reason": "end_turn", "truncated": true}\n\n',
    ]);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls).toEqual([
      { name: "token", payload: "spezzato" },
      { name: "done", payload: { stop_reason: "end_turn", truncated: true } },
    ]);
  });

  it("accetta terminatori CRLF", async () => {
    mockFetch([
      'event: token\r\ndata: {"text": "crlf"}\r\n\r\n' +
        'event: done\r\ndata: {"stop_reason": null, "truncated": false}\r\n\r\n',
    ]);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls).toEqual([
      { name: "token", payload: "crlf" },
      { name: "done", payload: { stop_reason: null, truncated: false } },
    ]);
  });

  it("scarta frame con evento sconosciuto o JSON malformato", async () => {
    mockFetch([
      'event: heartbeat\ndata: {"x": 1}\n\n' +
        "event: token\ndata: {not json}\n\n" +
        'event: token\ndata: {"text": "valido"}\n\n' +
        'event: done\ndata: {"stop_reason": null, "truncated": false}\n\n',
    ]);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls.map((c) => c.name)).toEqual(["token", "done"]);
    expect(calls[0].payload).toBe("valido");
  });

  it("scarta frame con payload malformato (shape guard)", async () => {
    mockFetch([
      'event: token\ndata: {"text": 42}\n\n' + // text non-stringa
        'event: token\ndata: {"wrong": "campo"}\n\n' + // text assente
        'event: token\ndata: null\n\n' + // payload non-oggetto
        'event: status\ndata: {"nope": true}\n\n' + // stage assente
        'event: sources\ndata: {"sources": "non-array"}\n\n' +
        'event: citation\ndata: {"verified": true}\n\n' + // marker assente
        'event: error\ndata: {"message": 1}\n\n' + // message non-stringa: scartato, NON terminale
        'event: token\ndata: {"text": "valido"}\n\n' +
        'event: done\ndata: {"stop_reason": null, "truncated": false}\n\n',
    ]);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls).toEqual([
      { name: "token", payload: "valido" },
      { name: "done", payload: { stop_reason: null, truncated: false } },
    ]);
  });

  it("error è terminale: niente done, eventi successivi ignorati", async () => {
    mockFetch([
      'event: status\ndata: {"stage": "searching"}\n\n' +
        'event: error\ndata: {"message": "Si è verificato un errore."}\n\n' +
        'event: token\ndata: {"text": "mai consegnato"}\n\n',
    ]);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls.map((c) => c.name)).toEqual(["status", "error"]);
    expect(calls[1].payload).toBe("Si è verificato un errore.");
  });

  it("risposta HTTP non-ok → onError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nope", { status: 500 })),
    );
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls.map((c) => c.name)).toEqual(["error"]);
  });

  it("flusso chiuso senza done né error → onError", async () => {
    mockFetch(['event: token\ndata: {"text": "a metà"}\n\n']);
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls.map((c) => c.name)).toEqual(["token", "error"]);
  });

  it("abort silenzioso: nessuna callback dopo l'annullamento", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("annullato", "AbortError")),
    );
    const { calls, handlers } = recordingHandlers();
    const controller = new AbortController();
    controller.abort();
    await streamChat(USER_TURN, handlers, { signal: controller.signal });
    expect(calls).toEqual([]);
  });

  it("errore di rete (fetch reject non-abort) → onError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("failed to fetch")),
    );
    const { calls, handlers } = recordingHandlers();
    await streamChat(USER_TURN, handlers);
    expect(calls.map((c) => c.name)).toEqual(["error"]);
  });
});
