#!/bin/bash

# Script pour nettoyer les secrets de l'historique Git
# ⚠️ ATTENTION: Cela réécrit l'historique Git complet !

set -e

echo "========================================="
echo "  GIT HISTORY CLEANUP - SECRETS REMOVAL"
echo "========================================="
echo ""
echo "⚠️  WARNING: This will rewrite Git history!"
echo "⚠️  Make sure you have revoked the exposed API key first!"
echo ""
read -p "Have you revoked the old API key? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "❌ Please revoke the API key first!"
    echo "   Go to: https://aistudio.google.com/apikey"
    exit 1
fi

echo ""
echo "📋 Step 1: Creating backup..."
BACKUP_DIR="../AI_Agent_backup_$(date +%Y%m%d_%H%M%S)"
cp -r . "$BACKUP_DIR"
echo "✓ Backup created at: $BACKUP_DIR"

echo ""
echo "📋 Step 2: Removing .env from Git history..."

# Create a file with patterns to remove
cat > /tmp/env_patterns.txt <<'EOF'
GEMINI_API_KEY=AIzaSyD5CWzB0LkkEAmk5q9KboPiqlUDSYNKEJ8
OPENAI_API_KEY=sk-
ANTHROPIC_API_KEY=sk-ant-
EOF

# Use git filter-repo to remove sensitive patterns
git filter-repo --replace-text /tmp/env_patterns.txt --force

echo "✓ Secrets removed from history"

echo ""
echo "📋 Step 3: Verifying cleanup..."
if git log --all --full-history --source --all -S "AIzaSyD5CWzB0LkkEAmk5q9KboPiqlUDSYNKEJ8" | grep -q "commit"; then
    echo "⚠️  WARNING: Secret still found in history!"
else
    echo "✓ No secrets found in history"
fi

echo ""
echo "📋 Step 4: Force push required"
echo ""
echo "To complete the cleanup, run:"
echo "  git push origin main --force"
echo ""
echo "⚠️  This will overwrite the remote repository history!"
echo "⚠️  All collaborators will need to re-clone the repository!"
echo ""

rm /tmp/env_patterns.txt
