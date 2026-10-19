#!/usr/bin/env bash

# ==============================================================================
# ZuvoCloud WhatsApp ngrok Webhook Tunnel Launcher
# Automatically exposes FastAPI (port 8000) and prints Meta Webhook Configuration
# ==============================================================================

# Colors for terminal output
BOLD="\033[1m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
NC="\033[0m" # No Color

echo -e "${BOLD}${BLUE}====================================================${NC}"
echo -e "${BOLD}${BLUE}       ZuvoCloud WhatsApp Webhook Tunnel Launcher   ${NC}"
echo -e "${BOLD}${BLUE}====================================================${NC}\n"

# 1. Check Dependencies
NGROK_BIN=""
if [ -f "./ngrok.exe" ]; then
    NGROK_BIN="./ngrok.exe"
elif [ -f "./ngrok" ]; then
    NGROK_BIN="./ngrok"
elif command -v ngrok &> /dev/null; then
    NGROK_BIN="ngrok"
elif command -v ngrok.exe &> /dev/null; then
    NGROK_BIN="ngrok.exe"
fi

if ! command -v curl &> /dev/null; then
    echo -e "${RED}[ERROR] 'curl' is required but not installed or not in PATH.${NC}"
    exit 1
fi

if [ -z "$NGROK_BIN" ]; then
    echo -e "${RED}[ERROR] 'ngrok' is not installed or not in your PATH.${NC}"
    echo -e "${YELLOW}Please install ngrok:${NC}"
    echo -e "  - Download from: https://ngrok.com/download"
    echo -e "  - Or via winget: winget install ngrok.ngrok"
    echo -e "  - Or via npm:    npm install -g ngrok"
    exit 1
fi

# Determine python command
PYTHON_CMD="python"
if ! command -v python &> /dev/null; then
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    else
        echo -e "${RED}[ERROR] Python is required to extract ngrok tunnel metadata.${NC}"
        exit 1
    fi
fi

# 2. Check if FastAPI is running on port 8000
echo -e "--> Checking FastAPI server on http://localhost:8000..."
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/health 2>/dev/null || echo "000")

if [ "$HEALTH_STATUS" != "200" ]; then
    DOCS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/docs 2>/dev/null || echo "000")
    if [ "$DOCS_STATUS" != "200" ]; then
        echo -e "\n${RED}[ERROR] FastAPI is not running on port 8000.${NC}"
        echo -e "${YELLOW}Start ZuvoCloud first with:${NC}\n"
        echo -e "  ${BOLD}docker compose up -d --build${NC}\n"
        exit 1
    fi
fi
echo -e "${GREEN}[OK] FastAPI server is running on port 8000.${NC}\n"

# 3. Read META_VERIFY_TOKEN and NGROK_AUTHTOKEN from environment / .env
VERIFY_TOKEN="zuvo_secret_verify_token_jaipur_2026"
NGROK_TOKEN=""
if [ -f ".env" ]; then
    ENV_TOKEN=$(grep -E "^META_VERIFY_TOKEN=" .env | cut -d '=' -f2- | tr -d ' "\r\n')
    if [ -n "$ENV_TOKEN" ]; then
        VERIFY_TOKEN="$ENV_TOKEN"
    fi
    ENV_NGROK=$(grep -E "^NGROK_AUTHTOKEN=" .env | cut -d '=' -f2- | tr -d ' "\r\n')
    if [ -n "$ENV_NGROK" ]; then
        NGROK_TOKEN="$ENV_NGROK"
    fi
fi

# 4. Start ngrok in background with log capture
echo -e "--> Starting ngrok tunnel on port 8000..."
NGROK_LOG_FILE="ngrok.log"
rm -f "$NGROK_LOG_FILE"

# Check if an existing session is running on 4040
if curl -s http://127.0.0.1:4040/api/tunnels &> /dev/null; then
    echo -e "${YELLOW}[NOTE] An existing ngrok session was detected on port 4040. Checking tunnels...${NC}"
else
    NGROK_AUTH_PARAM=""
    if [ -n "$NGROK_TOKEN" ]; then
        NGROK_AUTH_PARAM="--authtoken=$NGROK_TOKEN"
    fi
    $NGROK_BIN http 8000 $NGROK_AUTH_PARAM --log="$NGROK_LOG_FILE" --log-format=term > /dev/null 2>&1 &
    NGROK_PID=$!
fi

# Setup cleanup trap on script exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down ngrok tunnel...${NC}"
    if [ -n "$NGROK_PID" ]; then
        kill "$NGROK_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 5. Poll local ngrok API (http://127.0.0.1:4040/api/tunnels) until ready
echo -e "--> Waiting for ngrok public HTTPS tunnel (max 15s)..."
MAX_ATTEMPTS=15
HTTPS_URL=""

