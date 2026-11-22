# Phase 3 : le contenu (veille, tendances, scénario, vérification)

## Le cycle

```
Veille ─► Tendances ─► Choix du sujet ─► Lecture de l'article ─► Scénario ─► Vérification ─► Brouillon
(flux RSS)  (classement)   (IA ou repli)    (texte complet)       (IA, JSON)   (IA + contrôles)  (en attente)
```

Rien n'est publié : le brouillon reste en attente de ta validation (mode `manual`). Facebook n'est pas connecté avant la phase 5.

## Commandes

| Commande | Effet |
|---|---|
| `python -m cosmos_ai news` | Lit les flux et affiche les sujets les plus populaires. N'utilise pas l'IA. |
| `python -m cosmos_ai run` | Produit un brouillon complet de Reel (utilise l'IA : un peu de quota gratuit). |
| `python -m cosmos_ai drafts` | Liste les brouillons et leur statut. |
| `python -m cosmos_ai show 1` | Affiche le brouillon n° 1 : voix off, légende, sources, vérification. |
| `python -m cosmos_ai reject 1` | Écarte le brouillon n° 1 (le sujet pourra être retraité). |

Les brouillons sont enregistrés dans `data/cosmos.db` (ignoré par Git).

## Veille

- Les sources sont des flux RSS/Atom listés dans `content.sources` de `config.yaml`.
- Une source en panne est ignorée, les autres continuent. `news` indique lesquelles ont échoué.
- **Sources configurées (11 flux, 9 éditeurs)** : NASA (3 flux), Space.com, Universe Today, Phys.org Espace, The Space Review, ESA Exploration Science, Futura Espace (français), et deux flux non testés (SpaceNews, ScienceDaily). Les neuf premiers ont été vérifiés (valides et à jour) ; pour les deux autres, `news` indique s'ils répondent. Une source en panne est simplement ignorée.
- Flux écartés après test : ancien flux ESA (404), blog JPL et ESA Science (derniers articles de 2020 à 2023), `science.nasa.gov/feed` (XML invalide).
- `news` affiche pour chaque source le nombre d'actualités lues (ou l'erreur), puis le total après retrait des doublons.
- Pour ajouter une source : une entrée `name` / `url` (https) / `publisher` dans `content.sources`, puis `python -m cosmos_ai news` pour la vérifier. Listes officielles : https://www.nasa.gov/rss-feeds et https://www.futura-sciences.com/flux-rss/.
- Limite : un titre anglais et un titre français sur le même événement ne sont pas regroupés (pas de traduction), donc Futura compte comme un éditeur à part.
- **`publisher`** : deux flux d'un même organisme (NASA et NASA communiqués) republient les mêmes articles. Ils sont regroupés sous un même éditeur et comptent pour **une** seule source dans le classement ; un article présent dans deux flux (même adresse) n'est gardé qu'une fois.
- Sécurité : le lecteur refuse les flux contenant des déclarations DTD ou d'entités (attaque par expansion XML), limite la taille des réponses (2 Mo), n'accepte que les liens `http(s)`, et n'accepte que des adresses `https://` dans la configuration.

## Détection des sujets populaires

Méthode sans service externe. Les actualités qui parlent du même événement sont regroupées, puis chaque groupe reçoit un score :

- **Nombre de sources distinctes** qui en parlent (le plus fort poids).
- **Mots d'intérêt** de la page (`interest_keywords`), sans tenir compte des accents ni de la casse.
- **Fraîcheur** (moins de `max_age_days` jours).

Limite à connaître : c'est la **popularité dans la presse spatiale**, pas une mesure de ce qui plaît sur Facebook. Les statistiques de la page (phase 6) permettront d'ajuster le classement.

## Sujets écartés

Les titres contenant un mot de `exclude_keywords` sont ignorés (cérémonies de remise de prix, financements, « status updates », webinaires…). La comparaison ignore la casse et les accents. Ajoute des mots dans `config.yaml` au fil de ce que tu vois dans `news`.

