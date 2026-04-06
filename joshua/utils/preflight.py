"""Pre-flight checks before each sprint cycle."""

import logging
import os
import subprocess
import time

log = logging.getLogger("joshua")


def check_memory(min_gb: float = 1.0) -> bool:
    """Check if available system memory exceeds min_gb. Returns True if OK."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    avail_gb = kb / (1024 * 1024)
                    if avail_gb < min_gb:
                        log.warning(
                            f"Low memory: {avail_gb:.1f} GB available (min: {min_gb} GB)")
                        return False
                    return True
    except (OSError, ValueError):
        pass
    # Fallback: macOS / psutil-free approach
    try:
        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            free_pages = 0
            for line in result.stdout.splitlines():
                if "Pages free:" in line or "Pages inactive:" in line:
                    free_pages += int(line.split(":")[1].strip().rstrip("."))
            avail_gb = (free_pages * 4096) / (1024**3)
            if avail_gb < min_gb:
                log.warning(
                    f"Low memory: {avail_gb:.1f} GB available (min: {min_gb} GB)")
                return False
            return True
    except (OSError, ValueError):
        pass
    return True  # Can't check — assume OK


def wait_for_memory(min_gb: float, timeout: int = 300, poll: int = 15) -> bool:
    """Wait until min_gb memory is available or timeout. Returns True if OK."""
    if check_memory(min_gb):
        return True
    log.info(f"Waiting for {min_gb} GB free memory (timeout {timeout}s)...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll)
        if check_memory(min_gb):
            log.info("Memory OK — continuing")
            return True
    log.warning(f"Memory still below {min_gb} GB after {timeout}s")
    return False


def check_disk_space(min_gb: float = 2.0) -> bool:
    """Check if available disk space exceeds min_gb. Returns True if OK."""
    try:
        stat = os.statvfs("/")
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        if free_gb < min_gb:
            log.warning(f"Low disk space: {free_gb:.1f} GB (min: {min_gb} GB)")
            return False
        return True
    except (OSError, AttributeError):
        # statvfs not available on all platforms (e.g. Windows)
        return True


def docker_cleanup() -> bool:
    """Prune Docker build cache, stopped containers, dangling images.

    Returns True if cleanup ran successfully.
    """
    cmds = [
        ["docker", "builder", "prune", "--all", "-f"],
        ["docker", "container", "prune", "-f"],
        ["docker", "image", "prune", "-f"],
    ]
    ok = True
    for cmd in cmds:
        try:
            subprocess.run(cmd, capture_output=True, timeout=120)
        except Exception as e:
            log.warning(f"Docker cleanup failed: {' '.join(cmd)} — {e}")
            ok = False
    return ok


def run_preflight(config: dict) -> list[str]:
    """Run configured pre-flight checks.

    Returns list of warning messages (empty = all OK).
    """
    warnings = []
    preflight = config.get("preflight", config.get("sprint", {}).get("preflight", {}))
    if not preflight:
        return warnings

    min_gb = preflight.get("min_disk_gb", preflight.get("disk_min_gb", 0))
    if min_gb and not check_disk_space(min_gb):
        if preflight.get("docker_cleanup", False):
            log.info("Running Docker cleanup to free disk space...")
            docker_cleanup()
            if not check_disk_space(min_gb):
                warnings.append(f"Disk space below {min_gb} GB even after cleanup")
        else:
            warnings.append(f"Disk space below {min_gb} GB")

    # Memory check
    min_mem = preflight.get("min_memory_gb", 0)
    if min_mem:
        mem_timeout = preflight.get("memory_wait_timeout", 0)
        if mem_timeout:
            if not wait_for_memory(min_mem, timeout=mem_timeout):
                warnings.append(
                    f"Memory below {min_mem} GB after waiting {mem_timeout}s")
        elif not check_memory(min_mem):
            warnings.append(f"Memory below {min_mem} GB")

    return warnings
