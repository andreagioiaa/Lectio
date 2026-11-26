=== SLIDE 1 ===

# Memoria Virtuale

Sistemi Operativi e Laboratorio

A. Formisano

Dipartimento di Scienze Matematiche, Informatiche e Fisiche

Anno Accademico 2025-26

## Contenuti

1Motivazioni

2Paginazione su Richiesta

3Rimpiazzamento delle Pagine

4Algoritmi di Rimpiazzamento delle Pagine

5Allocazione dei Frame

=== SLIDE 2 ===

# Motivazioni

- Caratteristica comune a tutte le tecniche di gestione della memoria viste finora: \textcolor{orange}{l’intero programma deve essere caricato in memoria per essere eseguito}
- In generale ciò non sarebbe necessario \textcolor{red}{Idea}: \textcolor{blue}{caricare in memoria solo la parte (ovvero le pagine) di programma che è, istante per istante, necessaria all’esecuzione}

\textcolor{red}{Conseguenze:}

- la dimensione dello spazio logico degli indirizzi è svincolata dalla dimensione dello spazio fisico
- lo spazio logico può essere maggiore dello spazio fisico
- aumenta il grado di multiprogrammazione (più processi mantenuti, \textcolor{blue}{parzialmente}, in memoria)

## Memoria Virtuale

Il meccanismo chiave è: \textcolor{blue}{swapping applicato alle singole pagine} invece che a interi processi

Memoria Virtuale Realizza la separazione (\textcolor{red}{virtualizzazione}) della memoria logica dalla memoria fisica:

## \textcolor{blue}{Memoria Virtuale =}

$^{Memoria fisica (RAM)}$\textcolor{blue}{+ Memoria secondaria (Disco)}

\textcolor{red}{Implementazione}:

Paginazione su richiesta (\textcolor{blue}{demand paging})

=== SLIDE 3 ===

# Paginazione su Richiesta

- Idea di base: una pagina viene caricata in memoria solo quando è necessaria per proseguire l’esecuzione
- Perchè funziona: si sfrutta il principio della \textcolor{blue}{località dei riferimenti}
- Vantaggi:
  - un processo occupa meno memoria, quindi si può mantenere “in esecuzione” un maggior numero di processi
  - meno operazioni di I/O quando è necessario caricare/scaricare un processo
- \textcolor{red}{Importante}: è necessario associare ad ogni pagina logica uno \textcolor{red}{stato}: \textcolor{blue}{in-memoria} o \textcolor{blue}{non-in-memoria}

## Schema di Funzionamento

![memoriavirtuale_imm1](memoriavirtuale-55306a/memoriavirtuale_imm1.png)

=== SLIDE 4 ===

# Tabella delle Pagine

In ogni entry della tabella delle pagine deve esserci un \textcolor{red}{bit di validità} che indica se la pagina è

- presente in memoria centrale (bit=1) o
- se si trova in memoria secondaria oppure l’accesso è illegale (bit=0).

Si hanno due diverse nozioni di \textcolor{red}{invalidità}: la pagina non è caricata in memoria centrale OPPURE la pagina non esiste, cioé “accesso a indirizzo non valido”

- inizialmente (all’avvio del processo) tutti i bit di validità sono 0
- se durante la traduzione degli indirizzi logici in indirizzi fisici si trova il bit di validità a 0, si ha un \textcolor{red}{page fault} (=pagina non caricata OPPURE indirizzo illegale)

## Esempio

![memoriavirtuale_imm2](memoriavirtuale-55306a/memoriavirtuale_imm2.png)

Riferimenti alle pagine 6 o 7 sono illegali.

Riferimenti alle pagine 3 o 4 generano page fault.

=== SLIDE 5 ===

# Gestione del Page Fault

Un page fault causa una interruzione. Il controllo passa al S.O.:

$^{1}$ il S.O. accede ad una tabella interna (associata al processo) per determinare la causa del page fault:

accesso illegale: abort (il processo viene terminato) accesso legittimo a pagina non in memoria: si deve attivare la procedura per caricarla

