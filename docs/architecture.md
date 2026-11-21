# Architecture de COSMOS-AI

## Vue d'ensemble

Un **agent unique** (`CosmosAgent`) exécute un cycle : une suite d'étapes (modules), puis la porte de validation, puis la publication.

```
Veille ─► Tendances ─► Choix du sujet ─► Recherche ─► Scénario ─► Vérification
                                                                      │
Publication ◄─ Porte de validation ◄─ Montage ◄─ Voix/sous-titres ◄─ Visuels
     │
     └─► Commentaires ─► Statistiques ─► Ajustement de la stratégie
                  (tout est enregistré dans la mémoire éditoriale)
```

## Modules prévus

| Module | Rôle | Phase |
|---|---|---|
| Veille scientifique | Surveiller les découvertes mondiales | 3 |
| Actualités spatiales et tech | Suivre NASA, ESA, SpaceX, etc. | 3 |
| Détection des tendances | Repérer les sujets susceptibles de plaire | 3 |
| Mémoire éditoriale | Éviter les répétitions | **1 (fait)** |
| Scénario | Écrire le script de 30 à 90 s | 3 |
| Vérification | Contrôler l'exactitude scientifique | 3 |
| Visuels scientifiques | Images et illustrations | 4 |
| Voix, sous-titres, montage | Produire le Reel complet | 4 |
| Publication Facebook | Publier via l'API Graph | 5 |
| Gestion des commentaires | Répondre aux abonnés | 6 |
| Statistiques et optimisation | Adapter la stratégie | 6 |

## Ports et adaptateurs

`src/cosmos_ai/ports.py` définit les interfaces : `LLMProvider`, `NewsSource`, `MediaSource`, `TTSProvider`, `VideoRenderer`, `Publisher`, `Notifier`.
Chaque service externe est un adaptateur qui implémente un port. L'agent ne dépend que des ports.

## Fournisseurs d'IA

- `LLMRouter` essaie les fournisseurs dans l'ordre de `config.yaml`.
- Erreur normale : passage au suivant.
- Quota atteint (`QuotaExceeded`) : le fournisseur est mis en pause (`cooldown_seconds`) puis réessayé.
- Tous en échec : `AllProvidersFailed` avec le détail de chaque panne.
- Le routeur est lui-même un `LLMProvider`.

## Porte de validation

| Mode | Comportement |
|---|---|
| `manual` | Le Reel est enregistré en `pending_approval` et le `Notifier` te prévient. `approve(id)` publie, `reject(id)` écarte. |
| `auto` | Le Reel est publié directement. Un échec de publication est enregistré (`publish_failed`) et peut être réessayé. |

## Statuts d'un Reel

`pending_approval` → `approved` → `published`
Autres : `rejected` (le sujet peut être retraité), `publish_failed` (réessayable).

## Sécurité

- Aucune clé dans le dépôt : variables d'environnement uniquement.
- Une étape en échec ne fait jamais tomber l'agent : le cycle est marqué `failed`.
- Le mode par défaut est `manual`.
