"""
Plugin Installer

Scans common locations for plugin files, installs them to the correct
audio plugin directories, removes macOS quarantine, and updates plugins.json.
"""

import asyncio
import platform
import shutil
import subprocess
from pathlib import Path

import plugin_scanner

# Plugin file extensions → destination directory
PLUGIN_DIRS = {
    ".vst3": Path.home() / "Library" / "Audio" / "Plug-Ins" / "VST3",
    ".vst":  Path.home() / "Library" / "Audio" / "Plug-Ins" / "VST",
    ".component": Path.home() / "Library" / "Audio" / "Plug-Ins" / "Components",
}

# Locations to scan for uninstalled plugins
SEARCH_DIRS = [
    Path.home() / "Downloads",
    Path.home() / "Desktop",
]


def _remove_quarantine(path: Path):
    """Remove Apple quarantine flag recursively."""
    subprocess.run(
        ["xattr", "-dr", "com.apple.quarantine", str(path)],
        capture_output=True,
    )


def _is_already_installed(plugin_path: Path) -> bool:
    """Check if a plugin with the same name is already installed."""
    suffix = plugin_path.suffix.lower()
    dest_dir = PLUGIN_DIRS.get(suffix)
    if dest_dir is None:
        return False
    return (dest_dir / plugin_path.name).exists()


def find_uninstalled_plugins() -> list[Path]:
    """Scan search locations for plugin files that aren't installed yet."""
    found = []
    for search_dir in SEARCH_DIRS:
        if not search_dir.exists():
            continue
        for suffix in PLUGIN_DIRS:
            # Plugins can be a single file or a bundle (directory with .vst3 etc.)
            for p in search_dir.rglob(f"*{suffix}"):
                # Skip nested plugins (e.g. inside an already-found bundle)
                if any(parent.suffix in PLUGIN_DIRS for parent in p.parents
                       if parent != search_dir):
                    continue
                if not _is_already_installed(p):
                    found.append(p)
    return found


def install_plugin(plugin_path: Path) -> dict:
    """
    Install a single plugin file/bundle.

    Returns a result dict with ok, name, and destination.
    """
    suffix = plugin_path.suffix.lower()
    dest_dir = PLUGIN_DIRS.get(suffix)

    if dest_dir is None:
        return {"ok": False, "name": plugin_path.name, "error": f"Unknown plugin type: {suffix}"}

    if not plugin_path.exists():
        return {"ok": False, "name": plugin_path.name, "error": "File not found"}

    try:
        # Remove quarantine before copying
        _remove_quarantine(plugin_path)

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / plugin_path.name

        if plugin_path.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(plugin_path, dest)
        else:
            shutil.copy2(plugin_path, dest)

        # Remove quarantine on destination too
        _remove_quarantine(dest)

        return {"ok": True, "name": plugin_path.name, "destination": str(dest)}

    except Exception as e:
        return {"ok": False, "name": plugin_path.name, "error": str(e)}


async def trigger_reaper_rescan() -> bool:
    """Tell REAPER to rescan for new VST plugins via the bridge."""
    try:
        from reaper_tools import TOOLS
        # REAPER action 40729: Perform full re-scan for all plug-ins
        result = await TOOLS["run_action"](40729)
        return result.get("ok", False)
    except Exception:
        return False


def install_all(plugin_paths: list[Path] = None, rescan: bool = True) -> dict:
    """
    Install all provided plugins (or auto-discover if none given).

    Args:
        plugin_paths: List of plugin paths to install. If None, auto-discovers.
        rescan: Whether to trigger REAPER rescan and update plugins.json after.

    Returns:
        Summary dict with installed/failed counts and details.
    """
    if plugin_paths is None:
        plugin_paths = find_uninstalled_plugins()

    if not plugin_paths:
        return {
            "ok": True,
            "message": "No new plugins found to install.",
            "installed": [],
            "failed": [],
        }

    installed = []
    failed = []

    for path in plugin_paths:
        result = install_plugin(path)
        if result["ok"]:
            installed.append(result["name"])
        else:
            failed.append({"name": result["name"], "error": result.get("error", "")})

    # Rescan REAPER and update plugins.json
    if installed and rescan:
        try:
            asyncio.run(trigger_reaper_rescan())
        except Exception:
            pass  # REAPER might not be running, that's ok

        # Small delay to let REAPER finish scanning
        import time
        time.sleep(2)

        try:
            plugin_scanner.scan_plugins()
        except Exception:
            pass

    return {
        "ok": True,
        "installed": installed,
        "failed": failed,
        "installed_count": len(installed),
        "failed_count": len(failed),
        "rescan_done": bool(installed and rescan),
    }


if __name__ == "__main__":
    print("Scanning for uninstalled plugins...")
    plugins = find_uninstalled_plugins()

    if not plugins:
        print("No new plugins found in Downloads or Desktop.")
    else:
        print(f"Found {len(plugins)} plugin(s) to install:")
        for p in plugins:
            print(f"  {p.name}")
        print()
        result = install_all(plugins)
        print(f"Installed: {result['installed_count']}, Failed: {result['failed_count']}")
        for name in result["installed"]:
            print(f"  ✓ {name}")
        for item in result["failed"]:
            print(f"  ✗ {item['name']}: {item['error']}")
