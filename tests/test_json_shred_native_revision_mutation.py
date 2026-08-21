"""Direct mutation witnesses for native file-revision serialization and probes."""

from __future__ import annotations

import ctypes
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import haute._json_shred as shred_mod


@pytest.mark.parametrize(
    ("revision", "expected"),
    [
        (
            shred_mod._StrongFileRevision((7, 11), 13, 17, 19),
            {
                "schema_version": 1,
                "kind": "posix_ctime_v1",
                "file_identity": [7, 11],
                "size": 13,
                "mtime_ns": 17,
                "change_token": 19,
            },
        ),
        (
            shred_mod._StrongFileRevision((23, bytes(range(16))), 29, 31, 37),
            {
                "schema_version": 1,
                "kind": "windows_usn_v1",
                "file_identity": [23, "000102030405060708090a0b0c0d0e0f"],
                "size": 29,
                "mtime_ns": 31,
                "change_token": 37,
            },
        ),
    ],
)
def test_native_revision_record_has_exact_platform_shape_and_round_trips(
    revision: shred_mod._StrongFileRevision, expected: dict[str, Any]
) -> None:
    record = shred_mod._native_revision_record(revision)

    assert record == expected
    assert shred_mod._parse_native_revision_record(record) == revision


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.__setitem__("schema_version", 0),
        lambda record: record.__setitem__("schema_version", True),
        lambda record: record.__setitem__("schema_version", 1.0),
        lambda record: record.__setitem__("schema_version", 2),
        lambda record: record.__setitem__("kind", "other"),
        lambda record: record.__setitem__("file_identity", "not-a-list"),
        lambda record: record.__setitem__("file_identity", [1]),
        lambda record: record.__setitem__("file_identity", [1, 2, 3]),
        lambda record: record.__setitem__("file_identity", [True, 2]),
        lambda record: record.__setitem__("file_identity", [1.0, 2]),
        lambda record: record.__setitem__("file_identity", [-1, 2]),
        lambda record: record.__setitem__("file_identity", [1, True]),
        lambda record: record.__setitem__("file_identity", [1, 1.0]),
        lambda record: record.__setitem__("file_identity", [1, 0]),
        lambda record: record.__setitem__("size", True),
        lambda record: record.__setitem__("size", 1.0),
        lambda record: record.__setitem__("size", -1),
        lambda record: record.__setitem__("mtime_ns", True),
        lambda record: record.__setitem__("mtime_ns", 1.0),
        lambda record: record.__setitem__("change_token", True),
        lambda record: record.__setitem__("change_token", 1.0),
        lambda record: record.__setitem__("change_token", 0),
        lambda record: record.__setitem__("change_token", -1),
    ],
)
def test_parse_native_revision_record_rejects_invalid_common_and_posix_values(
    mutate: Any,
) -> None:
    record = shred_mod._native_revision_record(shred_mod._StrongFileRevision((1, 2), 3, 4, 5))
    mutate(record)

    assert shred_mod._parse_native_revision_record(record) is None


@pytest.mark.parametrize("file_id", [2, "short", "z" * 32, "0" * 32])
def test_parse_native_revision_record_rejects_invalid_windows_id(file_id: Any) -> None:
    record = shred_mod._native_revision_record(
        shred_mod._StrongFileRevision((1, b"x" * 16), 3, 4, 5)
    )
    record["file_identity"][1] = file_id

    assert shred_mod._parse_native_revision_record(record) is None


def test_parse_native_revision_record_requires_exact_mapping() -> None:
    record = shred_mod._native_revision_record(shred_mod._StrongFileRevision((1, 2), 3, 4, 5))

    assert shred_mod._parse_native_revision_record(None) is None
    assert shred_mod._parse_native_revision_record({**record, "extra": None}) is None
    assert (
        shred_mod._parse_native_revision_record(
            {key: value for key, value in record.items() if key != "size"}
        )
        is None
    )


def test_posix_strong_file_revision_maps_exact_stat_fields() -> None:
    observed = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_dev=0,
        st_ino=1,
        st_size=0,
        st_mtime_ns=17,
        st_ctime_ns=1,
    )
    path = SimpleNamespace(stat=lambda: observed)

    assert shred_mod._posix_strong_file_revision(path) == shred_mod._StrongFileRevision(
        (0, 1), 0, 17, 1
    )


@pytest.mark.parametrize(
    "observed",
    [
        SimpleNamespace(st_mode=stat.S_IFDIR, st_ino=1, st_ctime_ns=1),
        SimpleNamespace(st_mode=stat.S_IFREG, st_ino=0, st_ctime_ns=1),
        SimpleNamespace(st_mode=stat.S_IFREG, st_ino=-1, st_ctime_ns=1),
        SimpleNamespace(st_mode=stat.S_IFREG, st_ino=1, st_ctime_ns=0),
        SimpleNamespace(st_mode=stat.S_IFREG, st_ino=1, st_ctime_ns=-1),
    ],
)
def test_posix_strong_file_revision_rejects_inadequate_identity(
    observed: SimpleNamespace,
) -> None:
    path = SimpleNamespace(stat=lambda: observed)

    assert shred_mod._posix_strong_file_revision(path) is None


class _NativeCallable:
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)


