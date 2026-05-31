"""Build the IntelliPlan extension zip with forward-slash paths.

Windows PowerShell's Compress-Archive writes Windows-style backslashes
into the ZIP central directory, which violates the ZIP spec and causes
Chromium-based browsers (Chrome / Edge / Brave) to fail to load icons
referenced from manifest.json. Python's zipfile module always writes
forward slashes, which is what the spec and every browser expects.
"""
import os
import zipfile

HERE      = os.path.dirname(os.path.abspath(__file__))
OUT_ZIP   = os.path.join(HERE, "IntelliPlan-Extension-v1.3.zip")

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


def main():
    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)

    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for archive_name, disk_path in ENTRIES:
            full = os.path.join(HERE, disk_path)
            if not os.path.exists(full):
                raise FileNotFoundError(f"Missing source file: {full}")
            zf.write(full, archive_name)

    # Verify
    with zipfile.ZipFile(OUT_ZIP, "r") as zf:
        for n in zf.namelist():
            assert "\\" not in n, f"Backslash in archive entry: {n!r}"
            print(n)

    size_kb = os.path.getsize(OUT_ZIP) / 1024
    print(f"\nWrote {OUT_ZIP} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
