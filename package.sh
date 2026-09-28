#!/usr/bin/env bash
# Build a self-contained collector package to send to a tester.
#
#   ./package.sh                      # points at the hosted demo
#   ./package.sh <url> <api-key> <email> <standard|strict>
#
# Produces valueledger-collector.zip. The recipient unzips it, double-clicks
# START-HERE (or runs ./start), and everything else is automatic.
set -euo pipefail
cd "$(dirname "$0")"

URL="${1:-https://valueledger-795896542461.us-central1.run.app}"
KEY="${2:-}"
# One shared demo identity, baked in, so a recipient is never asked anything.
# Override per-package: ./package.sh <url> <key> <email>
USER_EMAIL="${3:-demo@example.com}"
# strict withholds repository names, branches and rationales. Use it whenever the
# recipient's project names are not yours to publish.
PRIVACY="${4:-strict}"
OUT="valueledger-collector"
ZIP="${OUT}.zip"

# Pull the demo key from the service if one was not supplied.
if [ -z "$KEY" ]; then
  KEY=$(curl -fsS "${URL}/v1/demo-info" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("api_key",""))' \
        2>/dev/null || true)
fi
[ -n "$KEY" ] || { echo "No API key. Pass one: ./package.sh <url> <api-key>"; exit 1; }

rm -rf "$OUT" "$ZIP"
mkdir -p "$OUT"
cp -R collector/valueledger_collector "$OUT/"
cp collector/requirements.txt "$OUT/"
# The skill, twice: a ready-to-upload zip, and the plain folder for anyone who
# would rather copy it into place themselves.
mkdir -p "$OUT/claude-skill"
cp -R skill/attribute-work "$OUT/claude-skill/"
( cd skill && zip -qr "../${OUT}/attribute-work-skill.zip" attribute-work \
    -x '*/__pycache__/*' '*/.DS_Store' )
find "$OUT" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$OUT/config.json" <<CFG
{
  "url": "${URL}",
  "api_key": "${KEY}",
  "user": "${USER_EMAIL}",
  "privacy": "${PRIVACY}"
}
CFG

# ---- the one script a tester runs ----
cat > "$OUT/start" <<'START'
#!/usr/bin/env bash
# ValueLedger — attribute your Claude spend. Run: ./start
set -euo pipefail
cd "$(dirname "$0")"

EMAIL_ARG=""; ASSUME_YES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --email) EMAIL_ARG="$2"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    *) shift ;;
  esac
done

b(){ printf '\033[1m%s\033[0m\n' "$1"; }
d(){ printf '\033[2m%s\033[0m\n' "$1"; }
g(){ printf '\033[32m%s\033[0m\n' "$1"; }
r(){ printf '\033[31m%s\033[0m\n' "$1"; }

clear 2>/dev/null || true
b "ValueLedger"
d "  Reads the Claude sessions already on this machine, works out what each one"
d "  cost and what it was for, and shows you the result."
echo

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  command -v "$c" >/dev/null 2>&1 || continue
  v=$("$c" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null||echo 0)
  [ "$v" -ge 310 ] && { PY="$c"; break; }
done
if [ -z "$PY" ]; then
  r "  Python 3.10 or newer is needed, and I could not find it."
  echo "  Install it from https://www.python.org/downloads/ and run ./start again."
  exit 1
fi

if [ ! -d "$HOME/.claude/projects" ]; then
  r "  No Claude sessions found on this machine."
  echo "  ValueLedger reads what Claude Code or Cowork has already written to disk."
  echo "  Use one of them at least once, then run ./start again."
  exit 1
fi
N=$(find "$HOME/.claude/projects" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
d "  found ${N} Claude sessions on this machine"

if [ ! -d .venv ]; then
  d "  setting up (about 30 seconds, one time)…"
  "$PY" -m venv .venv >/dev/null
  ./.venv/bin/pip install -q --upgrade pip >/dev/null 2>&1 || true
  ./.venv/bin/pip install -q -r requirements.txt
fi

EMAIL="${EMAIL_ARG}"
if [ -n "$EMAIL" ]; then
  ./.venv/bin/python - "$EMAIL" <<'PYSAVE'
import json, pathlib, sys
p = pathlib.Path("config.json"); d = json.loads(p.read_text())
d["user"] = sys.argv[1].strip().lower()
p.write_text(json.dumps(d, indent=2))
PYSAVE
else
  EMAIL=$(./.venv/bin/python -c "import json;print(json.load(open('config.json'))['user'])")
fi
d "  attributing to ${EMAIL}"

export PYTHONPATH="$PWD"
export VALUELEDGER_CONFIG="$PWD/config.json"

echo
b "Step 1 of 2 — what's here (this costs nothing)"
./.venv/bin/python -m valueledger_collector.cli --full --dry-run || exit 1

echo
d "  Step 2 works out what each session was for, using a small Claude model."
d "  It reads your prompts on this machine only — they are never uploaded."
if [ -z "$ASSUME_YES" ]; then
  printf '  Continue? [Y/n] '
  if [ -r /dev/tty ] 2>/dev/null; then
    read -r a </dev/tty || a=Y
  else
    read -r a || a=Y
  fi
  case "${a:-Y}" in [Nn]*) d "  Stopped. Nothing was sent."; exit 0 ;; esac
fi

echo
b "Step 2 of 2 — classifying"
./.venv/bin/python -m valueledger_collector.cli --full || exit 1

URL=$(./.venv/bin/python -c "import json;print(json.load(open('config.json'))['url'])")
echo
g "Done."
echo
b "See your results"
echo "  ${URL}"
d "  Open that, choose any role, and look at the Ledger tab."
echo
d "  Run ./start again any time to pick up new sessions."
START
chmod +x "$OUT/start"

sed -e "s|{{SERVICE_URL}}|${URL}|g" -e "s|{{USER_EMAIL}}|${USER_EMAIL}|g" \
  -e "s|{{API_KEY}}|${KEY}|g" \
  collector/START-HERE.template.md > "$OUT/START-HERE.md"


zip -qr "$ZIP" "$OUT"
rm -rf "$OUT"
echo "  → ${ZIP}  ($(du -h "$ZIP" | cut -f1))"
echo "  points at: ${URL}"
