# Runs only on the local Windows host. Never sends the key over HTTP.
$ErrorActionPreference = 'Stop'
try {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
    $envFile = Join-Path $root '.env'
    $exampleFile = Join-Path $root '.env.example'
    $configuredExternally = [Environment]::GetEnvironmentVariable('CROSSMATCH_SETUP_KEY')
    if (-not [string]::IsNullOrWhiteSpace($configuredExternally)) {
        if ($configuredExternally -cnotmatch '^[a-zA-Z0-9_-]{32,256}$') {
            throw 'CROSSMATCH_SETUP_KEY in the Windows environment is too short or invalid. Use at least 32 random letters/numbers.'
        }
        Write-Host 'A valid setup key is already configured in the Windows environment. No file was changed.'
        exit 0
    }
    if (Test-Path -LiteralPath $envFile) {
        $lines = [IO.File]::ReadAllLines($envFile)
        $existingLines = @($lines | Where-Object { $_ -match '^\s*CROSSMATCH_SETUP_KEY\s*=' })
        if ($existingLines.Count -gt 1) {
            throw 'Multiple CROSSMATCH_SETUP_KEY entries were found in .env. Fix them manually first.'
        }
        if ($existingLines.Count -eq 1) {
            $value = ($existingLines[0] -split '=', 2)[1].Trim().Trim('"').Trim("'")
            if ($value -cnotmatch '^[a-zA-Z0-9_-]{32,256}$') {
                throw 'The setup key in .env is blank, weak, or invalid. Remove the CROSSMATCH_SETUP_KEY line and rerun this helper.'
            }
            Write-Host 'A valid one-time setup key is already in .env. No key was changed.'
            Write-Host 'To create the first admin, retrieve CROSSMATCH_SETUP_KEY locally from .env.'
            exit 0
        }
    } elseif (Test-Path -LiteralPath $exampleFile) {
        [IO.File]::Copy($exampleFile, $envFile)
    } else {
        [IO.File]::WriteAllText($envFile, '', ([System.Text.UTF8Encoding]::new($false)))
    }
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $key = [BitConverter]::ToString($bytes).Replace('-', '')
    $content = [IO.File]::ReadAllText($envFile)
    $prefix = if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) { "`r`n" } else { '' }
    [IO.File]::AppendAllText($envFile, "${prefix}CROSSMATCH_SETUP_KEY=$key`r`n", ([System.Text.UTF8Encoding]::new($false)))
    Write-Host ''
    Write-Host 'ONE-TIME FIRST-ADMIN SETUP KEY CREATED ON THIS COMPUTER.'
    Write-Host "Setup key: $key"
    Write-Host 'Also saved locally in .env as CROSSMATCH_SETUP_KEY.'
    Write-Host 'Keep this key private. It is NOT an account password.'
    Write-Host 'Use it on the first administrator setup page. The server blocks reuse after setup.'
    Write-Host ''
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
