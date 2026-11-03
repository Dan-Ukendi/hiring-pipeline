# À faire par le propriétaire

Liste tenue à jour au fil des étapes. Rien ici n'est faisable par le pipeline seul.

## Avant toute utilisation réelle

1. `profile.yaml` et `declarations.yaml` (phase 3) : 60 à 90 puces en français et en anglais, formulation du cursus d'ingénieur civil interrompu, `claim.degree_context`. Un fichier d'exemple factice sera fourni.
2. Fichier de secrets hors dépôt (SMTP, IMAP, Telegram), en permissions 600. Chemin par défaut `~/.config/jobpipe/secrets.toml`, ou variable `JOBPIPE_SECRETS_FILE`.
3. LibreOffice : `winget install TheDocumentFoundation.LibreOffice` (la chaîne de rendu de la phase 3 n'a pas de repli).
4. Premier envoi réel : à faire par vous seul. `JOBPIPE_SEND` reste à 0 tant que vous ne le changez pas.
5. Sous-adressage `+` et alignement SPF/DKIM/DMARC de l'adresse d'envoi (commande de diagnostic fournie à l'étape 4b).

## Phase 2 : le chiffre qui décide de la suite

Sur votre machine, avec la base réelle (le run de phase 1 doit avoir été fait) :

```
jobpipe apply-plan build --limit 100 --no-audit
```

La commande écrit un rapport dans `data/reports/apply-plan-*.html` et affiche `auto_full + auto_assisted: N of M (x%)`. Envoyez-moi cette ligne et la répartition par `feasibility_reason_code` : sous 10 %, la phase 4 se limite au courriel et aux dossiers.

Pour constituer des fixtures réelles : `jobpipe apply-plan archive URL --out tests/fixtures/pages/<cas>`.
Le fichier `config/apply/ats_policy.toml` démarre à `unknown` partout (non bloquant) ; à renseigner après lecture des conditions de chaque éditeur.
