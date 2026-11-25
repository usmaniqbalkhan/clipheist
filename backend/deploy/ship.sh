#!/bin/bash
# Ship everything: app to AWS, then commit + push (Vercel redeploys the landing page from main).
set -euo pipefail
cd "$(dirname "$0")/../.."
bash backend/deploy/push_aws.sh
git add -A
if git status --short | grep -qiE '(^|/)\.env|\.pem$|secrets/'; then
  echo "Refusing: a secret file is staged." >&2; git reset -q; exit 1
fi
git commit -q -m "${1:-ClipHeist: one sign-in across site and app, Modal render, DeepSeek AI, credits, link reuse}

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" || echo "nothing new to commit"
git push origin main
echo "Shipped. Vercel builds the landing page in about a minute."
