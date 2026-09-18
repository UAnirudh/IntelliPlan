"""Build the IntelliPlan extension zip with forward-slash paths.

Windows PowerShell's Compress-Archive writes Windows-style backslashes
into the ZIP central directory, which violates the ZIP spec and causes
Chromium-based browsers (Chrome / Edge / Brave) to fail to load icons
referenced from manifest.json. Python's zipfile module always writes
forward slashes, which is what the spec and every browser expects.
"""
import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# (path inside zip → path on disk relative to HERE)
ENTRIES = [
    ("manifest.json",        "manifest.json"),
    ("popup.html",           "popup.html"),
    ("popup.js",             "popup.js"),
    ("background.js",        "background.js"),
    ("content.js",           "content.js"),
    ("options.html",         "options.html"),
    ("icons/icon-16.png",    "icons/icon-16.png"),
    ("icons/icon-32.png",    "icons/icon-32.png"),
    ("icons/icon-48.png",    "icons/icon-48.png"),
    ("icons/icon-128.png",   "icons/icon-128.png"),
]


def _manifest():
    with open(os.path.join(HERE, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _content_scripts(manifest):
    """Every content script the manifest names, so the zip cannot omit one.

    The list used to be hand-maintained, which is fine until the manifest
    grows an entry -- the scraper modules were exactly that case. Chrome
    rejects a package whose manifest references a file it does not contain,
    so a stale list here breaks the upload rather than degrading quietly.
    """
    paths = []
    for block in manifest.get("content_scripts", []):
        paths.extend(block.get("js", []))
    return paths


def main():
    manifest = _manifest()
    version = manifest.get("version", "0.0.0")
    out_zip = os.path.join(HERE, f"IntelliPlan-Extension-v{version}.zip")

    entries = list(ENTRIES)
    known = {disk for _, disk in entries}
    for path in _content_scripts(manifest):
        if path not in known:
            entries.append((path, path))
            known.add(path)

    if os.path.exists(out_zip):
        os.remove(out_zip)

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for archive_name, disk_path in entries:
            full = os.path.join(HERE, disk_path)
            if not os.path.exists(full):
                raise FileNotFoundError(f"Missing source file: {full}")
            zf.write(full, archive_name)

    # Verify
    with zipfile.ZipFile(out_zip, "r") as zf:
        names = zf.namelist()
        for n in names:
            assert "\\" not in n, f"Backslash in archive entry: {n!r}"
            print(n)
        missing = [p for p in _content_scripts(manifest) if p not in names]
        assert not missing, f"Manifest references files not in the zip: {missing}"

    size_kb = os.path.getsize(out_zip) / 1024
    print(f"\nWrote {out_zip} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
