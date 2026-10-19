"""Decode native records without loading optional BSON packages on SQL paths."""


def unpack_record(raw):
    if '_ip_encoding' not in raw:
        return dict(raw)
    from intelliplan.persistence.codec import unpack_document
    return unpack_document(raw)