$^{2}$ cerca un frame vuoto (es., dalla tabella dei frame liberi) $^{3}$ trasferisce la pagina dal disco al frame $^{4}$ aggiorna le tabelle: la page table del processo, la tabella interna associata al processo, valid-bit,...

$^{5}$ \textcolor{blue}{si riavvia l’istruzione che ha generato il page fault}

Demand paging puro Si ha quando il primo accesso in memoria di un programma risulta essere sempre un page fault

## Gestione del Page Fault

Esempio in cui il S.O. trova un frame libero:

![memoriavirtuale_imm3](memoriavirtuale-55306a/memoriavirtuale_imm3.png)

=== SLIDE 6 ===

# Problema:

- Il passo di \textcolor{blue}{“riavvio dell’istruzione che ha generato il page fault”} potrebbe non essere banale
- Per esempio: pensate ad un page fault che occorra durante una istruzione di spostamento di un blocco di memoria, e supponiamo che il blocco origine e quello destinazione siano “overlapping”... Come procedere per il riavvio?
- Alcuni S.O. operano un \textcolor{blue}{ripristino del contenuto della memoria} prima di gestire il fault
- altri iniziano l’esecuzione di ogni istruzione \textcolor{blue}{solo se sono presenti tutte le pagine necessarie}

## Supporto alla Paginazione su Richiesta

Necessita di adeguato supporto hardware:

- tabella delle pagine (supporto alla paginazione)
- parte della memoria secondaria dedicata alle pagine non caricate (\textcolor{blue}{swap area})
- possibilità di riavviare le istruzioni (quando hanno generato fault)

=== SLIDE 7 ===

# Rimpiazzamento delle Pagine

\textcolor{red}{Cosa succede se non ci sono frame liberi?} Si deve attivare una procedura di \textcolor{red}{rimpiazzamento delle pagine}:

$^{1}$ ricercare di un frame in memoria, occupato da una pagina (detta pagina \textcolor{blue}{vittima}) $^{2}$ scambiare la pagina attualmente caricata con quella richiesta Osservazioni:

- solo se la pagina vittima è stata modificata si deve aggiornare la sua copia sul disco
- sarebbe meglio selezionare una vittima tra le pagine non più necessarie. . . Come?
- si deve evitare di scegliere una vittima che abbia alta probabilità di essere successivamente richiesta (evitare continui caricamenti e scaricamenti delle stesse pagine). . . Come?
- serve un algoritmo per scegliere le vittime, che massimizzi le performance (= minimizzi il numero di fault)

## Prestazioni della Paginazione su Richiesta

- La paginazione su richiesta influenza il tempo di accesso effettivo alla memoria (\textcolor{blue}{Effective Access Time}, EAT)
- Definendo il \textcolor{blue}{tasso di page fault} $p$ come la probabilità che si verifichi un page fault, abbiamo: EAT = (1 $-p)\times tm + p\times tg$ dove $tm$ è il tempo di accesso alla memoria e $tg$ è il tempo di gestione del page fault
- si può considerare $tg$ determinato da tre addendi:
  - tempo di servizio dell’interrupt
  - tempo di lettura della pagina (swap-in della singola pagina)
  - tempo di riavvio del processo

=== SLIDE 8 ===

# Un Esempio

Con questi valori tipici:

$$
• tm = 100 ns \\ • tg = 1 ms = 1000000 ns
$$

otteniamo EAT = $100 \times(1 + 9999\times p)ns$ Se volessimo un peggioramento al massimo del 10% dovremmo rispettare il vincolo

$$
110 > 100\times(1 + 9999\times p)
$$

ovvero mantenere $p < 10^{-4}$ Significa che possiamo permettere al più un page fault ogni $10^{4}$ accessi alla memoria principale Quindi: è importante scegliere bene le pagine vittime

## Il Rimpiazzamento delle Pagine

Abbiamo visto che:

- In assenza frame liberi, sono necessari due accessi alla memoria secondaria:
  - Uno per lo swap-out della pagina vittima
  - Uno per lo swap-in della pagina da caricare
