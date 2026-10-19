# ==============================================================================
# ZuvoCloud WhatsApp ngrok Webhook Tunnel Launcher (PowerShell)
# Automatically exposes FastAPI (port 8000) and prints Meta Webhook Configuration
# ==============================================================================

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "       ZuvoCloud WhatsApp Webhook Tunnel Launcher   " -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Dependencies
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 'ngrok' is not installed or not in your PATH." -ForegroundColor Red
    Write-Host "Please install ngrok:" -ForegroundColor Yellow
    Write-Host "  - Download from: https://ngrok.com/download"
    Write-Host "  - Or via winget: winget install ngrok.ngrok"
    Write-Host "  - Or via npm:    npm install -g ngrok"
    exit 1
}

# 2. Check if FastAPI is running on port 8000
Write-Host "--> Checking FastAPI server on http://localhost:8000..." -ForegroundColor Gray
$fastApiRunning = $false
try {
    $res = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health" -Method Get -TimeoutSec 3 -ErrorAction SilentlyContinue
    if ($res.status -eq "healthy" -or $res.status -eq "degraded") {
        $fastApiRunning = $true
    }
} catch {
    try {
        $docRes = Invoke-WebRequest -Uri "http://localhost:8000/docs" -Method Get -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($docRes.StatusCode -eq 200) {
            $fastApiRunning = $true
        }
    } catch {}
}

if (-not $fastApiRunning) {
    Write-Host "`n[ERROR] FastAPI is not running on port 8000." -ForegroundColor Red
    Write-Host "Start ZuvoCloud first with:`n" -ForegroundColor Yellow
    Write-Host "  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`n" -ForegroundColor White
    exit 1
}
Write-Host "[OK] FastAPI server is running on port 8000.`n" -ForegroundColor Green

# 3. Read META_VERIFY_TOKEN and NGROK_AUTHTOKEN from .env
$verifyToken = "zuvo_secret_verify_token_jaipur_2026"
$ngrokToken = ""
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^META_VERIFY_TOKEN=(.*)$") {
            $verifyToken = $matches[1].Trim().Trim('"').Trim("'")
        }
        if ($_ -match "^NGROK_AUTHTOKEN=(.*)$") {
            $ngrokToken = $matches[1].Trim().Trim('"').Trim("'")
        }
    }
}

# 4. Start ngrok in background
Write-Host "--> Starting ngrok tunnel on port 8000..." -ForegroundColor Gray
$ngrokArgs = "http 8000"
if ($ngrokToken) {
    $ngrokArgs = "http 8000 --authtoken=$ngrokToken"
}
$ngrokProcess = Start-Process ngrok -ArgumentList $ngrokArgs -PassThru -WindowStyle Hidden

# 5. Poll local ngrok API until ready
Write-Host "--> Waiting for ngrok public HTTPS tunnel (max 30s)..." -ForegroundColor Gray
$httpsUrl = ""
$attempts = 0
while ($attempts -lt 30 -and -not $httpsUrl) {
    Start-Sleep -Seconds 1
    $attempts++
    try {
        $tunnelData = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2 -ErrorAction SilentlyContinue
        foreach ($t in $tunnelData.tunnels) {
            if ($t.public_url.StartsWith("https://")) {
                $httpsUrl = $t.public_url
                break
            }
        }
    } catch {}
}

if (-not $httpsUrl) {
    Write-Host "`n[ERROR] Failed to obtain ngrok HTTPS URL after 30 seconds." -ForegroundColor Red
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "  1. Check if ngrok is authenticated: ngrok config add-authtoken <TOKEN>"
    Write-Host "  2. Test running ngrok directly:      ngrok http 8000"
    if ($ngrokProcess -and -not $ngrokProcess.HasExited) { Stop-Process -Id $ngrokProcess.Id -Force }
    exit 1
}

$webhookUrl = "$httpsUrl/api/v1/whatsapp/webhook"

# 6. Test Webhook Verification Handshake
Write-Host "`n--> Verifying Webhook Handshake endpoint..." -ForegroundColor Gray
$testChallenge = "zuvo_challenge_$(Get-Random)"
$handshakeOk = $false
try {
    $verifyRes = Invoke-WebRequest -Uri "$webhookUrl?hub.mode=subscribe&hub.verify_token=$verifyToken&hub.challenge=$testChallenge" -TimeoutSec 5 -ErrorAction SilentlyContinue
    if ($verifyRes.StatusCode -eq 200 -and $verifyRes.Content -eq $testChallenge) {
        $handshakeOk = $true
    }
} catch {}

# 7. Print Output
Write-Host "`n====================================================" -ForegroundColor Green
Write-Host "       ZUVOCLOUD WHATSAPP WEBHOOK TUNNEL READY      " -ForegroundColor Green
Write-Host "====================================================`n" -ForegroundColor Green

Write-Host "  FastAPI Backend:  http://localhost:8000"
Write-Host "  ngrok Tunnel:     $httpsUrl"
if ($handshakeOk) {
    Write-Host "  Handshake Test:   [OK] Verified (HTTP 200 - Challenge echo success)" -ForegroundColor Green
} else {
    Write-Host "  Handshake Test:   [WARN] Verification check pending (Ensure META_VERIFY_TOKEN matches)" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "----------------------------------------------------" -ForegroundColor Cyan
Write-Host " COPY AND PASTE THIS INTO META DEVELOPER PORTAL     " -ForegroundColor Cyan
Write-Host " (WhatsApp -> Configuration -> Webhook -> Edit)     " -ForegroundColor Cyan
Write-Host "----------------------------------------------------`n" -ForegroundColor Cyan

Write-Host "  Callback URL:" -ForegroundColor White
Write-Host "  $webhookUrl" -ForegroundColor Green
Write-Host ""
Write-Host "  Verify Token:" -ForegroundColor White
Write-Host "  $verifyToken" -ForegroundColor Green
Write-Host ""
Write-Host "  Webhook Field to Subscribe:" -ForegroundColor White
Write-Host "  messages" -ForegroundColor Green
Write-Host ""

Write-Host "----------------------------------------------------" -ForegroundColor Cyan
Write-Host "IMPORTANT:" -ForegroundColor Yellow
Write-Host "  - Keep this terminal window open."
Write-Host "  - If this script is closed, ngrok will stop and Meta cannot reach the server."
Write-Host "  - If ngrok restarts and gives a new URL, update Callback URL in Meta."
Write-Host "  - Press Ctrl+C to stop the tunnel cleanly."
Write-Host "====================================================`n" -ForegroundColor Cyan

try {
    Write-Host "Listening for incoming WhatsApp events... (Press Ctrl+C to terminate)"
    while ($true) {
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host "`nShutting down ngrok tunnel..." -ForegroundColor Yellow
    if ($ngrokProcess -and -not $ngrokProcess.HasExited) {
        Stop-Process -Id $ngrokProcess.Id -Force
    }
}
