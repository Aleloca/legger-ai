"use client";

/**
 * Il composer: textarea con autofocus, Invio invia, Maiusc+Invio va a
 * capo; disabilitato durante lo streaming.
 */

import { ArrowUp } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";

export function Composer({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
}) {
  const [value, setValue] = React.useState("");
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  // Riporta il focus quando lo streaming finisce.
  React.useEffect(() => {
    if (!disabled) textareaRef.current?.focus();
  }, [disabled]);

  const submit = () => {
    const text = value.trim();
    if (!text || disabled) return;
    setValue("");
    onSend(text);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t border-border px-6 py-4">
      <form
        className="mx-auto flex w-full max-w-3xl items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
          autoFocus
          rows={1}
          aria-label="Domanda sulla normativa"
          placeholder="Chiedi della normativa — es. «art. 2051 c.c.»"
          className="max-h-40 min-h-9 flex-1 resize-none rounded-md border border-input bg-card px-3 py-2 text-[0.9375rem] leading-normal field-sizing-content placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 focus-visible:outline-none disabled:opacity-60"
        />
        <Button
          type="submit"
          size="icon"
          aria-label="Invia"
          disabled={disabled || value.trim().length === 0}
        >
          <ArrowUp />
        </Button>
      </form>
    </div>
  );
}
