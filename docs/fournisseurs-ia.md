# Fournisseurs d'IA : Gemini et Groq

| Rôle | Fournisseur | API utilisée | Modèle par défaut |
|---|---|---|---|
| Principal | Google Gemini | `generateContent` (REST, API officielle) | `gemini-3.1-flash-lite` |
| Secondaire | Groq | `chat/completions` (compatible OpenAI) | `llama-3.3-70b-versatile` |

Aucun abonnement payant n'est nécessaire : les deux ont une offre gratuite.

## Obtenir la clé Gemini

1. Va sur <https://aistudio.google.com/apikey> avec ton compte Google.
2. Clique sur **Create API key**.
3. Copie la clé et range-la en lieu sûr.
4. Si le bouton est indisponible ou si tes projets n'apparaissent pas, ouvre **Dashboard**, puis **Projects**, puis **Import projects**. Sans droits sur un projet existant, crée un nouveau projet Google Cloud non rattaché à une organisation.

## Obtenir la clé Groq

1. Va sur <https://console.groq.com/keys> et connecte-toi (ou crée un compte).
2. Clique pour créer une clé d'API, donne-lui un nom.
3. Copie la clé tout de suite : elle ne sera plus affichée en entier ensuite.

Ensuite, déclare les deux clés comme indiqué dans `docs/installation.md`.

## Quotas gratuits

- Les limites (requêtes par minute, par jour, jetons) **varient selon le modèle et changent avec le temps** : consulte-les dans ton compte (AI Studio pour Gemini, `console.groq.com/settings/limits` pour Groq) plutôt que de te fier à un chiffre figé.
- Gemini : les limites s'appliquent **par projet**, pas par clé, et les quotas journaliers sont remis à zéro à minuit, heure du Pacifique.
- Gemini : à la date de vérification, la page de tarifs indiquait l'utilisation gratuite pour `gemini-3.1-flash-lite`. Pour un autre modèle (par exemple `gemini-3.8-flash`), vérifie qu'il est gratuit pour toi avant de l'écrire dans `model`.
- Groq : un dépassement renvoie HTTP 429 avec un en-tête `retry-after` (en secondes) que le routeur respecte.

## Comportement du basculement

| Situation avec Gemini | Réaction |
|---|---|
| Réponse normale | Utilisée. Groq n'est pas sollicité. |
| Quota atteint (HTTP 429) | Bascule immédiate sur Groq. Gemini est mis en pause pour le délai qu'il indique (sinon `cooldown_seconds`), puis réessayé automatiquement. |
| Service indisponible (HTTP 5xx, 408) | `max_retries` nouvelle(s) tentative(s) avec attente croissante (1 s, 2 s, 4 s...), puis bascule. |
| Délai dépassé | Même traitement qu'une indisponibilité. |
| Réseau coupé | Même traitement qu'une indisponibilité. |
| Clé refusée (401, 403, ou clé invalide) | Bascule immédiate, sans nouvelle tentative inutile. |
| Réponse vide ou bloquée (filtre de sécurité) | Bascule sur Groq. |

Si **tous** les fournisseurs échouent, l'erreur `AllProvidersFailed` détaille la panne de chacun. Le cycle de l'agent est alors marqué `failed` et rien n'est publié.

## Sécurité des clés

- Lues uniquement dans l'environnement, au démarrage.
- Gemini : envoyée dans l'en-tête `x-goog-api-key`, jamais dans l'URL, donc absente des journaux d'adresses.
- Masquée (`***`) dans tous les messages d'erreur, absente de la représentation texte des objets.
- La commande `check` indique seulement si une clé est présente.
