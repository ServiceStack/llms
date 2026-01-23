#!/usr/bin/env python3

import os
import re
import shutil
import subprocess
import sys
from typing import Optional, Tuple


def get_screen_resolution() -> Tuple[int, int]:
    """
    Get the current screen resolution (width, height).

    Supports Linux (Wayland/Hyprland, X11), macOS, and Windows.
    Returns the primary monitor's resolution.

    Returns:
        Tuple[int, int]: (width, height) in pixels

    Raises:
        RuntimeError: If unable to determine screen resolution
    """
    if sys.platform == "linux":
        return _get_linux_resolution()
    elif sys.platform == "darwin":
        return _get_macos_resolution()
    elif sys.platform == "win32":
        return _get_windows_resolution()
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")


def get_display_num() -> int:
    """
    Get the display number for the current session.

    On Linux (X11): Returns the X display number from $DISPLAY (e.g., :0 -> 0)
    On Linux (Wayland): Returns the Wayland display number from $WAYLAND_DISPLAY (e.g., wayland-0 -> 0)
    On macOS: Returns the display ID of the main display
    On Windows: Returns the index of the primary monitor (typically 0)

    Returns:
        int: The display number

    Raises:
        RuntimeError: If unable to determine display number
    """
    if sys.platform == "linux":
        return _get_linux_display_num()
    elif sys.platform == "darwin":
        return _get_macos_display_num()
    elif sys.platform == "win32":
        return _get_windows_display_num()
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")


def _get_linux_display_num() -> int:
    """Get display number on Linux (X11 or Wayland)."""

    # Try X11 DISPLAY environment variable first
    display = os.environ.get("DISPLAY")
    if display:
        # DISPLAY format: [hostname]:displaynumber[.screennumber]
        # Examples: :0, :1, localhost:0, :0.0
        match = re.search(r":(\d+)", display)
        if match:
            return int(match.group(1))

    # Try Wayland display
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    if wayland_display:
        # WAYLAND_DISPLAY format: wayland-N or just a socket name
        # Examples: wayland-0, wayland-1
        match = re.search(r"wayland-(\d+)", wayland_display)
        if match:
            return int(match.group(1))
        # If it's just "wayland-0" style, try to extract number
        match = re.search(r"(\d+)", wayland_display)
        if match:
            return int(match.group(1))
        # Default to 0 if we have a Wayland display but can't parse number
        return 0

    # Try Hyprland-specific: get focused monitor ID
    if shutil.which("hyprctl"):
        try:
            result = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                import json

                monitors = json.loads(result.stdout)
                if monitors:
                    # Return focused monitor ID or first monitor's ID
                    monitor = next((m for m in monitors if m.get("focused")), monitors[0])
                    return monitor.get("id", 0)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
            pass

    raise RuntimeError("Could not determine display number. Neither DISPLAY nor WAYLAND_DISPLAY is set.")


def _get_macos_display_num() -> int:
    """Get display number on macOS."""

    # Try using CoreGraphics via ctypes
    try:
        import ctypes
        import ctypes.util

        cg_path = ctypes.util.find_library("CoreGraphics")
        if cg_path:
            cg = ctypes.CDLL(cg_path)
            # CGMainDisplayID returns the display ID of the main display
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            return cg.CGMainDisplayID()
    except (OSError, AttributeError):
        pass

    # Try using AppKit
    try:
        from AppKit import NSScreen

        main_screen = NSScreen.mainScreen()
        # Get the display ID from the screen's deviceDescription
        device_desc = main_screen.deviceDescription()
        display_id = device_desc.get("NSScreenNumber", 0)
        return int(display_id)
    except ImportError:
        pass

    # Try using system_profiler
    try:
        result = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            # Look for Display ID or just return 0 for main display
            match = re.search(r"Display ID:\s*(\d+)", result.stdout)
            if match:
                return int(match.group(1))
            # If we found display info but no ID, assume main display is 0
            if "Resolution:" in result.stdout:
                return 0
    except subprocess.TimeoutExpired:
        pass

    # Default to 0 for main display
    return 0


