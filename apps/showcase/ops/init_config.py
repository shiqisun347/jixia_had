"""Create new private credentials interactively; never overwrite an existing file."""

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import password_hash  # noqa: E402


def initialize(directory: Path, password: str) -> None:
    if not password:
        raise ValueError("password_required")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    targets = [directory / "admin-hash", directory / "cookie-key"]
    if any(p.exists() or p.is_symlink() for p in targets):
        raise FileExistsError("private_credentials_already_exist")
    created = []
    try:
        for path, content in zip(
            targets,
            [password_hash(password).encode() + b"\n", secrets.token_bytes(32)],
            strict=True,
        ):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
    except Exception:
        for path in created:
            path.unlink()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    password = getpass.getpass("New Showcase admin password: ")
    if password != getpass.getpass("Repeat password: "):
        parser.error("passwords_do_not_match")
    initialize(args.directory, password)
    print("Created private admin-hash and cookie-key; no credential values were displayed.")


if __name__ == "__main__":
    main()