- Ciò determina un doppio trasferimento: tempo di page fault doppio (rispetto a quanto servirebbe al processo che ha generato il fault, se ci fosse un frame libero)

Una ottimizzazione: usare un flag nella page table: \textcolor{blue}{bit di modifica} o \textcolor{blue}{dirty bit}: indica se sono state fatte delle modifiche alla pagina (oper. di scrittura). Quindi si evita di trasferire su disco le vittime col dirty bit a 0

=== SLIDE 9 ===

# Due Sottoproblemi da Risolvere

$^{1}$ \textcolor{blue}{Rimpiazzamento delle pagine}: se si deve rimpiazzare una pagina, come scegliere la vittima?

$^{2}$ \textcolor{blue}{Allocazione dei frame}: quanti frame assegnare ad ogni processo?

## Frequenza di page fault

Si osservi che la frequenza di page fault è in genere inversamente proporzionale al numero di frame a disposizione:

![memoriavirtuale_imm4](memoriavirtuale-55306a/memoriavirtuale_imm4.png)

=== SLIDE 10 ===

# Algoritmi di Rimpiazzamento delle Pagine

- L’obiettivo è ottenere il minor numero possibile di fage fault
- gli algoritmi di rimpiazzamento delle pagine possono essere confrontati paragonando il loro operato rispetto alle sequenze di accessi alla memoria
- alcuni di questi accessi genereranno page fault
- invece di considerare gli indirizzi di memoria, ragioniamo considerando le pagine di memoria che contengono tali indirizzi

ad esempio...

## Algoritmi di Rimpiazzamento delle Pagine

- ...invece di considerare gli indirizzi di memoria, ragioniamo considerando le pagine di memoria che contengono tali indirizzi
- se la sequenza di indirizzi fosse 1050 707611001040 2095221020583111 con una dimensione delle pagine da 1KB, allora le pagine accedute sarebbero: 161 12 22 3
- tuttavia accessi successivi alla stessa pagina generano al massimo un page fault; quindi considereremo la sequenza

=== SLIDE 11 ===

# Algoritmi di Rimpiazzamento delle Pagine

Passiamo in rassegna i vari algoritmi di rimpiazzamento delle pagine confrontandoli rispetto ad una \textcolor{cyan}{sequenza di riferimenti a pagine}

La sequenza che useremo è:

7 0 1 2 0 3 0 4 2 3 0 3 2 1 2 0 1 7 0 1

Inoltre assumeremo di avere a disposizione \textcolor{red}{3 frame}

I vari algoritmi si caratterizzano definendo il criterio di \textcolor{blue}{selezione della vittima}

## FIFO

\textcolor{blue}{Selezione}: la pagina presente da più tempo in memoria

- semplice da implementare
- è un algoritmo cieco: non considera l’effettivo utilizzo delle pagine
- tende a aumentare la frequenza di page fault
- soffre della \textcolor{magenta}{Anomalia di Belady}

Nel nostro esempio otteniamo \textcolor{cyan}{15 page fault}:

![memoriavirtuale_imm5](memoriavirtuale-55306a/memoriavirtuale_imm5.png)

=== SLIDE 12 ===

# Anomalia di Belady

Consideriamo l’algoritmo FIFO applicato a questa sequenza di riferimenti, utilizzando 3 frame:

![memoriavirtuale_imm6](memoriavirtuale-55306a/memoriavirtuale_imm6.png)

Si verificano 9 page fault. Nel tentativo di diminuire la frequenza di page fault, aumentiamo a 4 il numero di frame a disposizione. Ci aspetteremmo meno page fault. Invece:

![memoriavirtuale_imm7](memoriavirtuale-55306a/memoriavirtuale_imm7.png)

\textcolor{red}{Il numero di page fault può non decrescere aumentando il numero di frame}

## Algoritmo ottimale

\textcolor{blue}{Selezione}: la pagina che sarà usata più tardi nel futuro