def _kernel32(
    *,
    handle: object = 99,
    fail_query: int | None = None,
    fail_usn: bool = False,
    directory: int = 0,
    size: int = 4,
    timestamp: int = 2,
    volume: int = 7,
    file_id: bytes = b"x" * 16,
    returned_length: int | None = None,
    record_length: int | None = None,
    major_version: int = 2,
    usn: int = 11,
    raises: BaseException | None = None,
) -> tuple[SimpleNamespace, dict[str, Any]]:
    calls: dict[str, Any] = {"close": []}

    def create_file(*args: object) -> object:
        calls["create"] = args
        if raises:
            raise raises
        return handle

    def get_information(_handle: object, info_class: int, target: object, size_arg: int) -> int:
        calls.setdefault("queries", []).append((info_class, size_arg))
        if raises:
            raise raises
        if info_class == fail_query:
            return 0
        if info_class == 0:
            value = ctypes.cast(target, ctypes.POINTER(shred_mod._WindowsFileBasicInfo)).contents
            value.LastWriteTime = shred_mod._WINDOWS_EPOCH_OFFSET_100NS + timestamp
        elif info_class == 1:
            value = ctypes.cast(target, ctypes.POINTER(shred_mod._WindowsFileStandardInfo)).contents
            value.EndOfFile = size
            value.Directory = directory
        else:
            value = ctypes.cast(target, ctypes.POINTER(shred_mod._WindowsFileIdInfo)).contents
            value.VolumeSerialNumber = volume
            value.FileId.Identifier[:] = file_id
        return 1

    def device_io_control(*args: object) -> int:
        calls["device"] = args
        if raises:
            raise raises
        if fail_usn:
            return 0
        offset = 24 if major_version == 2 else 40 if major_version == 3 else 24
        length = record_length if record_length is not None else offset + 8
        output = ctypes.cast(args[4], ctypes.POINTER(ctypes.c_ubyte * 4096)).contents
        output[:4] = length.to_bytes(4, "little")
        output[4:6] = major_version.to_bytes(2, "little")
        output[offset : offset + 8] = usn.to_bytes(8, "little", signed=True)
        ctypes.cast(args[6], ctypes.POINTER(ctypes.c_uint32)).contents.value = (
            returned_length if returned_length is not None else length
        )
        return 1

    return (
        SimpleNamespace(
            CreateFileW=_NativeCallable(create_file),
            GetFileInformationByHandleEx=_NativeCallable(get_information),
            DeviceIoControl=_NativeCallable(device_io_control),
            CloseHandle=_NativeCallable(lambda value: calls["close"].append(value) or 1),
        ),
        calls,
    )


def test_windows_strong_file_revision_queries_native_token_and_closes_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel32, calls = _kernel32(
        size=0,
        timestamp=1_234_567,
        volume=0,
        file_id=bytes(range(16)),
        returned_length=4_096,
        record_length=48,
        major_version=3,
        usn=0x0123_4567_89AB_CDEF,
    )

    def windll(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.setdefault("windll", []).append((args, kwargs))
        return kernel32

    monkeypatch.setattr(shred_mod.ctypes, "WinDLL", windll, raising=False)

    revision = shred_mod._windows_strong_file_revision(tmp_path / "data.json")

    assert revision == shred_mod._StrongFileRevision(
        (0, bytes(range(16))), 0, 123_456_700, 0x0123_4567_89AB_CDEF
    )
    assert calls["windll"] == [(("kernel32",), {"use_last_error": True})]
    assert calls["create"] == (str(tmp_path / "data.json"), 0x80, 0x7, None, 3, 0x80, None)
    assert calls["queries"] == [
        (0, ctypes.sizeof(shred_mod._WindowsFileBasicInfo)),
        (1, ctypes.sizeof(shred_mod._WindowsFileStandardInfo)),
        (18, ctypes.sizeof(shred_mod._WindowsFileIdInfo)),
    ]
    device = calls["device"]
    assert device[1] == shred_mod._FSCTL_READ_FILE_USN_DATA
    usn_input = ctypes.cast(device[2], ctypes.POINTER(shred_mod._WindowsReadFileUsnData)).contents
    assert usn_input.MinMajorVersion == 2
    assert usn_input.MaxMajorVersion == 3
    assert device[3] == ctypes.sizeof(shred_mod._WindowsReadFileUsnData)
    assert device[5] == shred_mod._WINDOWS_USN_OUTPUT_BUFFER_SIZE
    assert calls["close"] == [99]
    for callable_name in (
        "CreateFileW",
        "GetFileInformationByHandleEx",
        "DeviceIoControl",
        "CloseHandle",
    ):
        assert getattr(kernel32, callable_name).argtypes is not None
        assert getattr(kernel32, callable_name).restype is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"handle": None},
        {"handle": ctypes.c_void_p(-1).value},
        {"fail_query": 0},
        {"fail_query": 1},
        {"fail_query": 18},
        {"fail_usn": True},
        {"returned_length": 7},
        {"returned_length": 4097},
        {"record_length": 31, "returned_length": 31, "major_version": 2},
        {"record_length": 47, "returned_length": 47, "major_version": 3},
        {"record_length": 33, "returned_length": 32},
        {"major_version": 0},
        {"major_version": 4},
        {"usn": 0},
        {"usn": -1},
        {"directory": 1},
        {"size": -1},
        {"file_id": b"\0" * 16},
        {"raises": OSError("native failure")},
    ],
)
def test_windows_strong_file_revision_declines_invalid_native_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    kernel32, calls = _kernel32(**kwargs)
    monkeypatch.setattr(
        shred_mod.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False
    )

    assert shred_mod._windows_strong_file_revision(tmp_path / "data.json") is None
    invalid_handle = kwargs.get("handle", 99) in (None, ctypes.c_void_p(-1).value)
    expected_closes = [] if invalid_handle or "raises" in kwargs else [99]
    assert calls["close"] == expected_closes
