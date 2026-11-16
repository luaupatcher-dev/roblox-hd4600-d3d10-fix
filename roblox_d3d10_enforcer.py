"""Automated DirectX 11 Feature Level 10_0 (0xA000 / 40960) enforcer for Roblox.

Configures the native Windows Direct3D runtime override registry keys used by
`dxcpl.exe`, `d3dconfig.exe`, `dxgi.dll`, and `d3d11.dll`:

    HKCU\\Software\\Microsoft\\Direct3D\\Application<N>
        Name         (REG_SZ) = <full path to RobloxPlayerBeta.exe>
        D3DBehaviors (REG_SZ) = RequireSDKLayers=0;FeatureLevelLimit=40960;ForceWARP=0;DisableFLUpgrade=1

No Roblox files, DLLs, or process memory are touched.

Important Windows Runtime Note:
    `d3d11.dll` requires `D3D11_3SDKLayers.dll` (provided by the Windows
    "Graphics Tools" optional feature: `Tools.Graphics.DirectX~~~~0.0.1.0`) to
    honor `FeatureLevelLimit`. If Graphics Tools is not installed, `d3d11.dll`
    silently zeroes `FeatureLevelLimit`. Run `--status` to verify readiness.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import glob
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Dict, List, Optional, Tuple
import winreg

D3D_REG_SUBKEY = r"Software\Microsoft\Direct3D"
D3D12_REG_SUBKEY = r"Software\Microsoft\Direct3D\Direct3D12"
D3D_CP_REG_SUBKEY = r"Software\Microsoft\Direct3D\ControlPanel"
ROBLOX_PLAYER_REG_SUBKEY = r"Software\ROBLOX Corporation\Environments\roblox-player"
TARGET_EXE_NAME = "RobloxPlayerBeta.exe"

# D3D_FEATURE_LEVEL_10_0 = 0xA000 (40960)
FEATURE_LEVEL_10_0 = 0xA000
APP_KEY_PATTERN = re.compile(r"^Application(\d+)$", re.IGNORECASE)
CP_APP_VALUE_PATTERN = re.compile(r"^Application(\d+)_Name$", re.IGNORECASE)

DEFAULT_LOG_PATH = Path(__file__).resolve().with_suffix(".log")

# Win32 constants for directory change monitoring
FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
FILE_NOTIFY_CHANGE_DIR_NAME = 0x00000002
FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
WATCH_FILTER = (
    FILE_NOTIFY_CHANGE_FILE_NAME
    | FILE_NOTIFY_CHANGE_DIR_NAME
    | FILE_NOTIFY_CHANGE_LAST_WRITE
)
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000
WAIT_FAILED = 0xFFFFFFFF
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


def setup_logging(log_path: Path, silent: bool) -> logging.Logger:
    """Configure file logging and optional console output."""
    logger = logging.getLogger("roblox_d3d10_enforcer")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if not silent:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def is_admin() -> bool:
    """Return True if the current process has Administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_elevated(argv: List[str]) -> int:
    """Relaunch the current script with UAC elevation."""
    script_path = str(Path(__file__).resolve())
    args = " ".join(f'"{arg}"' if " " in arg else arg for arg in [script_path, *argv])
    rc = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        args,
        str(Path(__file__).resolve().parent),
        1,
    )
    return 0 if rc > 32 else 1


def check_graphics_tools() -> Tuple[bool, bool]:
    """Check whether D3D11_3SDKLayers.dll and dxcpl.exe are present in System32."""
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    system32 = system_root / "System32"
    has_sdklayers = (system32 / "D3D11_3SDKLayers.dll").is_file()
    has_dxcpl = (system32 / "dxcpl.exe").is_file()
    return has_sdklayers, has_dxcpl