def _get_windows_display_num() -> int:
    """Get display number on Windows."""

    # Method 1: Try ctypes to enumerate monitors
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        # Get the primary monitor handle
        # MonitorFromPoint with MONITOR_DEFAULTTOPRIMARY (0x00000001)
        primary_monitor = user32.MonitorFromPoint(
            ctypes.wintypes.POINT(0, 0),
            1,  # MONITOR_DEFAULTTOPRIMARY
        )

        # Enumerate all monitors to find the index of the primary
        monitors = []

        def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            monitors.append(hMonitor)
            return True

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.wintypes.HMONITOR,
            ctypes.wintypes.HDC,
            ctypes.POINTER(ctypes.wintypes.RECT),
            ctypes.wintypes.LPARAM,
        )

        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)

        # Find the index of the primary monitor
        for i, mon in enumerate(monitors):
            if mon == primary_monitor:
                return i

        # Primary not found in list, return 0
        return 0

    except (AttributeError, OSError):
        pass

    # Method 2: Try win32api
    try:
        import win32api

        # Get all monitors
        monitors = win32api.EnumDisplayMonitors()
        # Find primary (usually the first one, or check flags)
        for i, (handle, dc, rect) in enumerate(monitors):
            info = win32api.GetMonitorInfo(handle)
            if info.get("Flags", 0) & 1:  # MONITORINFOF_PRIMARY
                return i
        return 0
    except ImportError:
        pass

    # Method 3: PowerShell to get monitor index
    try:
        ps_script = """
        Add-Type -AssemblyName System.Windows.Forms
        $screens = [System.Windows.Forms.Screen]::AllScreens
        for ($i = 0; $i -lt $screens.Count; $i++) {
            if ($screens[$i].Primary) {
                Write-Output $i
                break
            }
        }
        """
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass

    # Default to 0 (primary monitor)
    return 0


def _get_linux_resolution() -> Tuple[int, int]:
    """Get resolution on Linux, trying Wayland first, then X11."""

    # Try Hyprland first
    resolution = _try_hyprctl()
    if resolution:
        return resolution

    # Try wlr-randr (generic Wayland)
    resolution = _try_wlr_randr()
    if resolution:
        return resolution

    # Try xrandr (X11)
    resolution = _try_xrandr()
    if resolution:
        return resolution

    # Try xdpyinfo (X11 fallback)
    resolution = _try_xdpyinfo()
    if resolution:
        return resolution

    raise RuntimeError("Could not determine screen resolution. No supported display server found.")


def _try_hyprctl() -> Optional[Tuple[int, int]]:
    """Try getting resolution via hyprctl (Hyprland)."""
    if not shutil.which("hyprctl"):
        return None

    try:
        result = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            import json

            monitors = json.loads(result.stdout)
            if monitors:
                # Get the focused monitor or first one
                monitor = next((m for m in monitors if m.get("focused")), monitors[0])
                return (monitor["width"], monitor["height"])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
        pass
    return None


def _try_wlr_randr() -> Optional[Tuple[int, int]]:
    """Try getting resolution via wlr-randr (Wayland)."""
    if not shutil.which("wlr-randr"):
        return None

    try:
        result = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Parse output like: "  3840x2160 px, 60.000000 Hz (current)"
            match = re.search(r"(\d+)x(\d+)\s+px.*current", result.stdout)
            if match:
                return (int(match.group(1)), int(match.group(2)))
    except subprocess.TimeoutExpired:
        pass
    return None


def _try_xrandr() -> Optional[Tuple[int, int]]:
    """Try getting resolution via xrandr (X11)."""
    if not shutil.which("xrandr"):
        return None

    try:
        result = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Look for current resolution marked with *
            match = re.search(r"(\d+)x(\d+).*\*", result.stdout)
            if match:
                return (int(match.group(1)), int(match.group(2)))
    except subprocess.TimeoutExpired:
        pass
    return None


