"""Lossless JSON text updates for migrated extensible payloads."""
from decimal import Decimal
import json


def loads_exact(raw):
    def invalid_number(value):
        raise ValueError('JSON contains a non-JSON number')
    return json.loads(raw, parse_float=Decimal, parse_constant=invalid_number,
                      parse_int=lambda value: int(value) if len(value) <= 20 else Decimal(value))


def dumps_exact(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('JSON contains a non-finite number')
        return str(value)
    if isinstance(value, list):
        return '[' + ','.join(dumps_exact(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':' + dumps_exact(item)
                              for key, item in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
