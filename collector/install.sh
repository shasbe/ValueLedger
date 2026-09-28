#!/usr/bin/env bash
# ValueLedger collector — one-line install.
#
#   curl -fsSL {{SERVICE_URL}}/install.sh | bash
#
# Creates an isolated Python environment, configures this machine, backfills
# your existing Claude history, and schedules a daily run. Nothing is installed
# system-wide and nothing needs sudo.
set -euo pipefail

SERVICE_URL="${VALUELEDGER_URL:-{{SERVICE_URL}}}"
HOME_DIR="${HOME}/.valueledger"
APP_DIR="${HOME_DIR}/app"
VENV="${HOME_DIR}/venv"

b() { printf '\033[1m%s\033[0m\n' "$1"; }
d() { printf '\033[2m%s\033[0m\n' "$1"; }
g() { printf '\033[32m%s\033[0m\n' "$1"; }
r() { printf '\033[31m%s\033[0m\n' "$1"; }

b "ValueLedger collector"
d "  attributing Claude spend on this machine"
echo

# ---- python ----
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)
    if [ "$v" -ge 310 ]; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  r "  Python 3.10+ is required and was not found."
  echo "  macOS:  brew install python@3.13"
  echo "  or install from https://www.python.org/downloads/"
  exit 1
fi
d "  using $($PY --version)"

# ---- transcripts present? ----
if [ ! -d "${HOME}/.claude/projects" ]; then
  r "  No Claude transcripts found at ~/.claude/projects"
  echo "  The collector reads sessions Claude has already written to disk."
  echo "  Use Claude Code or Cowork at least once, then run this again."
  exit 1
fi
N=$(find "${HOME}/.claude/projects" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
d "  found ${N} transcripts to read"

# ---- fetch the collector ----
mkdir -p "$APP_DIR"
d "  downloading collector…"
curl -fsSL "${SERVICE_URL}/collector.tar.gz" | tar -xz -C "$APP_DIR" --strip-components=0

# ---- isolated environment ----
d "  creating environment…"
"$PY" -m venv "$VENV" >/dev/null
"$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

# ---- launcher on PATH ----
mkdir -p "${HOME}/.local/bin"
cat > "${HOME}/.local/bin/valueledger" <<LAUNCHER
#!/usr/bin/env bash
export PYTHONPATH="${APP_DIR}"
exec "${VENV}/bin/python" -m valueledger_collector.cli "\$@"
LAUNCHER
chmod +x "${HOME}/.local/bin/valueledger"

case ":${PATH}:" in
  *":${HOME}/.local/bin:"*) ;;
  *) for rc in "${HOME}/.zshrc" "${HOME}/.bashrc"; do
       [ -f "$rc" ] && ! grep -q '.local/bin' "$rc" 2>/dev/null \
         && echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
     done
     d "  added ~/.local/bin to your PATH (restart your shell, or run the full path)" ;;
esac

g "  installed"
echo
b "Now connect it"
d "  You need the service URL, your organization's API key, and your email."
echo
export VALUELEDGER_URL="$SERVICE_URL"
"${HOME}/.local/bin/valueledger" setup --url "$SERVICE_URL" </dev/tty || exit 1

echo
b "Previewing your history"
"${HOME}/.local/bin/valueledger" --full --dry-run || true

echo
printf '  Classify it now? [Y/n] '
read -r ans </dev/tty || ans="n"
case "${ans:-Y}" in
  [Nn]*) d "  skipped — run 'valueledger --full' whenever you like" ;;
  *) "${HOME}/.local/bin/valueledger" --full ;;
esac

echo
printf '  Run automatically every day? [Y/n] '
read -r ans </dev/tty || ans="n"
case "${ans:-Y}" in
  [Nn]*) d "  skipped — run 'valueledger schedule' later" ;;
  *) "${HOME}/.local/bin/valueledger" schedule ;;
esac

echo
g "Done."
d "  valueledger status     what's configured, when it last ran"
d "  valueledger            collect new sessions now"
d "  valueledger unschedule stop the daily run"
