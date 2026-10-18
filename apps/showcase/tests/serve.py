"""Start an owned loopback-only, empty synthetic test instance on a bound random port."""

import json
import socket
import tempfile
from pathlib import Path

import app
import uvicorn
from fixtures import prepare_static


def main():
    with tempfile.TemporaryDirectory(prefix="jx-showcase-test-") as folder:
        directory = Path(folder)
        app.ROOT = prepare_static(app.ROOT, directory / "site")
        password_file = directory / "admin-hash"
        password_file.write_text(app.password_hash("synthetic-test-password"))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            application = app.create_app(directory / "data", origin, password_file, secure=False)
            config = uvicorn.Config(application, access_log=False, log_level="warning")
            print(json.dumps({"origin": origin}), flush=True)
            uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
