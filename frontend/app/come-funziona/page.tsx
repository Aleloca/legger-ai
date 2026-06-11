import type { Metadata } from "next";
import Link from "next/link";

import {
  Abstract,
  PaperArticle,
  PaperFooterNav,
  Section,
} from "@/components/paper";

/**
 * /come-funziona — il sistema spiegato come un paper, per il lettore
 * curioso e non del mestiere: ogni termine tecnico è in corsivo e
 * definito alla prima occorrenza. I numeri citati provengono da
 * docs/corpus-analysis.md e dal design doc; le misure di validazione
 * sono documentate nella pagina gemella /metodologia.
 */

export const metadata: Metadata = {
  title: "Come funziona Legger — il sistema, dalla norma alla risposta",
  description:
    "Come Legger risponde alle domande sulla legislazione italiana: la fonte dei dati, la ricerca ibrida, la generazione vincolata e il controllo automatico delle citazioni. Ogni termine tecnico spiegato.",
};

export default function ComeFunzionaPage() {
  return (
    <PaperArticle
      kicker="Legger · Documentazione"
      title="Come funziona Legger"
      subtitle="Il sistema, dalla norma alla risposta"
    >
      <Abstract>
        <p>
          Legger risponde a domande sulla legislazione statale italiana
          ancorando ogni affermazione al testo ufficiale delle norme. Invece di
          chiedere a un modello linguistico di «ricordare» la legge, il sistema
          recupera i passaggi pertinenti da un archivio di 181.870 atti
          normativi derivato da Normattiva, li consegna al modello insieme a
          regole vincolanti e verifica automaticamente ogni citazione prodotta
          contro i testi effettivamente recuperati. Il risultato è una risposta
          in linguaggio naturale in cui ogni riferimento normativo è cliccabile
          e confrontabile con la fonte, affiancata dall&rsquo;elenco completo dei
          passaggi consultati. Questa pagina descrive l&rsquo;intero processo; le
          misurazioni che lo validano sono nella pagina{" "}
          <Link
            href="/metodologia"
            className="text-primary underline underline-offset-2"
          >
            Metodologia e validazione
          </Link>
          .
        </p>
      </Abstract>

      <Section n={1} title="Il problema: perché un modello generico non basta">
        <p>
          Un <em>modello linguistico di grandi dimensioni</em> (in inglese{" "}
          <em>large language model</em>, LLM) è un programma statistico
          addestrato su enormi quantità di testo, che impara a continuare un
          testo nel modo più plausibile. Produce prosa fluente e spesso
          corretta, ma mentre risponde non consulta alcuna fonte: attinge a
          regolarità apprese durante l&rsquo;addestramento. Quando quelle
          regolarità non bastano, il modello può produrre una{" "}
          <em>allucinazione</em>: un&rsquo;affermazione perfettamente plausibile
          nella forma e falsa nella sostanza.
        </p>
        <p>
          Nel diritto questo difetto è particolarmente grave, per tre ragioni.
          Primo, le <strong>citazioni inventate</strong>: un modello generico
          può citare con sicurezza un numero di articolo sbagliato o un atto
          che non esiste, e una citazione errata rende inutile — o dannosa —
          l&rsquo;intera risposta. Secondo, la <strong>vigenza</strong>: le norme
          cambiano di continuo, e ciò che il modello ha visto in addestramento
          può essere stato nel frattempo abrogato o riscritto (una norma è{" "}
          <em>vigente</em> quando è attualmente in vigore, <em>abrogata</em>{" "}
          quando è stata eliminata dall&rsquo;ordinamento). Terzo,
          l&rsquo;autorevolezza apparente: il tono sicuro del modello non
          distingue ciò che sa da ciò che inventa.
        </p>
        <p>
          Legger inverte il ruolo del modello: non gli chiede di sapere la
          legge, gli chiede di leggerla. L&rsquo;architettura si chiama{" "}
          <em>retrieval-augmented generation</em> («generazione aumentata dal
          recupero»): prima si recuperano dall&rsquo;archivio i testi normativi
          pertinenti, poi si genera la risposta vincolandola a quei soli testi.
          Le sezioni che seguono percorrono la catena, anello per anello.
        </p>
      </Section>

      <Section n={2} title="La fonte dei dati">
        <p>
          La fonte primaria è <strong>Normattiva</strong>, la banca dati
          pubblica ufficiale della normativa statale italiana. Legger non la
          interroga direttamente: si appoggia a <strong>italia-corpus</strong>,
          un progetto pubblico che converte gli atti di Normattiva in semplici
          file di testo — un file per atto — organizzati in 23 raccolte per
          tipo (codici, decreti legislativi, testi unici, regi decreti…). Le
          raccolte degli atti abrogati e dei decreti-legge decaduti sono
          separate dalle altre: da questa struttura Legger deriva lo stato di
          vigenza di ciascun atto. Dopo l&rsquo;eliminazione dei duplicati,
          l&rsquo;archivio indicizzato conta <strong>181.870 atti</strong>.
        </p>
        <p>
          Il corpus è mantenuto con <em>git</em>, uno strumento nato per lo
          sviluppo software che funziona come un registro storico delle
          modifiche: ogni cambiamento è registrato come un «commit», una
          fotografia datata di che cosa è cambiato, in quali file e quando.
          Quando Normattiva consolida una modifica normativa, il corpus riceve
          un commit; Legger scarica quotidianamente le novità e ri-indicizza
          soltanto i file toccati, non l&rsquo;intero archivio. Lo stesso
          registro renderà possibile, in roadmap, rispondere a domande come
          «com&rsquo;era questo articolo al 15 marzo 2024?».
        </p>
      </Section>

      <Section n={3} title="Dalla norma ai «chunk»">
        <p>
          Un motore di ricerca semantico non lavora su interi codici (il solo
          codice civile conta migliaia di articoli): i testi vanno prima divisi
          in <em>chunk</em>, porzioni autosufficienti da indicizzare e
          recuperare singolarmente. La scelta dell&rsquo;unità di taglio è la
          decisione più importante dell&rsquo;intero sistema, e per Legger
          l&rsquo;unità è <strong>l&rsquo;articolo</strong>: mai tagli a lunghezza
          fissa che spezzano un <em>comma</em> (il capoverso numerato che
          compone un articolo) a metà. Gli articoli molto lunghi — come gli
          articoli unici delle leggi di bilancio, con centinaia di commi —
          vengono divisi per gruppi di commi, con una piccola sovrapposizione
          tra un gruppo e il successivo per non perdere il filo.
        </p>
        <p>
          Un frammento isolato, però, non dice da dove viene: «Ciascuno è
          responsabile del danno cagionato dalle cose che ha in custodia…» non
          rivela né l&rsquo;atto né l&rsquo;articolo. Per questo ogni chunk è
          prefissato da un&rsquo;<em>intestazione contestuale</em> che ne dichiara
          la provenienza, ad esempio:
        </p>
        <blockquote className="my-4 border-l-2 border-primary/30 py-1 pl-4 font-sans text-sm leading-relaxed text-muted-foreground">
          Codice 262/1942 — Approvazione del testo del Codice civile
          <br />
          Art. 2051 — Danno cagionato da cosa in custodia
          <br />
          <span className="text-foreground/80 italic">
            Ciascuno è responsabile del danno cagionato dalle cose che ha in
            custodia, salvo che provi il caso fortuito.
          </span>
        </blockquote>
        <p>
          L&rsquo;intestazione viaggia insieme al testo anche nella fase di
          indicizzazione, e migliora sensibilmente la ricerca: la domanda
          «danno da cosa in custodia» trova l&rsquo;articolo anche quando il
          corpo del testo usa parole diverse. Applicato all&rsquo;intero
          archivio, il processo produce <strong>966.822 chunk</strong>, pari a
          circa 524 milioni di <em>token</em> (il token è l&rsquo;unità con cui
          i modelli misurano il testo: all&rsquo;incirca una parola breve o un
          pezzo di parola; in questo corpus, in media 2,26 caratteri per
          token).
        </p>
      </Section>

      <Section n={4} title="La ricerca ibrida">
        <p>
          Quando l&rsquo;utente pone una domanda, il sistema la cerca
          nell&rsquo;archivio per due strade complementari, perché le domande
          legali sono di due nature radicalmente diverse.
        </p>
        <p>
          La prima strada è semantica. Un modello di <em>embedding</em>{" "}
          trasforma ogni testo in un <em>vettore semantico</em>: un punto in
          uno spazio matematico a migliaia di dimensioni, costruito in modo
          che testi con significato simile finiscano vicini. L&rsquo;analogia
          più fedele è una mappa sterminata in cui ogni frammento di legge è
          uno spillo, e gli spilli sono raggruppati per argomento anziché in
          ordine alfabetico: la domanda dell&rsquo;utente diventa a sua volta
          uno spillo sulla mappa, e il sistema raccoglie i frammenti più
          vicini. È così che «il cane del vicino mi ha morso, chi mi paga i
          danni?» trova l&rsquo;art. 2052 del codice civile (danno cagionato da
          animali) pur non condividendo con esso quasi nessuna parola.
        </p>
        <p>
          La seconda strada è lessicale: l&rsquo;algoritmo <em>BM25</em>, un
          classico dei motori di ricerca, conta le parole esattamente in
          comune tra domanda e documento, dando più peso ai termini rari. È
          indispensabile per le query come «art. 613-bis c.p.», dove contano i
          caratteri esatti e non il significato. Le due classifiche vengono
          poi fuse con la <em>Reciprocal Rank Fusion</em> (RRF), una regola
          che premia i documenti ben posizionati in entrambe le liste: un
          chunk in alto sia per somiglianza di significato sia per parole in
          comune vince su uno forte in una sola delle due.
        </p>
        <p>
          Sopra le due strade c&rsquo;è una corsia preferenziale, il{" "}
          <em>fast path</em>: se la domanda contiene estremi normativi
          espliciti e riconoscibili («art. 2051 c.c.», «d.lgs. 81/2008»), un
          riconoscitore di pattern li estrae e recupera direttamente
          l&rsquo;articolo dall&rsquo;archivio, senza passare dal calcolo di
          somiglianza: una citazione esatta batte qualunque punteggio, e i
          risultati così ottenuti vanno in testa al contesto. Completano la
          pipeline la riscrittura della domanda alla luce della conversazione
          (così «e il comma successivo?» diventa una domanda autonoma) e
          l&rsquo;inseguimento dei rinvii: se i passaggi recuperati richiamano
          espressamente altre norme («ai sensi dell&rsquo;articolo…»), anche
          gli articoli richiamati vengono aggiunti al contesto, entro un
          budget massimo di testo.
        </p>
      </Section>

      <Section n={5} title="La generazione vincolata">
        <p>
          I passaggi recuperati — di norma una decina — vengono consegnati al
          modello linguistico insieme a un <em>prompt di sistema</em>: le
          istruzioni permanenti che definiscono che cosa il modello può e non
          può fare. Le regole sono poche e senza eccezioni: rispondere{" "}
          <strong>solo</strong> sulla base dei passaggi forniti («se
          un&rsquo;informazione non è nei passaggi, non la conosci»); ogni
          affermazione normativa deve citare la fonte con un <em>marker</em>,
          un&rsquo;etichetta leggibile dalla macchina nel formato{" "}
          <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[0.85em]">
            [[codice-civile|art.2051]]
          </code>
          ; se il contesto non basta, dichiararlo e suggerire come riformulare
          la domanda — una risposta mancata è accettabile, una citazione
          inventata no; se un passaggio segnala che un articolo è stato
          abrogato, riferirlo fedelmente, senza mai ricostruire il testo
          previgente.
        </p>
        <p>
          Le regole, da sole, sono promesse. Per questo a valle della
          generazione opera un <em>guardrail</em>, un controllo automatico:
          mentre la risposta viene trasmessa, ogni marker emesso è confrontato
          con l&rsquo;insieme dei passaggi consegnati al modello. Se
          l&rsquo;atto o l&rsquo;articolo citato non era nel contesto, la
          citazione viene marcata «non verificata» e mostrata in ambra
          nell&rsquo;interfaccia.
        </p>
        <p>
          Un&rsquo;avvertenza di onestà semantica: «verificata» ha qui un
          significato preciso e limitato. Vuol dire che la citazione punta a
          un testo che il sistema ha davvero recuperato e mostrato al modello
          — cioè che il modello non sta citando a memoria. <em>Non</em> vuol
          dire che la citazione sia giuridicamente corretta o pertinente al
          caso: quel giudizio resta al lettore, ed è per questo che la fonte è
          sempre a un click di distanza.
        </p>
      </Section>

      <Section n={6} title="Trasparenza">
        <p>
          Tre scelte di interfaccia rendono il sistema ispezionabile. In calce
          a ogni risposta compare l&rsquo;elenco delle{" "}
          <strong>fonti consultate</strong>: tutti i passaggi consegnati al
          modello, compresi quelli che la risposta non cita — ciò che il
          modello ha letto, non solo ciò che ha usato. Ogni citazione porta un{" "}
          <strong>badge di vigenza</strong>: verde per le norme vigenti,
          grigio per le abrogate, ambra per le citazioni non verificate dal
          guardrail. E ogni citazione apre la <em>split-view</em>: un pannello
          affiancato alla chat con il testo integrale dell&rsquo;atto, fatto
          scorrere fino all&rsquo;articolo citato e con i commi rilevanti
          evidenziati, più il collegamento alla pagina ufficiale su
          Normattiva. L&rsquo;interfaccia è disegnata perché verificare sia
          più facile che fidarsi.
        </p>
      </Section>

      <Section n={7} title="Limiti dichiarati">
        <p>
          Un sistema onesto dichiara dove finisce. Questi sono i limiti
          attuali di Legger.
        </p>
        <p>
          <strong>La vigenza ha la granularità dell&rsquo;atto.</strong> Lo
          stato vigente/abrogato deriva dalla raccolta in cui l&rsquo;atto è
          archiviato e vale per l&rsquo;atto intero. Un singolo articolo
          abrogato dentro un codice vigente — per esempio l&rsquo;art. 594 del
          codice penale (ingiuria), abrogato nel 2016 — è riconoscibile solo
          dalla nota di abrogazione presente nel testo consolidato, che il
          sistema riporta fedelmente; il badge dell&rsquo;atto, però, resta
          «vigente».
        </p>
        <p>
          <strong>Solo normativa statale.</strong> Niente giurisprudenza (le
          sentenze dei tribunali), normativa regionale, circolari o prassi
          amministrativa: sono in roadmap, fuori dal perimetro attuale.
        </p>
        <p>
          <strong>La storia parte dal 2025.</strong> Il registro git del
          corpus inizia con la nascita del progetto italia-corpus: per le
          versioni anteriori di una norma fa fede Normattiva.
        </p>
        <p>
          <strong>L&rsquo;intelligenza artificiale può sbagliare.</strong> Il
          guardrail elimina le citazioni a memoria, ma non ogni possibile
          errore di lettura o di sintesi del modello. Il disclaimer in fondo a
          ogni pagina — strumento informativo, non costituisce consulenza
          legale; fa fede la Gazzetta Ufficiale — non è una formula di stile:
          descrive esattamente il perimetro entro cui il sistema è stato
          progettato e validato.
        </p>
      </Section>

      <PaperFooterNav href="/metodologia" label="Metodologia e validazione" />
    </PaperArticle>
  );
}
