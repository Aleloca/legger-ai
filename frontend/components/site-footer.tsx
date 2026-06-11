import Link from "next/link";

/**
 * Footer persistente: il disclaimer più i due link silenziosi alle
 * pagine editoriali, separati da un filetto verticale. Estratto dal
 * root layout per essere testabile senza montare <html> e i font.
 */
export function SiteFooter() {
  return (
    <footer className="border-t border-border px-6 py-3">
      <p className="mx-auto flex max-w-3xl flex-wrap items-center justify-center gap-x-3 gap-y-1 text-center text-xs leading-relaxed text-muted-foreground">
        <span>
          Strumento informativo, non costituisce consulenza legale. Fa fede la
          Gazzetta Ufficiale.
        </span>
        <span
          aria-hidden="true"
          className="hidden h-3 w-px self-center bg-border sm:inline-block"
        />
        <span className="whitespace-nowrap">
          <Link
            href="/come-funziona"
            className="underline-offset-2 transition-colors hover:text-primary hover:underline"
          >
            Come funziona
          </Link>
          {" · "}
          <Link
            href="/metodologia"
            className="underline-offset-2 transition-colors hover:text-primary hover:underline"
          >
            Metodologia
          </Link>
        </span>
      </p>
    </footer>
  );
}
