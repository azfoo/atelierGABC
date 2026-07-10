# Polices libres embarquées

Ce dossier contient les polices **libres (licence OFL)** livrées avec
l'application, pour que le rendu soit identique sur toutes les machines sans
qu'aucune police système ne soit requise. Elles remplacent les polices
propriétaires de l'ancien `defaults/configuration.tex` (Adobe Garamond Pro,
Garamond Premier Pro) — **non redistribuables**, donc jamais livrées ; elles
restent utilisables si une copie sous licence est installée sur la machine (le
contrôle de polices au chargement le vérifie).

## Jeu embarqué

| Fichiers | Famille | Rôle | Remplace | Licence |
|---|---|---|---|---|
| `EBGaramond-{Regular,Italic,Bold,BoldItalic}.otf` | **EB Garamond** | corps de texte — **police par défaut** (chiffres bas de casse, italique) | Adobe Garamond Pro | `OFL-EBGaramond.txt` |
| `Cormorant-{Regular,Italic,Medium,Bold}.otf` | **Cormorant** | initiales et grands corps (rouge liturgique) — disponible mais pas encore câblée à un style par défaut ; naturelle pour le style `initial` | Garamond Premier Pro | `OFL-Cormorant.txt` |

Toutes sous SIL Open Font License 1.1 (le fichier `OFL-*.txt` correspondant est
livré à côté). **`EB Garamond` est la valeur par défaut de `text_font`** (voir
`project.DEFAULT_TEXT_FONT` / `DEFAULT_TYPOGRAPHY`) ; un `text_font` vide
retombe sur cette même police à l'export, jamais sur le Latin Modern du moteur.

## Glyphes liturgiques ℟ ℣ † — fournis par GregorioTeX, pas par une police ici

Aucune police n'est embarquée pour les signes liturgiques : **GregorioTeX les
fournit lui-même**. Sa propre police `greextra` porte le dagger, la croix, les
R/V gothiques et les étoiles (`\GreDagger`, `\grecross`, `\Rbar`, `\Vbar`,
`\Abar`, `\GreStar`) ; dans le gabc on tape les raccourcis `+` `*` `R/` `V/`
`A/`. Les R/V barrés simples sont dessinés par-dessus la police de texte
courante (`\setmainfont`). Ces macros sont disponibles dans tout le document
(paquet chargé globalement), donc aussi utilisables en texte hors chant. Les
**paroles** gabc, elles, adoptent la police principale via fontspec. (Source :
`gregoriotex-symbols.tex`.)

## Comment c'est câblé

`exporter._font_env` (appelé par `_compile_env` et par tous les points qui
résolvent une police) ajoute ce dossier aux DEUX moteurs **dès qu'il contient au
moins un fichier de police** (sinon no-op) :

- `OSFONTDIR` pour luaotfload (côté TeX, indépendant de l'OS) ;
- un `fonts.conf` généré (`_fontconfig_file`, via `FONTCONFIG_FILE`) qui liste
  ce dossier + les dossiers de polices système (par OS) + ceux de LilyPond,
  pour le côté LilyPond/Pango.

État des vérifications : les deux familles se résolvent par nom dans les deux
moteurs **sous Linux** (compile fontspec réussie côté TeX ;
`lilypond -dshow-available-fonts` les liste côté Pango). Les plateformes
**macOS et Windows** ne sont pas encore couvertes par une exécution : le côté
LilyPond/Pango sous Windows (pas de `/etc/fonts`) est traité par le code mais
attend la CI (`.github/workflows/ci.yml`, qui ne tourne qu'après un push).

Pour ajouter des graisses/familles, déposer les fichiers ici (`.otf`, `.ttf` ou
`.ttc`) — aucune autre étape. Chaque police doit rester accompagnée de sa
licence OFL (`OFL-*.txt`) à la distribution.
