# Analisi del corpus italia-corpus

- Percorso: `/Users/aleloca/git/italia-corpus`
- Commit analizzato: `a380de55fd8690b7a30e0bc8492754ed4dbf585d` del 2026-06-09T11:58:52Z
- Numero di commit nella storia: 154 (clone shallow: false)
- Data dell'analisi: 2026-06-10
- Generato da `backend/scripts/analyze_corpus.py` (campione: 500 file/collezione, seed 20260610)

## Collezioni (cartelle top-level)

| Collezione | File .md | Dimensione |
| --- | ---: | ---: |
| Atti di attuazione Regolamenti UE | 39 | 1.3 MB |
| Atti di recepimento direttive UE | 1091 | 77.8 MB |
| Atti normativi abrogati (in originale) | 123837 | 665.6 MB |
| Codici | 40 | 30.6 MB |
| DL decaduti | 1731 | 3.3 MB |
| DL e leggi di conversione | 9742 | 120.8 MB |
| DL proroghe | 75 | 3.5 MB |
| DPCM | 359 | 17.7 MB |
| DPR | 47434 | 303.6 MB |
| Decreti Legislativi | 2911 | 205.8 MB |
| Decreti legislativi luogotenenziali | 1216 | 3.8 MB |
| Leggi contenenti deleghe | 77 | 8.6 MB |
| Leggi costituzionali | 49 | 575.7 KB |
| Leggi delega e relativi provvedimenti delegati | 1140 | 120.9 MB |
| Leggi di delegazione europea | 32 | 7.1 MB |
| Leggi di ratifica | 2333 | 81.7 MB |
| Leggi finanziarie e di bilancio | 57 | 26.3 MB |
| Regi decreti | 90982 | 71.4 GB |
| Regi decreti legislativi | 120 | 749.5 KB |
| Regolamenti di delegificazione | 375 | 21.7 MB |
| Regolamenti governativi | 1940 | 92.7 MB |
| Regolamenti ministeriali | 2077 | 98.3 MB |
| Testi Unici | 255 | 26.9 MB |
| **Totale** | **287912** | **73.3 GB** |

> NB: le dimensioni includono il padding NUL descritto in "Casi patologici":
> la quasi totalita' dei ~71 GB di `Regi decreti` e' padding, non testo.

Voci saltate dentro le collezioni (sottocartelle o file non `.md`): 0.

## Cartelle di vigenza (abrogati / decaduti)

Nomi ESATTI delle cartelle che codificano lo stato di vigenza:

- `Atti normativi abrogati (in originale)`
- `DL decaduti`

Tutte le altre collezioni contengono atti vigenti (o nella versione originale).

## Formati dei file (campione)

Due formati coesistono nel corpus:

