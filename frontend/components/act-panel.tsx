"use client";

/**
 * ActPanel: la split-view della norma (Task G4) — il gesto firma del
 * prodotto: click su un chip-citazione, e il testo dell'atto si apre
 * accanto alla chat, composto come carta.
 *
 * Layout
 * ------
 * - Desktop (lg+): colonna destra del grid di page.tsx (~45%), scroll
 *   indipendente, bordo sinistro a capello. Chiudibile con la X o Esc.
 * - Mobile (<lg): bottom sheet fisso (~85dvh, angoli alti arrotondati,
 *   maniglia visiva), overlay + slide-in dal basso — niente librerie di
 *   gesture, la maniglia è solo affordance visiva.
 *
 * Tipografia: il testo della norma è in Literata (--font-serif), layout
 * editoriale — numero d'articolo in maiuscoletto bordeaux nel margine
 * sinistro (inline su mobile), rubrica in corsivo, commi come paragrafi
 * con il numero in esponente discreto, intestazioni di partizione
 * (Libro/Titolo/Capo) come separatori in maiuscoletto. Deve sembrare
 * carta stampata, non una web page.
 *
 * Dati e targeting
 * ----------------
 * - fetchAct (lib/api.ts) ha la cache di sessione; il pannello inoltre
 *   NON rifà il fetch quando il re-target resta sullo stesso atto.
 * - All'apertura (e ad ogni re-target) si scorre all'articolo citato
 *   (id = campo `anchor` dell'API) con un impulso di sfondo
 *   (carta → evidenziatore → carta, classe .pulse-articolo applicata
 *   imperativamente così da ripartire anche su target identico); il
 *   comma citato resta evidenziato finché il target non cambia.
 * - Nessuna virtualizzazione: il Codice civile (3282 articoli) viene
 *   reso per intero. Gli ArticleBlock sono memoizzati, quindi un
 *   re-target ridisegna solo gli articoli il cui evidenziato cambia.
 *   `content-visibility: auto` sugli articoli lascia al browser il
 *   layout solo del viewport (con un'altezza intrinseca stimata per
 *   tenere stabile la scrollbar): misurato necessario per il CC.
 */

import { X } from "lucide-react";
import * as React from "react";

import { actRefLabel, actRefName } from "@/lib/act-labels";
import { fetchAct, type ActArticle, type ActDetail } from "@/lib/api";
import { dedupPartitionLabel, renderNormText } from "@/lib/norm-text";
import { cn } from "@/lib/utils";

/**
 * Da lg in su il pannello è una colonna affiancata (complementary);
 * sotto è un bottom sheet modale (dialog). Il ruolo ARIA deve seguire
 * il breakpoint: matchMedia, con guardia per jsdom (che non lo
 * implementa: nei test vale il ramo mobile).
 */
function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = React.useState(false);
  React.useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(min-width: 64rem)");
    const update = () => setIsDesktop(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return isDesktop;
}

/** Il bersaglio del pannello: l'atto e l'articolo/comma citati. */
export interface ActTarget {
  actRef: string;
  article: string;
  comma: string | null;
}

/**
 * Limite di Normattiva: non espone URL stabili e linkabili per atto
 * (gli URN richiedono estremi completi di data che non abbiamo per i
 * Codici, e le pagine di dettaglio sono dietro sessione). Il link più
 * affidabile è la ricerca semplice, dove l'utente incolla gli estremi.
 */
const NORMATTIVA_URL = "https://www.normattiva.it/ricerca/semplice";

