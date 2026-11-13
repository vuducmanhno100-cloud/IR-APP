"""Copy the part of a PostgreSQL 18 Windows install that IR Desktop runs, and check DLL dependencies.

collect: copies initdb, pg_ctl and postgres, the server modules the engine loads, the share files initdb reads, and
every DLL these import (read from the PE import tables). Add-ons in the source install (PostGIS, pgAdmin, Stack
Builder, PL/Python...) are left out because nothing the app runs imports them. The Visual C++ runtime is copied next
to the programs (app-local deployment): a clean Windows has no VC++ redistributable, and postgres.exe needs it.

check: fails when a PE file in a folder imports a DLL that is neither in that folder tree nor part of Windows.

Standard library only: build.ps1 runs it with the build venv's Python 3.13.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import struct
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

PROGRAMS = ("postgres.exe", "pg_ctl.exe", "initdb.exe")
# Extensions the engine creates (pg_trgm, pg_freespacemap) and the language initdb installs everywhere (plpgsql).
EXTENSIONS = ("plpgsql", "pg_trgm", "pg_freespacemap")
# initdb builds the text search dictionaries on dict_snowball; encoding conversions load on first use.
SERVER_MODULES = ("dict_snowball.dll", "*_and_*.dll")
# initdb runs with --no-locale, so message translations (share/locale) are not needed.
SHARE_DIRS = ("timezone", "timezonesets", "tsearch_data")

VC_RUNTIME = re.compile(r"^(vcruntime|msvcp|concrt|vccorlib|vcomp)\d+.*\.dll$", re.IGNORECASE)
API_SET = re.compile(r"^(api|ext)-ms-", re.IGNORECASE)
PE_SUFFIXES = (".exe", ".dll", ".pyd")
# Ship with every Windows 10/11. Anything else that a bundled file imports must ship with the app.
WINDOWS_DLLS = frozenset(
    name.lower() + ".dll"
    for name in """
    advapi32 authz bcrypt cfgmgr32 combase comctl32 comdlg32 credui crypt32 cryptbase cryptsp d2d1 d3d11 dbghelp
    dnsapi dwmapi dwrite dxgi gdi32 gdiplus hid imagehlp imm32 iphlpapi kernel32 kernelbase mpr msasn1 msi msimg32
    mswsock msvcrt ncrypt netapi32 normaliz ntdll ole32 oleacc oleaut32 pdh powrprof propsys psapi rpcrt4 secur32
    setupapi shcore shell32 shlwapi sspicli ucrtbase urlmon user32 userenv uxtheme version winhttp wininet winmm
    wintrust wldap32 ws2_32 wsock32 wtsapi32
    """.split()
)


class BundleError(Exception):
    pass


def pe_imports(path: Path) -> list[str]:
    """DLL names in the import and delay-load import tables of a PE file."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise BundleError(f"{path} is not a PE file")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        raise BundleError(f"{path} is not a PE file")
    _, section_count, _, _, _, optional_size, _ = struct.unpack_from("<HHIIIHH", data, pe + 4)
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic == 0x20B:
        image_base = struct.unpack_from("<Q", data, optional + 24)[0]
        dir_count, dirs = struct.unpack_from("<I", data, optional + 108)[0], optional + 112
    elif magic == 0x10B:
        image_base = struct.unpack_from("<I", data, optional + 28)[0]
        dir_count, dirs = struct.unpack_from("<I", data, optional + 92)[0], optional + 96
    else:
        raise BundleError(f"{path}: unknown optional header magic {magic:#x}")

    sections = []
    for i in range(section_count):
        vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", data, optional + optional_size + 40 * i + 8)
        sections.append((va, max(vsize, raw_size), raw_ptr))

    def offset(rva: int) -> int:
        for va, size, raw_ptr in sections:
            if va <= rva < va + size:
                return rva - va + raw_ptr
        raise BundleError(f"{path}: address {rva:#x} is outside every section")

    def name_at(rva: int) -> str:
        start = offset(rva)
        return data[start : data.index(b"\0", start)].decode("ascii")

    def directory(index: int) -> tuple[int, int]:
        return struct.unpack_from("<II", data, dirs + 8 * index) if index < dir_count else (0, 0)

    names: list[str] = []
    rva, _ = directory(1)
    if rva:
        position = offset(rva)
        while True:
            *_, name_rva, first_thunk = struct.unpack_from("<IIIII", data, position)
            if not name_rva and not first_thunk:
                break
            names.append(name_at(name_rva))
            position += 20
    rva, _ = directory(13)
    if rva:
        position = offset(rva)
        while True:
            attributes, name_rva = struct.unpack_from("<II", data, position)
            if not name_rva:
                break
            # Old linkers stored virtual addresses instead of RVAs (attribute bit 0 clear).
            names.append(name_at(name_rva if attributes & 1 else name_rva - image_base))
            position += 32
    return list(dict.fromkeys(names))


