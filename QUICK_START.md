# 🚀 Guide de Démarrage Rapide

## 📋 Prérequis

✅ Python 3.12.3 installé  
✅ Docker installé  
✅ Environnement virtuel `.venv` configuré  
✅ Dépendances Python installées  

---

## 🔧 Configuration (À FAIRE)

### 1. Obtenir une clé API OpenAI

1. Allez sur https://platform.openai.com/api-keys
2. Créez un nouveau projet ou sélectionnez-en un existant
3. Cliquez sur **"Create new secret key"**
4. Copiez la clé (elle commence par `sk-...`)

### 2. Configurer le fichier `.env`

Éditez le fichier `.env` et remplacez la clé API :

```bash
nano .env
```

Trouvez cette ligne :
```bash
OPENAI_API_KEY=sk-your-openai-key-here
```

Et remplacez `sk-your-openai-key-here` par votre vraie clé API.

**Exemple :**
```bash
OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234
```

Sauvegardez avec `Ctrl+O` puis `Entrée`, et quittez avec `Ctrl+X`.

---

## 🐳 Démarrage de Docker

Si Docker n'est pas démarré, lancez-le :

```bash
sudo systemctl start docker
```

Vérifiez que Docker fonctionne :

```bash
docker ps
```

---

## ▶️ Lancer l'Application

### Option 1 : Mode Standard (Recommandé pour débuter)

```bash
./start.sh
```

Cela va :
- ✅ Vérifier Docker
- ✅ Démarrer Elasticsearch
- ✅ Attendre que Elasticsearch soit prêt
- ✅ Initialiser l'index Elasticsearch
- ✅ Lancer l'API Flask

### Option 2 : Mode avec Monitoring (Avancé)

```bash
./start.sh --with-monitoring
```

En plus du mode standard, cela ajoute :
- 📊 Prometheus (http://localhost:9090)
- 📈 Grafana (http://localhost:3000)

---

## 🧪 Tester l'Application

Une fois l'application lancée, **ouvrez un nouveau terminal** et lancez :

```bash
cd "/home/red/Documents/GLSID3/preparation stage/AI_Agent"
./scripts/manual/smoke_test_api.sh
```

Cela va tester :
- ✅ Endpoint de santé (`/health`)
- ✅ Ingestion de document (`/ingest`)
- ✅ Requête RAG (`/query`)
- ✅ Métriques Prometheus (`/metrics`)

---

## 🌐 Accès aux Services

Après le démarrage, vous pouvez accéder à :

| Service | URL | Credentials |
|---------|-----|-------------|
| **API RAG** | http://localhost:8000 | API Key: `test-key-123` |
| **Health Check** | http://localhost:8000/health | Aucun |
| **Metrics** | http://localhost:8000/metrics | Aucun |
| **Elasticsearch** | http://localhost:9200 | `elastic` / `changeme` |
| **Grafana** | http://localhost:3000 | `admin` / `admin` |
| **Prometheus** | http://localhost:9090 | Aucun |

---

## 📝 Exemple d'Utilisation avec cURL

### 1. Vérifier la santé de l'API

```bash
curl http://localhost:8000/health
```

### 2. Ingérer un document

```bash
curl -X POST http://localhost:8000/ingest \
  -H "X-API-Key: test-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "content": "Python est un langage de programmation populaire pour l'\''IA et le machine learning.",
        "filename": "python_intro.txt",
        "metadata": {
          "category": "programmation",
          "language": "fr"
        }
      }
    ],
    "source": "raw"
  }'
```

### 3. Poser une question (RAG Query)

```bash
curl -X POST http://localhost:8000/query \
  -H "X-API-Key: test-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Qu'\''est-ce que Python ?",
    "top_k": 3
  }'
```

---

## 🛑 Arrêter l'Application

Pour arrêter tous les services :

```bash
docker compose down
```

Pour arrêter et supprimer les volumes (⚠️ supprime les données Elasticsearch) :

```bash
docker compose down -v
```

---

## 🐛 Dépannage

### Problème : "Cannot connect to Docker daemon"

**Solution :**
```bash
sudo systemctl start docker
```

### Problème : "No module named 'flask'"

**Solution :**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### Problème : "Elasticsearch not healthy after 30 attempts"

**Solutions :**
1. Vérifiez les logs : `docker compose logs elasticsearch`
2. Redémarrez Elasticsearch : `docker compose restart elasticsearch`
3. Vérifiez la mémoire disponible : `free -h`

### Problème : "Invalid API key"

**Solution :**
- Vérifiez que vous utilisez une des clés dans `.env` :
  - `test-key-123`
  - `dev-key-456`
  - `prod-key-789`

---

## 📚 Documentation

- **API Documentation** : Voir `README.md`
- **Observabilité** : Voir `OBSERVABILITY.md`
- **Configuration** : Voir `.env.example`

---

## 🎯 Checklist de Démarrage

- [ ] Docker est démarré (`sudo systemctl start docker`)
- [ ] Clé API OpenAI configurée dans `.env`
- [ ] Lancé `./start.sh` avec succès
- [ ] Testé avec `./scripts/manual/smoke_test_api.sh`
- [ ] Accédé à http://localhost:8000/health

**Une fois tous les points cochés, vous êtes prêt ! 🎉**
