/**
 * Seam di rendering del testo dell'assistant.
 *
 * Oggi i marker `[[act_ref|art.N|c.M]]` restano testo grezzo; il Task G3
 * sostituirà questa funzione perché restituisca chip-citazione cliccabili
 * al posto dei marker, usando le `citations` raccolte dallo stream.
 */

import type { ReactNode } from "react";

import type { Citation } from "@/lib/types";

export function renderAssistantText(
  text: string,
  citations: Citation[],
): ReactNode {
  void citations; // riservate a G3: oggi i marker non vengono sostituiti
  return text;
}
