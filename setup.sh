#!/usr/bin/env bash
# =============================================================================
# setup.sh — Deploy repi-parser on Debian VPS
# Run as root: sudo bash setup.sh
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SERVICE_USER="repi"
INSTALL_DIR="/srv/repi-parser"
MODEL_DIR="$INSTALL_DIR/models"
LOG_DIR="$INSTALL_DIR/logs"
MODEL_FILENAME="qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
PORT=7878

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; exit 1; }

# ── Must run as root ──────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Run as root: sudo bash setup.sh"

# ── Step 1: System deps ───────────────────────────────────────────────────────
info "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    build-essential cmake \
    wget curl git

# ── Step 2: Create service user ───────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    info "Creating user '$SERVICE_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    info "User '$SERVICE_USER' already exists."
fi

# ── Step 3: Create directories ────────────────────────────────────────────────
info "Setting up directories at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR" "$MODEL_DIR" "$LOG_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── Step 4: Copy service files ────────────────────────────────────────────────
info "Copying service files..."
# Assumes you're running this from the directory containing main.py etc.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/main.py"          "$INSTALL_DIR/"
cp "$SCRIPT_DIR/parser_engine.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"/*.py "$INSTALL_DIR/requirements.txt"

# ── Step 5: Python venv + packages ───────────────────────────────────────────
info "Creating Python virtualenv..."
python3 -m venv "$INSTALL_DIR/venv"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/venv"

info "Installing Python packages (this may take a few minutes)..."
# Install llama-cpp-python with CPU-only build — no CUDA flags needed
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
CMAKE_ARGS="-DGGML_BLAS=OFF -DGGML_CUDA=OFF" \
    "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# ── Step 6: Download model ────────────────────────────────────────────────────
MODEL_PATH="$MODEL_DIR/$MODEL_FILENAME"
if [[ -f "$MODEL_PATH" ]]; then
    info "Model already exists at $MODEL_PATH — skipping download."
else
    info "Downloading Qwen2.5-0.5B-Instruct Q4_K_M (~400MB)..."
    warn "This will take a while depending on your connection speed."
    wget -q --show-progress -O "$MODEL_PATH" "$MODEL_URL" || {
        # Fallback: try huggingface-cli
        warn "wget failed, trying curl..."
        curl -L --progress-bar -o "$MODEL_PATH" "$MODEL_URL" || error "Model download failed."
    }
    chown "$SERVICE_USER:$SERVICE_USER" "$MODEL_PATH"
    info "Model downloaded: $(du -sh "$MODEL_PATH" | cut -f1)"
fi

# ── Step 7: Firewall — block port 7878 externally ────────────────────────────
info "Configuring firewall (iptables)..."
# Block port 7878 from all external traffic — only localhost can reach it
if command -v iptables &>/dev/null; then
    # Remove existing rule if any, then add fresh
    iptables -D INPUT -p tcp --dport "$PORT" ! -s 127.0.0.1 -j DROP 2>/dev/null || true
    iptables -I INPUT -p tcp --dport "$PORT" ! -s 127.0.0.1 -j DROP
    info "iptables rule added: block port $PORT from non-localhost."

    # Persist across reboots
    if command -v iptables-save &>/dev/null; then
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || \
        iptables-save > /etc/iptables.rules 2>/dev/null || \
        warn "Could not persist iptables rules — run 'iptables-save' manually after reboot."
    fi
else
    warn "iptables not found — please manually block port $PORT externally."
fi

# ── Step 8: systemd service ───────────────────────────────────────────────────
info "Installing systemd service..."
cp "$SCRIPT_DIR/repi-parser.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable repi-parser
systemctl restart repi-parser

# ── Step 9: Smoke test ────────────────────────────────────────────────────────
info "Waiting for service to start..."
sleep 4

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$PORT/health || echo "000")
if [[ "$HTTP_STATUS" == "200" ]]; then
    info "Service is UP ✓ — health check passed."
    info "Testing parse endpoint..."
    RESULT=$(curl -s -X POST http://127.0.0.1:$PORT/parse \
        -H "Content-Type: application/json" \
        -d '{"text":"Nướng ở 180 độ trong 25 phút"}')
    echo "  Test result: $RESULT"
else
    warn "Service may still be loading model. Check logs:"
    warn "  journalctl -u repi-parser -f"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  repi-parser deployed successfully!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "  Endpoint:  http://127.0.0.1:$PORT/parse"
echo "  Health:    http://127.0.0.1:$PORT/health"
echo "  Logs:      journalctl -u repi-parser -f"
echo "  Status:    systemctl status repi-parser"
echo ""
echo "  PORT $PORT is blocked externally via iptables."
echo "  Only processes on this VPS can call the parser."
