# 🚨 Guide de Sécurité : Nettoyage des Secrets dans Git

## Problème

Vous avez accidentellement commité et pushé un fichier `.env` contenant des clés API sensibles sur GitHub. Même après avoir supprimé le fichier et fait un nouveau commit, **les secrets restent dans l'historique Git** et sont accessibles publiquement.

## ⚠️ DANGER IMMÉDIAT

Votre clé API est **PUBLIQUE** sur GitHub. N'importe qui peut :
- Utiliser votre clé API gratuitement
- Consommer votre quota
- Vous facturer des frais importants
- Accéder à vos données si la clé a ces permissions

## 🚨 ÉTAPE 1 : RÉVOQUER LA CLÉ API (IMMÉDIAT)

### Pour Gemini (Google AI Studio)
1. Allez sur : https://aistudio.google.com/apikey
2. Trouvez votre clé exposée : `AIzaSyD5CWzB0LkkEAmk5q9KboPiqlUDSYNKEJ8`
3. Cliquez sur **"Delete"** ou **"Revoke"**
4. Créez une **NOUVELLE clé API**
5. Gardez-la secrète cette fois !

### Pour OpenAI
1. Allez sur : https://platform.openai.com/api-keys
2. Trouvez la clé exposée
3. Cliquez sur **"Revoke"**
4. Créez une nouvelle clé

### Pour Anthropic
1. Allez sur : https://console.anthropic.com/settings/keys
2. Trouvez la clé exposée
3. Cliquez sur **"Delete"**
4. Créez une nouvelle clé

**⚠️ NE CONTINUEZ PAS AVANT D'AVOIR RÉVOQUÉ LES CLÉS !**

---

## 🔧 ÉTAPE 2 : NETTOYER L'HISTORIQUE GIT

### Option A : Avec le script automatique (Recommandé)

```bash
# 1. Assurez-vous d'avoir révoqué les clés API
# 2. Exécutez le script de nettoyage
./cleanup_secrets.sh

# 3. Suivez les instructions
# 4. Force push vers GitHub
git push origin main --force
```

### Option B : Manuel avec git filter-repo

```bash
# 1. Installer git-filter-repo
pip install git-filter-repo

# 2. Créer un fichier avec les patterns à remplacer
cat > /tmp/secrets.txt <<EOF
GEMINI_API_KEY=AIzaSyD5CWzB0LkkEAmk5q9KboPiqlUDSYNKEJ8==>GEMINI_API_KEY=***REMOVED***
OPENAI_API_KEY=sk-==>OPENAI_API_KEY=***REMOVED***
ANTHROPIC_API_KEY=sk-ant-==>ANTHROPIC_API_KEY=***REMOVED***
EOF

# 3. Nettoyer l'historique
git filter-repo --replace-text /tmp/secrets.txt --force

# 4. Vérifier que les secrets ont été supprimés
git log --all --full-history -S "AIzaSyD5CWzB0LkkEAmk5q9KboPiqlUDSYNKEJ8"
# Si aucun résultat = OK ✓

# 5. Force push
git push origin main --force
```

### Option C : BFG Repo-Cleaner (Alternative)

```bash
# 1. Télécharger BFG
wget https://repo1.maven.org/maven2/com/madgag/bfg/1.14.0/bfg-1.14.0.jar

# 2. Créer fichier avec secrets
echo "AIzaSyD5CWzB0LkkEAmk5q9KboPiqlUDSYNKEJ8" > secrets.txt

# 3. Nettoyer
java -jar bfg-1.14.0.jar --replace-text secrets.txt

# 4. Cleanup Git
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 5. Force push
git push origin main --force
```

---

## 🛡️ ÉTAPE 3 : PRÉVENIR LES FUTURES FUITES

### 1. Vérifier .gitignore

Le fichier `.gitignore` doit contenir :

```gitignore
# Environment variables (SENSITIVE)
.env
.env.*
!.env.example
*.key
*.pem
secrets/

# Logs (may contain secrets)
logs/
*.log
```

### 2. Utiliser .env.example

Créez `.env.example` avec des valeurs fictives :

```bash
GEMINI_API_KEY=your-gemini-api-key-here
OPENAI_API_KEY=sk-your-openai-key-here
```

Commitez seulement `.env.example`, jamais `.env` !

### 3. Installer git-secrets (Prévention automatique)