1. **Markdown** (conversione pandoc dell'HTML Normattiva): intestazioni setext
   (`====` per h1, `----` per h2) piu' ATX `###` nei file recenti.
2. **HTML Akoma Ntoso codificato base64** in un file `.md`: tutti i file con naming
   `YYYY-MM-DD_<codice>_VIGENZA_<data>_V0.md` o `..._ORIGINALE_V0.md` iniziano con
   `PGh0bWw` (= base64 di `<html`). NON sono Markdown.

| Collezione | Campione | Markdown | Base64-HTML | Frontmatter YAML | Senza intestazioni |
| --- | ---: | ---: | ---: | ---: | ---: |
| Atti di attuazione Regolamenti UE | 39 | 39 | 0 | 0 | 0 |
| Atti di recepimento direttive UE | 500 | 499 | 1 | 0 | 0 |
| Atti normativi abrogati (in originale) | 500 | 500 | 0 | 0 | 0 |
| Codici | 40 | 38 | 2 | 0 | 0 |
| DL decaduti | 500 | 500 | 0 | 0 | 0 |
| DL e leggi di conversione | 500 | 500 | 0 | 0 | 0 |
| DL proroghe | 75 | 75 | 0 | 0 | 0 |
| DPCM | 359 | 359 | 0 | 0 | 0 |
| DPR | 500 | 500 | 0 | 0 | 0 |
| Decreti Legislativi | 500 | 500 | 0 | 0 | 0 |
| Decreti legislativi luogotenenziali | 500 | 500 | 0 | 0 | 0 |
| Leggi contenenti deleghe | 77 | 77 | 0 | 0 | 0 |
| Leggi costituzionali | 49 | 49 | 0 | 0 | 0 |
| Leggi delega e relativi provvedimenti delegati | 500 | 499 | 1 | 0 | 0 |
| Leggi di delegazione europea | 32 | 32 | 0 | 0 | 0 |
| Leggi di ratifica | 500 | 497 | 3 | 0 | 0 |
| Leggi finanziarie e di bilancio | 57 | 57 | 0 | 0 | 0 |
| Regi decreti | 500 | 500 | 0 | 0 | 0 |
| Regi decreti legislativi | 120 | 120 | 0 | 0 | 0 |
| Regolamenti di delegificazione | 375 | 375 | 0 | 0 | 0 |
| Regolamenti governativi | 500 | 500 | 0 | 0 | 0 |
| Regolamenti ministeriali | 500 | 500 | 0 | 0 | 0 |
| Testi Unici | 255 | 254 | 1 | 0 | 0 |

## Censimento delle intestazioni (campione)

Conteggi aggregati per collezione sui file Markdown del campione. `s1`/`s2` sono
intestazioni setext di livello 1/2; `#N` sono intestazioni ATX di livello N.

| Collezione | s1 | s2 | #1 | #2 | #3 | #4 | #5 | #6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Atti di attuazione Regolamenti UE | 39 | 424 | 0 | 0 | 34 | 0 | 0 | 0 |
| Atti di recepimento direttive UE | 508 | 7145 | 0 | 0 | 4607 | 0 | 0 | 0 |
| Atti normativi abrogati (in originale) | 500 | 2298 | 0 | 0 | 423 | 0 | 0 | 0 |
| Codici | 41 | 1969 | 0 | 0 | 8685 | 0 | 0 | 0 |
| DL decaduti | 500 | 4403 | 0 | 0 | 881 | 0 | 0 | 0 |
| DL e leggi di conversione | 504 | 2379 | 0 | 0 | 212 | 0 | 0 | 0 |
| DL proroghe | 75 | 1088 | 0 | 0 | 445 | 0 | 0 | 0 |
| DPCM | 409 | 3375 | 0 | 0 | 1698 | 0 | 0 | 0 |
| DPR | 500 | 2233 | 0 | 0 | 593 | 0 | 0 | 0 |
| Decreti Legislativi | 527 | 7023 | 0 | 0 | 6471 | 0 | 0 | 0 |
| Decreti legislativi luogotenenziali | 500 | 3265 | 0 | 0 | 520 | 0 | 0 | 0 |
| Leggi contenenti deleghe | 77 | 868 | 0 | 0 | 436 | 0 | 0 | 0 |
| Leggi costituzionali | 49 | 440 | 0 | 0 | 318 | 0 | 0 | 0 |
| Leggi delega e relativi provvedimenti delegati | 508 | 6238 | 0 | 0 | 5518 | 0 | 0 | 0 |
| Leggi di delegazione europea | 32 | 418 | 0 | 0 | 923 | 0 | 0 | 0 |
| Leggi di ratifica | 531 | 4484 | 0 | 0 | 482 | 0 | 0 | 0 |
| Leggi finanziarie e di bilancio | 582 | 2005 | 0 | 0 | 1336 | 0 | 0 | 0 |
| Regi decreti | 500 | 1552 | 0 | 0 | 65 | 0 | 0 | 0 |
| Regi decreti legislativi | 120 | 914 | 0 | 0 | 108 | 0 | 0 | 0 |
| Regolamenti di delegificazione | 397 | 4360 | 0 | 0 | 2454 | 0 | 0 | 0 |
| Regolamenti governativi | 519 | 4287 | 0 | 0 | 3006 | 0 | 0 | 0 |
| Regolamenti ministeriali | 521 | 6255 | 0 | 0 | 1663 | 0 | 0 | 0 |
| Testi Unici | 258 | 2711 | 0 | 0 | 9559 | 0 | 0 | 0 |

### Marcatori di articolo (campione)

Tre stili distinti di marcatura degli articoli:

- `atx`: intestazione ATX `### Art. N` (atti recenti, ~post 2000);
- `setext`: intestazione setext `Art. N` + `----` (es. articoli del decreto di approvazione);
- `plain`: riga piatta `<Titolo atto>-art. N [bis|ter|...]` NON marcata come heading
  (atti storici multivigenti, es. Codice Penale, Codice di procedura civile).

| Collezione | File con `### Art` | File con `Art.` setext | File con marcatore plain |
| --- | ---: | ---: | ---: |
| Atti di attuazione Regolamenti UE | 0 | 37 | 0 |
| Atti di recepimento direttive UE | 93 | 407 | 11 |
| Atti normativi abrogati (in originale) | 7 | 367 | 23 |
| Codici | 24 | 14 | 12 |
| DL decaduti | 38 | 462 | 0 |
| DL e leggi di conversione | 0 | 474 | 37 |
| DL proroghe | 6 | 69 | 0 |
| DPCM | 57 | 303 | 4 |
| DPR | 7 | 433 | 9 |
| Decreti Legislativi | 80 | 413 | 6 |
| Decreti legislativi luogotenenziali | 15 | 479 | 3 |
| Leggi contenenti deleghe | 14 | 63 | 0 |
| Leggi costituzionali | 7 | 42 | 0 |
| Leggi delega e relativi provvedimenti delegati | 96 | 399 | 7 |
| Leggi di delegazione europea | 22 | 10 | 0 |
| Leggi di ratifica | 2 | 467 | 168 |
| Leggi finanziarie e di bilancio | 41 | 16 | 1 |
| Regi decreti | 5 | 493 | 17 |
| Regi decreti legislativi | 4 | 99 | 1 |
| Regolamenti di delegificazione | 64 | 311 | 7 |
| Regolamenti governativi | 67 | 407 | 54 |
| Regolamenti ministeriali | 55 | 443 | 6 |
| Testi Unici | 59 | 188 | 160 |

### Partizioni (LIBRO / PARTE / TITOLO / CAPO / SEZIONE) nelle intestazioni

| Partizione | Occorrenze nei heading (campione) | Esempi |
| --- | ---: | --- |
| LIBRO | 0 |  |
| PARTE | 7 | `Parte I - SOLVENTI DA UTILIZZARE, RISPETTANDO LE CORRETTE PRASSI DI FABBRICAZION`; `PARTE`; `PARTE I - UFFICI AUTOMATIZZATI` |
| TITOLO | 3 | `titolo`; `TITOLO`; `Titolo` |
| CAPO | 7849 | `CAPO I Titolo I DEFINIZIONI`; `CAPO II Titolo II CAMPO DI APPLICAZIONE`; `CAPO III Titolo III IMMISSIONE IN COMMERCIO Capo I Autorizzazione all'immissione` |
| SEZIONE | 0 |  |

Esempi di testo per livello di intestazione (primi incontrati nel campione):

- livello 1: `DECRETO-LEGGE 06 luglio 1993 n. 216`; `DECRETO 20 maggio 1988 n. 179`; `DECRETO LEGISLATIVO 17 marzo 1995 n. 220`; `DECRETO LEGISLATIVO 24 febbraio 1997 n. 89`; `DECRETO LEGISLATIVO 08 novembre 2021 n. 210`
- livello 2: `Adempimenti finanziari per l'attuazione del regolamento CEE n. 880/92 `; `IL PRESIDENTE DELLA REPUBBLICA`; `Art. 1`; `Art. 2`; `Il presente decreto, munito del sigillo dello Stato, sara' inserito ne`
- livello 3: `Dato a Roma, addi' 6 luglio 1993`; `Roma, addi' 20 maggio 1988`; `Dato a Roma, addi' 8 novembre 2021`; `Data a Roma, addi' 27 gennaio 1971`; `Data a Roma, addi' 9 agosto 1993`

## Collezione `Codici` (analisi completa)

Distribuzione delle dimensioni (byte) sui 40 file:

| min | p25 | mediana | p75 | max | media |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 30.6 KB | 281.5 KB | 443.5 KB | 940.4 KB | 3.7 MB | 782.2 KB |

Primi 50 caratteri di 10 file campione:

| File | Primi 50 caratteri |
| --- | --- |
| 1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md | `PGh0bWw+CiAgICA8aGVhZD4KICAgICAgICA8TUVUQSBodHRwLW` |
| Codice dei contratti pubblici. 16G00062.md | `DECRETO LEGISLATIVO 18 aprile 2016 n. 50\n\n\nDECRETO` |
| Codice del Terzo settore a norma dellarticolo 1 comma 2 lett | `DECRETO LEGISLATIVO 03 luglio 2017 n. 117\n\n\nDECRET` |
| Codice del consumo a norma dellarticolo 7 della legge 29 lug | `DECRETO LEGISLATIVO 06 settembre 2005 n. 206\n\n\nDEC` |
| Codice della normativa statale in tema di ordinamento e merc | `DECRETO LEGISLATIVO 23 maggio 2011 n. 79\n\n\nDECRETO` |
| Codice della proprieta industriale a norma dellarticolo 15de | `DECRETO LEGISLATIVO 10 febbraio 2005 n. 30\n\n\nDECRE` |
| Codice delle comunicazioni elettroniche.md | `DECRETO LEGISLATIVO 01 agosto 2003 n. 259\n\n\nDECRET` |
| Codice dellordinamento militare. 10G0089.md | `DECRETO LEGISLATIVO 15 marzo 2010 n. 66\n\n\nDECRETO ` |
| Disposizioni per lattuazione del Codice di procedura civile  | `REGIO DECRETO 18 dicembre 1941 n. 1368\n\n\nREGIO DEC` |
| Norme di attuazione di coordinamento e transitorie del codic | `DECRETO LEGISLATIVO 28 luglio 1989 n. 271\n\n\nDECRET` |

## Naming convention dei file

Classi rilevate (regex in `analyze_corpus.py`):

| Collezione | Classe | File nel campione | Esempi |
| --- | --- | ---: | --- |
| Atti di attuazione Regolamenti UE | solo titolo | 13 | `Adempimenti finanziari per lattuazione del regolamento CEE n`; `Attuazione degli articoli 8 e 9 del regolamento CEE n. 2092-` |
| Atti di attuazione Regolamenti UE | titolo troncato + hash | 26 | `Applicazione del regolamento CEE n. 570-88 relativo allavend`; `Attuazione della direttiva UE 2019-944 del Parlamento europe` |
| Atti di recepimento direttive UE | data_codice_vigenza (base64-HTML) | 1 | `2014-07-18_14G00113_VIGENZA_2026-04-30_V0.md` |
| Atti di recepimento direttive UE | solo titolo | 248 | `Attuazione della direttiva 1999-10-CE in materia di etichett`; `Attuazione della direttiva 1999-22-CE relativa alla custodia` |
| Atti di recepimento direttive UE | titolo + codice redazionale | 63 | `Attuazione della Direttiva 2008-114-CE recante lindividuazio`; `Attuazione della Direttiva 2009-143-CE del Consiglio del 26 ` |
| Atti di recepimento direttive UE | titolo troncato + hash | 188 | `Adeguamento alle direttive 83-181-CEE e 83-183-CEE del 28 ma`; `Adeguamento del testo unico delle disposizioni in materia di` |
| Atti normativi abrogati (in originale) | solo titolo | 101 | `Applicazione di tassa di famiglia. 0600226R.md`; `Approvativo del regolamento di polizia urbana pei Comuni di ` |
| Atti normativi abrogati (in originale) | titolo + codice redazionale | 341 | `12ª prelevazione dal fondo di riserva per le spese imprevist`; `3ª Prelevazione dal fondo di riserva per le spese impreviste` |
| Atti normativi abrogati (in originale) | titolo + suffisso _N (duplicato) | 8 | `Autorizzazione allIstituto tecnico industriale Quintino Sell`; `Differimento del termine di cui allart. 89 del decreto delPr` |
| Atti normativi abrogati (in originale) | titolo troncato + hash | 50 | `Aggiornamento del R. decreto 27 aprile 1936-XIV n. 1150 e de`; `Approvazione dei contributi scolastici suppletivi dovuti dai` |
| Codici | data_codice_vigenza (base64-HTML) | 2 | `1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md`; `1942-04-17_042U0318_VIGENZA_2026-04-29_V0.md` |
| Codici | solo titolo | 18 | `Approvazione del Regolamento per lesecuzione del Codice dell`; `Approvazione del codice di procedura penale.md` |
| Codici | titolo + codice redazionale | 17 | `Approvazione del testo definitivo del Codice Penale. 030U139`; `Approvazione del testo definitivo del Codice della navigazio` |
| Codici | titolo troncato + hash | 3 | `Codice della normativa statale in tema di ordinamento e merc`; `Codice in materia di protezione dei dati personali  recante ` |
| DL decaduti | solo titolo | 217 | `Applicazione dellarticolo 10 della legge 15 aprile 1985 n. 1`; `Assegnazione allENEA di un contributo per il quarto bimestre` |
| DL decaduti | titolo + codice redazionale | 10 | `Disposizioni urgenti di correzione a recenti norme in materi`; `Disposizioni urgenti in materia di accise sui tabacchi. 10G0` |
| DL decaduti | titolo + suffisso _N (duplicato) | 212 | `Adeguamento di canoni e di contributi per lesercizio di staz`; `Assegnazione di fondi alla regione autonoma della Sardegna p` |
| DL decaduti | titolo troncato + hash | 61 | `Adattamento della capacita di produzione della flotta pesche`; `Armonizzazione delle disposizioni in materia di imposte sugl` |
| DL e leggi di conversione | solo titolo | 84 | `Conversione in cattedre di ruolo ordinario dei posti di ruol`; `Conversione in legge con modificazione del decreto-legge 14 ` |
| DL e leggi di conversione | titolo + codice redazionale | 146 | `Conversione in legge con modificazione del R. decreto 21 ott`; `Conversione in legge con modificazione del R. decreto-legge ` |
| DL e leggi di conversione | titolo troncato + hash | 270 | `Conversione in legge con aggiunta di un capoverso allart. 2 `; `Conversione in legge con aggiunte e modifiche del R. decreto` |
| DL proroghe | solo titolo | 40 | `Definizione e proroga di termini nonche conseguentidisposizi`; `Disposizioni urgenti in materia di monitoraggio e trasparenz` |
| DL proroghe | titolo + codice redazionale | 18 | `Disposizioni urgenti in materia di proroga di termini legisl`; `Disposizioni urgenti in materia di proroga di termini normat` |
| DL proroghe | titolo + suffisso _N (duplicato) | 8 | `Proroga di termini in materia di acque di balneazione_2.md`; `Proroga di termini in materia di acque di balneazione_3.md` |
| DL proroghe | titolo troncato + hash | 9 | `Disposizioni urgenti concernenti la proroga di termini per l`; `Misure urgenti in materia di accesso al credito e di adempim` |
| DPCM | solo titolo | 105 | `Approvazione del nuovo Statuto dellAssociazione italiana del`; `Approvazione del nuovo statuto dellAssociazione italiana del` |
| DPCM | titolo + codice redazionale | 87 | `Definizione dei termini di conclusione dei procedimenti ammi`; `Modifica al decreto del Presidente del Consiglio dei Ministr` |
| DPCM | titolo + suffisso _N (duplicato) | 2 | `Nuovo regolamento di esecuzione della legge 9 luglio 1990 n.`; `Regolamento di attuazione della legge 7 agosto 1990 n. 241 i` |
| DPCM | titolo troncato + hash | 165 | `Approvazione del regolamento concernente la determinazione d`; `Attuazione dellarticolo 2 comma 3 della legge 7 agosto 1990 ` |
| DPR | solo titolo | 371 | `Adeguamento delle pensioni a carico del fondo di previdenza `; `Adeguamento ed integrazione delle norme di attuazione dello ` |
| DPR | titolo + codice redazionale | 3 | `Recepimento del provvedimento di concertazione per il person`; `Regolamento recante approvazione dello Statuto dellAgenzia n` |
| DPR | titolo + suffisso _N (duplicato) | 75 | `Assegnazione del numero dei seggi ai collegi per la elezione`; `Assegnazione di tre posti di tecnico laureato presso lUniver` |
| DPR | titolo troncato + hash | 51 | `Abrogazione parziale a seguito di referendum popolare del co`; `Approvazione del nuovo statuto e modificazione della denomin` |
| Decreti Legislativi | solo titolo | 273 | `Abrogazione del regio decreto-legge 15 novembre 1943 n. 7-B `; `Accettazione dei buoni del Tesoro quinquennali 5 scadenti il` |
| Decreti Legislativi | titolo + codice redazionale | 66 | `Attribuzione a comuni province citta metropolitane e regioni`; `Attuazione della decisione quadro 2006-783-GAI relativa alla` |
| Decreti Legislativi | titolo + suffisso _N (duplicato) | 2 | `Modificazioni delle aliquote dellimposta di fabbricazione su`; `Modificazioni delle aliquote dellimposta di fabbricazione su` |
| Decreti Legislativi | titolo troncato + hash | 159 | `Adeguamento della normativa nazionale alle disposizioni del `; `Adeguamento della normativa nazionale alle disposizioni del ` |
| Decreti legislativi luogotenenziali | solo titolo | 138 | `Abrogazione del decreto legislativo Luogotenenziale 22 marzo`; `Abrogazione del decreto legislativo Luogotenenziale 5 ottobr` |
| Decreti legislativi luogotenenziali | titolo + codice redazionale | 305 | `Abolizione del monopolio dei carboni e dei metalli e istituz`; `Abolizione del titolo di eccellenza. 045U0406.md` |
| Decreti legislativi luogotenenziali | titolo troncato + hash | 57 | `Abrogazione del R. decreto-legge 15 marzo 1944 n. 97 che ha `; `Abrogazione del R. decreto-legge 20 settembre 1941 n. 1134 c` |
| Leggi contenenti deleghe | solo titolo | 20 | `Delega al Governo concernente la disciplina dellimpresa soci`; `Delega al Governo in materia di occupazione e mercato del la` |
| Leggi contenenti deleghe | titolo + codice redazionale | 37 | `Delega al Governo e altre disposizioni in materia di spettac`; `Delega al Governo in materia di contratti pubblici. 22G00087` |
| Leggi contenenti deleghe | titolo troncato + hash | 20 | `Adesione della Repubblica italiana al Protocollo di modifica`; `Adesione della Repubblica italiana al Trattato concluso il 2` |
| Leggi costituzionali | solo titolo | 36 | `Assegnazione di tre Senatori ai comuni di Trieste Duino Auri`; `Cessazione degli effetti dei commi primo e secondo della XII` |
| Leggi costituzionali | titolo + codice redazionale | 11 | `Introduzione del principio del pareggio di bilancio nella Ca`; `Modifica allarticolo 119 della Costituzione concernente il r` |
| Leggi costituzionali | titolo troncato + hash | 2 | `Modifiche allo Statuto speciale della regione Friuli-Venezia`; `Modifiche ed integrazioni alla legge costituzionale 23 febbr` |
| Leggi delega e relativi provvedimenti delegati | data_codice_vigenza (base64-HTML) | 1 | `2015-09-01_15G00151_VIGENZA_2026-04-29_V0.md` |
| Leggi delega e relativi provvedimenti delegati | solo titolo | 171 | `Abrogazione dellarticolo 3 della legge 11 giugno 1967 n. 441`; `Adeguamento dellordinamento nazionale alle disposizioni del ` |
| Leggi delega e relativi provvedimenti delegati | titolo + codice redazionale | 107 | `Attuazione del regolamento UE 2019-1238 del Parlamento europ`; `Attuazione della Direttiva 2012-13-UE sul diritto allinforma` |
| Leggi delega e relativi provvedimenti delegati | titolo + suffisso _N (duplicato) | 9 | `Delega al Presidente della Repubblica per la concessione di `; `Delegazione al Presidente della Repubblica per la concession` |
| Leggi delega e relativi provvedimenti delegati | titolo troncato + hash | 212 | `Adeguamento del testo unico delle disposizioni in materia di`; `Adeguamento della disciplina sanzionatoria prevista dal test` |
| Leggi di delegazione europea | solo titolo | 17 | `Disposizioni in materia di attuazione di direttive comunitar`; `Disposizioni per ladempimento di obblighi derivanti dallaapp` |
| Leggi di delegazione europea | titolo + codice redazionale | 15 | `Delega al Governo per il recepimento delle direttive europee`; `Delega al Governo per il recepimento delle direttive europee` |
| Leggi di ratifica | data_codice_vigenza (base64-HTML) | 3 | `2026-04-23_26G00076_ORIGINALE_V0.md`; `2026-04-29_26G00070_ORIGINALE_V0.md` |
| Leggi di ratifica | solo titolo | 183 | `Ratifica ai sensi dellart. 6 del decreto legislativo luogote`; `Ratifica con modificazione del decreto legislativo 3 maggio ` |
| Leggi di ratifica | titolo + codice redazionale | 28 | `Ratifica della cessione gratuita di alcuni cimeli della Regi`; `Ratifica ed esecuzione dei Protocolli al Trattato del Nord A` |
| Leggi di ratifica | titolo troncato + hash | 286 | `Ratifica con modificazione del decreto legislativo 17 aprile`; `Ratifica con modificazioni dei decreti legislativi 13 settem` |
| Leggi finanziarie e di bilancio | solo titolo | 38 | `Disposizioni diverse per lattuazione della manovra di finanz`; `Disposizioni in materia di finanza pubblica.md` |
| Leggi finanziarie e di bilancio | titolo + codice redazionale | 17 | `Bilancio di previsione dello Stato per lanno finanziario 201`; `Bilancio di previsione dello Stato per lanno finanziario 201` |
| Leggi finanziarie e di bilancio | titolo + suffisso _N (duplicato) | 2 | `Misure di razionalizzazione della finanza pubblica_2.md`; `Misure di razionalizzazione della finanza pubblica_3.md` |
| Regi decreti | solo titolo | 110 | `Accettazione ad accettare un lascito. 0600365R.md`; `Approvativo della Cassa sociale di risparmio costituita in C` |
| Regi decreti | titolo + codice redazionale | 365 | `13ª prelevazione dal fondo di riserva per le spese imprevist`; `14ª prelevazione dal fondo di riserva per le spese imprevist` |
| Regi decreti | titolo troncato + hash | 25 | `Applicazione del R. decreto 10 agosto 1923 n. 1824 che proro`; `Approvazione della convenzione stipulata tra il comune di Ma` |
| Regi decreti legislativi | solo titolo | 101 | `Abolizione dellimposta sui frutti dei titoli al portatore em`; `Abrogazione delle disposizioni che sanciscono lobbligo della` |
| Regi decreti legislativi | titolo troncato + hash | 19 | `Aumento dellammontare delle anticipazioni autorizzate ai sen`; `Collocamento in ausiliaria o dispensa dal servizio a domanda` |
| Regolamenti di delegificazione | solo titolo | 164 | `Regolamento che stabilisce le condizioni nelle quali e obbli`; `Regolamento concernente disposizioni relative alla banda mus` |
| Regolamenti di delegificazione | titolo + codice redazionale | 29 | `Regolamento concernente il conferimento dellabilitazione sci`; `Regolamento concernente la revisione della disciplina delle ` |
| Regolamenti di delegificazione | titolo troncato + hash | 182 | `Modifica del regolamento recante individuazione degli interv`; `Modifiche al decreto del Presidente della Repubblica 27 dice` |
| Regolamenti governativi | solo titolo | 314 | `Adeguamento dei limiti dimporto indicati nel Regolamento gen`; `Approvazione del Regolamento per lesecuzione del Codice dell` |
| Regolamenti governativi | titolo + codice redazionale | 26 | `Regolamento concernente lorganizzazione dellAgenzia nazional`; `Regolamento concernente modalita e criteri di valutazione de` |
| Regolamenti governativi | titolo + suffisso _N (duplicato) | 1 | `Modificazioni al regolamento per la coltivazione indigena de` |
| Regolamenti governativi | titolo troncato + hash | 159 | `Approvazione del regolamento che stabilisce i criteri per la`; `Approvazione del regolamento concernente la composizione e l` |
| Regolamenti ministeriali | solo titolo | 176 | `Disposizioni di adeguamento al regolamento UE n. 165-2014 de`; `Disposizioni integrative del regolamento concernente la isti` |
| Regolamenti ministeriali | titolo + codice redazionale | 67 | `Regolamento ai sensi dellarticolo 6 del decreto legislativo `; `Regolamento concernente la banca dati nazionale destinata al` |
| Regolamenti ministeriali | titolo + suffisso _N (duplicato) | 2 | `Regolamento concernente disposizioni di attuazione degli art`; `Regolamento recante modifica del decreto legislativo 25 genn` |
| Regolamenti ministeriali | titolo troncato + hash | 255 | `Modifica al regolamento e funzionamento dellAgenzia italiana`; `Modifica ed integrazione del decreto ministeriale 6 aprile 2` |
| Testi Unici | data_codice_vigenza (base64-HTML) | 1 | `1993-09-30_093G0428_VIGENZA_2026-04-29_V0.md` |
| Testi Unici | solo titolo | 65 | `Approvazione del Testo Unico delle leggi per la composizione`; `Approvazione del testo unico delle disposizioni concernenti ` |
| Testi Unici | titolo + codice redazionale | 183 | `Approvazione del Testo unico delle norme per la protezione d`; `Approvazione del testo unico della legge comunale e provinci` |
| Testi Unici | titolo troncato + hash | 6 | `Approvazione del testo unico delle leggi e delle norme giuri`; `Approvazione del testo unico delle leggi sul matrimonio degl` |

## Casi patologici

### 10 file piu' grandi (tutto il corpus)

| File | Dimensione |
| --- | ---: |
| Regolamenti governativi/Regolamento recante abrogazione espressa delle norme regolamentari vigenti c | 28.1 MB |
| DPR/Regolamento recante abrogazione espressa delle norme regolamentari vigenti che hanno esaurito la | 28.1 MB |
| Decreti Legislativi/Abrogazione di disposizioni legislative statali a norma dellarticolo 14 comma 14 | 6.3 MB |
| Regi decreti/1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md | 3.7 MB |
| Codici/1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md | 3.7 MB |
| Decreti Legislativi/Codice dellordinamento militare. 10G0089.md | 3.6 MB |
| Codici/Codice dellordinamento militare. 10G0089.md | 3.6 MB |
| Leggi delega e relativi provvedimenti delegati/Disposizioni in materia di armonizzazione dei sistemi | 3.3 MB |
| Decreti Legislativi/Disposizioni in materia di armonizzazione dei sistemi contabili e degli schemi d | 3.3 MB |
| Leggi delega e relativi provvedimenti delegati/Norme in materia ambientale.md | 3.1 MB |

### 10 file piu' piccoli (tutto il corpus)

| File | Dimensione | Primi 50 caratteri |
| --- | ---: | --- |
| Atti normativi abrogati (in originale)/Annullamento di partita 020U1371.md | 253 B | `REGIO DECRETO 24 giugno 1920 n. 1371\n\n\nREGIO DECRE` |
| Atti normativi abrogati (in originale)/Annullamento di partita. 021U1702.md | 255 B | `REGIO DECRETO 24 aprile 1921 n. 1702\n\n\nREGIO DECRE` |
| Atti normativi abrogati (in originale)/Inammissibilita di ricorso. 021U0455.md | 257 B | `REGIO DECRETO 17 marzo 1921 n. 455\n\n\nREGIO DECRETO` |
| Atti normativi abrogati (in originale)/Annullazione di partita. 020U1948.md | 258 B | `REGIO DECRETO 12 ottobre 1920 n. 1948\n\n\nREGIO DECR` |
| Atti normativi abrogati (in originale)/Modificazione di statuto. 023U0491.md | 260 B | `REGIO DECRETO 15 febbraio 1923 n. 491\n\n\nREGIO DECR` |
| Atti normativi abrogati (in originale)/Annullamento di deliberazioni. 021U0664.md | 264 B | `REGIO DECRETO 10 aprile 1921 n. 664\n\n\nREGIO DECRET` |
| Atti normativi abrogati (in originale)/Annullamento di partita. 016U0504.md | 279 B | `DECRETO LUOGOTENENZIALE 19 marzo 1916 n. 504\n\n\nDEC` |
| Atti normativi abrogati (in originale)/Annullamento di partita. 017U1857.md | 285 B | `DECRETO LUOGOTENENZIALE 23 agosto 1917 n. 1857\n\n\nD` |
| Atti normativi abrogati (in originale)/Annullamento di partita. 018U0283.md | 285 B | `DECRETO LUOGOTENENZIALE 06 gennaio 1918 n. 283\n\n\nD` |
| Atti normativi abrogati (in originale)/Annullamento di partita. 016U1863.md | 291 B | `DECRETO LUOGOTENENZIALE 26 novembre 1916 n. 1863\n\n` |

### File Markdown senza alcuna intestazione: 0 nel campione


### File base64-HTML nel campione: 8

File Markdown del campione con commi numerati a inizio riga (`1. `, `2-bis. `): 3767

### Padding NUL in coda ai file (campione)

Molti file (quasi tutti in `Regi decreti`) sono atti brevi gonfiati fino a ~1 MiB
con byte NUL (`\x00`) in coda: un difetto di generazione del corpus che spiega
quasi tutta la dimensione su disco. Il contenuto utile termina al primo NUL.

| Collezione | File con padding > 1 KB | Padding totale | Dimensione campione |
| --- | ---: | ---: | ---: |
| Atti di attuazione Regolamenti UE | 0/39 | 0 B | 1.3 MB |
| Atti di recepimento direttive UE | 0/500 | 0 B | 35.0 MB |
| Atti normativi abrogati (in originale) | 0/500 | 0 B | 2.6 MB |
| Codici | 0/40 | 0 B | 30.6 MB |
| DL decaduti | 0/500 | 0 B | 1.0 MB |
| DL e leggi di conversione | 0/500 | 0 B | 8.3 MB |
| DL proroghe | 0/75 | 0 B | 3.5 MB |
| DPCM | 0/359 | 0 B | 17.7 MB |
| DPR | 0/500 | 0 B | 3.3 MB |
| Decreti Legislativi | 0/500 | 0 B | 43.8 MB |
| Decreti legislativi luogotenenziali | 0/500 | 0 B | 1.7 MB |
| Leggi contenenti deleghe | 0/77 | 0 B | 8.6 MB |
| Leggi costituzionali | 0/49 | 0 B | 575.7 KB |
| Leggi delega e relativi provvedimenti delegati | 0/500 | 0 B | 49.9 MB |
| Leggi di delegazione europea | 0/32 | 0 B | 7.1 MB |
| Leggi di ratifica | 0/500 | 0 B | 16.8 MB |
| Leggi finanziarie e di bilancio | 0/57 | 0 B | 26.3 MB |
| Regi decreti | 396/500 | 395.2 MB | 395.8 MB |
| Regi decreti legislativi | 0/120 | 0 B | 749.5 KB |
| Regolamenti di delegificazione | 0/375 | 0 B | 21.7 MB |
| Regolamenti governativi | 0/500 | 0 B | 17.4 MB |
| Regolamenti ministeriali | 0/500 | 0 B | 24.5 MB |
| Testi Unici | 0/255 | 0 B | 26.9 MB |

### Duplicazione tra collezioni

Le collezioni si sovrappongono, ma MOLTO meno di quanto suggeriscano i soli nomi: 95492 filename (su 190739 unici, 287912 file totali) compaiono in 2+ collezioni, ma solo 6303 coppie (filename, dimensione) coincidono.

- **95492 collisioni di nome**: in larga parte atti DIVERSI che condividono il titolo sanificato. Es. `Modificazioni delle aliquote dellimposta di fabbricazione su alcuni prodotti petroliferi.md` compare in 5 collezioni: 4 atti distinti più una copia identica — le dimensioni non coincidono tra 4 dei 5 file. Il nome file, da solo, non identifica l'atto.
- **~6303 duplicati plausibili** (stesso nome E stessa dimensione in 2+ collezioni): lo stesso atto archiviato in piu' collezioni. La conferma definitiva richiederebbe un hash del contenuto; la dimensione identica e' un proxy conservativo.

Esempi di duplicati stesso-nome-stessa-dimensione (i piu' grandi):

- `Regolamento recante abrogazione espressa delle norme regolamentari vigenti che hanno esaur` (28.1 MB, 2 collezioni)
- `1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md` (3.7 MB, 2 collezioni)
- `Codice dellordinamento militare. 10G0089.md` (3.6 MB, 2 collezioni)
- `Disposizioni in materia di armonizzazione dei sistemi contabili e degli schemi di bilancio` (3.3 MB, 2 collezioni)
- `Norme in materia ambientale.md` (3.1 MB, 3 collezioni)

## Assunzioni del parser

Sezione curata manualmente nella costante ``PARSER_ASSUMPTIONS`` dello script: la
rigenerazione del report la include automaticamente, ma il contenuto va aggiornato
a mano se il corpus cambia. Verificata sui file reali del commit indicato in testa
al report.

**A1 — Due formati di file, il parser gestisce solo il Markdown (per ora).**
I file `YYYY-MM-DD_<codice>_(VIGENZA_<data>|ORIGINALE)_V<n>.md` NON sono Markdown:
sono HTML Akoma Ntoso codificato base64 (iniziano con `PGh0bWw`). In `Codici` ce ne
sono 2 e uno e' il **Codice Civile** (`1942-04-04_042U0262_VIGENZA_2026-04-29_V0.md`):
per coprirlo servira' un decodificatore base64+HTML dedicato (l'HTML usa classi
`*-akn`: `article-num-akn`, `attachment-just-text`, ...). Il parser B3 rileva il
formato con `content.startswith("PGh0bWw")` e in tal caso delega o salta con warning.

**A2 — Intestazioni: setext, non solo ATX.**
Il piano assumeva `^#{1,6} `; in realta' i file usano **setext**: titolo dell'atto =
h1 (`====`), sottotitoli/partizioni/preambolo = h2 (`----`). Gli ATX compaiono solo
a livello 3 (`### Art. N` negli atti recenti, piu' righe di firma tipo `### Dato a
Roma, addi' ...`); livelli 1-2 e 4-6 ATX: zero occorrenze nel campione. Regole: riga
di soli `=` (h1) o soli `-` (h2)
preceduta da riga non vuota = heading setext; `----` preceduto da riga vuota =
separatore orizzontale (usato prima dei blocchi `AGGIORNAMENTO (n)`), non heading.
Nessun frontmatter YAML in tutto il campione.

**A3 — Identita' dell'atto dalla prima intestazione h1, non dal filename.**
La prima h1 e' sempre `<TIPO ATTO> <data estesa> n. <numero>` (es. `REGIO DECRETO 28
ottobre 1940 n. 1443`, `DECRETO LEGISLATIVO 02 gennaio 2018 n. 1 (Raccolta 2018)`).
La h2 successiva contiene il titolo e spesso il codice redazionale GU tra parentesi
(es. `Codice della protezione civile. (18G00011)`). `act_ref` canonico derivato da
qui: `tipo:AAAA-MM-GG;numero` (stile URN NIR, gia' usato nei link Normattiva interni
ai file: `urn:nir:stato:decreto.legislativo:2018-01-02;1`). Il filename serve solo
come chiave di fallback/dedup: e' il titolo troncato e sanificato (apostrofi e
virgole rimossi!), con varianti: `titolo + codice redazionale` (` 18G00011.md`),
`titolo + hash` (`_2346b7135fe2.md`), `titolo + _N` per duplicati, `solo codice`
(`012U0343.md`). Non e' univoco ne' stabile: non usarlo come act_ref primario.

**A4 — Articolo = tre stili di marcatura, tutti da supportare.**
1. **ATX**: `### Art. N` (atti ~post-2000; rubrica nella riga successiva, riferimenti
   alle fonti tra parentesi).
2. **setext h2**: `Art. N` sottolineato con `----` (articoli del decreto/regio decreto
   di approvazione, atti brevi).
3. **plain marker** (atti storici multivigenti, es. Codice Penale, Codice di procedura
   civile): riga piatta `<TITOLO ATTO>-art. N [bis|ter|...]` (es. `Codice Penale-art. 3
   bis`) seguita da riga ` Art. N.` (spesso dentro un link Normattiva) e dalla rubrica
   tra parentesi. NON e' un heading: il parser deve riconoscere il pattern
   `^\S.*-art\. \d+` come confine di articolo.
Suffissi: `bis`, `ter`, ... scritti dopo il numero (`art. 3 bis` → `3-bis` in act_ref).
Attenzione: gli h3 ATX NON sono solo articoli (compaiono anche per le firme, es.
`### Dato a Roma, addi' 6 luglio 1993`): filtrare con `^### Art\.?\s*\d`.

**A5 — Commi = paragrafi numerati `N.` / `N-bis.` a inizio riga.**
Negli atti recenti i commi sono righe `1. <testo>`, `2-bis. <testo>`. Negli atti
storici i commi spesso NON sono numerati (solo capoversi). Il chunker deve trattare
la numerazione dei commi come best-effort, non come invariante.

**A6 — Gerarchia libro/titolo/capo: presente solo negli atti recenti, in forma sporca.**
Nei file recenti le partizioni sono h2 setext con testo `CAPO <Romano progressivo>`
seguito dalla partizione originale, con o senza ` - ` (es. `CAPO III - Sezione II
Organizzazione del Servizio nazionale`, `CAPO I Titolo I DEFINIZIONI`): il prefisso
`CAPO N` e' un contatore artificiale del convertitore (7849 occorrenze nel campione
contro 0 LIBRO e 0 SEZIONE a inizio heading), la partizione vera e' nel testo che
segue. Negli atti storici in Markdown (CP, CPC) la gerarchia LIBRO/TITOLO/CAPO NON e'
presente come heading: compare solo nei riferimenti inline. Il parser tratta la
gerarchia come metadato opzionale (nullable), mai obbligatorio.

