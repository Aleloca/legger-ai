# Fixture dal corpus reale

File copiati da `italia-corpus` (commit `a380de55fd8690b7a30e0bc8492754ed4dbf585d`,
lo stesso analizzato in `docs/corpus-analysis.md`). I percorsi relativi sotto
`corpus/` replicano ESATTAMENTE i nomi di cartella e di file del corpus (spazi e
parentesi inclusi): i test di derivazione di act_ref e vigenza dipendono dai path
reali. I riferimenti A1-A10 rimandano alla sezione "Assunzioni del parser" di
`docs/corpus-analysis.md`.

Nessun file e' stato alterato: i tre file troncati sono prefissi byte-identici
dell'originale, tagliati a un confine sicuro (l'unica eccezione e' il padding NUL
di `012U0343.md`, accorciato ma ricostruito con gli stessi byte `\x00`).

| Fixture (path sotto `corpus/`) | Dim. orig. → committata | Fenomeni coperti | Troncamento |
| --- | --- | --- | --- |
| `Codici/Codice della protezione civile. 18G00011.md` | 230.191 B → identico | Atto recente con articoli ATX `### Art. N` (51) e setext h1/h2 (A2, A4.1); partizioni `CAPO N - ...` come h2 setext con prefisso artificiale del convertitore (A6); convenzioni Normattiva: `((...))` (58), blocchi `AGGIORNAMENTO (n)` preceduti da `----` separatore (5), link URN NIR (198) (A8); h1 con `(Raccolta 2018)` nel titolo (A3); commi numerati `1.`/`2-bis.` (A5). | nessuno |
| `Atti di attuazione Regolamenti UE/Attuazione del regolamento CE n. 3381-94 e della decisione n. 94-942-PESC sullesportazione di beni a duplice uso.md` | 2.047 B → identico | Atto breve con articoli setext-h2 `Art. N` (A4.2); ogni articolo contiene solo `((PROVVEDIMENTO ABROGATO DAL ...))`: atto abrogato inline pur stando in una collezione "vigente" (A7, A8). | nessuno |
| `Atti di attuazione Regolamenti UE/Modalita di consegna del vino in distilleria inapplicazione dellart. 1 del regolamento CEE n. 1410-87 peri produttori che nella campagna 1986-87 non hannotrasformato i mosti in mosti concentrati.md` | 3.053 B → identico | Atto breve setext-h2 con contenuto reale (A4.2); titolo h2 setext che si estende su 4 righe prima della riga di `-`; h2 spurie (`IL MINISTRO`, `Attesa`) da non confondere con articoli; link EUR-Lex oltre a URN NIR (A8); commi non numerati (A5). | nessuno |
| `Codici/Approvazione del testo definitivo del Codice Penale. 030U1398.md` | 1.125.924 B → 109.082 B | Codice storico multivigente: marcatori plain `Codice Penale-art. N [bis\|ter\|...]` NON heading (96 marcatori, inclusi `3 bis`, `20 bis`, `32 bis/ter/quater/quinquies`) (A4.3); decreto di approvazione con articoli setext-h2 `Art. 1-3` (A4.2); gerarchia LIBRO/TITOLO assente dai heading (A6); filename `titolo + codice redazionale` (A3). | Prefisso byte-identico tagliato subito prima della riga `Codice Penale-art. 85`: restano gli artt. 1-84 del CP piu' il decreto di approvazione. |
| `Codici/1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md` | 3.853.416 B → 2.255.616 B | Codice Civile: HTML Akoma Ntoso codificato base64 (inizia con `PGh0bWw`), NON Markdown (A1); naming `YYYY-MM-DD_codice_VIGENZA_data_V0.md` (A3); classi `*-akn` (`article-num-akn`, `attachment-name`, `attachment-just-text`, `ins-akn`, `art_abrogato-akn`, `art_aggiornamento-akn`). Richiesto dalla eval di Fase C: il CC esiste solo in questo formato. | Prefisso base64 byte-identico (lunghezza multipla di 4, decodifica sempre valida) corrispondente a ~1,6 MB di HTML decodificato: preleggi complete + artt. 1-2051 del CC, 283 blocchi aggiornamento, 160 articoli abrogati. Il taglio cade esattamente prima del `<div>` dell'art. 2052: l'art. 2051 ("Danno cagionato da cosa in custodia") e' completo, l'HTML decodificato e' un prefisso del documento (parser HTML leniente richiesto, nessun tag di chiusura finale). Esteso dall'originale taglio all'art. 1170 per la eval di Fase C, che richiede l'art. 2051 c.c. |
| `Regi decreti/012U0343.md` | 1.047.968 B → 4.459 B | Padding NUL in coda (A9): 363 B di contenuto reale + byte `\x00`; il parser deve troncare al primo NUL. Filename = solo codice redazionale (A3); heading anomalo `### (012U0343)` sottolineato con `----`; articolo unico con `((PROVVEDIMENTO ABROGATO DAL ...))` (A8). | Contenuto reale intatto (363 B, identico all'originale); padding NUL ridotto da ~1 MiB a 4.096 byte (stessi byte `\x00`, solo in numero minore). |
| `Leggi finanziarie e di bilancio/Bilancio di previsione dello Stato per lanno finanziario 2025 e bilancio pluriennale per il triennio 2025-2027. 24G00229.md` | 899.755 B → 232.573 B | Articolo-fiume (A5, A10): `### Art. 1` ATX con centinaia di commi numerati `N.` a inizio riga (249 nel fixture, 1.069 nell'originale); filename `titolo + codice redazionale` (A3); fitta rete di link URN NIR con frammenti `~art1-com238` (A8). | Prefisso byte-identico tagliato subito prima del comma `250.` dell'art. 1: il fixture termina dentro l'art. 1 (gli artt. 2+ non sono presenti). |
| `Atti normativi abrogati (in originale)/Annullamento di partita 020U1371.md` | 253 B → identico | Atto piu' piccolo del corpus (A10); collezione → vigenza `abrogato` (A7); articolo unico setext con corpo letterale `null`; filename senza punto prima del codice redazionale (A3). | nessuno |
| `DL decaduti/Disposizioni urgenti in materia sanitaria_11.md` | 347 B → identico | Collezione → vigenza `decaduto` (A7); articoli setext con corpo `DECRETO DECADUTO`; filename con suffisso `_N` di disambiguazione duplicati (A3, A10). | nessuno |

Totale committato: ~2,8 MB (9 file).

Copertura della checklist del task B2: il punto 1 (atto moderno ATX), il punto 9
(partizioni CAPO) e il punto 10 (convenzioni Normattiva) sono coperti dallo stesso
file (Codice della protezione civile); il punto 7 (atto minuscolo) e il punto 8a
(collezione abrogati) sono coperti dallo stesso file (`Annullamento di partita`);
il punto 2 (setext-h2) ha due fixture (atto vuoto-abrogato e atto con contenuto)
oltre al decreto di approvazione del CP.
