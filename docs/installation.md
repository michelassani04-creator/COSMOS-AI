# Installation et configuration

## 1. Installer le projet

Python 3.10 ou plus récent est nécessaire. Le projet n'a qu'une dépendance (PyYAML) : les appels aux API utilisent la bibliothèque standard.

Sous Termux :

```bash
pkg install python git
cd ~/COSMOS-AI
pip install -e ".[dev]"
```

Lancer les tests (aucune clé ni connexion nécessaire) :

```bash
pytest
```

## 2. Obtenir les clés d'API

Voir `docs/fournisseurs-ia.md` : Gemini (principal) et Groq (secondaire), tous deux utilisables gratuitement.

## 3. Déclarer les clés

Les clés se déclarent dans des **variables d'environnement**. Elles ne vont jamais dans `config.yaml`, ni dans le dépôt, ni dans une conversation.

Sous Termux ou Linux, ouvre le fichier `~/.bashrc` avec un éditeur (`nano ~/.bashrc`) et ajoute à la fin :

```bash
export GEMINI_API_KEY="ta-clé-gemini"
export GROQ_API_KEY="ta-clé-groq"
```

Puis recharge-le : `source ~/.bashrc`.

Évite de taper la clé directement dans une commande (`echo ... >> ~/.bashrc`) : elle resterait dans l'historique du terminal.

## 4. Vérifier

```bash
python -m cosmos_ai check
```

Affiche pour chaque fournisseur s'il est « prêt » ou ce qui manque (jamais la clé elle-même). Exemple :

```
Fournisseurs d'IA activés (ordre d'essai) :
  1. gemini : prêt
  2. groq : gemini: clé absente ...
```

Test avec une vraie requête (consomme un tout petit peu de quota gratuit) :

```bash
python -m cosmos_ai ping
```

Affiche par exemple `Réponse de gemini : OK`. Si Gemini est indisponible ou son quota atteint, la réponse vient de Groq : c'est le basculement automatique.

## 5. Configuration (`config.yaml`)

| Réglage | Rôle | Défaut |
|---|---|---|
| `llm.providers` | Liste des fournisseurs. **L'ordre = la priorité.** | gemini, groq |
| `enabled` | Active ou désactive un fournisseur | `true` |
| `model` | Modèle à utiliser. Vide = modèle par défaut | vide |
| `api_key_env` | Nom de la variable d'environnement de la clé | `GEMINI_API_KEY`, `GROQ_API_KEY` |
| `timeout_seconds` | Délai d'attente d'une requête | 30 |
| `max_retries` | Nouvelles tentatives si le service est indisponible (0 à 5) | 1 |
| `llm.cooldown_seconds` | Pause d'un fournisseur au quota atteint, si celui-ci n'indique pas de délai | 300 |

Pour changer de fournisseur principal, inverse l'ordre dans la liste. Aucun code à modifier.

## 6. GitHub Actions (plus tard)

Quand l'agent tournera sur GitHub Actions, les clés seront enregistrées comme **secrets** du dépôt (Settings, Secrets and variables, Actions), jamais dans les fichiers.

## Dépannage

| Message | Cause probable |
|---|---|
| `clé absente (variable d'environnement ... non définie)` | La variable n'est pas définie dans ce terminal : refais `source ~/.bashrc` |
| `clé d'API refusée (HTTP 401/403)` | Clé erronée, copiée avec un espace, ou révoquée |
| `quota ou limite de débit atteint` | Limite gratuite atteinte : le routeur bascule sur le fournisseur suivant |
| `délai de 30 s dépassé` | Connexion lente : augmente `timeout_seconds` |
| `modèle ... introuvable ou inaccessible` | Le modèle n'existe plus ou n'est pas ouvert à ton compte : voir « Si un modèle est refusé » dans `docs/fournisseurs-ia.md` |
| `Tous les fournisseurs ont échoué` | Aucun fournisseur disponible : lis le détail de chaque panne dans le message |