def is_windows_dll(name: str) -> bool:
    lower = name.lower()
    return bool(API_SET.match(lower)) or lower in WINDOWS_DLLS


def system32_has(name: str) -> bool:
    return os.name == "nt" and (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name).exists()


@dataclass
class Closure:
    files: dict[str, Path] = field(default_factory=dict)
    runtime: set[str] = field(default_factory=set)
    windows: set[str] = field(default_factory=set)
    assumed: set[str] = field(default_factory=set)
    missing: dict[str, set[str]] = field(default_factory=dict)


def resolve(roots: list[Path], available: dict[str, Path], runtime_available: set[str]) -> Closure:
    """Follow imports from the roots through the DLLs in `available` (lower-case name -> path)."""
    closure = Closure()
    queue = deque(roots)
    seen = {p.name.lower() for p in roots}
    while queue:
        current = queue.popleft()
        closure.files[current.name.lower()] = current
        for name in pe_imports(current):
            lower = name.lower()
            if lower in available:
                if lower not in seen:
                    seen.add(lower)
                    queue.append(available[lower])
            elif VC_RUNTIME.match(lower) and lower in runtime_available:
                closure.runtime.add(lower)
            elif is_windows_dll(lower):
                closure.windows.add(lower)
            elif not VC_RUNTIME.match(lower) and system32_has(name):
                closure.assumed.add(lower)
            else:
                closure.missing.setdefault(name, set()).add(current.name)
    return closure


def describe_missing(missing: dict[str, set[str]]) -> str:
    return "; ".join(f"{dll} (needed by {', '.join(sorted(users))})" for dll, users in sorted(missing.items()))


def by_name(folder: Path) -> dict[str, Path]:
    return {p.name.lower(): p for p in folder.iterdir() if p.is_file()} if folder.is_dir() else {}


def size_mb(paths) -> float:
    return sum(p.stat().st_size for p in paths) / 1024**2


