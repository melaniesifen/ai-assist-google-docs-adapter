from __future__ import annotations

import hashlib


def hash_content(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
