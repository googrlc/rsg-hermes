#!/usr/bin/env python3
"""
Archive a committed commission document to Nextcloud over WebDAV, returning the
stored path (which goes into commission_statements.archive_url / the batch row).

Spec §9 layout:
  statements  -> Commission Statements/{Carrier}/{YYYY}/{YYYY-MM-DD}_{filename}
  rate sheets -> Commission Rate Sheets/{Carrier}/{YYYY}/{YYYY-MM-DD}_{filename}

Creds (env, or fetched inline from 1Password if unset):
  NEXTCLOUD_URL           e.g. https://nextcloud.example/  (WebDAV base is derived)
  NEXTCLOUD_USER
  NEXTCLOUD_APP_PASSWORD
Current server (2026-07-08): https://nextcloud-x6wle-u69864.vm.elestio.app  (Nextcloud
33; the old nextcloud-enwyl host is dead). Basic auth with the stored LOGIN passwords
returns 401 — WebDAV needs a Nextcloud **app-password** (Settings → Security → Create
new app password). Set NEXTCLOUD_URL/USER/APP_PASSWORD once that token exists.

Usage:
  nextcloud_archive.py --file "/path/May 2026.csv" --carrier "NEXT INS US CO" \
      --date 2026-05-31 --kind statement
Prints the archive path on success; exits non-zero on failure (never deletes source).
"""
import argparse, os, subprocess, sys
from datetime import date
from urllib.parse import quote

try:
    import requests
except ImportError:
    sys.exit("requests not installed: pip install requests")


def _op(field: str) -> str:
    try:
        out = subprocess.run(
            ["op", "read", f"op://rsg_infrastructure/Nextcloud WebDAV/{field}"],
            capture_output=True, text=True, timeout=20,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def creds():
    url = (os.environ.get("NEXTCLOUD_URL") or _op("url")).rstrip("/")
    user = os.environ.get("NEXTCLOUD_USER") or _op("username")
    pw = os.environ.get("NEXTCLOUD_APP_PASSWORD") or _op("password")
    if not url:
        sys.exit("Nextcloud URL unavailable (env NEXTCLOUD_URL or 1Password 'Nextcloud WebDAV'/url).")
    if not user or not pw:
        sys.exit("Nextcloud user/password unavailable (env or 1Password 'Nextcloud WebDAV').")
    return url, user, pw


def dav_base(url: str, user: str) -> str:
    return f"{url}/remote.php/dav/files/{user}"


def ensure_dirs(session, base, folders):
    """MKCOL each path segment (idempotent — 405 = already exists)."""
    path = base
    for seg in folders:
        path = f"{path}/{quote(seg)}"
        r = session.request("MKCOL", path, timeout=30)
        if r.status_code not in (201, 405, 301):
            raise RuntimeError(f"MKCOL {path} -> {r.status_code} {r.text[:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--carrier", required=True)
    ap.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD statement date")
    ap.add_argument("--kind", choices=["statement", "rate_sheet"], default="statement")
    a = ap.parse_args()

    if not os.path.isfile(a.file):
        sys.exit(f"file not found: {a.file}")

    url, user, pw = creds()
    base = dav_base(url, user)
    root = "Commission Statements" if a.kind == "statement" else "Commission Rate Sheets"
    year = a.date[:4]
    fname = f"{a.date}_{os.path.basename(a.file)}"
    folders = [root, a.carrier, year]

    s = requests.Session()
    s.auth = (user, pw)
    ensure_dirs(s, base, folders)

    remote = f"{base}/" + "/".join(quote(x) for x in folders) + "/" + quote(fname)
    with open(a.file, "rb") as fh:
        r = s.put(remote, data=fh, timeout=120)
    if r.status_code not in (200, 201, 204):
        sys.exit(f"PUT {remote} -> {r.status_code} {r.text[:200]}")

    # Store the human path (not the full WebDAV URL) as archive_url.
    print("/".join(folders) + "/" + fname)


if __name__ == "__main__":
    main()
