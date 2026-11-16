# Fix Roblox Freezing / Crashing on Intel HD 4600 & 4400 (Direct3D Feature Level `10_0`)

If you have a 4th Gen Intel Haswell iGPU (**Intel HD 4600 / HD 4400**) and Roblox freezes or crashes (`AppHangB1` / `MoAppHang`) after a few minutes in-game, reinstalling Intel's driver will not help—the legacy Haswell D3D11 driver (`igd10iumd64.dll`) has an unpatched bug when games use Feature Level `11_0` / `11_1` (`ID3D11DeviceContext::GetData`).

## Why FastFlags (`ClientAppSettings.json`) No Longer Work

Previously, players fixed this by setting `FFlagDebugGraphicsPreferD3D11FL10` to `true` via `ClientAppSettings.json` or bootstrappers like Bloxstrap.

Roblox has since enforced the **Fast Flag (FFlag) Allowlist**: the Roblox client now rejects any local FastFlag that is not on Roblox's approved server allowlist. If you check your `%LocalAppData%\Roblox\logs\*.log`, you will see:

```text
Warning [FLog::FlagFetchingStarterModule] Denied local configuration for: FFlagDebugGraphicsPreferD3D11FL10
```

Because `FFlagDebugGraphicsPreferD3D11FL10` is blocked at the client level, the **only clean, Hyperion-safe way** left is to cap `RobloxPlayerBeta.exe` at **Direct3D Feature Level `10_0` (`0xA000`)** at the **Windows Direct3D runtime layer** using Microsoft's built-in **Graphics Tools** (`dxcpl.exe` / `d3dconfig.exe`).

---

## Transparency & Safety (How to Verify Nothing Is Stolen or Tampered With)

You should never blindly run scripts from the internet on a machine with your Roblox account. Here is everything this repo touches—and everything it **never** touches:

- **Zero Roblox file or memory tampering:** Does not patch, hook, inject DLLs into, or read/write `RobloxPlayerBeta.exe` memory or `%LocalAppData%\Roblox` files (100% compliant with Hyperion anti-cheat).
- **Zero network access:** `roblox_d3d10_enforcer.py` only imports Python standard-library modules (`argparse`, `ctypes`, `glob`, `logging`, `os`, `pathlib`, `re`, `sys`, `time`, `winreg`). It does **not** import `urllib`, `requests`, `socket`, or `http`, and cannot connect to the internet or read browser cookies / `.ROBLOSECURITY`.
- **Only touches Windows' official Direct3D Control Panel keys:** It writes strictly to `HKCU\Software\Microsoft\Direct3D` (`Application<N>`, `ControlPanel`, and `Direct3D12\Application<N>`)—the exact same registry keys created by Microsoft's official `dxcpl.exe` and `d3dconfig.exe` tools.
- **Want zero third-party scripts?** Use **Option 1 (100% Manual)** below using only Microsoft's built-in Windows tools.

---

## Prerequisite (Required for Both Manual & Automated Methods)

Windows' `d3d11.dll` **only** enforces `FeatureLevelLimit` when Microsoft's **Graphics Tools** (`D3D11_3SDKLayers.dll`) optional feature is installed. Without it, `d3d11.dll` silently ignores feature-level limits.

Install it once using an **Administrator** PowerShell or Command Prompt:

```powershell
DISM /Online /Add-Capability /CapabilityName:Tools.Graphics.DirectX~~~~0.0.1.0
```

*(Alternatively: Windows **Settings → System → Optional features → View features → Graphics Tools → Install**.)*

---

## Option 1: 100% Manual Method (Built-In Microsoft Tools Only, No Scripts)

If you prefer not to run any Python or PowerShell script, you can do the exact same setup manually using Microsoft's built-in `d3dconfig.exe` or `dxcpl.exe` GUI.

> **Note:** Roblox changes its folder path (`%LocalAppData%\Roblox\Versions\version-xxxx\`) on every client update, so with the manual method you will need to re-add the new `RobloxPlayerBeta.exe` path after Roblox updates.

### Via `d3dconfig.exe` (3 commands, no GUI needed)
Open PowerShell and run (replace `<version-xxxx>` with your newest folder in `%LocalAppData%\Roblox\Versions`):

```powershell
$rbx = (Get-ChildItem "$env:LocalAppData\Roblox\Versions\version-*\RobloxPlayerBeta.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
d3dconfig.exe apps --add $rbx
d3dconfig.exe device feature-level-limit=10_0
d3dconfig.exe device no-feature-level-upgrade=true
```

### Or via `dxcpl.exe` (GUI)
1. Press `Win + R`, type `dxcpl`, and press Enter.
2. Go to the **Direct3D 10/11/12** tab (or **Scope → Edit List...**).
3. Click **Edit List...**, browse to `%LocalAppData%\Roblox\Versions\version-xxxx\RobloxPlayerBeta.exe`, click **Add**, then **OK**.
4. Under **Device Settings**:
   - **Feature level limit:** `10_0`
   - Check **Disable Feature Level Upgrade**
5. Click **Apply** and **OK**.

---

## Option 2: Automated Python + Auto-Updater (Handles Roblox Updates Automatically)

Because Roblox creates a new `version-*` folder whenever it updates, [`roblox_d3d10_enforcer.py`](./roblox_d3d10_enforcer.py) watches `%LocalAppData%\Roblox\Versions` (plus Bloxstrap/Fishstrap folders if used) and updates the `dxcpl`/`d3dconfig` registry entry in-place before `RobloxPlayerBeta.exe` launches.

### Step 1: Install Python 3 (if not already installed)
Skip this if `python --version` already prints Python 3.x (note: Windows' default `python` command can open the Microsoft Store stub—installing via `winget` gives you a real Python binary):

```powershell
winget install -e --id Python.Python.3.12
```

Open a **new** PowerShell window after installing Python so `PATH` refreshes.

### Step 2: Download this repository & test once
Clone the repo (or download as ZIP and extract it):

```powershell
git clone https://github.com/luaupatcher-dev/roblox-hd4600-d3d10-fix.git
cd roblox-hd4600-d3d10-fix
```

Check current status (read-only):

```powershell
python roblox_d3d10_enforcer.py --status
```

Apply the Feature Level `10_0` lock once:

```powershell
python roblox_d3d10_enforcer.py
```

You can confirm Windows registered it with Microsoft's official tool:

```powershell
d3dconfig.exe apps
d3dconfig.exe device
```

### Step 3: Enable Automatic Updates (Zero Manual Maintenance)
Run [`install_task.ps1`](./install_task.ps1) to register a per-user Scheduled Task (`RobloxD3D10Enforcer`, no admin rights needed) that runs a lightweight Win32 directory watcher (`--watch --silent`) at logon:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
```

*(If you prefer periodic 5-minute checks instead of a persistent watcher, pass `-OneShotPeriodic`.)*

---

## How to Verify It Worked in Roblox

After launching Roblox once, open the newest `*_Player_*.log` file in `%LocalAppData%\Roblox\logs\` and search for `GfxFeatureLevel`:

```text
D3D11: GfxCardName Intel(R) HD Graphics 4600 GfxCardVendor 8086 GfxFeatureLevel D3D10.0
```

You should see `GfxFeatureLevel D3D10.0` (and `Loaded ... shaders from pack d3d10`).

---

## How to Completely Uninstall / Revert

Remove the Scheduled Task and delete all Roblox Direct3D registry entries cleanly:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Uninstall
```

Or revert only the registry entries manually:

```powershell
python roblox_d3d10_enforcer.py --revert
```