- \textcolor{magenta}{garantisce il numero minimo di page fault}
- difficile da implementare
- bisogna conoscere in anticipo la sequenza di riferimenti
- è una pietra di paragone per gli altri algoritmi

Nel nostro esempio otteniamo \textcolor{cyan}{9 page fault}:

![memoriavirtuale_imm8](memoriavirtuale-55306a/memoriavirtuale_imm8.png)

=== SLIDE 13 ===

# LRU: Least Recently Used

\textcolor{blue}{Selezione}: la pagina che non è usata da più tempo

  - è un modo per approssimare l’algoritmo ottimo sfruttando il passato per prevedere il futuro
  - difficile da implementare
    - tenere traccia dei tempi degli accessi alle pagine
    - serve supporto hardware

Nel nostro esempio otteniamo \textcolor{cyan}{12 page fault}:

![memoriavirtuale_imm9](memoriavirtuale-55306a/memoriavirtuale_imm9.png)

## Implementazioni di LRU

Come trovare la pagina usata meno di recente?

\textcolor{cyan}{tramite un contatore}:

  - un contatore associato ad ogni pagina
  - ad ogni referenza un timestamp viene scritto nel contatore
  - si deve cercare la pagina col timestamp più vecchio

\textcolor{cyan}{tramite uno stack}:

![memoriavirtuale_imm10](memoriavirtuale-55306a/memoriavirtuale_imm10.png)

- ad ogni riferimento si inserisce il numero di pagina sullo stack
- e si elimina l’eventuale altra occorrenza nello stack
- selezione: la pagina in fondo allo stack
- deve essere fatto a livello HW

=== SLIDE 14 ===

# Approssimazioni di LRU

\textcolor{cyan}{uso del reference bit}:

- un bit associato ad ogni pagina, inizialmente 0
- ad ogni referenza si pone a 1 (via HW)
- si seleziona una delle pagine con bit 0

\textcolor{cyan}{più reference bit}:

- si usano K bit per pagina (ad es. uno shift register)
- a intervalli regolari i reference bit delle pagine vengono salvati (si fa scorrere i bit contenuti nei registri)
- K bit interpretati come un valore intero
- seleziona la pagina col valore inferiore

\textcolor{cyan}{NUR — Not Used Recently}:

- si usano reference bit R e dirty bit M
- ad ogni referenza si pone R a 1 e ad ogni modifica si pone M a 1
- periodicamente si riazzerano tutti i reference bit
- si considera MR come un numero binario $C\in{0,1,2,3}$ e seleziona una delle pagina col valore $C$ inferiore

## Tecniche basate su conteggio

\textcolor{cyan}{LFU — Least Frequently Used}:

- ad ogni riferimento si incrementa un contatore dei riferimenti fatti alla pagina
- si seleziona una delle pagina col contatore inferiore (controindicazione: le pagine che presentano solo molti riferimenti iniziali (es. dati input, inizializzazioni) non verranno usate ma hanno contatore elevato)

\textcolor{cyan}{MFU — Most Frequently Used}:

- criterio di selezione opposto a LFU
- motivato dal fatto che una pagina appena caricata ha un contatore basso, ma si presume venga riferita nei prossimi accessi (per il principio di località)

=== SLIDE 15 ===

# Algoritmo di seconda chance

- L’algoritmo di \textcolor{cyan}{seconda chance} è una variante di FIFO in cui si tiene conto del reference bit
- l’intento è evitare di selezionare una pagina riferita anche se “vecchia”
- si considerano le pagine in una coda circolare, ordinate in base al tempo di caricamento (come FIFO)
- quando una pagina è riferita si pone il suo reference bit a 1
- quando serve selezionare una pagina vittima si parte dalla più vecchia (la vittima che sarebbe scelta da FIFO) e si controlla il reference bit:
  - se è 0 si seleziona la pagina
  - se è 1 gli si concede una seconda chance; si azzera il bit e si passa alla successiva pagina nella coda circolare (intuitivamente è come se la pagina che è stata “risparmiata” diventasse la più giovane)

## Algoritmo di seconda chance

