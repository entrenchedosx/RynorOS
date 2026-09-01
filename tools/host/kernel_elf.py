"""Bounded ELF64 symbol reader for independent QEMU evidence, no binary tools."""
import struct


def read_symbols(path, names):
    data = path.read_bytes()
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 62:
        raise ValueError('Invalid x86-64 ELF evidence file')

    def extent(offset, size):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('ELF extent outside file')
        return data[offset:offset + size]

    offset = struct.unpack_from('<Q', data, 40)[0]
    stride, count = struct.unpack_from('<HH', data, 58)
    if stride != 64 or not 0 < count <= 4096:
        raise ValueError('Unsupported ELF section table')
    sections = list(struct.iter_unpack('<IIQQQQIIQQ', extent(offset, stride * count)))
    result = {}
    for section in sections:
        if section[1] != 2:
            continue
        if section[9] != 24 or section[6] >= count or section[5] % 24:
            raise ValueError('Invalid ELF symbol table')
        strings = sections[section[6]]
        strings_data = extent(strings[4], strings[5])
        for name, _, _, _, value, size in struct.iter_unpack('<IBBHQQ', extent(section[4], section[5])):
            if name >= len(strings_data):
                raise ValueError('Invalid ELF symbol name')
            end = strings_data.find(b'\0', name)
            if end < 0:
                raise ValueError('Unterminated ELF symbol name')
            symbol = strings_data[name:end].decode('ascii')
            if symbol in names:
                if symbol in result:
                    raise ValueError('Duplicate evidence symbol: ' + symbol)
                result[symbol] = (value, size)
    if set(result) != set(names):
        raise ValueError('Missing ELF evidence symbols')
    return result
