# À faire par le propriétaire

Rien ici n'est faisable par le pipeline seul. Aucun envoi réel n'a été fait et aucun code ne peut en faire un sans les trois verrous ci-dessous.

## Avant toute utilisation réelle

1. **`profile/profile.yaml` et `profile/declarations.yaml`** : copiez les fichiers `.example.yaml` et remplacez tout. 60 à 90 puces en français et en anglais, formulation de votre cursus d'ingénieur civil interrompu (`edu.*.phrasings`), texte de `claim.degree_context` (marqué `requires_human_review`), titres de CV, `never_claim`, fourchette salariale, préavis. Les exemples sont entièrement fictifs. Ces deux fichiers sont ignorés par git.
2. **Secrets** : un fichier hors dépôt, en permissions 600, `~/.config/jobpipe/secrets.toml` (ou `JOBPIPE_SECRETS_FILE`) :

   ```
   [smtp]
   host = "..."
   port = 587
   username = "..."
   password = "..."
   sender = "vous@votre-domaine"
   [imap]
   host = "..."
   username = "..."
   password = "..."
   [telegram]
   bot_token = "..."
   chat_id = "..."
   ```
3. **LibreOffice** : `winget install TheDocumentFoundation.LibreOffice` (la chaîne de rendu de la phase 3 n'a pas de repli ; `jobpipe generate` s'arrête avec ce message s'il est absent).
4. **Courriel** : `jobpipe submit doctor` vérifie SPF, DMARC et DKIM de votre domaine d'envoi. Le sous-adressage `+` doit être accepté par votre fournisseur : envoyez-vous un message à `vous+test@domaine`. Un relais non aligné fait atterrir les candidatures en indésirables sans aucun signal.
5. **Conditions d'utilisation** : `config/apply/ats_policy.toml` démarre à `unknown` partout (non bloquant, ligne explicite de la table de décision). À renseigner après lecture des conditions de chaque éditeur.

## Les chiffres que je n'ai pas pu produire (pas de réseau, pas de base)

```
jobpipe apply-plan build --limit 100 --no-audit
```

Envoyez-moi la ligne `auto_full + auto_assisted: N of M (x%)` et la répartition des `feasibility_reason_code` du rapport `data/reports/apply-plan-*.html`. Sous 10 %, la route courriel et la préparation de dossiers sont l'essentiel de ce que la phase 4 fera (c'est déjà la décision pour 4c, voir `etape-4.md`).

Ensuite, avec votre vrai profil :

```
jobpipe generate --no-llm --limit 5      # CV déterministe, à relire
jobpipe evaluate --phase documents --cases eval/documents.jsonl
```

C'est cette évaluation qui dit si l'étage A (option `--llm-plan`) bat le pré-score : sinon, il est à retirer.

## Le premier envoi réel : vous seul

1. `jobpipe submit enqueue` puis `jobpipe submit run` : simulation, rapport HTML `planned-*.html` à relire.
2. Vers une adresse de contrôle d'abord (une seconde boîte à vous) : créez une offre de test dont `apply_url` est `mailto:cette-adresse`.
3. Les trois verrous : `--send`, `send.enabled: true` dans `config/settings.yaml`, et `JOBPIPE_SEND=1` dans l'environnement.
4. `jobpipe submit confirm` puis `jobpipe recall "<employeur>"` : le dossier doit tout reconstituer.

Arrêt d'urgence : `jobpipe submit stop` (crée `data/STOP`).

## Planification

Deux tâches distinctes : la nuit `jobpipe chain nightly`, en journée (8 h à 19 h, en semaine) `jobpipe chain day`. Une file du matin peut contenir des plans de plusieurs nuits : la fraîcheur est vérifiée (`plan_stale`, `jobpipe apply-plan build --refresh`).

## Fixtures de pages réelles

`jobpipe apply-plan archive URL --out tests/fixtures/pages/<cas>` pour constituer un corpus réel.