**A7 — Vigenza dalla cartella top-level, con dedup.**
Mappa cartella → stato: `Atti normativi abrogati (in originale)` → `abrogato`;
`DL decaduti` → `decaduto`; tutte le altre 21 collezioni → `vigente` (testo
multivigente consolidato, salvo i file `*_ORIGINALE_*` che sono la versione originale
in GU). Le collezioni sono il primo (e unico) livello di directory: nessuna
sotto-cartella (invariante verificato a ogni run, vedi "Voci saltate" sopra).
ATTENZIONE alla sovrapposizione, su due piani distinti: ~95k filename compaiono in
2+ collezioni, ma sono in larga parte atti DIVERSI con lo stesso titolo sanificato
(stesse parole, dimensioni/contenuti differenti); i duplicati veri plausibili —
stesso nome E stessa dimensione — sono ~6.3k (es. `Codice dellordinamento
militare. 10G0089.md`, byte-identico per dimensione sia in `Codici` sia in
`Decreti Legislativi`). Conseguenza doppia per l'ingestion: deduplicare per
act_ref (mai per filename, che non identifica l'atto) e assegnare la vigenza con
priorita' alle cartelle abrogati/decaduti.

**A8 — Convenzioni Normattiva nel testo.**
`((testo))` = testo modificato/inserito dal consolidamento; blocchi `AGGIORNAMENTO
(n)` preceduti da `-----` = note di aggiornamento (da separare dal testo vigente in
fase di chunking); `N O T E` + `Note alle premesse:` = note redazionali a fine
articolo/atto; i riferimenti normativi sono link Markdown a URN
`http://www.normattiva.it/uri-res/N2Ls?urn:nir:...` (riusabili per il citation graph
e per derivare act_ref di destinazione).

**A9 — Padding NUL: la dimensione su disco mente.**
~80% dei file in `Regi decreti` (decine di migliaia) sono atti brevi gonfiati fino a
~1 MiB con byte NUL (`\x00`) in coda — un difetto del generatore del corpus che da
solo spiega ~71 dei ~73 GB totali. Il contenuto utile e' solo il prefisso fino al
primo NUL: il parser DEVE troncare al primo `\x00` (`text.split("\x00", 1)[0]`)
prima di qualunque elaborazione, e le stime di dimensione/chunking vanno fatte sul
testo troncato.

**A10 — Dimensioni e casi limite.**
Contenuto utile da 253 B (decreti di una riga, es. `Annullamento di partita`) a
28.1 MB (regolamenti "taglia-leggi" con elenchi di migliaia di atti abrogati);
alcuni file sono il solo decreto di approvazione, altri contengono l'intero codice.
Esistono file con suffisso `_2`/`_3` (versioni quasi-duplicate), file il cui nome e'
solo il codice redazionale. Nel campione nessun file Markdown e' privo di
intestazioni (c'e' sempre almeno la h1 del titolo), ma il parser non deve assumere
che un atto abbia articoli: un atto senza marcatori di articolo diventa un singolo
blocco.

---

# Checkpoint C6 — Benchmark embedding e GO/NO-GO (11 giugno 2026)

## Benchmark retrieval (hybrid dense+BM25, RRF, k=10, 30 query su Codici — 18.463 chunk)

| Embedder | recall@5 | recall@10 | MRR | explicit | natural | lay | trap |
|---|---|---|---|---|---|---|---|
| **voyage-4-large** | 93,3% | **96,7%** | 0,717 | 90% | 100% | 100% | 100% |
| **voyage-4** | 83,3% | **86,7%** | 0,611 | 60% | 100% | 100% | 100% |
| voyage-law-2 | 70,0% | 76,7% | 0,485 | 50% | 91,7% | 80% | 100% |
| bge-m3 | n/d | n/d | n/d | — | — | — | — |

Report JSON in `backend/eval/results/`. Note:

- **voyage-law-2** (modello "legale", generazione precedente) perde nettamente dai
  modelli generali voyage-4: scartato.
- **bge-m3** non valutato: inferenza inaffidabile su questo Mac Intel (MPS si blocca
  nei processi detached anche con `LEGGER_EMBED_DEVICE` e `max_length=4096`; CPU
  stalla sui lotti con chunk lunghi). Da ri-benchmarkare eventualmente sul VPS Linux
  (torch moderno, niente Rosetta) se il self-hosting diventa interessante.
- Le differenze tra voyage-4-large e voyage-4 sono interamente sulle query con
  **estremi espliciti** ("art. 2051 c.c."), che il fast path E1 risolverà con lookup
  deterministico bypassando il vettoriale. Sulle query semantiche (natural/lay/trap)
  entrambi fanno 100%.
- q04 ("art. 274 c.p.p.") oscilla al confine del top-10 tra run (non determinismo
  HNSW): il 96,7% di voyage-4-large è in realtà 96,7–100%.

## Stima bootstrap corpus completo (dry-run 11/6/2026, commit corpus a380de55)

- **181.870 file** da indicizzare; 106.042 duplicati cross-collezione saltati (dedup
  per act_ref); **0 errori di parsing sull'intero corpus**.
- **966.822 chunk**, ~1,18 mld caratteri ≈ **~524M token** (ratio misurato 2,26 char/token).
- Costo embedding oltre i 200M token gratuiti Voyage (324M a pagamento):
  voyage-4-large ≈ **39 $** · voyage-4 ≈ **19,5 $** · voyage-4-lite ≈ 6,5 $ ·
  bge-m3 self-hosted gratis ma ~27–54 h di CPU.
- Tempo stimato (API-bound, ~20–75 chunk/s osservati): ~4–13 h, ripartibile
  (resume a livello di file e di lotto).

## Verdetto: **GO**

Criteri del piano (§Fase 0): recall@10 ≥ 85% ✅ (due embedder lo superano, il
migliore al 96,7%) · stima fondata di chunk e costi ✅ · fast path estremi: rinviato
a E1 (lookup deterministico Postgres+regex, prossimo task; le query esplicite
mancate dal vettoriale sono esattamente il suo caso d'uso). Validazione qualitativa
end-to-end: chat CLI grounded 14/14 PASS (trascritto in `docs/c5-chat-transcript.md`),
zero marker malformati, zero estremi inventati, trappole gestite.

**Decisione embedder produzione**: rinviata alla scelta di budget (4-large massimizza
la robustezza, 4 costa la metà con pari resa semantica + fast path davanti).

---

# Appendice — Collisioni di filename su filesystem case-insensitive (11 giugno 2026)

Il clone locale su macOS (APFS case-insensitive) mostra 353 file "modificati" mai
toccati da noi: sono coppie di atti i cui filename sanificati differiscono solo per
maiuscole/minuscole (es. "Aumento del Fondo di dotazione dellEnte nazionale
idrocarburi.md" vs "...del fondo di dotazione dellEnte Nazionale Idrocarburi.md").
Sul filesystem i due path collidono: il checkout del secondo sovrascrive il primo e
git segnala il primo come modificato. Effetto locale: ~176 atti shadowed (contenuto
dell'uno indicizzato al posto dell'altro), ~0,1% del corpus, in gran parte abrogati
storici. Non è un bug della pipeline: l'identità dell'atto deriva dal contenuto (A3),
quindi nessun merge errato — solo atti mancanti.

**Requisito di deploy (H2): il clone del corpus DEVE stare su filesystem
case-sensitive** (ext4 sul VPS Linux: ok di default; su macOS servirebbe un volume
APFS case-sensitive). La `git pull --ff-only` del delta su macOS può rifiutare se
upstream tocca i file collisi: triage con `git checkout -f` prima del pull, o
eseguire l'ingestion solo sul VPS.
