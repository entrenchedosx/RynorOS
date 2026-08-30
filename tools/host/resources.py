"""Deterministic host packaging of the original OS icon; no guest renderer."""

import hashlib
import json
from pathlib import Path
import struct
import zipfile

ICON_PATH = "assets/branding/icon.png"
ICON_SHA256 = "beac0bc23e59cdad3ddbbddcee9cb7d9444c90c15654a4f9c520d7ce61c6b353"


def read_icon(root: Path) -> tuple[bytes, dict]:
    data = (root / ICON_PATH).read_bytes()
    if (len(data) < 33 or data[:16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" or
            data[24:29] != bytes((8, 6, 0, 0, 0))):
        raise ValueError("Official icon must retain its original 8-bit RGBA PNG header")
    width, height = struct.unpack_from(">II", data, 16)
    digest = hashlib.sha256(data).hexdigest()
    if (width, height) != (1254, 1254) or digest != ICON_SHA256:
        raise ValueError("Official icon dimensions/hash differ from the canonical original")
    return data, {"path": ICON_PATH, "role": "official-os-icon", "media_type": "image/png",
                  "width": width, "height": height, "bytes": len(data), "sha256": digest}


def package_resources(root: Path, output: Path) -> None:
    data, entry = read_icon(root)
    manifest = (json.dumps({"os": "RynorOS", "assets": [entry]}, sort_keys=True, indent=2) + "\n").encode()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in ((ICON_PATH, data), ("manifest.json", manifest)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