```bash
# Installation
brew install git-secrets  # macOS
# ou
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets && sudo make install

# Configuration dans votre repo
cd /path/to/your/repo
git secrets --install
git secrets --register-aws
git secrets --add 'AIza[0-9A-Za-z_-]{35}'  # Pattern Gemini
git secrets --add 'sk-[0-9a-zA-Z]{48}'     # Pattern OpenAI
git secrets --add 'sk-ant-[0-9a-zA-Z-]{95}' # Pattern Anthropic

# Test
echo "GEMINI_API_KEY=AIzaSyD..." > test.txt
git add test.txt
# Devrait bloquer le commit !
```

### 4. Pre-commit Hook

Créez `.git/hooks/pre-commit` :

```bash
#!/bin/bash

# Check for secrets before committing
if git diff --cached --name-only | grep -q "^\.env$"; then
    echo "❌ ERROR: Attempting to commit .env file!"
    echo "   Use .env.example instead"
    exit 1
fi

# Check for API keys in staged files
if git diff --cached | grep -qE "(GEMINI_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)=AIza|sk-"; then
    echo "❌ ERROR: API key detected in staged files!"
    echo "   Remove secrets before committing"
    exit 1
fi

exit 0
```

Rendez-le exécutable :
```bash
chmod +x .git/hooks/pre-commit
```

---

## ✅ ÉTAPE 4 : VÉRIFICATIONS POST-NETTOYAGE

### 1. Vérifier l'historique local

```bash
# Chercher la clé dans l'historique
git log --all --full-history -S "AIzaSyD5CWzB0LkkEAmk5q9KboPiqlUDSYNKEJ8"

# Si aucun résultat = ✓ OK
```

### 2. Vérifier GitHub

```bash
# Après force push, vérifiez sur GitHub
# https://github.com/Otmanesabiri/RAG-Automation-Agent/search?q=AIzaSyD

# Si "We couldn't find any code matching..." = ✓ OK
```

### 3. Scanner avec TruffleHog (Optionnel)

```bash
# Installer
pip install truffleHog

# Scanner le repo
trufflehog git file://. --json
```

---

## 📋 CHECKLIST COMPLÈTE

- [ ] **Clés API révoquées** (Gemini, OpenAI, Anthropic)
- [ ] **Nouvelles clés créées** (et gardées secrètes!)
- [ ] **Historique Git nettoyé** (git filter-repo ou BFG)
- [ ] **Force push effectué** (`git push --force`)
- [ ] **`.gitignore` mis à jour** (inclut `.env`)
- [ ] **`.env.example` créé** (valeurs fictives)
- [ ] **`.env` ajouté à .gitignore**
- [ ] **Pre-commit hook installé**
- [ ] **git-secrets configuré** (optionnel)
- [ ] **Historique vérifié** (aucun secret trouvé)
- [ ] **GitHub vérifié** (search ne trouve rien)
- [ ] **Nouvelles clés configurées localement**
- [ ] **Application testée** avec nouvelles clés

---

## ⚠️ AVERTISSEMENTS

### Pour les collaborateurs

Si vous avez des collaborateurs, **ils doivent TOUS** :

```bash
# Supprimer leur copie locale
rm -rf /path/to/repo

# Re-cloner depuis GitHub
git clone https://github.com/Otmanesabiri/RAG-Automation-Agent.git
```

**NE PAS faire `git pull` !** L'historique a été réécrit.

### Sauvegarde

Avant de nettoyer l'historique :

```bash
# Créer une sauvegarde complète
cp -r /path/to/repo /path/to/repo_backup_$(date +%Y%m%d)
```

---

## 🔗 RESSOURCES

- **git-filter-repo** : https://github.com/newren/git-filter-repo
- **BFG Repo-Cleaner** : https://rtyley.github.io/bfg-repo-cleaner/
- **git-secrets** : https://github.com/awslabs/git-secrets
- **GitHub Secrets Scanning** : https://docs.github.com/en/code-security/secret-scanning
- **TruffleHog** : https://github.com/trufflesecurity/trufflehog

---

## 📞 SUPPORT

Si vous avez des questions :
1. Consultez la documentation Git : https://git-scm.com/docs
2. GitHub Security : https://docs.github.com/en/code-security
3. Créez une issue : https://github.com/Otmanesabiri/RAG-Automation-Agent/issues

---

**Date de création** : 30 Octobre 2025  
**Auteur** : RAG Automation Team  
**Priorité** : 🚨 CRITIQUE - Sécurité

## 🔁 Pre-commit (gitleaks)

Le dépôt fournit `.pre-commit-config.yaml` (gitleaks + détection de clés privées) :

```bash
pip install pre-commit
pre-commit install
```

