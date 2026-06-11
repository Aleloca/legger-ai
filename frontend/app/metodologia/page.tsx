import type { Metadata } from "next";
import Link from "next/link";

import {
  Abstract,
  PaperArticle,
  PaperFooterNav,
  PaperTable,
  Section,
  Td,
  Th,
} from "@/components/paper";

/**
 * /metodologia — il processo e le prove, in stile paper. Tutti i numeri
 * provengono dai documenti di progetto: docs/corpus-analysis.md
 * (analisi del corpus, benchmark C6, stima bootstrap, reranking E3,
 * appendice collisioni), docs/c5-chat-transcript.md (validazione 14/14)
 * e dai report JSON in backend/eval/results/. Nessun numero inventato.
 */

export const metadata: Metadata = {
  title: "Metodologia e validazione — come Legger è stato misurato",
  description:
    "Il processo di costruzione di Legger e tutti i test di validazione con i numeri reali: analisi del corpus, benchmark degli embedding (recall@10 96,7%), reranking misurato ed escluso, validazione end-to-end 14/14.",
};

export default function MetodologiaPage() {
  return (
    <PaperArticle
      kicker="Legger · Documentazione"
      title="Metodologia e validazione"
      subtitle="Il processo, le misure e i loro limiti"
    >
      <Abstract>
        <p>
          Legger è stato costruito per fasi, ognuna chiusa da una verifica
          misurabile prima di procedere. Questa pagina documenta il processo
          con i numeri reali: l&rsquo;analisi del corpus (287.912 file in 23
          collezioni, con i difetti trovati e gestiti), il set di 30 domande
          di valutazione, il benchmark dei modelli di embedding — il migliore
          raggiunge il 96,7% di recall@10 — la decisione, misurata e non
          supposta, di escludere il reranking, la validazione end-to-end su 14
          conversazioni (14/14 superate, incluse le domande-trappola) e i
          costi effettivi. Chiude con i limiti della validazione stessa: 30
          domande sono un campione, non una dimostrazione. Per la descrizione
          del sistema, vedi{" "}
          <Link
            href="/come-funziona"
            className="text-primary underline underline-offset-2"
          >
            Come funziona Legger
          </Link>
          .
        </p>
      </Abstract>

      <Section n={1} title="Approccio">
        <p>
          Lo sviluppo è proceduto per fasi, ciascuna con un{" "}
          <em>cancello go/no-go</em>: prima di costruire una parte del
          prodotto si fissa un criterio quantitativo di accettazione — per la
          qualità della ricerca, un recall@10 di almeno l&rsquo;85% (la metrica
          è spiegata nella sezione 4) — e lo si misura su un prototipo. Se la
          misura è sotto soglia non si procede: si itera sul punto debole o si
          cambia strada. È il contrario del costruire tutto e sperare che
          funzioni.
        </p>
        <p>
          Per il codice deterministico — il parser dei file, il chunker, il
          riconoscimento degli estremi normativi, il guardrail delle citazioni
          — si è lavorato in modalità <em>test-driven</em>: prima si scrive un
          test automatico che definisce il comportamento atteso (e che
          inizialmente fallisce), poi il codice che lo soddisfa. I casi
          patologici scoperti nell&rsquo;analisi del corpus sono diventati test
          permanenti: ogni modifica futura li riesegue.
        </p>
      </Section>

      <Section n={2} title="Analisi del corpus">
        <p>
          Prima riga di qualunque pipeline: conoscere i propri dati. Il corpus
          conta <strong>287.912 file di testo</strong> in{" "}
          <strong>23 collezioni</strong>, per 73,3 GB nominali su disco.
          L&rsquo;analisi sistematica (campioni di 500 file per collezione,
          più la collezione Codici al completo) ha prodotto una serie di
          scoperte che hanno plasmato il parser.
        </p>
        <p>
          <strong>Due formati coesistono.</strong> La quasi totalità dei file
          è Markdown con intestazioni <em>setext</em> (titoli «sottolineati»
          con righe di = e -, anziché preceduti da #), ma alcuni file — tra
          cui, ironicamente, il Codice civile — sono in realtà HTML nel
          formato Akoma Ntoso codificato in <em>base64</em> (una codifica che
          traveste dati arbitrari da testo semplice): è servito un
          decodificatore dedicato.
        </p>
        <p>
          <strong>La dimensione su disco mente.</strong> Circa 71 dei 73,3 GB
          sono byte nulli (NUL) accodati ai file della collezione Regi decreti
          — un difetto del generatore del corpus: atti brevi gonfiati fino a
          ~1 MiB di vuoto. Il parser tronca al primo byte nullo.
        </p>
        <p>
          <strong>Tre stili di marcatura degli articoli.</strong> Gli atti
          recenti marcano gli articoli come intestazioni{" "}
          <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[0.85em]">
            ### Art. N
          </code>
          , quelli intermedi come titoli sottolineati, e gli atti storici
          multivigenti (codice penale, codice di procedura civile) usano una
          riga piatta del tipo «Codice Penale-art. 3 bis» che non è
          un&rsquo;intestazione affatto. Il parser li supporta tutti e tre.
        </p>
        <p>
          <strong>Il nome del file non identifica l&rsquo;atto.</strong>{" "}
          95.492 nomi di file compaiono in due o più collezioni, ma sono in
          gran parte atti <em>diversi</em> che condividono il titolo; i
          duplicati plausibili (stesso nome e stessa dimensione) sono circa
          6.303. Conseguenza: l&rsquo;identità di un atto si ricava dal
          contenuto (la prima intestazione, con tipo, data e numero), mai dal
          nome del file. Un&rsquo;appendice dell&rsquo;analisi ha inoltre
          rilevato che su un filesystem che ignora maiuscole e minuscole (il
          default di macOS) 353 nomi collidono tra loro e ~176 atti
          risulterebbero «oscurati» (~0,1% del corpus): da qui il requisito di
          deployment che il corpus viva su un filesystem case-sensitive.
        </p>
        <p>
          Il bootstrap dell&rsquo;indice, eseguito su queste fondamenta, ha
          processato <strong>181.870 atti</strong> (saltando 106.042 duplicati
          tra collezioni) e prodotto <strong>966.822 chunk</strong> —{" "}
          <strong>zero errori di parsing</strong> sull&rsquo;intero corpus.
        </p>
      </Section>

      <Section n={3} title="Il set di valutazione">
        <p>
          La qualità della ricerca non si giudica a sensazione: si misura su
          un set di domande con risposta attesa nota. Il set conta{" "}
          <strong>30 domande</strong>, ciascuna con il suo bersaglio —
          l&rsquo;articolo che una risposta corretta deve recuperare — in
          quattro categorie:
        </p>
        <ul className="my-3 list-disc space-y-2 pl-5 marker:text-muted-foreground">
          <li>
            <strong>esplicite</strong> (10): estremi normativi precisi, come
            «art. 2051 c.c.» o «articolo 186 del codice della strada»;
          </li>
          <li>
            <strong>naturali</strong> (12): linguaggio giuridico senza
            estremi, come «entro quanto tempo si prescrive la richiesta di
            risarcimento per un fatto illecito?» (bersaglio: art. 2947 c.c.) o
            «il datore di lavoro risponde dei danni causati a terzi dal
            proprio dipendente?» — il cui bersaglio, l&rsquo;art. 2049 c.c.,
            parla di «padroni e committenti» con lessico ottocentesco;
          </li>
          <li>
            <strong>cittadino</strong> (5): linguaggio comune, come «il cane
            del vicino mi ha morso, chi mi paga i danni?» (bersaglio: art.
            2052 c.c.);
          </li>
          <li>
            <strong>trappole</strong> (3): domande costruite per indurre
            l&rsquo;errore. «Provvedimenti d&rsquo;urgenza art. 770 c.p.c.»:
            l&rsquo;art. 770 esiste ma riguarda altro — i provvedimenti
            d&rsquo;urgenza sono all&rsquo;art. 700, e il sistema deve
            correggere il numero, non assecondarlo. «Ingiuria art. 594 codice
            penale»: l&rsquo;articolo è stato abrogato nel 2016 — la risposta
            corretta riferisce l&rsquo;abrogazione, non il testo previgente.
          </li>
        </ul>
      </Section>

      <Section n={4} title="Il benchmark degli embedding">
        <p>
          Due metriche, da capire prima della tabella.{" "}
          <em>Recall@k</em> è la percentuale di domande per cui il
          passaggio-bersaglio compare tra i primi k risultati della ricerca:
          recall@10 = 96,7% significa che in 29 domande su 30 il testo giusto
          era tra i primi dieci passaggi consegnati al modello. <em>MRR</em>{" "}
          (<em>Mean Reciprocal Rank</em>) è la media del reciproco della
          posizione del bersaglio: 1 se compare sempre primo, 0,5 se in media
          secondo, 0,1 se decimo — misura quindi <strong>quanto in alto</strong>{" "}
          arriva il risultato giusto, non solo se arriva.
        </p>
        <p>
          Condizioni di prova: ricerca ibrida (semantica + lessicale, fusione
          RRF) con k = 10, sulla collezione Codici — 40 atti, 18.463 chunk, la
          più interrogata e la più ostica strutturalmente — con le 30 domande
          della sezione 3. Quattro modelli di embedding candidati; le ultime
          quattro colonne riportano il recall@10 per categoria di domanda.
        </p>
        <PaperTable caption="Benchmark embedding (10 giugno 2026). Recall e MRR sull'intero set; le colonne per categoria sono recall@10. Report JSON integrali in backend/eval/results/.">
          <thead>
            <tr>
              <Th>Modello</Th>
              <Th numeric>recall@5</Th>
              <Th numeric>recall@10</Th>
              <Th numeric>MRR</Th>
              <Th numeric>esplicite</Th>
              <Th numeric>naturali</Th>
              <Th numeric>cittadino</Th>
              <Th numeric>trappole</Th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <Td>
                <strong>voyage-4-large</strong>
              </Td>
              <Td numeric>93,3%</Td>
              <Td numeric>
                <strong>96,7%</strong>
              </Td>
              <Td numeric>0,717</Td>
              <Td numeric>90%</Td>
              <Td numeric>100%</Td>
              <Td numeric>100%</Td>
              <Td numeric>100%</Td>
            </tr>
            <tr>
              <Td>voyage-4</Td>
              <Td numeric>83,3%</Td>
              <Td numeric>86,7%</Td>
              <Td numeric>0,611</Td>
              <Td numeric>60%</Td>
              <Td numeric>100%</Td>
              <Td numeric>100%</Td>
              <Td numeric>100%</Td>
            </tr>
            <tr>
              <Td>voyage-law-2</Td>
              <Td numeric>70,0%</Td>
              <Td numeric>76,7%</Td>
              <Td numeric>0,485</Td>
              <Td numeric>50%</Td>
              <Td numeric>91,7%</Td>
              <Td numeric>80%</Td>
              <Td numeric>100%</Td>
            </tr>
            <tr>
              <Td>bge-m3</Td>
              <Td numeric>n/d</Td>
              <Td numeric>n/d</Td>
              <Td numeric>n/d</Td>
              <Td numeric>—</Td>
              <Td numeric>—</Td>
              <Td numeric>—</Td>
              <Td numeric>—</Td>
            </tr>
          </tbody>
        </PaperTable>
        <p>
          Quattro osservazioni. Primo: voyage-law-2, il modello{" "}
          <em>specializzato in testi legali</em> della generazione precedente,
          perde nettamente dai modelli generalisti più recenti — la
          specializzazione dichiarata non sostituisce la misura. Secondo:
          bge-m3, il candidato auto-ospitato, non è stato valutato perché
          l&rsquo;inferenza si è rivelata inaffidabile sulla macchina di
          sviluppo (un Mac Intel): escluso per impraticabilità, non per
          demerito. Terzo: l&rsquo;intero divario tra i due voyage-4 è
          concentrato nelle domande con estremi espliciti — esattamente il
          caso che il fast path deterministico risolve senza ricerca
          semantica; sulle domande semantiche (naturali, cittadino, trappole)
          entrambi fanno il 100%. Quarto: la misura non è perfettamente
          deterministica — l&rsquo;indice vettoriale usa HNSW, una struttura
          di ricerca approssimata, e una domanda oscilla al confine del
          top-10 tra un&rsquo;esecuzione e l&rsquo;altra: il 96,7% di
          voyage-4-large è in realtà un intervallo 96,7–100%.
        </p>
        <p>
          Il cancello (recall@10 ≥ 85%) è stato superato da due modelli:{" "}
          <strong>go</strong>. In produzione l&rsquo;indice usa voyage-4-large.
        </p>
      </Section>

      <Section n={5} title="La decisione sul reranking">
        <p>
          Un <em>reranker</em> è un secondo modello che rilegge le coppie
          domanda–documento restituite dalla ricerca e le riordina: esamina i
          due testi insieme, quindi in teoria giudica la pertinenza meglio
          della sola vicinanza tra vettori — al prezzo di rileggere ogni
          coppia, una per una. Molte pipeline lo includono per default.
          Invece di presumere il beneficio, lo abbiamo misurato sulle stesse
          30 domande, sopra la ricerca ibrida:
        </p>
        <ul className="my-3 list-disc space-y-2 pl-5 marker:text-muted-foreground">
          <li>
            recall@10: da 96,7% a 96,7% — <strong>+0,0 punti</strong>;
          </li>
          <li>recall@5: da 93,3% a 83,3% — una regressione;</li>
          <li>MRR: da 0,734 a 0,748 — un guadagno marginale;</li>
          <li>
            latenza: da 0,53 a <strong>132,77 secondi per domanda</strong> su
            CPU — circa 250 volte più lento.
          </li>
        </ul>
        <p>
          Nessun guadagno dove conta, una regressione su recall@5 e un costo
          di latenza inaccettabile: il reranking è{" "}
          <strong>disattivato di default</strong> (resta dietro un interruttore
          di configurazione, rivalutabile su altro hardware). È il secondo
          episodio della stessa lezione del benchmark: misurare batte
          supporre.
        </p>
      </Section>

      <Section n={6} title="Validazione end-to-end">
        <p>
          Le metriche di ricerca non bastano: serve verificare che il sistema
          completo — recupero, generazione, citazioni — si comporti bene su
          conversazioni reali. La validazione manuale ha eseguito{" "}
          <strong>14 turni di conversazione</strong> sulla pipeline reale
          (incluse due conversazioni a più turni e una domanda fuori corpus),
          trascritti integralmente. Per ogni turno si è verificato: che il
          retrieval contenesse il bersaglio, che la risposta fosse ancorata ai
          passaggi, che i marker di citazione fossero ben formati e tutti
          riconducibili ai chunk recuperati, e che i rifiuti fossero corretti.
        </p>
        <p>
          Esito: <strong>14/14 superati</strong>. Zero marker malformati, zero
          marker «orfani» (citazioni di testi mai recuperati), zero estremi
          inventati. Le trappole sono state gestite: sull&rsquo;art. 594 c.p.
          il sistema ha riferito l&rsquo;abrogazione (d.lgs. 7/2016) senza
          ricostruire il testo previgente; su «art. 770 c.p.c.» ha corretto
          esplicitamente il numero in 700, segnalando l&rsquo;equivoco. Sulla
          domanda fuori corpus ha rifiutato di rispondere, citando solo il
          rinvio realmente presente nei passaggi e suggerendo come
          riformulare.
        </p>
        <p>
          La validazione ha anche documentato un limite, poi corretto: i
          follow-up conversazionali («e il comma successivo?») facevano
          ricerca sul testo letterale del messaggio, recuperando rumore — il
          sistema degradava con onestà, dichiarando il contesto insufficiente
          invece di inventare. La riscrittura della domanda con il contesto
          della conversazione, oggi parte della pipeline, è la risposta a quel
          limite. In produzione, il controllo che in validazione era manuale è
          automatico e permanente: il guardrail verifica ogni marker di ogni
          risposta contro i passaggi recuperati, in tempo reale.
        </p>
      </Section>

      <Section n={7} title="Costi e infrastruttura">
        <p>
          Ordine di grandezza, in trasparenza. L&rsquo;indicizzazione iniziale
          dell&rsquo;intero corpus — l&rsquo;embedding di ~524 milioni di token,
          di cui 200 milioni in fascia gratuita — è costata{" "}
          <strong>circa 39 dollari, una tantum</strong>, con voyage-4-large
          (le alternative stimate: ~19,5 $ con voyage-4, ~6,5 $ con
          voyage-4-lite). L&rsquo;esercizio gira su un singolo VPS europeo
          (ordine dei 30–50 € al mese) più i costi variabili delle API di
          generazione; il budget complessivo della fase beta è sotto i 150 €
          al mese. Non è un dettaglio contabile: costi bassi permettono di
          tenere il prodotto accessibile senza compromettere la qualità della
          pipeline.
        </p>
      </Section>

      <Section n={8} title="Limiti della validazione">
        <p>
          Le misure vanno lette per quello che sono. <strong>30 domande sono
          un campione</strong>: con questa numerosità un punto percentuale
          vale un terzo di domanda, e piccole differenze tra modelli non sono
          statisticamente significative — il set è destinato a crescere.{" "}
          <strong>Il benchmark copre la sola collezione Codici</strong> (40
          atti, 18.463 chunk), non l&rsquo;intero corpus indicizzato: è la
          collezione più interrogata e la più difficile strutturalmente, ma
          resta una parte del tutto. <strong>Il giudizio di ancoraggio è
          stato manuale</strong> sulle 14 conversazioni: una valutazione
          automatica e continua della qualità delle risposte (
          <em>LLM-as-judge</em>: un secondo modello che giudica le risposte
          del primo contro le fonti) è in roadmap, non in produzione. Infine,
          come già notato, l&rsquo;indice approssimato introduce una piccola
          variabilità tra esecuzioni, dichiarata accanto a ogni numero che ne
          è toccato.
        </p>
      </Section>

      <Section n={9} title="Riproducibilità">
        <p>
          Tutto ciò che questa pagina afferma è ricontrollabile. Il corpus è
          pubblico (il progetto italia-corpus, su GitHub), con l&rsquo;intera
          storia git; l&rsquo;analisi del corpus è rigenerabile con lo script
          che l&rsquo;ha prodotta e riporta il commit esatto su cui è stata
          eseguita. I report integrali dei benchmark — con il dettaglio per
          singola domanda — sono file JSON conservati nel repository del
          progetto (<code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[0.85em]">backend/eval/results/</code>
          ), e il set di valutazione è rieseguibile con un comando a ogni
          modifica della pipeline: i numeri di domani si confronteranno con
          quelli di oggi, sulla stessa bilancia. La trascrizione completa
          della validazione end-to-end, con tutte le risposte e i controlli,
          è anch&rsquo;essa nel repository.
        </p>
      </Section>

      <PaperFooterNav href="/come-funziona" label="Come funziona Legger" />
    </PaperArticle>
  );
}