def _get_candidate_globs() -> List[str]:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    prog_files_x86 = os.environ.get("ProgramFiles(x86)", "")
    prog_files = os.environ.get("ProgramFiles", "")

    patterns: List[str] = []
    if local_app_data:
        patterns.extend(
            [
                os.path.join(local_app_data, "Roblox", "Versions", "version-*", TARGET_EXE_NAME),
                os.path.join(local_app_data, "Bloxstrap", "Versions", "*", TARGET_EXE_NAME),
                os.path.join(local_app_data, "Bloxstrap", TARGET_EXE_NAME),
                os.path.join(local_app_data, "Fishstrap", "Versions", "*", TARGET_EXE_NAME),
                os.path.join(local_app_data, "Fishstrap", TARGET_EXE_NAME),
            ]
        )
    if prog_files_x86:
        patterns.append(
            os.path.join(prog_files_x86, "Roblox", "Versions", "version-*", TARGET_EXE_NAME)
        )
    if prog_files:
        patterns.append(
            os.path.join(prog_files, "Roblox", "Versions", "version-*", TARGET_EXE_NAME)
        )
    return patterns


def _read_roblox_registry_client_exe() -> Optional[Path]:
    """Read clientExe from Roblox's HKCU environment key if it exists on disk."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            ROBLOX_PLAYER_REG_SUBKEY,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "clientExe")
            if isinstance(value, str) and value.strip():
                candidate = Path(value.strip())
                if candidate.is_file() and candidate.name.lower() == TARGET_EXE_NAME.lower():
                    return candidate
    except OSError:
        return None
    return None


def discover_roblox_executables() -> List[Path]:
    """Return all discovered RobloxPlayerBeta.exe paths sorted newest-first by mtime."""
    seen: Dict[str, Path] = {}
    for pattern in _get_candidate_globs():
        for match in glob.glob(pattern):
            path = Path(match)
            if path.is_file():
                try:
                    resolved = path.resolve()
                except OSError:
                    resolved = path
                seen[str(resolved).lower()] = resolved

    reg_exe = _read_roblox_registry_client_exe()
    if reg_exe is not None:
        try:
            resolved = reg_exe.resolve()
        except OSError:
            resolved = reg_exe
        seen[str(resolved).lower()] = resolved

    candidates = list(seen.values())
    candidates.sort(key=lambda p: (p.stat().st_mtime, str(p).lower()), reverse=True)
    return candidates


def parse_d3d_behaviors(raw: str) -> Dict[str, str]:
    """Parse a semicolon-delimited D3DBehaviors string into an ordered dict."""
    parsed: Dict[str, str] = {}
    for token in raw.split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        k, v = token.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k:
            parsed[k] = v
    return parsed


def build_d3d_behaviors(
    existing_raw: str = "",
    feature_level: int = FEATURE_LEVEL_10_0,
    disable_fl_upgrade: bool = True,
    include_disable_fl_upgrade: bool = True,
) -> str:
    """Build D3DBehaviors string preserving existing flags while enforcing limits."""
    flags = parse_d3d_behaviors(existing_raw)

    ordered_defaults: Dict[str, str] = {
        "RequireSDKLayers": flags.get("RequireSDKLayers", "0"),
        "FeatureLevelLimit": str(feature_level),
        "ForceWARP": flags.get("ForceWARP", "0"),
    }
    if include_disable_fl_upgrade:
        ordered_defaults["DisableFLUpgrade"] = "1" if disable_fl_upgrade else "0"

    merged: Dict[str, str] = {}
    for k, v in flags.items():
        if k in ordered_defaults:
            merged[k] = ordered_defaults[k]
        else:
            merged[k] = v
    for k, v in ordered_defaults.items():
        if k not in merged:
            merged[k] = v

    return ";".join(f"{k}={v}" for k, v in merged.items())


def _is_roblox_entry(name_value: str) -> bool:
    if not name_value:
        return False
    return Path(name_value.strip().strip('"')).name.lower() == TARGET_EXE_NAME.lower()


def _enum_application_entries(
    hive: int,
    base_subkey: str = D3D_REG_SUBKEY,
) -> List[Tuple[int, str, str, str]]:
    """Return list of (index, subkey_name, Name, D3DBehaviors) under a Direct3D key."""
    entries: List[Tuple[int, str, str, str]] = []
    try:
        with winreg.OpenKey(
            hive,
            base_subkey,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as base_key:
            subkey_count, _, _ = winreg.QueryInfoKey(base_key)
            for i in range(subkey_count):
                sub_name = winreg.EnumKey(base_key, i)
                match = APP_KEY_PATTERN.match(sub_name)
                if not match:
                    continue
                idx = int(match.group(1))
                app_name = ""
                behaviors = ""
                with winreg.OpenKey(
                    base_key,
                    sub_name,
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                ) as app_key:
                    try:
                        val, _ = winreg.QueryValueEx(app_key, "Name")
                        if isinstance(val, str):
                            app_name = val
                    except FileNotFoundError:
                        pass
                    try:
                        val, _ = winreg.QueryValueEx(app_key, "D3DBehaviors")
                        if isinstance(val, str):
                            behaviors = val
                    except FileNotFoundError:
                        pass
                entries.append((idx, sub_name, app_name, behaviors))
    except FileNotFoundError:
        return []

    entries.sort(key=lambda item: item[0])
    return entries


def _sync_app_subkey_group(
    hive: int,
    base_subkey: str,
    target_str: str,
    include_disable_fl_upgrade: bool,
    logger: logging.Logger,
) -> Tuple[int, str, bool]:
    """Sync Application<N> entries under `base_subkey` and return (idx, subkey, changed)."""
    entries = _enum_application_entries(hive, base_subkey)
    roblox_entries = [e for e in entries if _is_roblox_entry(e[2])]
    used_indices = {e[0] for e in entries}

    changed = False
    if roblox_entries:
        keep_idx, keep_subkey, keep_name, keep_behaviors = roblox_entries[0]
        desired_behaviors = build_d3d_behaviors(
            keep_behaviors,
            include_disable_fl_upgrade=include_disable_fl_upgrade,
        )

        if len(roblox_entries) > 1:
            with winreg.OpenKey(
                hive,
                base_subkey,
                0,
                winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
            ) as base_key:
                for _, dup_subkey, dup_name, _ in roblox_entries[1:]:
                    winreg.DeleteKey(base_key, dup_subkey)
                    logger.info(
                        "Removed duplicate/stale Roblox entry %s\\%s (%s)",
                        base_subkey,
                        dup_subkey,
                        dup_name,
                    )
                    changed = True

        if keep_name.lower() != target_str.lower() or keep_behaviors != desired_behaviors:
            with winreg.CreateKeyEx(
                hive,
                f"{base_subkey}\\{keep_subkey}",
                0,
                winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
            ) as app_key:
                winreg.SetValueEx(app_key, "Name", 0, winreg.REG_SZ, target_str)
                winreg.SetValueEx(
                    app_key, "D3DBehaviors", 0, winreg.REG_SZ, desired_behaviors
                )
            changed = True
        return keep_idx, keep_subkey, changed

    free_idx = 0
    while free_idx in used_indices:
        free_idx += 1
    new_subkey = f"Application{free_idx}"
    desired_behaviors = build_d3d_behaviors(
        "",
        include_disable_fl_upgrade=include_disable_fl_upgrade,
    )

    with winreg.CreateKeyEx(
        hive,
        f"{base_subkey}\\{new_subkey}",
        0,
        winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
    ) as app_key:
        winreg.SetValueEx(app_key, "Name", 0, winreg.REG_SZ, target_str)
        winreg.SetValueEx(app_key, "D3DBehaviors", 0, winreg.REG_SZ, desired_behaviors)

    return free_idx, new_subkey, True


def _sync_control_panel(
    hive: int,
    target_idx: int,
    target_str: str,
) -> bool:
    """Sync ControlPanel Application<N>_Name and D3DBehaviors so dxcpl.exe/d3dconfig.exe match."""
    changed = False
    with winreg.CreateKeyEx(
        hive,
        D3D_CP_REG_SUBKEY,
        0,
        winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
    ) as cp_key:
        _, val_count, _ = winreg.QueryInfoKey(cp_key)
        existing_app_vals: List[Tuple[int, str, str]] = []
        existing_behaviors = ""
        for i in range(val_count):
            vname, vdata, _ = winreg.EnumValue(cp_key, i)
            if vname == "D3DBehaviors" and isinstance(vdata, str):
                existing_behaviors = vdata
            else:
                m = CP_APP_VALUE_PATTERN.match(vname)
                if m and isinstance(vdata, str):
                    existing_app_vals.append((int(m.group(1)), vname, vdata))

        desired_val_name = f"Application{target_idx}_Name"
        found_target = False
        for _, vname, vdata in existing_app_vals:
            if _is_roblox_entry(vdata):
                if vname.lower() == desired_val_name.lower():
                    found_target = True
                    if vdata.lower() != target_str.lower():
                        winreg.SetValueEx(cp_key, vname, 0, winreg.REG_SZ, target_str)
                        changed = True
                else:
                    winreg.DeleteValue(cp_key, vname)
                    changed = True

        if not found_target:
            winreg.SetValueEx(cp_key, desired_val_name, 0, winreg.REG_SZ, target_str)
            changed = True

        desired_behaviors = build_d3d_behaviors(
            existing_behaviors,
            include_disable_fl_upgrade=True,
        )
        if existing_behaviors != desired_behaviors:
            winreg.SetValueEx(
                cp_key, "D3DBehaviors", 0, winreg.REG_SZ, desired_behaviors
            )
            changed = True

    return changed


def sync_registry(
    target_exe: Path,
    hive: int,
    logger: logging.Logger,
) -> Tuple[str, bool]:
    """Idempotently sync Direct3D, Direct3D12, and ControlPanel entries for `target_exe`."""
    # dxcpl.exe and d3dconfig.exe normalize registered paths to lowercase
    target_str = str(target_exe).lower()

    keep_idx, keep_subkey, d3d11_changed = _sync_app_subkey_group(
        hive, D3D_REG_SUBKEY, target_str, True, logger
    )
    _, _, d3d12_changed = _sync_app_subkey_group(
        hive, D3D12_REG_SUBKEY, target_str, False, logger
    )
    cp_changed = _sync_control_panel(hive, keep_idx, target_str)

    changed = d3d11_changed or d3d12_changed or cp_changed
    if changed:
        logger.info(
            "Synced %s + ControlPanel + Direct3D12 -> '%s' (FeatureLevelLimit=%d, DisableFLUpgrade=1)",
            keep_subkey,
            target_str,
            FEATURE_LEVEL_10_0,
        )
    else:
        logger.info(
            "Already up to date (%s -> %s)",
            keep_subkey,
            target_str,
        )
    return keep_subkey, changed


def revert_registry(hive: int, logger: logging.Logger) -> int:
    """Remove all RobloxPlayerBeta.exe entries from Direct3D, Direct3D12, and ControlPanel."""
    removed = 0
    for base_subkey in (D3D_REG_SUBKEY, D3D12_REG_SUBKEY):
        entries = _enum_application_entries(hive, base_subkey)
        roblox_entries = [e for e in entries if _is_roblox_entry(e[2])]
        if roblox_entries:
            with winreg.OpenKey(
                hive,
                base_subkey,
                0,
                winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
            ) as base_key:
                for _, sub_name, app_name, _ in roblox_entries:
                    winreg.DeleteKey(base_key, sub_name)
                    logger.info("Removed %s\\%s (%s)", base_subkey, sub_name, app_name)
                    removed += 1

    try:
        with winreg.OpenKey(
            hive,
            D3D_CP_REG_SUBKEY,
            0,
            winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
        ) as cp_key:
            _, val_count, _ = winreg.QueryInfoKey(cp_key)
            to_delete: List[str] = []
            existing_behaviors = ""
            for i in range(val_count):
                vname, vdata, _ = winreg.EnumValue(cp_key, i)
                if vname == "D3DBehaviors" and isinstance(vdata, str):
                    existing_behaviors = vdata
                elif CP_APP_VALUE_PATTERN.match(vname) and isinstance(vdata, str) and _is_roblox_entry(vdata):
                    to_delete.append(vname)

            for vname in to_delete:
                winreg.DeleteValue(cp_key, vname)
                logger.info("Removed %s\\%s", D3D_CP_REG_SUBKEY, vname)
                removed += 1

            if to_delete and existing_behaviors:
                reset_behaviors = build_d3d_behaviors(
                    existing_behaviors,
                    feature_level=0,
                    disable_fl_upgrade=False,
                    include_disable_fl_upgrade=True,
                )
                winreg.SetValueEx(
                    cp_key, "D3DBehaviors", 0, winreg.REG_SZ, reset_behaviors
                )
    except FileNotFoundError:
        pass

    if removed == 0:
        logger.info("No Roblox Direct3D Application entries found to remove.")
    return removed


def _get_watch_directories() -> List[Path]:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return []
    candidates = [
        Path(local_app_data) / "Roblox" / "Versions",
        Path(local_app_data) / "Bloxstrap" / "Versions",
        Path(local_app_data) / "Fishstrap" / "Versions",
    ]
    return [p for p in candidates if p.is_dir()]


def watch_and_sync(hive: int, logger: logging.Logger) -> int:
    """Block on Win32 directory change notifications and sync on Roblox updates."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FindFirstChangeNotificationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
    kernel32.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
    kernel32.FindNextChangeNotification.restype = wintypes.BOOL
    kernel32.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
    kernel32.FindCloseChangeNotification.restype = wintypes.BOOL
    kernel32.WaitForMultipleObjects.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.WaitForMultipleObjects.restype = wintypes.DWORD

    watch_dirs = _get_watch_directories()
    if not watch_dirs:
        logger.warning("No Roblox/Bootstrapper Versions directories exist to watch.")
        return 1

    handles: List[int] = []
    try:
        for d in watch_dirs:
            h = kernel32.FindFirstChangeNotificationW(str(d), True, WATCH_FILTER)
            if h == INVALID_HANDLE_VALUE or not h:
                err = ctypes.get_last_error()
                logger.warning("Failed to watch %s (WinError %d)", d, err)
            else:
                handles.append(h)
                logger.info("Watching directory for updates: %s", d)

        if not handles:
            return 1

        handle_arr = (wintypes.HANDLE * len(handles))(*handles)
        while True:
            wait_res = kernel32.WaitForMultipleObjects(
                len(handles), handle_arr, False, INFINITE
            )
            if wait_res == WAIT_FAILED:
                logger.error("WaitForMultipleObjects failed (WinError %d)", ctypes.get_last_error())
                return 1

            idx = wait_res - WAIT_OBJECT_0
            if 0 <= idx < len(handles):
                # Brief settle delay while installer finishes writing RobloxPlayerBeta.exe
                time.sleep(0.75)
                exes = discover_roblox_executables()
                if exes:
                    sync_registry(exes[0], hive, logger)
                if not kernel32.FindNextChangeNotification(handles[idx]):
                    logger.error(
                        "FindNextChangeNotification failed (WinError %d)",
                        ctypes.get_last_error(),
                    )
                    return 1
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user.")
        return 0
    finally:
        for h in handles:
            kernel32.FindCloseChangeNotification(h)


