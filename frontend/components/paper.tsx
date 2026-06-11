import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Layout "paper" condiviso dalle pagine editoriali (/come-funziona,
 * /metodologia): struttura da articolo scientifico — abstract, sezioni
 * numerate con etichetta in maiuscoletto — composta con i token del
 * design system (Literata per la lettura lunga, Fraunces per i titoli,
 * bordi a capello). Server components puri: nessun JS client.
 */

export function PaperArticle({
  kicker,
  title,
  subtitle,
  children,
}: {
  kicker: string;
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <article className="mx-auto w-full max-w-2xl px-6 py-12 md:py-16">
        <nav className="mb-10 font-sans text-sm">
          <Link
            href="/"
            className="text-muted-foreground transition-colors hover:text-primary"
          >
            ← Torna alla chat
          </Link>
        </nav>
        <header className="mb-8">
          <p className="mb-3 font-sans text-xs font-semibold tracking-[0.18em] text-primary uppercase">
            {kicker}
          </p>
          <h1 className="font-display text-3xl font-medium tracking-tight text-balance md:text-4xl">
            {title}
          </h1>
          {subtitle ? (
            <p className="mt-3 font-serif text-lg text-muted-foreground italic">
              {subtitle}
            </p>
          ) : null}
        </header>
        <div className="font-serif text-[1.0625rem] leading-[1.85]">
          {children}
        </div>
      </article>
    </div>
  );
}

/** Blocco abstract: tra filetti orizzontali, come in un paper. */
export function Abstract({ children }: { children: ReactNode }) {
  return (
    <section className="my-8 border-y border-border py-6">
      <h2 className="mb-3 font-sans text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase">
        Abstract
      </h2>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

/** Sezione numerata: etichetta § in maiuscoletto + titolo Fraunces. */
export function Section({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="mt-12">
      <p className="font-sans text-xs font-semibold tracking-[0.18em] text-primary uppercase">
        § {n}
      </p>
      <h2 className="mt-1 mb-4 font-display text-2xl font-medium tracking-tight">
        {title}
      </h2>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

/** Coda dell'articolo: rimando alla pagina gemella, sopra un filetto. */
export function PaperFooterNav({
  href,
  label,
}: {
  href: string;
  label: string;
}) {
  return (
    <nav className="mt-14 flex items-baseline justify-between gap-4 border-t border-border pt-6 font-sans text-sm">
      <Link
        href="/"
        className="text-muted-foreground transition-colors hover:text-primary"
      >
        ← Torna alla chat
      </Link>
      <Link
        href={href}
        className="text-muted-foreground transition-colors hover:text-primary"
      >
        {label} →
      </Link>
    </nav>
  );
}

/* ------------------------------------------------------------------ */
/* Tabella stile "markdown in chat": bordi a capello, numeri tabulari.  */
/* ------------------------------------------------------------------ */

export function PaperTable({
  caption,
  children,
}: {
  caption?: string;
  children: ReactNode;
}) {
  return (
    <figure className="my-5">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-sans text-sm">
          {children}
        </table>
      </div>
      {caption ? (
        <figcaption className="mt-2 font-sans text-xs leading-relaxed text-muted-foreground">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

export function Th({
  children,
  numeric,
}: {
  children?: ReactNode;
  numeric?: boolean;
}) {
  return (
    <th
      className={`border-b border-foreground/25 px-2 py-1.5 font-semibold ${
        numeric ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  numeric,
}: {
  children?: ReactNode;
  numeric?: boolean;
}) {
  return (
    <td
      className={`border-b border-border px-2 py-1.5 align-top ${
        numeric ? "text-right tabular-nums" : "text-left whitespace-nowrap"
      }`}
    >
      {children}
    </td>
  );
}
