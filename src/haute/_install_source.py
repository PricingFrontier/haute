"""Where an installed distribution came from, read from its PEP 610 ``direct_url.json``.

A distribution installed from a package index has no ``direct_url.json``: its version
number names the code the index holds. One installed from a Git repository, an archive
or a local directory records that location, and its version number names nothing an
index can reproduce. An editable install points at a working tree.
"""

from __future__ import annotations

import json
from importlib import metadata
from typing import Literal, TypeAlias
from urllib.parse import unquote, urlparse

InstallKind: TypeAlias = Literal["wheel", "editable", "direct_url"]


def install_source(distribution: metadata.Distribution) -> tuple[InstallKind, str | None]:
    """Classify *distribution* by its ``direct_url.json``: its kind and location.

    ``("wheel", None)`` for an index install; ``("editable", path)`` for an editable
    checkout; ``("direct_url", url_or_path)`` for any other direct-URL install.
    """
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        return "wheel", None
    record = json.loads(raw)
    url = record["url"]
    parsed = urlparse(url)
    location = unquote(parsed.path) if parsed.scheme == "file" else url
    if record.get("dir_info", {}).get("editable") is True:
        return "editable", location
    return "direct_url", location
