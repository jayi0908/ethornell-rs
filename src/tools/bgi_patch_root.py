#!/usr/bin/env python3
"""Build a runnable game root for a BGI fan patch.

Chinese patches for BGI games usually ship either translated PackFile entries
or an Enigma Virtual Box executable whose virtual files override the original
archives.  This helper hardlinks a clean game root and, when --override is
given, replaces PackFile entries whose basename exists in that directory.

Example (DaitosyokanNoHitsujikai, after `evbunpack --legacy-fs --ignore-pe`):
  bgi_patch_root.py --game ~/games/大图书馆的牧羊人 \
      --out /tmp/bgi_run/lib --override /tmp/evb_files \
      --add-to sysgrp.arc --exclude backup

Example (Eustia, whose translated archives are already in the game root):
  bgi_patch_root.py --game ~/games/秽翼的尤斯蒂娅 \
      --out /tmp/bgi_run/eustia --exclude backup --exclude 备份

Run the result with:
  ETHORNELL_TEXT_ENCODING=gbk ETHORNELL_FONT=/path/Daito_Hei.ttf \
      ethornell run --game /tmp/bgi_run/lib
"""
import argparse
import os
import shutil
import struct
from pathlib import Path

PACK_MAGIC = b"PackFile    "


def u32(buf, offset):
    return struct.unpack_from("<I", buf, offset)[0]


def read_packfile(path):
    data = Path(path).read_bytes()
    if data[:12] != PACK_MAGIC:
        raise ValueError(f"not a PackFile: {path}")
    count = u32(data, 12)
    base = 16 + 32 * count
    entries = []
    for index in range(count):
        record = data[16 + 32 * index:16 + 32 * (index + 1)]
        name = record[:16].split(b"\0", 1)[0]
        relative = u32(record, 16)
        size = u32(record, 20)
        payload = data[base + relative:base + relative + size]
        if len(payload) != size:
            raise ValueError(f"bad entry range in {path}")
        entries.append({"name": name, "raw": payload, "tail": record[24:32]})
    return entries


def write_packfile(path, entries):
    count = len(entries)
    base = 16 + 32 * count
    body = bytearray()
    records = []
    cursor = 0
    for entry in entries:
        if cursor % 4:
            padding = 4 - cursor % 4
            body.extend(b"\0" * padding)
            cursor += padding
        records.append((entry["name"], cursor, len(entry["raw"]), entry.get("tail", b"\0" * 8)))
        body.extend(entry["raw"])
        cursor += len(entry["raw"])
    output = bytearray(PACK_MAGIC)
    output.extend(struct.pack("<I", count))
    for name, relative, size, tail in records:
        record = bytearray(32)
        record[:len(name[:16])] = name[:16]
        struct.pack_into("<I", record, 16, relative)
        struct.pack_into("<I", record, 20, size)
        record[24:32] = tail[:8]
        output.extend(record)
    output.extend(body)
    temporary = str(path) + ".new"
    Path(temporary).write_bytes(output)
    os.replace(temporary, path)


def hardlink_tree(source, destination, excluded):
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for item in source.iterdir():
        if item.name in excluded or item.name == ".DS_Store":
            continue
        if item.is_dir():
            # Directories such as backup/ contain original archives and must
            # not be scanned together with the patched root files.
            continue
        target = destination / item.name
        try:
            os.link(item, target)
        except OSError:
            shutil.copy2(item, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--override", type=Path, help="flat directory of translated files (EVB extraction)")
    parser.add_argument("--add-to", help="archive that receives override names absent from every archive")
    parser.add_argument("--exclude", action="append", default=[], help="top-level name to omit (repeatable)")
    args = parser.parse_args()

    overrides = {}
    if args.override:
        overrides = {item.name: item for item in args.override.iterdir() if item.is_file()}

    archives = {}
    for path in args.game.iterdir():
        if path.is_file() and path.suffix.lower() == ".arc":
            try:
                archives[path.name] = read_packfile(path)
            except ValueError:
                pass

    replaced = {name: set() for name in archives}
    existing = {entry["name"].decode("cp932", "replace") for entries in archives.values() for entry in entries}
    for name, entries in archives.items():
        for entry in entries:
            key = entry["name"].decode("cp932", "replace")
            if key in overrides:
                entry["raw"] = overrides[key].read_bytes()
                replaced[name].add(key)
    missing = [name for name in overrides if name not in existing and name != "fonts.arc"]
    if missing and args.add_to:
        archive = args.add_to.lower()
        target = next((name for name in archives if name.lower() == archive), None)
        if target is None:
            raise SystemExit(f"--add-to archive not found: {args.add_to}")
        for name in missing:
            archives[target].append({"name": name.encode("ascii"), "raw": overrides[name].read_bytes(), "tail": b"\0" * 8})
            replaced[target].add(name)
    hardlink_tree(args.game, args.out, set(args.exclude))
    if (args.game / "Data_CHS").is_dir():
        (args.out / "Data_CHS").mkdir(exist_ok=True)
    for name, entries in archives.items():
        if replaced.get(name):
            write_packfile(args.out / name, entries)
            print(f"{name}: replaced {len(replaced[name])}")
    if "fonts.arc" in overrides:
        shutil.copy2(overrides["fonts.arc"], args.out / "fonts.arc")
        print("fonts.arc: copied")


if __name__ == "__main__":
    main()