def print_status(hive: int, hive_label: str, logger: logging.Logger) -> None:
    has_sdklayers, has_dxcpl = check_graphics_tools()
    exes = discover_roblox_executables()
    entries = _enum_application_entries(hive)

    logger.info("=== Environment Status ===")
    logger.info("Admin privileges      : %s", is_admin())
    logger.info("D3D11_3SDKLayers.dll  : %s", "PRESENT" if has_sdklayers else "MISSING")
    logger.info("dxcpl.exe             : %s", "PRESENT" if has_dxcpl else "MISSING")
    if not has_sdklayers:
        logger.warning(
            "Graphics Tools is NOT installed. Run as Administrator: "
            "DISM /Online /Add-Capability /CapabilityName:Tools.Graphics.DirectX~~~~0.0.1.0"
        )

    logger.info("=== Discovered RobloxPlayerBeta.exe (%d) ===", len(exes))
    for i, exe in enumerate(exes):
        tag = " [LATEST]" if i == 0 else ""
        logger.info("  %s%s", exe, tag)

    logger.info("=== %s\\%s Entries (%d) ===", hive_label, D3D_REG_SUBKEY, len(entries))
    for _, sub_name, app_name, behaviors in entries:
        logger.info("  %s | Name=%s | D3DBehaviors=%s", sub_name, app_name, behaviors)

    try:
        with winreg.OpenKey(
            hive,
            D3D_CP_REG_SUBKEY,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as cp_key:
            _, val_count, _ = winreg.QueryInfoKey(cp_key)
            cp_apps: List[str] = []
            cp_behaviors = ""
            for i in range(val_count):
                vname, vdata, _ = winreg.EnumValue(cp_key, i)
                if vname == "D3DBehaviors" and isinstance(vdata, str):
                    cp_behaviors = vdata
                elif CP_APP_VALUE_PATTERN.match(vname) and isinstance(vdata, str):
                    cp_apps.append(f"{vname}={vdata}")
            logger.info("=== %s\\%s ===", hive_label, D3D_CP_REG_SUBKEY)
            for item in sorted(cp_apps):
                logger.info("  %s", item)
            if cp_behaviors:
                logger.info("  D3DBehaviors=%s", cp_behaviors)
    except FileNotFoundError:
        logger.info("=== %s\\%s (not present) ===", hive_label, D3D_CP_REG_SUBKEY)


def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        description="Enforce Direct3D Feature Level 10_0 for Roblox via native Windows Direct3D registry."
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Suppress console output (log file only).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print current Graphics Tools, Roblox paths, and Direct3D registry entries without modifying anything.",
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help="Remove all RobloxPlayerBeta.exe entries from the Direct3D registry.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Sync immediately and then watch Roblox Versions folders for updates.",
    )
    parser.add_argument(
        "--hive",
        choices=["hkcu", "hklm"],
        default="hkcu",
        help="Registry hive to target (default: hkcu, the native dxcpl/dxgi/d3d11 hive).",
    )
    parser.add_argument(
        "--elevate",
        action="store_true",
        help="Request UAC elevation automatically when targeting HKLM without admin rights.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help=f"Path to log file (default: {DEFAULT_LOG_PATH}).",
    )

    args = parser.parse_args(raw_argv)
    logger = setup_logging(args.log_file, args.silent)

    hive = winreg.HKEY_CURRENT_USER if args.hive == "hkcu" else winreg.HKEY_LOCAL_MACHINE
    hive_label = "HKCU" if args.hive == "hkcu" else "HKLM"

    if args.hive == "hklm" and not args.status and not is_admin():
        if args.elevate:
            logger.info("HKLM selected without admin privileges; requesting UAC elevation.")
            return relaunch_elevated(raw_argv)
        logger.error(
            "Writing to HKLM requires Administrator privileges. "
            "Use default HKCU (recommended for dxcpl/d3d11), run elevated, or pass --elevate."
        )
        return 1

    if args.status:
        print_status(hive, hive_label, logger)
        return 0

    has_sdklayers, _ = check_graphics_tools()
    if not has_sdklayers:
        logger.warning(
            "D3D11_3SDKLayers.dll is not installed in System32. "
            "Windows d3d11.dll requires the 'Graphics Tools' optional feature "
            "(DISM /Online /Add-Capability /CapabilityName:Tools.Graphics.DirectX~~~~0.0.1.0) "
            "before FeatureLevelLimit=40960 takes effect at runtime."
        )

    if args.revert:
        revert_registry(hive, logger)
        return 0

    exes = discover_roblox_executables()
    if not exes:
        logger.error("No RobloxPlayerBeta.exe installation found.")
        return 1

    sync_registry(exes[0], hive, logger)

    if args.watch:
        return watch_and_sync(hive, logger)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
