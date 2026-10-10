"""Just enough protobuf to build requests and read replies without schemas.

Messages decode to {field number: [values]}: ints for varints, bytes for
everything else. Field numbers come from the POGOProtos definitions.
"""

import collections
import struct


def uint(value):
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def integer(number, value):
    return uint(number << 3) + uint(value)


def blob(number, value):
    if isinstance(value, str):
        value = value.encode("utf8")
    return uint((number << 3) | 2) + uint(len(value)) + value


def real(number, value):
    return uint((number << 3) | 1) + struct.pack("<d", value)


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("oversized varint")


def fields(data):
    result = collections.defaultdict(list)
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("invalid field")
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            if offset + length > len(data):
                raise ValueError("truncated fixed field")
            value = data[offset : offset + length]
            offset += length
        elif wire == 2:
            length, offset = varint(data, offset)
            if offset + length > len(data):
                raise ValueError("truncated bytes")
            value = data[offset : offset + length]
            offset += length
        else:
            raise ValueError("unsupported wire type")
        result[number].append(value)
    return result


def one(message, number, default=None):
    return message.get(number, [default])[-1]


def text(message, number):
    value = one(message, number, b"")
    return value.decode("utf8") if isinstance(value, bytes) else ""


def double(message, number, default=0.0):
    """A double field's value; the reader keeps fixed fields as raw bytes."""
    value = one(message, number)
    if isinstance(value, bytes) and len(value) == 8:
        return struct.unpack("<d", value)[0]
    return default


def packed_ints(message, number):
    """A repeated int field, whether the sender packed it or not."""
    out = []
    for value in message.get(number, []):
        if isinstance(value, int):
            out.append(value)
        else:
            offset = 0
            while offset < len(value):
                item, offset = varint(value, offset)
                out.append(item)
    return out
