# WBS 產生器 — 離線安裝腳本(Windows 10 / PowerShell)
# 需求:已安裝 Python 3.12(py -3.12 可啟動)
# 用法:在本資料夾按住 Shift 右鍵 →「在此處開啟 PowerShell」→ 執行 .\install.ps1
$ErrorActionPreference = "Stop"

Write-Host "=== WBS 產生器 安裝 ===" -ForegroundColor Cyan

# 1. 檢查 Python 3.12
try {
    $ver = & py -3.12 --version 2>&1
    Write-Host "[1/3] 找到 $ver"
} catch {
    Write-Host "找不到 Python 3.12,請先安裝(python.org 或軟體中心)" -ForegroundColor Red
    exit 1
}

# 2. 建立虛擬環境
if (-not (Test-Path ".venv")) {
    Write-Host "[2/3] 建立虛擬環境 .venv ..."
    & py -3.12 -m venv .venv
} else {
    Write-Host "[2/3] .venv 已存在,略過"
}

# 3. 離線安裝(全部套件都在 wheels\,不需網路)
Write-Host "[3/3] 安裝套件(離線)..."
& .\.venv\Scripts\python.exe -m pip install --no-index --find-links wheels wbsgen --quiet --disable-pip-version-check

# 驗證
& .\.venv\Scripts\wbs.exe --help *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "驗證失敗:wbs 指令無法執行" -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "安裝完成!" -ForegroundColor Green
Write-Host ""
Write-Host "快速開始(以樣本契約為例):"
Write-Host "  .\.venv\Scripts\wbs.exe auto sample\contract_11108.pdf" -ForegroundColor Yellow
Write-Host ""
Write-Host "詳細說明請看 README_測試說明.md"
