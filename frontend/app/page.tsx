import { Chat } from "@/components/chat";

export default function Home() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-b border-border px-6 py-3">
        <span className="font-display text-xl font-medium tracking-tight">
          legger<span className="text-primary">.</span>
        </span>
      </header>
      <Chat />
    </div>
  );
}
