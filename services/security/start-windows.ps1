# Windows에서 한 번에 실행: PC 내부 IP 찾기 → .env에 넣기 → (IP가 바뀌었으면) 인증서 다시 만들기 → docker compose → 브라우저 열기
# 직접 부르지 말고 같은 폴더의 start-windows.bat을 더블클릭한다.
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

# 1. 기본 게이트웨이가 있는 어댑터(= 지금 인터넷에 쓰는 Wi-Fi·유선)의 IPv4
$ip = Get-NetIPConfiguration |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" -and $_.InterfaceAlias -notmatch "vEthernet|WSL|Docker" } |
    Select-Object -First 1 -ExpandProperty IPv4Address |
    Select-Object -ExpandProperty IPAddress
if (-not $ip) { throw "PC의 내부 IP를 찾지 못했습니다. Wi-Fi에 연결되어 있는지 확인하세요." }
Write-Host "PC 내부 IP: $ip"

# 2. .env의 SECURITY_CERT_HOST를 이 IP로 맞춘다 (.env가 없으면 .env.example에서 만든다)
# BOM 없는 UTF-8로 읽고 쓴다 (docker compose가 BOM을 싫어하고, Windows PowerShell 기본값은 한글이 깨진다)
$utf8 = New-Object System.Text.UTF8Encoding $false
$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $root ".env.example") $envPath }
$all = [System.IO.File]::ReadAllLines($envPath, $utf8)
$old = ($all | Where-Object { $_ -match "^SECURITY_CERT_HOST=" } | Select-Object -First 1) -replace "^SECURITY_CERT_HOST=", ""
$lines = @($all | Where-Object { $_ -notmatch "^SECURITY_CERT_HOST=" }) + "SECURITY_CERT_HOST=$ip"
[System.IO.File]::WriteAllLines($envPath, [string[]]$lines, $utf8)

# 3. IP가 바뀌었으면 예전 IP로 만든 인증서를 지워 security가 새로 만들게 한다
$certs = "services\security\certs"
if ($old -ne $ip -and (Test-Path "$certs\cert.pem")) {
    Write-Host "IP가 바뀌어($old → $ip) 인증서를 다시 만듭니다."
    Remove-Item "$certs\cert.pem", "$certs\key.pem" -ErrorAction SilentlyContinue
}

# 4. 실행
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose 실행에 실패했습니다. Docker Desktop이 켜져 있는지 확인하세요." }
if ($old -ne $ip) { docker compose up -d --force-recreate security | Out-Null }

Write-Host ""
Write-Host "준비 완료"
Write-Host "  PC   : http://localhost  (브라우저가 열립니다)"
Write-Host "  휴대폰: 같은 Wi-Fi에서 PC 화면의 QR을 찍고, 처음 한 번 인증서 경고에서 계속 진행"
Start-Process "http://localhost"
