from __future__ import annotations

import importlib


def test_main_is_an_import_safe_factory_not_a_direct_server_entry() -> None:
    module = importlib.import_module("jx_core.main")

    assert callable(module.build_app)
    assert not hasattr(module, "app")
