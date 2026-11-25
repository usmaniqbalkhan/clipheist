#!/bin/bash
# Push backend + app UI to AWS, add the new keys to the server .env (values never printed), rebuild.
set -euo pipefail
cd /home/farhann-saleem/Desktop/github/hackathon/30-hack/clipheist
H=ubuntu@56.228.122.243
S="ssh -i $HOME/.ssh/clip.pem -o BatchMode=yes"
rsync -az --delete -e "$S" --exclude .venv --exclude data --exclude .env --exclude secrets --exclude __pycache__ \
  backend/ $H:~/clipheist/backend/
rsync -az --delete -e "$S" --exclude __pycache__ peakcut-ui/ $H:~/clipheist/peakcut-ui/
# copy only these keys from the local .env, replacing any old line for the same key
grep -E '^(PEAKCUT_ASR_URL|PEAKCUT_ASR_TOKEN|PEAKCUT_RENDER_URL|DEEPSEEK_API_KEY)=' backend/.env | \
  $S $H 'set -e; f=~/clipheist/backend/.env; umask 077; new=$(cat); for k in PEAKCUT_ASR_URL PEAKCUT_ASR_TOKEN PEAKCUT_RENDER_URL DEEPSEEK_API_KEY; do sed -i "/^$k=/d" $f; done; tail -c1 $f | read -r _ || echo >> $f; printf "%s\n" "$new" >> $f; chmod 600 $f; grep -o "^[A-Z_]*=" $f | tr "\n" " "; echo'
$S $H 'cd ~/clipheist/backend && sudo docker compose up -d --build 2>&1 | tail -5 && sudo docker compose ps --format "{{.Name}} {{.Status}}"'
