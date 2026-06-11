export default function Home() {
  return (
    <div className="flex flex-1 flex-col">
      <main className="flex flex-1 flex-col items-center justify-center px-6">
        <div className="flex flex-col items-center gap-5 pb-16 text-center">
          <span
            aria-hidden
            className="block h-px w-12 bg-primary/60"
          />
          <h1 className="font-display text-7xl font-medium tracking-tight text-foreground sm:text-8xl">
            legger<span className="text-primary">.</span>
          </h1>
          <p className="text-sm tracking-[0.18em] uppercase text-muted-foreground">
            La normativa italiana, letta con rigore
          </p>
        </div>
      </main>
      <footer className="border-t border-border px-6 py-5">
        <p className="mx-auto max-w-3xl text-center text-xs leading-relaxed text-muted-foreground">
          Strumento informativo, non costituisce consulenza legale. Fa fede la
          Gazzetta Ufficiale.
        </p>
      </footer>
    </div>
  );
}
