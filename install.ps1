# Install auto-sort on Windows, or update it. Paste this into PowerShell:
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/snepssen/auto-sort/main/install.ps1 | iex"
#
# It finds Python -- offering to install it with winget if there is none --
# fetches install.py, and runs it. Everything else is in install.py.

# In a script block of its own: run through `iex`, this is the person's
# own PowerShell session, and `exit` would close their window.
& {
    $ErrorActionPreference = "Stop"
    $branch = if ($env:AUTO_SORT_BRANCH) { $env:AUTO_SORT_BRANCH } else { "main" }
    $installer = "https://raw.githubusercontent.com/snepssen/auto-sort/$branch/install.py"

    function Find-Python {
        # "python" on a fresh Windows can be a shortcut that opens the Store
        # instead of running anything, so every candidate is asked to prove it.
        $check = "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"
        # One string each, split at "|" when used: PowerShell flattens an
        # array of arrays into one list of words.
        $candidates = @("py|-3", "python",
                        "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe|-3")
        foreach ($candidate in $candidates) {
            $parts = $candidate -split "\|"
            $exe = $parts[0]
            $rest = @($parts | Select-Object -Skip 1)
            try {
                & $exe @rest -c $check 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { return $candidate }
            } catch { }
        }
        return $null
    }

    $python = Find-Python
    if (-not $python) {
        Write-Host ""
        Write-Host "  auto-sort needs Python 3.8 or newer, and this computer does not have it yet."
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            $answer = Read-Host "  Install Python now with winget? [Y/n]"
            if ($answer -eq "" -or $answer -match "^[Yy]") {
                winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
                $python = Find-Python
            }
        }
        if (-not $python) {
            Write-Host ""
            Write-Host "  Get it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'),"
            Write-Host "  then open a new PowerShell window and paste the install line again."
            return
        }
    }

    $script = Join-Path $env:TEMP "auto-sort-install.py"
    Invoke-WebRequest -UseBasicParsing -Uri $installer -OutFile $script
    try {
        $parts = $python -split "\|"
        $exe = $parts[0]
        $rest = @($parts | Select-Object -Skip 1)
        & $exe @rest $script @args
    } finally {
        Remove-Item -ErrorAction SilentlyContinue $script
    }
} @args