![memoriavirtuale_imm11](memoriavirtuale-55306a/memoriavirtuale_imm11.png)

=== SLIDE 16 ===

# Allocazione dei Frame

Supponiamo di voler caricare (parzialmente) in memoria $N$ processi e di poter utilizzare $M$ frame

  - dobbiamo decidere quanti frame assegnare ad ogni processo
  - ci potrebbero essere dei vincoli sul numero minimo di frame
  - l’istruzione che ha causato il page fault deve essere riavviata, quindi tutte le pagine che usa devono essere presenti
  - il numero di frame deve essere sufficiente ad eseguire l’istruzione che prevede il maggior numero di riferimenti possibile
    - per esempio, se il linguaggio macchina prevede indirizzamento indiretto allora servono almeno tre pagine/frame: una contiene l’istruzione, una il dato riferito, ma questo è a sua volta un riferimento a un dato che può essere in una terza pagina
    - una altro esempio: consideriamo una istruzione che copia/sposta da memoria a memoria: l’istruzione potrebbe essere a cavallo di due pagine, sia sorgente che destinazione potrebbero essere indirizzamenti indiretti. Quindi in questo caso servono 6 frame

## Schemi di allocazione

Supponendo di dover allocare $M$ frame suddividendoli tra $N$ processi ecco due semplici strategie di allocazione:

\textcolor{cyan}{allocazione uniforme}:

- a ciascun processo si concedono $M/N$ frame

\textcolor{cyan}{allocazione proporzionale}:

- si considera la dimensione $D_{i}$ di ogni processo $P_{i}$
- al processo $P_{i}$ si concede una frazione del numero di frame proporzionale alla sua dimensione: $M\ast D_{i}/\Sigma_{j}D_{j}$
- si possono usare anche altri criteri, come la priorità

=== SLIDE 17 ===

# Contesto di rimpiazzamento

Supponiamo che un processo $P$ generi un page fault e sia necessario selezionare una pagina vittima

\textcolor{cyan}{contesto locale}:

- la pagina vittima viene scelta tra le pagine di $P$

\textcolor{cyan}{contesto globale}:

- la pagina vittima viene scelta tra le pagine di tutti i processi

## Thrashing

  - Consideriamo un sistema in cui il numero di frame allocati ai processi è troppo basso
  - il numero di page fault tenderà ad aumentare
  - dato che i frame sono pochi, il sistema tenderà a selezionare pagine vittime che molto probabilmente verranno riferite nei successivi accessi
  - i processi spenderanno molto tempo nell’attesa della gestione dei page fault
  - quindi molti processi in stato di waiting e pochi in stato di ready
  - globalmente la CPU tenderà ad essere sottoutilizzata
  - il S.O. per rimediare al sottoutilizzo della CPU aumenta il grado di multiprogrammazione
  - ciò peggiora la situazione: più processi, più page fault

In tale situazione le performance di tutto il sistema crollano. Tale fenomeno si chiama \textcolor{red}{thrashing}

=== SLIDE 18 ===

# Thrashing

![memoriavirtuale_imm12](memoriavirtuale-55306a/memoriavirtuale_imm12.png)

## Località dei riferimenti

- Si osservi che, nella situazione descritta prima, per evitare il thrashing sarebbe stato necessario diminuire il grado di multiprogrammazione. Per esempio usando lo swapping al fine di assicurare i frame necessari ai processi presenti in memoria
- ma, quanti sono i frame necessari a un processo?
- si può rispondere considerando un \textcolor{cyan}{modello di località} dell’esecuzione del processo
  - LOCALITÀ TEMPORALE: locazioni riferite recentemente hanno buona probabilità di essere riferite nuovamente (a causa del modo in cui è scritto il codice: loop, funzioni, uso di stack, contatori,...)
  - LOCALITÀ SPAZIALE: se si è riferita una locazione è probabile che si riferiranno anche le locazioni ad essa vicine (a causa del modo in cui sono strutturati codice e dati: istruzioni sequenziali, array,...)

=== SLIDE 19 ===

