"""Check the EXE's primary icon against the original ICO image payloads."""

import argparse
import hashlib
import json
from pathlib import Path
import struct

import pefile


def verify(executable: Path, icon_path: Path) -> dict:
    source = icon_path.read_bytes()
    reserved, icon_type, count = struct.unpack_from("<HHH", source)
    if reserved != 0 or icon_type != 1 or not count:
        raise ValueError("Invalid source ICO")
    expected = []
    for index in range(count):
        size, offset = struct.unpack_from("<II", source, 6 + 16 * index + 8)
        expected.append(hashlib.sha256(source[offset:offset + size]).hexdigest())
    pe = pefile.PE(str(executable))
    images = {}
    groups = []
    for category in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if category.id == 3:  # RT_ICON
            for resource in category.directory.entries:
                data = resource.directory.entries[0].data.struct
                images[resource.id] = hashlib.sha256(pe.get_data(data.OffsetToData, data.Size)).hexdigest()
        elif category.id == 14:  # RT_GROUP_ICON, ordinal 1 is the shell/tray default.
            groups = category.directory.entries
    if not groups:
        raise ValueError("EXE has no icon group")
    main = groups[0]
    data = main.directory.entries[0].data.struct
    group = pe.get_data(data.OffsetToData, data.Size)
    group_count = struct.unpack_from("<H", group, 4)[0]
    image_ids = [struct.unpack_from("<H", group, 6 + 14 * i + 12)[0] for i in range(group_count)]
    if [images.get(image_id) for image_id in image_ids] != expected:
        raise ValueError("EXE primary icon does not match the project ICO")
    return {"passed": True, "primary_icon_group": main.id, "source_images": count,
            "source_ico_sha256": hashlib.sha256(source).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("icon", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.executable, args.icon)))