def _try_xdpyinfo() -> Optional[Tuple[int, int]]:
    """Try getting resolution via xdpyinfo (X11)."""
    if not shutil.which("xdpyinfo"):
        return None

    try:
        result = subprocess.run(["xdpyinfo"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            match = re.search(r"dimensions:\s+(\d+)x(\d+)", result.stdout)
            if match:
                return (int(match.group(1)), int(match.group(2)))
    except subprocess.TimeoutExpired:
        pass
    return None


def _get_macos_resolution() -> Tuple[int, int]:
    """Get resolution on macOS using system_profiler."""
    try:
        result = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            # Look for "Resolution: 2560 x 1440" or similar
            match = re.search(r"Resolution:\s+(\d+)\s*x\s*(\d+)", result.stdout)
            if match:
                return (int(match.group(1)), int(match.group(2)))
    except subprocess.TimeoutExpired:
        pass

    # Fallback: try using AppKit via Python (if available)
    try:
        from AppKit import NSScreen

        frame = NSScreen.mainScreen().frame()
        return (int(frame.size.width), int(frame.size.height))
    except ImportError:
        pass

    raise RuntimeError("Could not determine screen resolution on macOS")


def _get_windows_resolution() -> Tuple[int, int]:
    """Get resolution on Windows."""

    # Method 1: Try ctypes (no external dependencies)
    resolution = _try_windows_ctypes()
    if resolution:
        return resolution

    # Method 2: Try win32api (pywin32)
    resolution = _try_windows_win32api()
    if resolution:
        return resolution

    # Method 3: Try PowerShell
    resolution = _try_windows_powershell()
    if resolution:
        return resolution

    # Method 4: Try wmic (deprecated but still works on older systems)
    resolution = _try_windows_wmic()
    if resolution:
        return resolution

    raise RuntimeError("Could not determine screen resolution on Windows")


def _try_windows_ctypes() -> Optional[Tuple[int, int]]:
    """Try getting resolution via ctypes (built-in)."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()  # Handle DPI scaling
        width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        if width > 0 and height > 0:
            return (width, height)
    except (AttributeError, OSError):
        pass
    return None


def _try_windows_win32api() -> Optional[Tuple[int, int]]:
    """Try getting resolution via win32api (pywin32)."""
    try:
        import win32api

        width = win32api.GetSystemMetrics(0)
        height = win32api.GetSystemMetrics(1)
        if width > 0 and height > 0:
            return (width, height)
    except ImportError:
        pass
    return None


def _try_windows_powershell() -> Optional[Tuple[int, int]]:
    """Try getting resolution via PowerShell."""
    try:
        # Using Add-Type to access System.Windows.Forms
        ps_script = """
        Add-Type -AssemblyName System.Windows.Forms
        $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        "$($screen.Width)x$($screen.Height)"
        """
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            match = re.match(r"(\d+)x(\d+)", result.stdout.strip())
            if match:
                return (int(match.group(1)), int(match.group(2)))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _try_windows_wmic() -> Optional[Tuple[int, int]]:
    """Try getting resolution via wmic (legacy, but works on older Windows)."""
    try:
        result = subprocess.run(
            ["wmic", "path", "Win32_VideoController", "get", "CurrentHorizontalResolution,CurrentVerticalResolution"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            for line in lines[1:]:  # Skip header
                match = re.match(r"(\d+)\s+(\d+)", line.strip())
                if match:
                    return (int(match.group(1)), int(match.group(2)))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


if __name__ == "__main__":
    try:
        width, height = get_screen_resolution()
        print(f"Screen resolution: {width}x{height}")

        display_num = get_display_num()
        print(f"Display number: {display_num}")
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
