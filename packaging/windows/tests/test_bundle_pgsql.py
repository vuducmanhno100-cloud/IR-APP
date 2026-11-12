"""Tests for bundle_pgsql on synthetic PE files: run with `pytest packaging/windows/tests` (any OS)."""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bundle_pgsql  # noqa: E402

CRT = "api-ms-win-crt-runtime-l1-1-0.dll"


def make_pe(path: Path, imports=(), delay=()):
    """Smallest PE32+ image the parser reads: headers and one section holding the import tables and DLL names."""
    section_rva, section_raw = 0x1000, 0x200
    import_table = 20 * (len(imports) + 1)
    delay_table = 32 * (len(delay) + 1)
    names, name_rvas = b"", []
    for name in (*imports, *delay):
        name_rvas.append(section_rva + import_table + delay_table + len(names))
        names += name.encode() + b"\0"
    body = b"".join(struct.pack("<IIIII", 0, 0, 0, rva, 0x10) for rva in name_rvas[: len(imports)]) + bytes(20)
    body += b"".join(struct.pack("<8I", 1, rva, 0, 0, 0, 0, 0, 0) for rva in name_rvas[len(imports) :]) + bytes(32)
    body += names

    header = bytearray(section_raw)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x40)
    header[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", header, 0x44, 0x8664, 1, 0, 0, 0, 240, 0x22)
    optional = 0x58
    struct.pack_into("<H", header, optional, 0x20B)
    struct.pack_into("<Q", header, optional + 24, 0x140000000)
    struct.pack_into("<I", header, optional + 108, 16)
    if imports:
        struct.pack_into("<II", header, optional + 112 + 8 * 1, section_rva, import_table)
    if delay:
        struct.pack_into("<II", header, optional + 112 + 8 * 13, section_rva + import_table, delay_table)
    struct.pack_into("<8sIIII", header, optional + 240, b".idata", len(body), section_rva, len(body), section_raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + body)


def touch(path: Path, text: str = "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def pg(tmp_path):
    """A PostgreSQL install with add-ons next to the core, like the EnterpriseDB installer plus Stack Builder."""
    root = tmp_path / "PostgreSQL" / "18"
    b, lib, share = root / "bin", root / "lib", root / "share"
    make_pe(b / "postgres.exe", ["libssl-3-x64.dll", "VCRUNTIME140.dll", "KERNEL32.dll", CRT], delay=["icuuc77.dll"])
    make_pe(b / "pg_ctl.exe", ["libpq.dll", "ADVAPI32.dll"])
    make_pe(b / "initdb.exe", ["libpq.dll", "WS2_32.dll"])
    make_pe(b / "libpq.dll", ["libssl-3-x64.dll", "Secur32.dll"])
    make_pe(b / "libssl-3-x64.dll", ["libcrypto-3-x64.dll"])
    make_pe(b / "libcrypto-3-x64.dll", ["WS2_32.dll", "CRYPT32.dll"])
    make_pe(b / "icuuc77.dll", ["MSVCP140.dll"])
    make_pe(b / "libgdal-35.dll", ["libcrypto-3-x64.dll"])
    make_pe(b / "stackbuilder.exe", ["wxbase32u.dll"])
    for module in ("plpgsql", "pg_trgm", "pg_freespacemap", "dict_snowball", "utf8_and_win", "postgis-3"):
        make_pe(lib / f"{module}.dll", ["postgres.exe", "VCRUNTIME140.dll"])
    touch(lib / "wxmsw32u_core.lib")
    for extension in ("plpgsql", "pg_trgm", "pg_freespacemap", "postgis"):
        touch(share / "extension" / f"{extension}.control")
        touch(share / "extension" / f"{extension}--1.0.sql")
    touch(share / "extension" / "pg_trgm--1.5--1.6.sql")
    for name in ("postgres.bki", "snowball_create.sql", "postgresql.conf.sample"):
        touch(share / name)
    touch(share / "timezone" / "Asia" / "Ho_Chi_Minh")
    touch(share / "tsearch_data" / "english.stop")
    touch(share / "contrib" / "postgis-3.6" / "proj.db")
    touch(share / "locale" / "vi" / "LC_MESSAGES" / "postgres-18.mo")
    touch(share / "doc" / "README")
    crt = tmp_path / "Microsoft.VC143.CRT"
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        make_pe(crt / name, ["KERNEL32.dll", CRT])
    return root, crt


def listing(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


def test_reads_import_and_delay_import_tables(tmp_path):
    make_pe(tmp_path / "a.exe", ["libpq.dll", "KERNEL32.dll"], delay=["icuuc77.dll"])
    assert bundle_pgsql.pe_imports(tmp_path / "a.exe") == ["libpq.dll", "KERNEL32.dll", "icuuc77.dll"]


def test_rejects_files_that_are_not_pe(tmp_path):
    touch(tmp_path / "fake.dll", "not a dll")
    with pytest.raises(bundle_pgsql.BundleError, match="not a PE file"):
        bundle_pgsql.pe_imports(tmp_path / "fake.dll")


def test_collects_only_the_core_and_its_dll_closure(pg, tmp_path):
    root, crt = pg
    dest = tmp_path / "dist" / "pgsql"
    touch(dest / "README.txt")
    bundle_pgsql.collect(root, dest, crt)
    files = listing(dest)

    assert {f"bin/{n}" for n in ("postgres.exe", "pg_ctl.exe", "initdb.exe", "libpq.dll", "libssl-3-x64.dll")} <= files
    assert {"bin/libcrypto-3-x64.dll", "bin/icuuc77.dll"} <= files  # reached through libssl and a delay import
    assert {"bin/vcruntime140.dll", "bin/vcruntime140_1.dll", "bin/msvcp140.dll"} <= files
    assert {"bin/libgdal-35.dll", "bin/stackbuilder.exe"}.isdisjoint(files)
    lib = {f for f in files if f.startswith("lib/")}
    assert lib == {f"lib/{m}.dll" for m in ("plpgsql", "pg_trgm", "pg_freespacemap", "dict_snowball", "utf8_and_win")}
    extension = {f for f in files if f.startswith("share/extension/")}
    assert "share/extension/pg_trgm--1.5--1.6.sql" in extension
    assert not any("postgis" in f for f in extension)
    assert {"share/postgres.bki", "share/timezone/Asia/Ho_Chi_Minh", "share/tsearch_data/english.stop"} <= files
    assert not any(f.startswith(("share/contrib/", "share/locale/", "share/doc/")) for f in files)
    assert "README.txt" in files
    bundle_pgsql.check([dest])


def test_refuses_without_the_visual_cpp_runtime(pg, tmp_path):
    root, _ = pg
    with pytest.raises(bundle_pgsql.BundleError, match="VCRUNTIME140.dll.*--crt-dir"):
        bundle_pgsql.collect(root, tmp_path / "out", None)


def test_reports_a_dll_nobody_ships(pg, tmp_path):
    root, crt = pg
    make_pe(root / "bin" / "libssl-3-x64.dll", ["libcrypto-3-x64.dll", "libkrb5.dll"])
    with pytest.raises(bundle_pgsql.BundleError, match=r"libkrb5\.dll \(needed by libssl-3-x64\.dll\)"):
        bundle_pgsql.collect(root, tmp_path / "out", crt)


def test_requires_the_extensions_the_engine_creates(pg, tmp_path):
    root, crt = pg
    (root / "lib" / "pg_freespacemap.dll").unlink()
    with pytest.raises(bundle_pgsql.BundleError, match="pg_freespacemap"):
        bundle_pgsql.collect(root, tmp_path / "out", crt)


def test_check_flags_runtime_dlls_missing_from_a_folder(tmp_path):
    make_pe(tmp_path / "engine" / "ir-engine.exe", ["python313.dll", "KERNEL32.dll"])
    make_pe(tmp_path / "engine" / "_internal" / "python313.dll", ["VCRUNTIME140.dll", CRT])
    with pytest.raises(bundle_pgsql.BundleError, match="VCRUNTIME140.dll"):
        bundle_pgsql.check([tmp_path / "engine"])
    make_pe(tmp_path / "engine" / "_internal" / "VCRUNTIME140.dll", ["KERNEL32.dll"])
    bundle_pgsql.check([tmp_path / "engine"])


def test_command_line_exit_codes(pg, tmp_path, capsys):
    root, crt = pg
    assert bundle_pgsql.main(["collect", "--source", str(root), "--dest", str(tmp_path / "out")]) == 1
    assert "ERROR: Visual C++ runtime" in capsys.readouterr().err
    assert bundle_pgsql.main(["collect", "--source", str(root), "--dest", str(tmp_path / "out"), "--crt-dir", str(crt)]) == 0
    assert "Visual C++ runtime (app-local)" in capsys.readouterr().out