for ((i=1; i<=MAX_ATTEMPTS; i++)); do
    # Check if ngrok process exited prematurely
    if [ -n "$NGROK_PID" ] && ! kill -0 "$NGROK_PID" 2>/dev/null; then
        echo -e "\n${RED}[ERROR] ngrok process stopped unexpectedly.${NC}"
        if [ -f "$NGROK_LOG_FILE" ]; then
            echo -e "${YELLOW}--- ngrok Log Output ---${NC}"
            cat "$NGROK_LOG_FILE"
            echo -e "${YELLOW}------------------------${NC}\n"
        fi
        echo -e "${YELLOW}Common Fix (Free Authtoken Required):${NC}"
        echo -e "  1. Sign up / login for free at: ${BOLD}https://dashboard.ngrok.com/signup${NC}"
        echo -e "  2. Copy your authtoken from:   ${BOLD}https://dashboard.ngrok.com/get-started/your-authtoken${NC}"
        echo -e "  3. Run: ${BOLD}ngrok config add-authtoken <YOUR_AUTHTOKEN>${NC}\n"
        exit 1
    fi

    HTTPS_URL=$($PYTHON_CMD -c "
import urllib.request, json
try:
    req = urllib.request.Request('http://127.0.0.1:4040/api/tunnels', headers={'User-Agent': 'curl/7.68.0'})
    with urllib.request.urlopen(req, timeout=1.5) as response:
        data = json.loads(response.read().decode('utf-8'))
        for t in data.get('tunnels', []):
            url = t.get('public_url', '')
            if url.startswith('https://'):
                print(url)
                break
except Exception:
    pass
" 2>/dev/null)

    if [ -n "$HTTPS_URL" ]; then
        break
    fi
    sleep 1
done

if [ -z "$HTTPS_URL" ]; then
    echo -e "\n${RED}[ERROR] Failed to obtain ngrok HTTPS URL.${NC}"
    if [ -f "$NGROK_LOG_FILE" ]; then
        echo -e "${YELLOW}--- ngrok Log Output ---${NC}"
        cat "$NGROK_LOG_FILE"
        echo -e "${YELLOW}------------------------${NC}\n"
    fi
    echo -e "${YELLOW}Troubleshooting:${NC}"
    echo -e "  1. Check if ngrok is authenticated: ngrok config add-authtoken <TOKEN>"
    echo -e "  2. Test running ngrok directly:      ngrok http 8000"
    exit 1
fi

WEBHOOK_URL="${HTTPS_URL}/api/v1/whatsapp/webhook"

# 6. Test Webhook Verification Handshake via public URL
echo -e "\n--> Verifying Webhook Handshake endpoint..."
TEST_CHALLENGE="zuvo_challenge_test_$(date +%s)"
VERIFY_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${WEBHOOK_URL}?hub.mode=subscribe&hub.verify_token=${VERIFY_TOKEN}&hub.challenge=${TEST_CHALLENGE}" 2>/dev/null || echo "000")
VERIFY_BODY=$(curl -s "${WEBHOOK_URL}?hub.mode=subscribe&hub.verify_token=${VERIFY_TOKEN}&hub.challenge=${TEST_CHALLENGE}" 2>/dev/null || echo "")

if [ "$VERIFY_STATUS" = "200" ] && [ "$VERIFY_BODY" = "$TEST_CHALLENGE" ]; then
    VERIFICATION_MSG="${GREEN}[OK] Verified (HTTP 200 - Challenge echo success)${NC}"
else
    VERIFICATION_MSG="${YELLOW}[WARN] Handshake returned HTTP ${VERIFY_STATUS} (Check META_VERIFY_TOKEN in .env)${NC}"
fi

# 7. Print Output & Meta Configuration Instructions
echo -e "\n${BOLD}${GREEN}====================================================${NC}"
echo -e "${BOLD}${GREEN}       ZUVOCLOUD WHATSAPP WEBHOOK TUNNEL READY      ${NC}"
echo -e "${BOLD}${GREEN}====================================================${NC}\n"

echo -e "  ${BOLD}FastAPI Backend:${NC}  http://localhost:8000"
echo -e "  ${BOLD}ngrok Tunnel:${NC}     ${HTTPS_URL}"
echo -e "  ${BOLD}Handshake Test:${NC}   ${VERIFICATION_MSG}\n"

echo -e "${BOLD}${BLUE}----------------------------------------------------${NC}"
echo -e "${BOLD}${BLUE} COPY AND PASTE THIS INTO META DEVELOPER PORTAL     ${NC}"
echo -e "${BOLD}${BLUE} (WhatsApp -> Configuration -> Webhook -> Edit)     ${NC}"
echo -e "${BOLD}${BLUE}----------------------------------------------------${NC}\n"

echo -e "  ${BOLD}Callback URL:${NC}"
echo -e "  ${GREEN}${WEBHOOK_URL}${NC}\n"

echo -e "  ${BOLD}Verify Token:${NC}"
echo -e "  ${GREEN}${VERIFY_TOKEN}${NC}\n"

echo -e "  ${BOLD}Webhook Field to Subscribe:${NC}"
echo -e "  ${GREEN}messages${NC}\n"

echo -e "${BOLD}${BLUE}----------------------------------------------------${NC}"
echo -e "${BOLD}${YELLOW}IMPORTANT:${NC}"
echo -e "  - Keep this terminal window open."
echo -e "  - If this script is closed, ngrok will stop and Meta cannot reach the server."
echo -e "  - If ngrok restarts and gives a new URL, update Callback URL in Meta."
echo -e "  - Press ${BOLD}Ctrl+C${NC} to stop the tunnel cleanly."
echo -e "${BOLD}${BLUE}====================================================${NC}\n"

# 8. Keep script alive until user presses Ctrl+C
echo -e "Listening for incoming WhatsApp events... (Press Ctrl+C to terminate)"
while true; do
    sleep 2
done