export function ActPanel({
  target,
  onClose,
}: {
  target: ActTarget;
  onClose: () => void;
}) {
  const [act, setAct] = React.useState<ActDetail | null>(null);
  const [error, setError] = React.useState<{
    actRef: string;
    message: string;
  } | null>(null);
  const [attempt, setAttempt] = React.useState(0);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const panelRef = React.useRef<HTMLElement>(null);
  const isDesktop = useIsDesktop();

  // Gestione del focus: all'apertura il focus entra nel pannello
  // (tabIndex -1 sul contenitore); alla chiusura (unmount) torna
  // all'elemento che l'ha aperto — il chip o la riga-fonte cliccata.
  React.useEffect(() => {
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    panelRef.current?.focus();
    return () => opener?.focus();
  }, []);

  // Esc chiude il pannello, da qualunque punto della pagina.
  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Fetch dell'atto: le dipendenze sono SOLO act_ref e il contatore dei
  // retry — il re-target nello stesso atto non riesegue l'effetto (e
  // quindi non rifà il fetch), riusa lo stato già caricato. Lo stato
  // dell'atto/errore precedente NON viene azzerato qui (niente setState
  // sincrono nell'effetto): `current`/`shownError` qui sotto mostrano
  // solo ciò che appartiene al bersaglio attuale.
  React.useEffect(() => {
    let cancelled = false;
    fetchAct(target.actRef).then(
      (loaded) => {
        if (cancelled) return;
        setAct(loaded);
        setError(null);
      },
      (err: unknown) => {
        if (cancelled) return;
        setError({
          actRef: target.actRef,
          message: err instanceof Error ? err.message : "Errore imprevisto.",
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [target.actRef, attempt]);

  /** L'atto in stato, ma solo se è quello del bersaglio (altrimenti: caricamento). */
  const current = act && act.act_ref === target.actRef ? act : null;
  const shownError =
    error && error.actRef === target.actRef ? error.message : null;

  // L'articolo bersaglio: la prima occorrenza del numero citato
  // (gli anchor disambiguano i numeri ripetuti, qui basta la prima).
  const targetArticle = React.useMemo(
    () => current?.articles.find((a) => a.number === target.article) ?? null,
    [current, target.article],
  );

  // Scroll all'articolo citato + impulso. Dipende dall'IDENTITÀ di
  // `target` (page.tsx crea un oggetto nuovo ad ogni click), così anche
  // il click ripetuto sulla stessa citazione (e ogni re-target nello
  // stesso atto già aperto) riparte scroll e impulso.
  //
  // Lo scroll viene RIBADITO per qualche frame: con `content-visibility:
  // auto` il primo scrollIntoView naviga su altezze STIMATE degli
  // articoli non ancora renderizzati — su atti lunghi (Codice civile)
  // WebKit/Firefox atterrano lontani dal bersaglio, percepito come
  // "non scrolla". Dopo il primo salto il motore impagina la zona di
  // atterraggio e le stime diventano misure: si ricontrolla per qualche
  // rAF e si corregge finché la posizione non è stabile sul bersaglio.
  React.useEffect(() => {
    if (!targetArticle) return;
    const container = scrollRef.current;
    const el = container?.querySelector<HTMLElement>(
      `#${CSS.escape(targetArticle.anchor)}`,
    );
    if (!container || !el) return;
    // jsdom non implementa scrollIntoView: guardia per i test.
    el.scrollIntoView?.({ block: "start" });
    el.classList.remove("pulse-articolo");
    void el.offsetWidth; // reflow: fa ripartire l'animazione
    el.classList.add("pulse-articolo");

    // Correzione post-layout (vedi sopra): a ogni frame si misura la
    // deriva REALE del bersaglio dal bordo del viewport (rect, non stime)
    // e si corregge scrollTop direttamente — ripetere scrollIntoView non
    // basta: Firefox ricalcola lo stesso bersaglio sbagliato. Ci si ferma
    // appena la posizione è giusta, con un tetto di sicurezza di 12 frame.
    const margin = Number.parseFloat(getComputedStyle(el).scrollMarginTop) || 0;
    let frames = 0;
    let stable = 0;
    let raf = 0;
    const settle = () => {
      const drift =
        el.getBoundingClientRect().top -
        container.getBoundingClientRect().top -
        margin;
      if (Math.abs(drift) > 1) {
        container.scrollTop += drift;
        stable = 0;
      } else {
        stable += 1; // due frame fermi di fila: assestato davvero
      }
      frames += 1;
      if (stable < 2 && frames < 12) raf = requestAnimationFrame(settle);
    };
    if (typeof requestAnimationFrame === "function") {
      raf = requestAnimationFrame(settle);
    }
    return () => cancelAnimationFrame(raf);
  }, [target, targetArticle]);

  // Titolo amichevole: la denominazione corrente del registro quando lo
  // slug è noto («Codice civile»), altrimenti il titolo dell'API; gli
  // estremi grezzi scendono nel sottotitolo.
  const title =
    actRefName(target.actRef) ?? current?.title ?? actRefLabel(target.actRef);
  const rawTitle =
    current?.title && current.title !== title ? current.title : null;

  return (
    <>
      {/* Scrim del bottom sheet (solo mobile): il tap fuori chiude.
          Assoluto nell'area contenuto (non fixed): header dell'app e
          footer-disclaimer restano leggibili anche a sheet aperto. */}
      <div
        aria-hidden
        onClick={onClose}
        className="animate-in fade-in absolute inset-0 z-40 bg-foreground/25 duration-300 lg:hidden"
      />
      <aside
        ref={panelRef}
        tabIndex={-1}
        role={isDesktop ? "complementary" : "dialog"}
        aria-modal={isDesktop ? undefined : true}
        aria-label="Testo della norma"
        className={cn(
          // mobile: bottom sheet, assoluto nell'area contenuto così il
          // footer-disclaimer (sotto) resta visibile
          "animate-in slide-in-from-bottom absolute inset-x-0 bottom-0 z-50 flex h-[85%] flex-col rounded-t-xl border-t border-border bg-background shadow-[0_-8px_32px_color-mix(in_srgb,var(--color-foreground)_18%,transparent)] outline-none duration-300",
          // desktop: colonna del grid, scroll indipendente
          "lg:static lg:z-auto lg:h-auto lg:min-h-0 lg:animate-none lg:rounded-none lg:border-t-0 lg:border-l lg:shadow-none",
        )}
      >
        {/* Maniglia visiva del bottom sheet */}
        <div aria-hidden className="flex justify-center pt-2 pb-1 lg:hidden">
          <span className="h-1 w-10 rounded-full bg-border" />
        </div>

        <header className="border-b border-border px-6 pt-2 pb-3 lg:pt-4">
          <div className="flex items-start justify-between gap-4">
            <h2 className="font-display text-lg font-medium tracking-tight">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Chiudi il pannello"
              className="-mr-2 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              <X aria-hidden className="size-4" />
            </button>
          </div>

          {current ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>
                {current.act_type} ·{" "}
                {/* gli estremi grezzi dell'API, retrocessi dal titolo */}
                {rawTitle ?? actRefLabel(current.act_ref)}
              </span>
              <VigenzaBadge vigenza={current.vigenza} />
              <a
                href={NORMATTIVA_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline underline-offset-2"
              >
                Apri su Normattiva
              </a>
            </div>
          ) : null}

          {current && targetArticle ? (
            <Breadcrumb act={current} article={targetArticle} />
          ) : null}

          {current && !targetArticle ? (
            <p className="mt-2 text-xs text-non-verificato">
              art. {target.article}: articolo non indicato nell&apos;atto
            </p>
          ) : null}
        </header>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 py-6"
        >
          {shownError ? (
            <ErrorCard
              message={shownError}
              onRetry={() => {
                setError(null);
                setAttempt((n) => n + 1);
              }}
            />
          ) : current ? (
            <ActBody
              act={current}
              targetAnchor={targetArticle?.anchor ?? null}
              targetComma={target.comma}
            />
          ) : (
            <Skeleton />
          )}
        </div>
      </aside>
    </>
  );
}

function VigenzaBadge({ vigenza }: { vigenza: string }) {
  const vigente = vigenza === "vigente";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-px font-medium tracking-wide [font-variant-caps:small-caps]",
        vigente
          ? "border-vigente/30 bg-vigente-muted text-vigente"
          : "border-abrogato/30 bg-abrogato-muted text-abrogato",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          vigente ? "bg-vigente" : "bg-abrogato",
        )}
      />
      {vigenza}
    </span>
  );
}

/** Titolo dell'atto → partizioni dell'articolo citato → art. N. */
function Breadcrumb({ act, article }: { act: ActDetail; article: ActArticle }) {
  const crumbs = [
    actRefName(act.act_ref) ?? act.title ?? actRefLabel(act.act_ref),
    // il corpus duplica talvolta l'etichetta di partizione ("CAPO II CAPO
    // II DISPOSIZIONI…"): dedup di display, vedi lib/norm-text.tsx
    ...article.path.map(dedupPartitionLabel),
    `art. ${article.number}`,
  ];
  return (
    <nav
      aria-label="Posizione dell'articolo citato"
      className="mt-2 truncate text-xs text-muted-foreground"
    >
      {crumbs.map((crumb, i) => (
        <React.Fragment key={i}>
          {i > 0 ? <span aria-hidden> › </span> : null}
          <span className={i === crumbs.length - 1 ? "text-foreground" : undefined}>
            {crumb}
          </span>
        </React.Fragment>
      ))}
    </nav>
  );
}

/** Il testo integrale, articolo per articolo, con i separatori di partizione. */
function ActBody({
  act,
  targetAnchor,
  targetComma,
}: {
  act: ActDetail;
  targetAnchor: string | null;
  targetComma: string | null;
}) {
  return (
    <div className="mx-auto max-w-2xl font-serif">
      {act.articles.map((article, i) => {
        // separatore quando la gerarchia cambia rispetto all'articolo precedente
        const pathKey = article.path.join("›");
        const previousPath = i > 0 ? act.articles[i - 1].path.join("›") : null;
        const showPath = article.path.length > 0 && pathKey !== previousPath;
        const isTarget = article.anchor === targetAnchor;
        return (
          <React.Fragment key={article.anchor}>
            {showPath ? <PartitionHeader path={article.path} /> : null}
            <ArticleBlock
              article={article}
              highlightComma={isTarget ? targetComma : null}
            />
          </React.Fragment>
        );
      })}
    </div>
  );
}

/** Separatore di partizione (Libro › Titolo › Capo) in maiuscoletto. */
function PartitionHeader({ path }: { path: string[] }) {
  return (
    <div className="mt-8 mb-2 border-b border-border pb-1 first:mt-0">
      <p className="font-sans text-[0.6875rem] font-medium tracking-[0.08em] text-muted-foreground [font-variant-caps:small-caps]">
        {path.map(dedupPartitionLabel).join("  ·  ")}
      </p>
    </div>
  );
}

/**
 * Un articolo, composto come su carta. Memoizzato: con il Codice civile
 * (3282 articoli) il re-target deve ridisegnare solo gli articoli il cui
 * `highlightComma` cambia, non l'intero corpo.
 */
const ArticleBlock = React.memo(function ArticleBlock({
  article,
  highlightComma,
}: {
  article: ActArticle;
  /** Il comma da tenere evidenziato (solo sull'articolo citato). */
  highlightComma: string | null;
}) {
  return (
    <article
      id={article.anchor}
      // content-visibility: il browser salta layout/paint degli articoli
      // fuori viewport; l'altezza intrinseca stimata tiene ferma la
      // scrollbar. Indispensabile per il CC, innocuo per gli atti brevi.
      className="scroll-mt-3 rounded-sm py-4 [contain-intrinsic-block-size:auto_180px] [content-visibility:auto] lg:grid lg:grid-cols-[5rem_minmax(0,1fr)] lg:gap-x-5"
    >
      <div className="font-sans text-sm font-medium tracking-wide text-primary [font-variant-caps:small-caps] lg:pt-px lg:text-right">
        art. {article.number}
      </div>
      <div className="min-w-0">
        {article.heading ? (
          <h3 className="mb-1.5 text-[0.9375rem] leading-6 italic">
            {renderNormText(article.heading)}
          </h3>
        ) : null}
        {article.commi.map((comma, i) => (
          <p
            key={i}
            data-comma={comma.number ?? undefined}
            data-evidenziato={
              highlightComma !== null && comma.number === highlightComma
                ? ""
                : undefined
            }
            className={cn(
              "my-1.5 text-[0.9375rem] leading-7 first:mt-0 last:mb-0",
              highlightComma !== null &&
                comma.number === highlightComma &&
                "-mx-1.5 rounded-sm bg-evidenzia px-1.5",
            )}
          >
            {comma.number ? (
              <sup className="mr-1 font-sans text-[0.6875rem] text-muted-foreground">
                {comma.number}
              </sup>
            ) : null}
            {renderNormText(comma.text)}
          </p>
        ))}
      </div>
    </article>
  );
});

/** Tre paragrafi fantasma mentre l'atto arriva (il CC pesa ~2 MB). */
function Skeleton() {
  return (
    <div aria-hidden className="mx-auto max-w-2xl space-y-6">
      {[0, 1, 2].map((i) => (
        <div key={i} className="animate-pulse space-y-2">
          <div className="h-3 w-24 rounded-sm bg-muted" />
          <div className="h-3 w-full rounded-sm bg-muted" />
          <div className="h-3 w-full rounded-sm bg-muted" />
          <div className="h-3 w-3/4 rounded-sm bg-muted" />
        </div>
      ))}
    </div>
  );
}

function ErrorCard({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="mx-auto max-w-2xl rounded-md border border-primary/20 bg-accent px-4 py-3 text-sm leading-relaxed text-accent-foreground">
      <p>{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 rounded-md border border-primary/30 bg-card px-3 py-1 text-xs font-medium text-primary transition-colors hover:bg-background"
      >
        Riprova
      </button>
    </div>
  );
}
