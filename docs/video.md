# Phase 4 : la production vidéo

À partir d'un brouillon, l'agent produit un Reel vertical (1080 × 1920, format 9:16) : voix off, images, sous-titres, montage. **Rien n'est publié** : la vidéo est créée dans `output/reel-N/`.

```
Brouillon ─► Voix off ─► Choix des images ─► Téléchargement ─► Montage ─► reel-N.mp4
(texte validé)  (Edge TTS)   (IA, anglais)      (NASA)         (FFmpeg)   + credits.txt
```

## Installation sur Termux (une seule fois)

```
pkg install ffmpeg -y
pip install edge-tts
python -m cosmos_ai check        # doit afficher « FFmpeg installé ; edge-tts installé »
```

Internet est nécessaire pour la voix (Edge TTS) et pour les images (NASA).

## Utilisation

```
python -m cosmos_ai run          # produit un brouillon (étape 3)
python -m cosmos_ai video 3      # produit la vidéo du brouillon n° 3
python -m cosmos_ai video 3 --sans-images    # essai rapide : fond sombre généré
```

Résultat dans `output/reel-3/` : `reel-3.mp4` (la vidéo), `voix.mp3`, `images/` et `clips/` (médias téléchargés) et `credits.txt` (crédit de chaque image et sources d'information). Le dossier `output/` est ignoré par Git.

Le montage est long sur un téléphone (plusieurs minutes pour un Reel de 60 s) : laisse l'écran allumé, un pourcentage s'affiche. Pour l'alléger : `video.max_clips: 0`, moins d'images (`video.max_images`), `crf: 28`, ou `width: 720` / `height: 1280` / `fps: 20`.

## Obtenir la clé Pexels (gratuite) pour des vidéos plus « cinéma »

La NASA publie surtout de vraies images, parfois ternes. Pexels propose des centaines de plans spatiaux très propres (nébuleuses, planètes, approches de galaxies, souvent en images de synthèse), libres de droits, et même des **vidéos verticales** qui évitent de recadrer.

1. Va sur https://www.pexels.com/api/ , crée un compte gratuit et demande une clé (immédiat).
2. Ajoute-la dans Termux comme tes autres clés : `nano ~/.bashrc`, ajoute la ligne `export PEXELS_API_KEY="ta-clé"`, enregistre (Ctrl+O, Entrée, Ctrl+X), puis `source ~/.bashrc`. Ne la partage jamais dans une conversation ni dans GitHub.
3. `python -m cosmos_ai check` doit afficher « Pexels : clé présente ».

Sans clé, Pexels est ignoré et seules les vidéos de la NASA sont utilisées. L'ordre et le choix des sources se règlent avec `video.clip_sources: [pexels, nasa]` : les deux sont utilisées **à tour de rôle**.

**Honnêteté éditoriale** : les vidéos de synthèse sont des illustrations, pas des photographies de l'objet. Le programme le signale dans les notes et dans `credits.txt`. Ne les présente pas comme des images réelles, surtout quand tu parles d'un objet précis (un trou noir n'a jamais été filmé en gros plan). Côté NASA, les simulations et animations sont repérées par leur titre et ne passent qu'après les vraies images.

## Ce que fait chaque étape

- **Voix off** : Edge TTS (voix neuronales françaises gratuites). La voix se change dans `config.yaml` (`video.voice`, par exemple `fr-FR-DeniseNeural`), la vitesse avec `video.rate` (`-5%`). Le service n'est pas officiel : s'il change ou disparaît, le fournisseur de voix est remplaçable (interface `TTSProvider`).
- **Images et courtes vidéos** : l'IA propose 3 recherches en anglais d'après le script. Les images viennent de la bibliothèque de la NASA ; les courtes vidéos (`video.max_clips`, 3 par défaut, 0 pour n'avoir que des images) viennent de Pexels et de la NASA à tour de rôle. Les plans alternent : image, vidéo, image, vidéo…
- **Qualité et forfait Internet** : `video.clip_quality: haute` télécharge jusqu'à 1080p (4K si la NASA la propose), `standard` 720p, plus léger. `video.max_clip_mb` (80 par défaut) refuse tout fichier plus gros, **avant** de le télécharger. Compte jusqu'à 100 à 250 Mo par Reel en qualité haute : fais-le plutôt en Wi-Fi.
- **Fluidité** : 30 images par seconde (`video.fps`). 60 est très fluide mais rend le montage deux fois plus long sur un téléphone.
- **Appel à l'action** : à la fin de la voix et dans la légende, la phrase `content.call_to_action` de `config.yaml` invite à s'abonner, commenter et partager ; l'IA termine son texte par une question ouverte qui donne envie de répondre. Un carton doré « ABONNE-TOI · COMMENTE · PARTAGE » s'affiche pendant les dernières secondes (`video.end_card`). Les mots de la phrase sont retirés du budget du script pour rester dans 30 à 90 s. Pour les brouillons déjà écrits, la phrase est ajoutée à la voix au moment de `video`. Vide = aucun appel. Les portraits, logos et réunions sont écartés. Sans résultat, des recherches générales (nébuleuse, galaxie, étoiles) sont essayées ; sans image du tout, un fond sombre fixe est utilisé.
- **Transitions** : fondus enchaînés fluides entre les plans (fondu, glissement, dissolution, ouverture circulaire…), durée réglable avec `video.transition_seconds`. Les images ont un lent mouvement de zoom ; les vidéos jouent leur extrait (le début est sauté, souvent un titre).
- **Musique d'ambiance** : une nappe spatiale originale (accord de La mineur qui respire lentement, avec de rares notes aiguës comme des étoiles) est générée par FFmpeg : aucun droit à payer, aucun téléchargement. Elle **baisse automatiquement quand la voix parle**. Niveau : `video.music_volume` (0,05 discrète à 0,5 forte). Pour ta propre musique : `video.music_file: assets/musique.mp3` (vérifie les droits : une musique du commerce peut faire bloquer le Reel). `video.music: false` coupe la musique.
- **Effets sonores** : un grave profond au tout début (façon bande-annonce) et un léger souffle à chaque transition. `video.sfx: false` les coupe.
- **Sous-titres** : le script est découpé en groupes de 2 à 5 mots, répartis sur la durée de la voix, en blanc avec contour sombre, plus le filigrane « COSMOS EN POCHE » en haut. La position laisse libre la zone du bas où Facebook affiche ses boutons.
- **Look** : ton bleuté, léger voile sombre progressif en bas pour la lisibilité. La vidéo dure la voix plus 0,8 s.

## Montage de secours

Selon sa version, FFmpeg peut refuser certains filtres. Le montage essaie trois niveaux, du plus riche au plus sûr : (1) zoom animé + fondus enchaînés, (2) images fixes + fondus enchaînés, (3) images fixes + petits fondus par plan. Le premier qui réussit est gardé ; la « Note » affichée à la fin dit quel niveau a servi. Si un niveau échoue, la cause exacte est enregistrée dans `output/reel-N/ffmpeg-erreur.log`. Pendant le montage, un pourcentage s'affiche.

## Droits des images et vidéos

Les images et vidéos de la bibliothèque de la NASA sont en général utilisables librement, avec mention de la source. **Ce n'est pas garanti pour chaque image** : certaines appartiennent à des partenaires. Le fichier `credits.txt` donne le titre et l'identifiant de chaque image ou vidéo, ainsi que la musique utilisée, pour la vérifier sur images.nasa.gov, et la légende doit citer « NASA ». Regarde les images avant de publier.

## Sécurité

- Seules des adresses `https://` du domaine `nasa.gov` sont téléchargées (redirections vérifiées) : des JPEG/PNG de 8 Mo maximum et des MP4 de 60 Mo maximum, reconnus par leur contenu et non par leur nom.
- Le texte lu sur Internet n'est jamais passé à une commande : le script va dans un fichier, et FFmpeg est lancé sans interpréteur de commandes.
- Les réglages `video.*` sont validés (voix, vitesse, chemins sans caractères spéciaux).

## Dépannage

| Message | Cause et solution |
|---|---|
| `edge-tts n'est pas installé` | `pip install edge-tts` |
| `aucun son produit` | Pas d'Internet, ou service Edge TTS indisponible : réessaie plus tard |
| `FFmpeg introuvable` | `pkg install ffmpeg -y` |
| Les sous-titres n'apparaissent pas ou sont en carrés | Police absente : mets dans `config.yaml` `video.font_dir: /system/fonts` et `video.font_name: Roboto` |
| `image ignorée` | Image introuvable ou refusée : le Reel utilise les autres images |
| `aucune image : fond sombre généré` | La NASA n'a rien renvoyé : relance, ou essaie sans l'IA |

## Limites actuelles

- La voix et les images n'ont pas encore été testées sur ton téléphone : c'est l'objet de la validation.
- Les images sont choisies d'après le texte, sans voir leur contenu : certaines peuvent illustrer le sujet de façon approximative. Vérifie la vidéo avant toute publication.
- Les vidéos NASA sont recadrées au centre pour le format vertical : les bords du plan sont perdus.
- La musique est une ambiance générée, simple : pour un rendu plus riche, utilise `video.music_file`.
- Pas d'animation de texte avancée.

## Format long (5 minutes) et 60 images par seconde

- `python -m cosmos_ai run --duree 300` : vise un script d'environ 5 minutes (30 à 600 s). L'agent lit plusieurs articles pour avoir assez de matière ; relis le texte avec soin, une durée longue augmente le risque d'approximations.
- Le nombre d'images et de vidéos suit la durée : un plan toutes les `seconds_per_plan` secondes (10 par défaut), au plus `max_plans`.
- `fps: 60` dans `config.yaml`. Le montage de 5 minutes à 60 images/s est très long sur un téléphone : pour un essai, `python -m cosmos_ai video ID --fps 30`.
- Au-delà de 120 s, le zoom animé est désactivé (trop lent) ; les transitions restent.
- Facebook : l'API des Reels accepte 3 à 90 s. Une vidéo de 5 minutes sera sans doute publiée comme vidéo normale en phase 5 (à vérifier alors).

## Corriger un brouillon avant le montage

`python -m cosmos_ai edit ID` ouvre la voix off et la légende dans nano. Garde les deux repères `=== VOIX OFF ===` et `=== LÉGENDE ===`. À la sortie, les alertes (mots emphatiques, guillemets, vouvoiement) sont recalculées, et l'ancienne vidéo est invalidée : refais-la avec `video ID`. Les chiffres et les faits modifiés restent à vérifier avec les sources.

## Connexion instable

- Avant le montage, `video` teste si les serveurs de la voix et de la NASA répondent et prévient sinon.
- Les requêtes (articles, recherches, images, vidéos) sont refaites jusqu'à 3 fois avec une pause qui s'allonge ; la voix jusqu'à 4 fois.
- Les fournisseurs d'IA : si tous échouent sur une panne passagère (503, réponse vide), l'agent refait jusqu'à 3 tours avec une pause de 5 s, 10 s. Une clé refusée n'est pas réessayée.
- Si la connexion est vraiment coupée, rien ne remplace un bon réseau : relance quand il revient.
