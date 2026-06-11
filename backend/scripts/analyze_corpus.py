"""Analizza la struttura del corpus italia-corpus e stampa un report Markdown su stdout.

Uso (dalla directory backend/):

    uv run python scripts/analyze_corpus.py > ../docs/corpus-analysis.md

Lo script e' deterministico (campionamento con seed fisso) e usa solo la stdlib.
Il percorso del corpus viene letto da ``legger.settings.Settings`` (CORPUS_PATH).
"""

from __future__ import annotations

import random
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from legger.settings import Settings

SAMPLE_PER_COLLECTION = 500
SEED = 20260610
MAX_HEADINGS_PER_FILE = 5000

ATX_RE = re.compile(r"^(#{1,6}) (.+?)\s*$")
SETEXT_H1_RE = re.compile(r"^=+\s*$")
SETEXT_H2_RE = re.compile(r"^-{2,}\s*$")
# Marcatore di articolo "vecchio formato": riga piatta tipo "Codice Penale-art. 3 bis"
PLAIN_ART_RE = re.compile(r"^\S.*-art\. \d+[a-z .]*$")
HEADING_ART_RE = re.compile(r"^Art\.?\s*\d+", re.IGNORECASE)
PARTITION_RE = re.compile(r"^(LIBRO|PARTE|TITOLO|CAPO|SEZIONE)\b", re.IGNORECASE)
COMMA_RE = re.compile(r"^\d+(-[a-z]+)?\. \S")

# Naming dei file
NAME_DATED_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_([0-9A-Z]+)_(VIGENZA_\d{4}-\d{2}-\d{2}|ORIGINALE)_V(\d+)\.md$"
)
NAME_CODE_ONLY_RE = re.compile(r"^\d{2,3}[A-Z][0-9A-Z]{4,5}\.md$")
NAME_TITLE_CODE_RE = re.compile(r"[. ](\d{2,3}[A-Z][0-9A-Z]{4,5})(_\d+)?\.md$")
NAME_HASH_RE = re.compile(r"_[0-9a-f]{12}(_\d+)?\.md$")
NAME_DUP_RE = re.compile(r"_\d+\.md$")

B64_ALPHABET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


@dataclass
class FileScan:
    """Risultato della scansione di un singolo file Markdown."""

    is_base64: bool = False
    has_frontmatter: bool = False
    atx_counts: Counter = field(default_factory=Counter)  # livello -> n
    setext_counts: Counter = field(default_factory=Counter)  # livello (1|2) -> n
    headings: list[tuple[int, str]] = field(default_factory=list)
    atx_art: int = 0
    setext_art: int = 0
    plain_art: int = 0
    comma_lines: int = 0
    n_lines: int = 0


def nul_padding_bytes(path: Path) -> int:
    """Byte di padding NUL/whitespace in coda al file (letti a blocchi dal fondo)."""
    pad_chars = b"\x00 \t\r\n"
    size = path.stat().st_size
    pad = 0
    block = 1 << 16
    with path.open("rb") as fh:
        pos = size
        while pos > 0:
            start = max(0, pos - block)
            fh.seek(start)
            chunk = fh.read(pos - start)
            stripped = chunk.rstrip(pad_chars)
            pad += len(chunk) - len(stripped)
            if stripped:
                break
            pos = start
    return pad


def detect_base64(path: Path) -> bool:
    """True se il file e' un blob base64 (HTML Akoma Ntoso codificato)."""
    with path.open("rb") as fh:
        head = fh.read(64)
    if head.startswith(b"PGh0bWw"):  # base64 di "<html"
        return True
    text = head.decode("ascii", errors="ignore")
    return len(text) >= 64 and all(c in B64_ALPHABET for c in text)


