<#
.SYNOPSIS
    Registers or removes the RobloxD3D10Enforcer Windows Scheduled Task.

.DESCRIPTION
    Configures a per-user Windows Scheduled Task (no admin rights required for HKCU)
    that keeps HKCU\Software\Microsoft\Direct3D\Application<N> synced to the latest
    RobloxPlayerBeta.exe with FeatureLevelLimit=40960 (Direct3D Feature Level 10_0).

    By default, runs `roblox_d3d10_enforcer.py --watch --silent` at logon (plus a
    5-minute self-healing trigger with MultipleInstances=IgnoreNew) so Roblox updates
    are detected immediately before RobloxPlayerBeta.exe launches.

.PARAMETER OneShotPeriodic
    Run one-shot sync at logon and every 5 minutes instead of keeping a background watcher.

.PARAMETER Uninstall
    Remove the Scheduled Task, stop any running watcher, and revert the Roblox
    Direct3D registry entries.

.PARAMETER TaskName
    Scheduled Task name (default: RobloxD3D10Enforcer).
#>
[CmdletBinding()]
param(
    [switch]$OneShotPeriodic,
    [switch]$Uninstall,
    [string]$TaskName = "RobloxD3D10Enforcer"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnforcerPy = Join-Path $ScriptDir "roblox_d3d10_enforcer.py"
$D3DRegPath = "HKCU:\Software\Microsoft\Direct3D"

function Find-RealPython {
    param([switch]$PreferWindowless)

    $names = if ($PreferWindowless) { @("pythonw.exe", "python.exe", "python3.exe") } else { @("python.exe", "python3.exe", "pythonw.exe") }

    # 1. Check PATH excluding Microsoft Store WindowsApps aliases
    foreach ($n in $names) {
        $cmds = Get-Command $n -All -ErrorAction SilentlyContinue | Where-Object {
            $_.Source -and ($_.Source -notlike "*\Microsoft\WindowsApps\*") -and (Test-Path $_.Source)
        }
        foreach ($c in $cmds) {
            if ((Get-Item $c.Source).Length -gt 0) {
                return $c.Source
            }
        }
    }

    # 2. Check standard install locations and Registry PythonCore
    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($regRoot in @("HKCU:\Software\Python\PythonCore", "HKLM:\SOFTWARE\Python\PythonCore", "HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore")) {
        if (Test-Path $regRoot) {
            Get-ChildItem $regRoot -ErrorAction SilentlyContinue | ForEach-Object {
                $ip = Join-Path $_.PSPath "InstallPath"
                if (Test-Path $ip) {
                    $dir = (Get-ItemProperty $ip -ErrorAction SilentlyContinue).'(default)'
                    if ($dir) {
                        foreach ($n in $names) {
                            $candidates.Add((Join-Path $dir $n))
                        }
                    }
                }
            }
        }
    }

    foreach ($base in @(
        "$env:LocalAppData\Programs\Python\Python*",
        "C:\Python*",
        "C:\Program Files\Python*"
    )) {
        Get-Item $base -ErrorAction SilentlyContinue | ForEach-Object {
            foreach ($n in $names) {
                $candidates.Add((Join-Path $_.FullName $n))
            }
        }
    }

    foreach ($cand in $candidates) {
        if ($cand -and (Test-Path $cand) -and ((Get-Item $cand).Length -gt 0)) {
            return $cand
        }
    }
    return $null
}

function Remove-RobloxDirect3DEntries {
    $py = Find-RealPython
    if ($py -and (Test-Path $EnforcerPy)) {
        & $py "`"$EnforcerPy`"" --revert
        if ($LASTEXITCODE -eq 0) {
            return
        }
    }

    if (-not (Test-Path $D3DRegPath)) {
        Write-Host "[INFO] $D3DRegPath does not exist; nothing to revert."
        return
    }
    foreach ($subPath in @($D3DRegPath, "$D3DRegPath\Direct3D12")) {
        if (Test-Path $subPath) {
            Get-ChildItem $subPath -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^Application\d+$' } | ForEach-Object {
                $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
                if ($props.Name -and ([System.IO.Path]::GetFileName($props.Name.Trim('"')) -ieq "RobloxPlayerBeta.exe")) {
                    Remove-Item $_.PSPath -Recurse -Force
                    Write-Host "[OK] Removed registry entry $subPath\$($_.PSChildName) ($($props.Name))"
                }
            }
        }
    }
    $cpPath = "$D3DRegPath\ControlPanel"
    if (Test-Path $cpPath) {
        $cpKey = Get-Item $cpPath -ErrorAction SilentlyContinue
        if ($cpKey) {
            foreach ($valName in $cpKey.GetValueNames()) {
                if ($valName -match '^Application\d+_Name$') {
                    $valData = $cpKey.GetValue($valName)
                    if ($valData -and ([System.IO.Path]::GetFileName($valData.ToString().Trim('"')) -ieq "RobloxPlayerBeta.exe")) {
                        Remove-ItemProperty -Path $cpPath -Name $valName -Force
                        Write-Host "[OK] Removed registry value $cpPath\$valName"
                    }
                }
            }
        }
    }
}

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.State -eq 'Running') {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] Unregistered Scheduled Task '$TaskName'."
    } else {
        Write-Host "[INFO] Scheduled Task '$TaskName' is not registered."
    }

    Remove-RobloxDirect3DEntries
    Write-Host "[DONE] Uninstall and registry revert complete."
    exit 0
}

if (-not (Test-Path $EnforcerPy)) {
    throw "Required script not found: $EnforcerPy"
}

# Check Graphics Tools (D3D11_3SDKLayers.dll)
$sdkLayers = Join-Path $env:SystemRoot "System32\D3D11_3SDKLayers.dll"
$dxcplPath = Join-Path $env:SystemRoot "System32\dxcpl.exe"
if (-not (Test-Path $sdkLayers)) {
    Write-Warning "D3D11_3SDKLayers.dll is MISSING from System32."
    Write-Warning "Windows d3d11.dll ignores FeatureLevelLimit unless the 'Graphics Tools' optional feature is installed."
    Write-Warning "Run this in an elevated (Administrator) terminal:"
    Write-Warning "  DISM /Online /Add-Capability /CapabilityName:Tools.Graphics.DirectX~~~~0.0.1.0"
} else {
    Write-Host "[OK] Graphics Tools SDK Layers detected: $sdkLayers"
}
if (Test-Path $dxcplPath) {
    Write-Host "[OK] dxcpl.exe detected: $dxcplPath"
}

# Locate real Python (prefer pythonw.exe for hidden background execution)
$pythonExe = Find-RealPython -PreferWindowless
if (-not $pythonExe) {
    Write-Error @"
No Python installation was found (only Microsoft Store WindowsApps aliases may exist).
Install Python 3 first, e.g.:
  winget install -e --id Python.Python.3.12
Then re-run:
  powershell -ExecutionPolicy Bypass -File "$PSCommandPath"
"@
    exit 1
}
Write-Host "[OK] Using Python executable: $pythonExe"

$pyArgs = if ($OneShotPeriodic) {
    "`"$EnforcerPy`" --silent"
} else {
    "`"$EnforcerPy`" --watch --silent"
}

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument $pyArgs -WorkingDirectory $ScriptDir

$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

# Stop existing task before replacing
$old = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($old -and $old.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Principal $principal `
    -Settings $settings `
    -Description "Enforces Direct3D Feature Level 10_0 (0xA000) for RobloxPlayerBeta.exe via native Windows Direct3D registry." `
    -Force | Out-Null

Write-Host "[OK] Registered Scheduled Task '$TaskName' (Mode: $(if ($OneShotPeriodic) { 'OneShotPeriodic' } else { 'Watch + 5m Self-Heal' }))."

# Perform immediate initial sync using a console python if available, then start the task
$consolePy = Find-RealPython
& $consolePy "`"$EnforcerPy`""
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Initial sync exited with code $LASTEXITCODE. Check roblox_d3d10_enforcer.log."
}

if (-not $OneShotPeriodic) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "[OK] Started background watcher task '$TaskName'."
}
