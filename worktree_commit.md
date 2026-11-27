# Commit, PR e nuove versioni di Lectio

Istruzioni per Claude (e per chiunque faccia un commit o apra una PR). Leggile
**prima di ogni commit o pull request**: decidono se la modifica pubblica una
nuova versione di Lectio.

## Come nasce una release

La versione si scrive in un solo posto: `study_manager/config.py`.

```python
APP_VERSION = "0.1.5"            # versione di questa build
MIN_SUPPORTED_VERSION = "0.1.5"  # le versioni piu' vecchie devono aggiornare
```

Non serve creare tag o release a mano. Al merge in `main`, il workflow
`.github/workflows/release.yml` controlla se il tag `v<APP_VERSION>` esiste
gia':

- **se non esiste**, compila `Lectio.exe`, genera `update.json` e pubblica la
  Release `v<APP_VERSION>` (con il tag sul commit di merge);
- **se esiste**, non pubblica niente: la modifica arrivera' agli utenti con la
  prossima versione.

Quindi alzare `APP_VERSION` in una PR basta per far uscire la nuova versione.
Lasciarla com'e' basta per non farla uscire. Gli utenti la ricevono all'avvio
successivo di Lectio (`study_manager/services/updater.py`).

## 1. Decidi se questa modifica e' una nuova versione

Siamo in beta: le versioni sono `0.MINOR.PATCH`.

| Modifica | Versione |
|---|---|
| Solo correzioni di bug, testi o piccoli ritocchi | `PATCH` +1 (0.1.5 → 0.1.6) |
| Nuove funzioni o cambi visibili | `MINOR` +1, `PATCH` a 0 (0.1.6 → 0.2.0) |
| Solo documentazione, test, CI o refactor senza effetti per l'utente | nessuna nuova versione |

Se l'utente ha detto di non pubblicare ("non fare release", "lo pubblico dopo"),
non toccare `APP_VERSION` anche se le modifiche lo meriterebbero. Se non e'
chiaro, chiedi prima del commit. Una PR alza la versione **una volta sola**,
anche se contiene piu' commit.

## 2. Se c'e' una nuova versione

1. Alza `APP_VERSION` in `study_manager/config.py`. Deve essere piu' alta
   dell'ultima release pubblicata (`git tag --list "v*" --sort=-v:refname`).
2. Decidi su `MIN_SUPPORTED_VERSION` (vedi sotto). Di norma **resta com'e'**.
3. Riscrivi `RELEASE_NOTES.md` con le novita' di questa versione: e' il testo
   che gli utenti leggono nella finestra di aggiornamento. Scrivi in italiano e
   per chi usa l'app (cosa cambia per lui), in pochi punti. Lascia il commento
   iniziale.
4. Rigenera i metadati dell'exe e controlla:
   ```
   python tools/release.py version-info
   python tools/release.py info
   python -m unittest discover -s tests
   ```
   Fai il commit anche di `version_info.txt`. Non va modificato a mano.
5. Messaggio di commit: la prima riga inizia con `Lectio <versione>:`, ad
   esempio `Lectio 0.1.6: correzioni al riassunto del corso`.
6. PR: il titolo inizia con `Release v<versione>:`. Nella descrizione metti una
   sezione **Versione** che dica:
   - `Al merge viene pubblicata la v0.1.6`;
   - se `MIN_SUPPORTED_VERSION` e' cambiata, quale valore ha e **perche'**
     l'aggiornamento diventa obbligatorio.

## 3. Se non c'e' una nuova versione

- Non toccare `APP_VERSION`, `MIN_SUPPORTED_VERSION`, `RELEASE_NOTES.md` e
  `version_info.txt`.
- Nella descrizione della PR scrivi nella sezione **Versione**:
  `Nessuna nuova versione: le modifiche usciranno con la prossima release`.

## Aggiornamento obbligatorio (`MIN_SUPPORTED_VERSION`)

Chi ha una versione **piu' bassa** di `MIN_SUPPORTED_VERSION` non puo' piu' usare
Lectio finche' non aggiorna (finestra bloccante con "Aggiorna ora" o "Chiudi
Lectio"). Alzala solo se una versione vecchia farebbe danni, ad esempio:

- cambia il formato della **cartella sincronizzata** tra piu' PC, e un Lectio
  vecchio sovrascriverebbe o romperebbe i dati di uno nuovo;
- cambia il formato dei file di **condivisione** delle materie in modo
  incompatibile;
- una versione vecchia ha un bug che corrompe o perde dati.

Una modifica allo schema del database **da sola non basta**: ogni copia migra il
proprio database all'avvio. Di solito si alza al valore della nuova versione
(es. `APP_VERSION = "0.2.0"`, `MIN_SUPPORTED_VERSION = "0.2.0"`) e mai sopra
`APP_VERSION` (la CI fallisce). Non alzarla mai senza dirlo all'utente.

## Da non fare

- Non creare tag o release a mano e non pubblicare dal proprio PC: lo fa la CI.
- Non abbassare `APP_VERSION` sotto l'ultima release: chi ha la versione piu'
  alta non riceverebbe piu' aggiornamenti.
- Non segnare le release come "pre-release": `releases/latest/download/update.json`
  le ignorerebbe e nessuno riceverebbe l'aggiornamento.
- Non rinominare `Lectio.exe` o `update.json` nella release: l'app li cerca con
  questi nomi.

## Dopo il merge

Controlla in **Actions → Build e Release** che il run su `main` sia verde e che la
Release `v<versione>` contenga sia `Lectio.exe` sia `update.json`.
