"""Generate a VAPID key pair for Web Push.

    python vapid.py

VAPID ("Voluntary Application Server Identification") is how a push service
— Google's, Mozilla's, Apple's — knows a push is really from you. You
generate one key pair, once, and keep it forever: the public key is baked
into every browser subscription, so **rotating the private key invalidates
every existing subscription** and every student has to re-enable
notifications. Generate it once, store it, do not regenerate it casually.

Output is written for environment variables rather than files. The previous
version of this script printed a multi-line PEM, which is unusable as an
env var on most hosts — the newlines are either rejected or silently
mangled, and the failure shows up later as a signature error on every push.
The private key below is a single-line base64url string, which is what
pywebpush accepts and what survives a copy-paste into a dashboard.
"""

import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid


def b64(data: bytes) -> str:
    """base64url with padding stripped, as the Web Push spec requires."""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def main() -> None:
    vapid = Vapid()
    vapid.generate_keys()

    public = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    # The raw 32-byte scalar, not PKCS#8. This is the form pywebpush wants
    # when the key arrives as a string rather than a file path.
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")

    # ASCII only. A Windows console defaults to cp1252 and raises
    # UnicodeEncodeError on the first non-ASCII character, which would make
    # this script crash before printing the keys it just generated.
    print("Add these to your environment (Railway > Variables, or .env locally).")
    print("Keep VAPID_PRIVATE_KEY secret — treat it like a password.\n")
    print(f"VAPID_PUBLIC_KEY={b64(public)}")
    print(f"VAPID_PRIVATE_KEY={b64(private)}")
    print("VAPID_EMAIL=you@yourdomain.com")
    print(
        "\nVAPID_EMAIL must be an address a push service can contact you at;"
        "\nit is sent as the 'sub' claim and some services reject pushes without it."
    )


if __name__ == "__main__":
    main()
