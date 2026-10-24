"""Display metadata for a specific running pnpm sync checkpoint."""
import datetime
import re
import subprocess


def get_sync_metadata(path: str, commit: str) -> dict[str, str]:
  if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
    return {}

  try:
    body = subprocess.check_output(["git", "show", "--no-patch", "--format=%B", commit], cwd=path,
                                   stderr=subprocess.DEVNULL, encoding="utf-8")
    trailers = dict(line.split(":", 1) for line in body.splitlines() if line.startswith("IQ-Sync-") and ":" in line)
    if "IQ-Sync-Base-Branch" not in trailers:
      return {}
    synced_at = (trailers.get("IQ-Sync-Local-At") or trailers.get("IQ-Sync-At", "")).strip()
    timestamp = datetime.datetime.fromisoformat(synced_at)
    if timestamp.utcoffset() is None:
      return {}
    return {"commit": commit, "synced_at": synced_at}
  except (OSError, subprocess.CalledProcessError, ValueError):
    return {}
