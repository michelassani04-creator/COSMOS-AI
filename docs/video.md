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

Résultat dans `output/reel-3/` : `reel-3.mp4` (la vidéo), `voix.mp3`, `images/` (les images téléchargées) et `credits.txt` (crédit de chaque image et sources d'information). Le dossier `output/` est ignoré par Git.

Le montage est long sur un téléphone (plusieurs minutes pour un Reel de 60 s) : laisse l'écran allumé. Pour un essai plus rapide, baisse `video.max_images` ou augmente `video.crf`.

## Ce que fait chaque étape

- **Voix off** : Edge TTS (voix neuronales françaises gratuites). La voix se change dans `config.yaml` (`video.voice`, par exemple `fr-FR-DeniseNeural`), la vitesse avec `video.rate` (`-5%`). Le service n'est pas officiel : s'il change ou disparaît, le fournisseur de voix est remplaçable (interface `TTSProvider`).
- **Images** : l'IA propose 3 recherches en anglais d'après le script, puis le programme cherche dans la bibliothèque d'images de la NASA (gratuite, sans clé). Les portraits, logos et réunions sont écartés. Sans IA disponible, des recherches génériques (nébuleuse, galaxie, étoiles) sont utilisées. Sans image, un fond sombre généré est utilisé.
- **Sous-titres** : le script est découpé en groupes de 2 à 5 mots, répartis sur la durée de la voix, en blanc avec contour sombre, plus le filigrane « COSMOS EN POCHE » en haut. La position laisse libre la zone du bas où Facebook affiche ses boutons.
- **Montage** : chaque image a un lent mouvement de zoom, un léger ton bleuté, un fondu, et un voile sombre progressif en bas pour la lisibilité. La vidéo dure la voix plus 0,8 s.

## Droits des images

Les images de la bibliothèque de la NASA sont en général utilisables librement, avec mention de la source. **Ce n'est pas garanti pour chaque image** : certaines appartiennent à des partenaires. Le fichier `credits.txt` donne le titre et l'identifiant de chaque image pour la vérifier sur images.nasa.gov, et la légende doit citer « NASA ». Regarde les images avant de publier.

## Sécurité

- Seules des adresses `https://` du domaine `nasa.gov` sont téléchargées (redirections vérifiées), uniquement des JPEG/PNG de 8 Mo maximum, reconnus par leur contenu et non par leur nom.
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
- Pas de musique de fond, pas d'animation de texte avancée.
