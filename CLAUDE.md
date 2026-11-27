# Lectio

App desktop per Windows (Python 3.12 + CustomTkinter + SQLite), distribuita
come un solo `Lectio.exe` creato con PyInstaller. La documentazione completa è
in [README.md](README.md).

## Prima di ogni commit o pull request

Leggi e segui [worktree_commit.md](worktree_commit.md). Spiega:

- quando una modifica è una nuova versione e come alzare `APP_VERSION`
  (ed eventualmente `MIN_SUPPORTED_VERSION`) in `study_manager/config.py`;
- come aggiornare `RELEASE_NOTES.md` e `version_info.txt`;
- come scrivere il messaggio di commit e la descrizione della PR.

Al merge in `main` di una nuova `APP_VERSION`, la CI pubblica la release da sola:
non creare tag o release a mano.

## Comandi utili

- Test: `python -m unittest discover -s tests`
- Versione e controllo del tag: `python tools/release.py info`
- Build locale dell'exe: `.\build.ps1`
