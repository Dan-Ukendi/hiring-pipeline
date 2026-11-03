# jobpipe

Pipeline local de candidature à des postes juniors IA, GenAI, ML et data engineering en Région de Bruxelles-Capitale.

| Phase | Rôle | Commandes principales |
|---|---|---|
| 1 | Découverte, extraction, filtres, audit | `jobpipe run`, `jobpipe report`, `jobpipe probe` |
| 2 | Analyse de la page de candidature | `jobpipe apply-plan probe|build|archive|report` |
| 3 | CV et lettre adaptés, réponses aux questions | `jobpipe generate`, `jobpipe review list|approve|reject` |
| 4 | File d'envoi, courriel, preuves, dossiers | `jobpipe submit enqueue|run|confirm|approve|status|stop|doctor`, `jobpipe recall`, `jobpipe export` |
| Suivi | Rapport consolidé, relances, notification, entretien | `jobpipe dashboard|followups|notify|backup|purge|evaluate`, `jobpipe chain nightly|day` |

Documentation de conception : `docs/plans/`. Interfaces partagées : `src/jobpipe/contract/`. Rapports de construction et ce qu'il reste à faire : `docs/reports/`.

## Règles qui ne se négocient pas

- Le LLM ne produit jamais de faits : aucune sortie de modèle n'atteint une table factuelle autrement que par un modèle Pydantic validé.
- Aucun appel à un point de terminaison qui exige une clé d'employeur ou de partenaire. Aucun contournement de CAPTCHA ou de bouclier.
- Un envoi réel exige trois verrous : `--send`, `send.enabled: true` et `JOBPIPE_SEND=1`. Tout le reste est une simulation.
- Les tests passent sans réseau et sans Ollama : garde de sockets de session (boucle locale autorisée), `JOBPIPE_OFFLINE=1`, cassettes pour les appels de modèle.

## Installation

```
python -m venv .venv
pip install -e ".[dev,browser]"
```

LibreOffice est requis pour la phase 3 (Windows : `winget install TheDocumentFoundation.LibreOffice`).

Configuration : `config/*.yaml`, règles de la phase 2 dans `config/apply/*.toml`, profil dans `profile/` (copier les fichiers `.example.yaml`). Secrets (SMTP, IMAP, Telegram) dans un fichier hors du dépôt, en permissions 600 : `~/.config/jobpipe/secrets.toml` ou `JOBPIPE_SECRETS_FILE`.