![memoriavirtuale_imm13](memoriavirtuale-55306a/memoriavirtuale_imm13.png)

## Modello del working set

- La tecnica del \textcolor{blue}{working set} mira a determinare la dimensione della località attuale del processo
- Per ogni dato processo $P_{i}$ si definisce una \textcolor{cyan}{finestra di working set} considerando gli ultimi ∆riferimenti che $P_{i}$ ha effettuato
- si definisce il \textcolor{red}{working set} $WSi$ come l’insieme delle pagine riferite negli ultimi ∆ accessi
- la dimensione del $WS_{i}$, il \textcolor{red}{working set size} $WSS_{i}$, stima la dimensione della località attuale, ovvero il numero di frame necessari a $P_{i}$

Se il S.O. rileva che il numero di frame a disposizione è inferiore alla somma delle working set size $\Sigma_{i}WSS_{i}$, allora si deve diminuire il grado di multiprogrammazione

=== SLIDE 20 ===

# Modello del working set

Un esempio: consideriamo la sequenza di accessi:

![memoriavirtuale_imm14](memoriavirtuale-55306a/memoriavirtuale_imm14.png)

con ∆= 10, allora avremo

- all’istante $t_{1}$ un $WS(t_{1}) = {1,2,5,6,7}$ e quindi WSS$(t_{1}) = 5$
- all’istante $t_{2}$ un $WS(t_{2}) = {3,4}$ e quindi WSS$(t_{2}) = 2$

## Modello del working set

  - La scelta di ∆è importante
    - se ∆è troppo piccolo allora non si cattura interamente la località attuale del processo (e si avranno più page fault)
    - se ∆è troppo grande allora si tende a unire località diverse concedendo al processo più frame del necessario (al limite, aumentando ∆si cattura tutto lo spazio di indirizzamento del processo)
  - La valutazione al runtime dei $WS$ e delle WSS può comportare inefficienza
  - un modo per approssimare il $WS$ prevede di usare i reference bit:
    - a intervalli regolari si salvano i reference bit delle pagine e si azzerano
    - le pagine che hanno almeno un bit a 1 tra quelli salvati negli ultimi $k$ intervalli compongono una approssimazione del working set

=== SLIDE 21 ===

# Page fault frequency

Una tecnica alternativa è basata sul monitoraggio della \textcolor{red}{frequenza di page fault}

- si fissa una soglia minima alla frequenza di page fault: un numero di page fault inferiore alla soglia indica che si può sottrarre frame al processo
- si fissa una soglia massima alla frequenza di page fault: un numero di page fault superiore alla soglia indica che il processo è in sofferenza e si deve concedere più frame
- una frequenza di page fault compresa tra le due soglie risulta accettabile

![memoriavirtuale_imm15](memoriavirtuale-55306a/memoriavirtuale_imm15.png)

=== SLIDE 22 ===

# Compressione della memoria

- Abbiamo visto che vi sono situazioni in cui non è proponibile effettuare né swapping di processi né rimpiazzamento delle pagine
- ciò è il caso dei S.O. per dispositivi mobili come Android e IOS (presenza di SSD, assenza di HD)
- in tali situazioni si può sostituire la paginazione virtuale con la \textcolor{cyan}{compressione della memoria} (o quantomeno attivare paginazione solo se la compressione è insufficiente)

## Compressione della memoria

- quando è necessario liberare della memoria si selezionano $k$ frame (che contengono pagine modificate) e si salvano in formato compresso in uno dei frame liberi che viene posto nella lista dei frame compressi
- così facendo si liberando $k- 1$ frame
- se uno dei frame compressi viene riferito allora si attua la decompressione
- l’efficienza della tecnica dipende dalla efficienza degli algoritmi di compressione (bilanciamento tra: rapporto di compressione e velocità di esecuzione)

=== SLIDE 23 ===

Esempio di compressione

![memoriavirtuale_imm16](memoriavirtuale-55306a/memoriavirtuale_imm16.png)

![memoriavirtuale_imm17](memoriavirtuale-55306a/memoriavirtuale_imm17.png)
