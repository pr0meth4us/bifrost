#!/bin/bash
# scripts/init_dev.sh
# Automated Developer Initiation & Verification Script for Bifrost

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0;m'

echo -e "${BLUE}=== Bifrost Developer Initiation ===${NC}"

# 1. Check Python virtual environment
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}[!] Python virtual environment (.venv) not found. Setting up...${NC}"
    uv venv
fi
echo -e "${GREEN}[✓] Virtual environment detected.${NC}"

# 2. Install dependencies via uv
echo -e "${BLUE}[*] Restoring dependencies...${NC}"
uv pip install -r requirements.txt

# 3. Check syntax compilation
echo -e "${BLUE}[*] Running syntax compilation check...${NC}"
python3 -m py_compile run.py config.py bifrost/__init__.py bot/main.py bot/services.py bot/handlers/payment.py bot/handlers/admin.py
echo -e "${GREEN}[✓] Compilation check passed.${NC}"

# 4. Load past session memory context
echo -e "${BLUE}[*] Pulling past session context from memory...${NC}"
export ANTIGRAVITY_MEM_DB="/Users/nicksng/.antigravity-mem/memory.db"
if [ -f "/opt/homebrew/bin/antigravity-mem" ]; then
    /opt/homebrew/bin/antigravity-mem context -p "/Users/nicksng/code/bifrost" -q "init" || true
    echo -e "${GREEN}[✓] Memory context loaded.${NC}"
else
    echo -e "${YELLOW}[!] Memory CLI not found at /opt/homebrew/bin/antigravity-mem. Skipping context pull.${NC}"
fi

# 5. Run test suites
echo -e "${BLUE}[*] Running database proxy test suite...${NC}"
.venv/bin/python3 "/Users/nicksng/.gemini/antigravity-ide/brain/6237d06b-53b6-453a-b0ec-837e1c74c3b1/scratch/test_tenant_db.py" || { echo -e "${RED}[✗] DB Proxy tests failed.${NC}"; exit 1; }

echo -e "${BLUE}[*] Running Telegram bot integration test suite...${NC}"
.venv/bin/python3 "/Users/nicksng/.gemini/antigravity-ide/brain/6237d06b-53b6-453a-b0ec-837e1c74c3b1/scratch/test_bot_integration.py" || { echo -e "${RED}[✗] Bot integration tests failed.${NC}"; exit 1; }

echo -e "${GREEN}=== [✓] Setup & Integration Verified Successfully! ===${NC}"
echo -e "Refer to ${YELLOW}.agents/AGENTS.md${NC} for client integration and deployment checklists."