def scan_file(path: Path) -> FileScan:
    """Scansione riga per riga: intestazioni ATX/setext, marcatori, frontmatter."""
    scan = FileScan()
    if detect_base64(path):
        scan.is_base64 = True
        return scan

    prev_line: str | None = None
    prev_was_heading_underline = False
    with path.open(encoding="utf-8", errors="replace") as fh:
        for i, raw in enumerate(fh):
            line = raw.rstrip("\n")
            scan.n_lines += 1
            if i == 0 and line.strip() == "---":
                scan.has_frontmatter = True

            m = ATX_RE.match(line)
            if m:
                level = len(m.group(1))
                scan.atx_counts[level] += 1
                if len(scan.headings) < MAX_HEADINGS_PER_FILE:
                    scan.headings.append((level, m.group(2)))
                if HEADING_ART_RE.match(m.group(2)):
                    scan.atx_art += 1
                prev_line = line
                prev_was_heading_underline = False
                continue

            is_underline = False
            if prev_line and prev_line.strip() and not prev_was_heading_underline:
                level = 0
                if SETEXT_H1_RE.match(line):
                    level = 1
                elif SETEXT_H2_RE.match(line):
                    level = 2
                if level:
                    is_underline = True
                    scan.setext_counts[level] += 1
                    text = prev_line.strip()
                    if len(scan.headings) < MAX_HEADINGS_PER_FILE:
                        scan.headings.append((level, text))
                    if HEADING_ART_RE.match(text):
                        scan.setext_art += 1

            if not is_underline:
                if PLAIN_ART_RE.match(line):
                    scan.plain_art += 1
                if COMMA_RE.match(line):
                    scan.comma_lines += 1

            prev_was_heading_underline = is_underline
            prev_line = line
    return scan


def total_headings(scan: FileScan) -> int:
    return sum(scan.atx_counts.values()) + sum(scan.setext_counts.values())


def classify_filename(name: str) -> str:
    if NAME_DATED_RE.match(name):
        return "data_codice_vigenza (base64-HTML)"
    if NAME_CODE_ONLY_RE.match(name):
        return "solo codice redazionale"
    if NAME_TITLE_CODE_RE.search(name):
        return "titolo + codice redazionale"
    if NAME_HASH_RE.search(name):
        return "titolo troncato + hash"
    if NAME_DUP_RE.search(name):
        return "titolo + suffisso _N (duplicato)"
    return "solo titolo"


def fmt_size(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.1f} KB"
    return f"{n} B"


def first_chars(path: Path, n: int = 50) -> str:
    with path.open(encoding="utf-8", errors="replace") as fh:
        text = fh.read(n * 4)
    return text[:n].replace("\n", "\\n").replace("|", "\\|")


def git_info(corpus: Path) -> dict[str, str]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(corpus), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        return {
            "sha": run("rev-parse", "HEAD"),
            "commit_date": run("log", "-1", "--format=%cI"),
            "n_commits": run("rev-list", "--count", "HEAD"),
            "shallow": run("rev-parse", "--is-shallow-repository"),
        }
    except FileNotFoundError:
        sys.exit("Comando `git` non trovato nel PATH: installare git per generare il report.")
    except subprocess.CalledProcessError as exc:
        sys.exit(
            f"Impossibile leggere i metadati git di {corpus} "
            f"(git {' '.join(exc.cmd[3:])}: {exc.stderr.strip() or exc.returncode}). "
            "Il corpus deve essere un clone git di italia-corpus."
        )


def percentiles(values: list[int]) -> dict[str, int]:
    values = sorted(values)
    qs = statistics.quantiles(values, n=4) if len(values) > 1 else [values[0]] * 3
    return {
        "min": values[0],
        "p25": int(qs[0]),
        "mediana": int(qs[1]),
        "p75": int(qs[2]),
        "max": values[-1],
    }