def collect(source: Path, dest: Path, crt_dir: Path | None) -> None:
    bin_dir, lib_dir, share_dir = source / "bin", source / "lib", source / "share"
    for folder in (bin_dir, lib_dir, share_dir / "extension"):
        if not folder.is_dir():
            raise BundleError(f"{folder} not found; --source must be the PostgreSQL folder that holds bin, lib and share")
    binaries, libraries = by_name(bin_dir), by_name(lib_dir)
    programs = []
    for program in PROGRAMS:
        if program not in binaries:
            raise BundleError(f"{program} not found in {bin_dir}")
        programs.append(binaries[program])

    modules = {p for pattern in SERVER_MODULES for name, p in libraries.items() if fnmatch.fnmatch(name, pattern)}
    extension_files = []
    for extension in EXTENSIONS:
        module = libraries.get(f"{extension}.dll")
        control = share_dir / "extension" / f"{extension}.control"
        if not module or not control.exists():
            raise BundleError(f"extension {extension} is incomplete in {source} (needs lib\\{extension}.dll and its control file)")
        modules.add(module)
        extension_files += [control, *sorted((share_dir / "extension").glob(f"{extension}--*.sql"))]

    runtime_files = by_name(crt_dir) if crt_dir else {}
    closure = resolve(programs + sorted(modules), binaries, set(runtime_files))
    needed_runtime = {m for m in closure.missing if VC_RUNTIME.match(m.lower())}
    if needed_runtime:
        hint = f"not in {crt_dir}" if crt_dir else "pass --crt-dir (Microsoft.VC14x.CRT folder of the Visual Studio redist)"
        raise BundleError(f"Visual C++ runtime {', '.join(sorted(needed_runtime))} {hint}")
    if closure.missing:
        raise BundleError(f"unresolved DLL imports: {describe_missing(closure.missing)}")

    bin_files = [p for name, p in closure.files.items() if name in binaries]
    # The whole runtime set is small (about 2 MB) and its DLLs import each other.
    runtime = [p for name, p in runtime_files.items() if name.endswith(".dll")] if closure.runtime else []
    share_files = [p for p in share_dir.iterdir() if p.is_file()]

    for sub in ("bin", "lib", "share/extension"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    for p in bin_files + runtime:
        shutil.copy2(p, dest / "bin" / p.name)
    for p in modules:
        shutil.copy2(p, dest / "lib" / p.name)
    for p in share_files:
        shutil.copy2(p, dest / "share" / p.name)
    for p in extension_files:
        shutil.copy2(p, dest / "share" / "extension" / p.name)
    for name in SHARE_DIRS:
        if (share_dir / name).is_dir():
            shutil.copytree(share_dir / name, dest / "share" / name, dirs_exist_ok=True)

    copied = [p for p in dest.rglob("*") if p.is_file()]
    left_out = [p for name, p in binaries.items() if name not in closure.files]
    print(f"PostgreSQL core: {len(copied)} files, {size_mb(copied):.0f} MB in {dest}")
    print(f"  bin: {len(bin_files)} of {len(binaries)} files ({size_mb(left_out):.0f} MB left out); lib: {len(modules)} modules")
    if runtime:
        print(f"  Visual C++ runtime (app-local): {', '.join(sorted(p.name for p in runtime))}")
    if closure.assumed:
        print(f"  WARNING: assumed part of Windows (found in System32): {', '.join(sorted(closure.assumed))}")


def check(folders: list[Path]) -> None:
    """Every PE file's imports must resolve inside its folder tree or to Windows itself."""
    problems = {}
    for folder in folders:
        files = [p for p in folder.rglob("*") if p.is_file()]
        names = {p.name.lower() for p in files}
        pe_files = [p for p in files if p.suffix.lower() in PE_SUFFIXES]
        for path in pe_files:
            for name in pe_imports(path):
                lower = name.lower()
                if lower in names or is_windows_dll(lower) or (not VC_RUNTIME.match(lower) and system32_has(name)):
                    continue
                problems.setdefault(name, set()).add(str(path.relative_to(folder)))
        print(f"{folder}: {len(pe_files)} PE files checked")
    if problems:
        raise BundleError(f"DLLs that a clean Windows lacks and the bundle does not ship: {describe_missing(problems)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    collect_cmd = commands.add_parser("collect", help="copy the PostgreSQL core and its DLLs")
    collect_cmd.add_argument("--source", type=Path, required=True, help="PostgreSQL folder with bin, lib, share")
    collect_cmd.add_argument("--dest", type=Path, required=True)
    collect_cmd.add_argument("--crt-dir", type=Path, help="Microsoft.VC14x.CRT folder to copy the VC++ runtime from")
    check_cmd = commands.add_parser("check", help="fail on DLL imports a clean Windows cannot satisfy")
    check_cmd.add_argument("folders", type=Path, nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            collect(args.source, args.dest, args.crt_dir)
        else:
            check(args.folders)
    except BundleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