Piège YAML : un mot qui se termine par `:` doit être entre guillemets (`- "challenge:"`).

## Choix du sujet

1. Les sujets déjà traités sont exclus : même actualité (par son adresse) ou sujet similaire (mémoire éditoriale).
2. L'IA choisit parmi les `candidates` meilleurs sujets, en voyant la liste des sujets récents.
3. Si l'IA est indisponible ou répond de façon inexploitable, le sujet le mieux classé est pris.
4. Sans aucune actualité exploitable, un sujet de culture scientifique est pris dans `evergreen_topics` (sans source d'actualité, ce que la vérification signale).
5. Si tout est épuisé, le cycle est ignoré (`skipped`).

## Lecture de l'article

Pour le sujet choisi, l'agent lit le texte complet de l'article (adresse `https://` uniquement, 2 Mo maximum, paragraphes et intertitres seulement, 6000 caractères). Sans cela, l'IA ne verrait que le titre et le résumé du flux et comblerait les trous de mémoire : c'est ce qui a produit « flashs radio » et « précision inédite » dans le premier brouillon, absents de l'article NASA. Si l'article est illisible, le script s'appuie sur le résumé seul et le brouillon l'indique dans « Vérification ». Désactivable avec `fetch_articles: false`.

## Scénario

- L'IA reçoit ton identité éditoriale (`content.editorial` de `config.yaml`) comme consignes : tutoiement, ton, style, sujets prioritaires, règles absolues, exemple d'accroche.
- Durée : 30 à 90 secondes, soit 75 à 225 mots (débit de 2,5 mots par seconde). Si le texte est trop court ou trop long, l'IA a une deuxième chance avec la correction demandée ; sinon le cycle échoue proprement.
- Les actualités lues sur Internet sont transmises à l'IA comme **données**, encadrées par des balises, avec l'ordre d'ignorer toute instruction qu'elles contiendraient.
- La légende Facebook reçoit automatiquement la ligne « Sources : … ».

## Vérification : ce qu'elle fait et ne fait pas

Deux niveaux, enregistrés dans le brouillon (champ « Vérification ») :

1. **Contrôles automatiques sans IA**, fiables, appliqués au script **et à la légende** : passage entre guillemets (risque de citation inventée), formulations sensationnalistes ou complotistes, mots emphatiques (« révolutionnaire », « inédit », « incroyable »…), hashtags, absence de source, et **chiffres ou ordres de grandeur (« million », « milliard ») absents des sources**. Limite : les nombres écrits en toutes lettres (« quinze ans ») ne sont pas contrôlés.
2. **Relecture par une seconde requête à l'IA** : cherche les erreurs factuelles, chiffres non sourcés, hypothèses présentées comme des faits. Si elle propose un script corrigé de longueur valide, il est appliqué et signalé.

**Une IA qui relit une IA ne garantit pas l'exactitude.** Elle peut laisser passer une erreur : sur le premier brouillon réel, elle a répondu « aucun problème » alors que le script ajoutait des affirmations absentes de l'article. Les consignes de relecture sont désormais plus strictes (vérification phrase par phrase, doute à signaler), mais seule ta lecture attrape les interprétations (par exemple « cartographier ces phares cosmiques »). C'est pourquoi chaque brouillon porte la mention « ne remplace pas ta validation », et pourquoi la règle « aucune fausse information » exige ta relecture tant que le mode est `manual`. Vérifie en priorité les chiffres, les dates et les noms.

## Identité visuelle

La section `content.visual` (format 9:16, bleu profond, noir spatial, touches lumineuses, style cinématographique) est enregistrée dans la configuration. Elle sera utilisée en phase 4 pour les visuels et le montage.

## Modifier l'identité éditoriale

Tout se règle dans `config.yaml`, section `content.editorial`, sans toucher au code : ajoute une règle, change l'exemple, passe `tone` à `vous`, ajuste la durée.