def collect_collections(corpus: Path) -> tuple[dict[str, list[tuple[str, int]]], list[str]]:
    """Per ogni collezione (cartella top-level), elenco ordinato (filename, size).

    Restituisce anche le voci saltate DENTRO le collezioni (sottocartelle o file
    non `.md`), cosi' l'invariante "nessuna sotto-struttura" e' verificato a ogni
    esecuzione invece di essere assunto in silenzio.
    """
    collections: dict[str, list[tuple[str, int]]] = {}
    skipped: list[str] = []
    for entry in sorted(corpus.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name == ".git":
            continue
        files: list[tuple[str, int]] = []
        for f in sorted(entry.iterdir(), key=lambda p: p.name):
            if f.is_file() and f.suffix == ".md":
                files.append((f.name, f.stat().st_size))
            else:
                skipped.append(f"{entry.name}/{f.name}")
        collections[entry.name] = files
    return collections, skipped


def report() -> None:
    settings = Settings()
    corpus = settings.corpus_path
    if not corpus.is_dir():
        sys.exit(f"Corpus non trovato in {corpus}: clonare italia-corpus e/o settare CORPUS_PATH")

    info = git_info(corpus)
    collections, skipped_entries = collect_collections(corpus)
    rng = random.Random(SEED)

    print("# Analisi del corpus italia-corpus")
    print()
    print(f"- Percorso: `{corpus}`")
    print(f"- Commit analizzato: `{info['sha']}` del {info['commit_date']}")
    print(
        f"- Numero di commit nella storia: {info['n_commits']} (clone shallow: {info['shallow']})"
    )
    print(f"- Data dell'analisi: {date.today().isoformat()}")
    print(
        f"- Generato da `backend/scripts/analyze_corpus.py` (campione: {SAMPLE_PER_COLLECTION} file/collezione, seed {SEED})"
    )

    # ------------------------------------------------------------- collezioni
    print()
    print("## Collezioni (cartelle top-level)")
    print()
    print("| Collezione | File .md | Dimensione |")
    print("| --- | ---: | ---: |")
    tot_files = tot_size = 0
    for name, files in collections.items():
        size = sum(s for _, s in files)
        tot_files += len(files)
        tot_size += size
        print(f"| {name} | {len(files)} | {fmt_size(size)} |")
    print(f"| **Totale** | **{tot_files}** | **{fmt_size(tot_size)}** |")
    print()
    print('> NB: le dimensioni includono il padding NUL descritto in "Casi patologici":')
    print("> la quasi totalita' dei ~71 GB di `Regi decreti` e' padding, non testo.")
    print()
    print(
        f"Voci saltate dentro le collezioni (sottocartelle o file non `.md`): "
        f"{len(skipped_entries)}."
    )
    if skipped_entries:
        print("ATTENZIONE: l'invariante \"nessuna sotto-struttura\" NON vale piu':")
        for rel in skipped_entries[:10]:
            print(f"- `{rel[:110]}`")

    # ------------------------------------------------- vigenza: cartelle dedicate
    print()
    print("## Cartelle di vigenza (abrogati / decaduti)")
    print()
    print("Nomi ESATTI delle cartelle che codificano lo stato di vigenza:")
    print()
    for name in collections:
        low = name.lower()
        if "abrogat" in low or "decadut" in low:
            print(f"- `{name}`")
    print()
    print("Tutte le altre collezioni contengono atti vigenti (o nella versione originale).")

    # ------------------------------------------------------------ scansione campioni
    scans: dict[str, list[tuple[str, FileScan]]] = {}
    for name, files in collections.items():
        names = [fn for fn, _ in files]
        chosen = (
            names
            if len(names) <= SAMPLE_PER_COLLECTION
            else rng.sample(names, SAMPLE_PER_COLLECTION)
        )
        scans[name] = [(fn, scan_file(corpus / name / fn)) for fn in sorted(chosen)]

    # --------------------------------------------------------------- formati
    print()
    print("## Formati dei file (campione)")
    print()
    print("Due formati coesistono nel corpus:")
    print()
    print("1. **Markdown** (conversione pandoc dell'HTML Normattiva): intestazioni setext")
    print("   (`====` per h1, `----` per h2) piu' ATX `###` nei file recenti.")
    print("2. **HTML Akoma Ntoso codificato base64** in un file `.md`: tutti i file con naming")
    print("   `YYYY-MM-DD_<codice>_VIGENZA_<data>_V0.md` o `..._ORIGINALE_V0.md` iniziano con")
    print("   `PGh0bWw` (= base64 di `<html`). NON sono Markdown.")
    print()
    print(
        "| Collezione | Campione | Markdown | Base64-HTML | Frontmatter YAML | Senza intestazioni |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for name, items in scans.items():
        n = len(items)
        b64 = sum(1 for _, s in items if s.is_base64)
        fm = sum(1 for _, s in items if s.has_frontmatter)
        nohead = sum(1 for _, s in items if not s.is_base64 and total_headings(s) == 0)
        print(f"| {name} | {n} | {n - b64} | {b64} | {fm} | {nohead} |")

    # ------------------------------------------------------- censimento intestazioni
    print()
    print("## Censimento delle intestazioni (campione)")
    print()
    print("Conteggi aggregati per collezione sui file Markdown del campione. `s1`/`s2` sono")
    print("intestazioni setext di livello 1/2; `#N` sono intestazioni ATX di livello N.")
    print()
    print("| Collezione | s1 | s2 | #1 | #2 | #3 | #4 | #5 | #6 |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name, items in scans.items():
        md = [s for _, s in items if not s.is_base64]
        s1 = sum(s.setext_counts[1] for s in md)
        s2 = sum(s.setext_counts[2] for s in md)
        atx = [sum(s.atx_counts[lv] for s in md) for lv in range(1, 7)]
        print(f"| {name} | {s1} | {s2} | " + " | ".join(str(a) for a in atx) + " |")

    print()
    print("### Marcatori di articolo (campione)")
    print()
    print("Tre stili distinti di marcatura degli articoli:")
    print()
    print("- `atx`: intestazione ATX `### Art. N` (atti recenti, ~post 2000);")
    print(
        "- `setext`: intestazione setext `Art. N` + `----` (es. articoli del decreto di approvazione);"
    )
    print("- `plain`: riga piatta `<Titolo atto>-art. N [bis|ter|...]` NON marcata come heading")
    print("  (atti storici multivigenti, es. Codice Penale, Codice di procedura civile).")
    print()
    print("| Collezione | File con `### Art` | File con `Art.` setext | File con marcatore plain |")
    print("| --- | ---: | ---: | ---: |")
    for name, items in scans.items():
        md = [s for _, s in items if not s.is_base64]
        a = sum(1 for s in md if s.atx_art)
        b = sum(1 for s in md if s.setext_art)
        c = sum(1 for s in md if s.plain_art)
        print(f"| {name} | {a} | {b} | {c} |")

    print()
    print("### Partizioni (LIBRO / PARTE / TITOLO / CAPO / SEZIONE) nelle intestazioni")
    print()
    part_counter: Counter[str] = Counter()
    part_examples: dict[str, list[str]] = {}
    level_examples: dict[int, list[str]] = {}
    for _, items in scans.items():
        for _, s in items:
            for level, text in s.headings:
                m = PARTITION_RE.match(text)
                if m:
                    key = m.group(1).upper()
                    part_counter[key] += 1
                    ex = part_examples.setdefault(key, [])
                    if len(ex) < 3 and text not in ex:
                        ex.append(text)
                lv_ex = level_examples.setdefault(level, [])
                if len(lv_ex) < 5 and text not in lv_ex:
                    lv_ex.append(text)
    print("| Partizione | Occorrenze nei heading (campione) | Esempi |")
    print("| --- | ---: | --- |")
    for key in ("LIBRO", "PARTE", "TITOLO", "CAPO", "SEZIONE"):
        exs = "; ".join(f"`{e[:80]}`" for e in part_examples.get(key, []))
        print(f"| {key} | {part_counter.get(key, 0)} | {exs} |")
    print()
    print("Esempi di testo per livello di intestazione (primi incontrati nel campione):")
    print()
    for level in sorted(level_examples):
        exs = "; ".join(f"`{e[:70]}`" for e in level_examples[level])
        print(f"- livello {level}: {exs}")

    # ---------------------------------------------------------------- Codici
    print()
    print("## Collezione `Codici` (analisi completa)")
    print()
    codici = collections.get("Codici", [])
    sizes = [s for _, s in codici]
    if sizes:
        p = percentiles(sizes)
        print("Distribuzione delle dimensioni (byte) sui", len(codici), "file:")
        print()
        print("| min | p25 | mediana | p75 | max | media |")
        print("| ---: | ---: | ---: | ---: | ---: | ---: |")
        print(
            f"| {fmt_size(p['min'])} | {fmt_size(p['p25'])} | {fmt_size(p['mediana'])} "
            f"| {fmt_size(p['p75'])} | {fmt_size(p['max'])} | {fmt_size(int(statistics.mean(sizes)))} |"
        )
        print()
        sample = rng.sample(codici, min(10, len(codici)))
        print("Primi 50 caratteri di 10 file campione:")
        print()
        print("| File | Primi 50 caratteri |")
        print("| --- | --- |")
        for fn, _ in sorted(sample):
            print(f"| {fn[:60]} | `{first_chars(corpus / 'Codici' / fn)}` |")

    # ------------------------------------------------------------ naming file
    print()
    print("## Naming convention dei file")
    print()
    print("Classi rilevate (regex in `analyze_corpus.py`):")
    print()
    print("| Collezione | Classe | File nel campione | Esempi |")
    print("| --- | --- | ---: | --- |")
    for name, items in scans.items():
        classes: dict[str, list[str]] = {}
        for fn, _ in items:
            classes.setdefault(classify_filename(fn), []).append(fn)
        for cls in sorted(classes):
            fns = classes[cls]
            exs = "; ".join(f"`{fn[:60]}`" for fn in fns[:2])
            print(f"| {name} | {cls} | {len(fns)} | {exs} |")

    # -------------------------------------------------------------- patologici
    print()
    print("## Casi patologici")
    print()
    all_files = [
        (size, f"{cname}/{fn}") for cname, files in collections.items() for fn, size in files
    ]
    all_files.sort()
    print("### 10 file piu' grandi (tutto il corpus)")
    print()
    print("| File | Dimensione |")
    print("| --- | ---: |")
    for size, rel in reversed(all_files[-10:]):
        print(f"| {rel[:100]} | {fmt_size(size)} |")
    print()
    print("### 10 file piu' piccoli (tutto il corpus)")
    print()
    print("| File | Dimensione | Primi 50 caratteri |")
    print("| --- | ---: | --- |")
    for size, rel in all_files[:10]:
        print(f"| {rel[:100]} | {fmt_size(size)} | `{first_chars(corpus / rel)}` |")
    print()
    nohead_examples = [
        f"{name}/{fn}"
        for name, items in scans.items()
        for fn, s in items
        if not s.is_base64 and total_headings(s) == 0
    ]
    print(f"### File Markdown senza alcuna intestazione: {len(nohead_examples)} nel campione")
    print()
    for rel in nohead_examples[:10]:
        print(f"- `{rel[:110]}`")
    b64_total = sum(1 for _, items in scans.items() for _, s in items if s.is_base64)
    print()
    print(f"### File base64-HTML nel campione: {b64_total}")
    print()
    comma_files = sum(
        1 for _, items in scans.items() for _, s in items if not s.is_base64 and s.comma_lines
    )
    print(
        f"File Markdown del campione con commi numerati a inizio riga (`1. `, `2-bis. `): {comma_files}"
    )

    print()
    print("### Padding NUL in coda ai file (campione)")
    print()
    print("Molti file (quasi tutti in `Regi decreti`) sono atti brevi gonfiati fino a ~1 MiB")
    print("con byte NUL (`\\x00`) in coda: un difetto di generazione del corpus che spiega")
    print("quasi tutta la dimensione su disco. Il contenuto utile termina al primo NUL.")
    print()
    print("| Collezione | File con padding > 1 KB | Padding totale | Dimensione campione |")
    print("| --- | ---: | ---: | ---: |")
    for name, items in scans.items():
        padded = 0
        pad_bytes = 0
        sample_bytes = 0
        for fn, _ in items:
            path = corpus / name / fn
            sample_bytes += path.stat().st_size
            pad = nul_padding_bytes(path)
            if pad > 1024:
                padded += 1
                pad_bytes += pad
        print(
            f"| {name} | {padded}/{len(items)} | {fmt_size(pad_bytes)} | {fmt_size(sample_bytes)} |"
        )

    print()
    print("### Duplicazione tra collezioni")
    print()
    name_count: Counter[str] = Counter()
    pair_count: Counter[tuple[str, int]] = Counter()
    for files in collections.values():
        for fn, size in files:
            name_count[fn] += 1
            pair_count[(fn, size)] += 1
    dup_names = sum(1 for c in name_count.values() if c > 1)
    dup_pairs = sum(1 for c in pair_count.values() if c > 1)
    print(
        f"Le collezioni si sovrappongono, ma MOLTO meno di quanto suggeriscano i soli nomi: "
        f"{dup_names} filename (su {len(name_count)} unici, {tot_files} file totali) compaiono "
        f"in 2+ collezioni, ma solo {dup_pairs} coppie (filename, dimensione) coincidono."
    )
    print()
    print(
        f"- **{dup_names} collisioni di nome**: in larga parte atti DIVERSI che condividono il "
        "titolo sanificato. Es. `Modificazioni delle aliquote dellimposta di fabbricazione su "
        "alcuni prodotti petroliferi.md` compare in 5 collezioni: 4 atti distinti più una copia "
        "identica — le dimensioni non coincidono tra 4 dei 5 file. Il nome file, da solo, non "
        "identifica l'atto."
    )
    print(
        f"- **~{dup_pairs} duplicati plausibili** (stesso nome E stessa dimensione in 2+ "
        "collezioni): lo stesso atto archiviato in piu' collezioni. La conferma definitiva "
        "richiederebbe un hash del contenuto; la dimensione identica e' un proxy conservativo."
    )
    print()
    print("Esempi di duplicati stesso-nome-stessa-dimensione (i piu' grandi):")
    print()
    same_size_dups = sorted(
        ((fn, size, c) for (fn, size), c in pair_count.items() if c > 1),
        key=lambda x: (-x[1], x[0]),
    )
    for fn, size, c in same_size_dups[:5]:
        print(f"- `{fn[:90]}` ({fmt_size(size)}, {c} collezioni)")

    print()
    print(PARSER_ASSUMPTIONS.strip())


PARSER_ASSUMPTIONS = """
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
   `^\\S.*-art\\. \\d+` come confine di articolo.
Suffissi: `bis`, `ter`, ... scritti dopo il numero (`art. 3 bis` → `3-bis` in act_ref).
Attenzione: gli h3 ATX NON sono solo articoli (compaiono anche per le firme, es.
`### Dato a Roma, addi' 6 luglio 1993`): filtrare con `^### Art\\.?\\s*\\d`.

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
~1 MiB con byte NUL (`\\x00`) in coda — un difetto del generatore del corpus che da
solo spiega ~71 dei ~73 GB totali. Il contenuto utile e' solo il prefisso fino al
primo NUL: il parser DEVE troncare al primo `\\x00` (`text.split("\\x00", 1)[0]`)
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
"""


if __name__ == "__main__":
    report()
