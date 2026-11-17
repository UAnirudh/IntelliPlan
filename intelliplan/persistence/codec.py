"""Lossless storage of large text fields without oversized connector payloads."""
from __future__ import annotations

import zlib

from bson import Binary

ENCODING_KEY = "_ip_encoding"
ENCODING = "zlib-utf8-v1"
MAX_TEXT_BYTES = 16 * 1024 * 1024


def pack_document(document: dict) -> dict:
    if ENCODING_KEY in document:
        raise ValueError("Document contains a reserved storage field")
    packed = dict(document)
    encodings = {}
    for name, value in document.items():
        if not isinstance(value, str) or name == "_id":
            continue
        encoded = value.encode("utf-8")
        if len(encoded) <= 8192:
            continue
        if len(encoded) > MAX_TEXT_BYTES:
            raise ValueError("Text exceeds the supported document size")
        compressed = zlib.compress(encoded)
        if len(compressed) * 2 < len(encoded):
            packed[name] = Binary(compressed, subtype=128)
            encodings[name] = ENCODING
    if encodings:
        packed[ENCODING_KEY] = encodings
    return packed


def unpack_document(document: dict) -> dict:
    unpacked = dict(document)
    encodings = unpacked.pop(ENCODING_KEY, {})
    if not isinstance(encodings, dict):
        raise ValueError("Invalid storage encoding metadata")
    for name, encoding in encodings.items():
        value = unpacked.get(name)
        if encoding != ENCODING or not isinstance(value, Binary) or value.subtype != 128:
            raise ValueError("Unsupported storage encoding")
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(bytes(value), MAX_TEXT_BYTES + 1)
        if len(decoded) > MAX_TEXT_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError("Compressed field is invalid or exceeds its size limit")
        unpacked[name] = decoded.decode("utf-8")
    return unpacked


def update_fields(values: dict) -> dict:
    """Keep per-field encoding metadata coherent in the same atomic update."""
    packed = pack_document(values)
    encodings = packed.pop(ENCODING_KEY, {})
    unset = {}
    for field in values:
        if field in encodings:
            packed[f"{ENCODING_KEY}.{field}"] = encodings[field]
        else:
            unset[f"{ENCODING_KEY}.{field}"] = ""
    result = {"$set": packed}
    if unset:
        result["$unset"] = unset
    return result
