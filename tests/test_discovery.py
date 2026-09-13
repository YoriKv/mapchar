from __future__ import annotations

import os

from mapchar.plugins.base import Stage
from mapchar.plugins.discovery import (
    ENV_VAR,
    TrustStore,
    discover,
    plugin_roots,
    seed_examples,
)
from mapchar.plugins.registry import default_registry


def test_roots_and_seed(tmp_path, monkeypatch):
    user = tmp_path / "user"
    seed_examples(str(user))
    assert (user / "README.txt").exists() and (
        user / "mappings" / "_example_banked.toml"
    ).exists()
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "extra"))
    roots = plugin_roots(str(user), str(tmp_path / "proj"))
    assert [r[0] for r in roots] == [
        str(tmp_path / "extra"),
        str(user),
        str(tmp_path / "proj" / "plugins"),
    ]


def test_discover_presets_code_and_issues(tmp_path):
    user = tmp_path / "user"
    (user / "mappings").mkdir(parents=True)
    (user / "mappings" / "mine.toml").write_text(
        'id = "mine"\nname = "Mine"\nengine = "banked"\n'
        "[params]\nbank_size = 0x2000\nbank_base = 0xA000\n"
    )
    (user / "mappings" / "bad.toml").write_text("engine = 'nope'\n")
    (user / "compression").mkdir()
    (user / "compression" / "swap.py").write_text(
        "from mapchar.plugins.base import PluginInfo, Stage\n"
        "class R:\n"
        "    info = PluginInfo('swap2', 'Swap', Stage.COMPRESSION)\n"
        "    def decompress(self, data, ctx): return data[::-1]\n"
        "    def compress(self, data, ctx): return data[::-1]\n"
        "def register(r): r.register(R())\n"
    )
    (user / "compression" / "wrong_stage.py").write_text(
        "from mapchar.plugins.base import PluginInfo, Stage\n"
        "class C:\n"
        "    info = PluginInfo('c', 'C', Stage.CONTAINER)\n"
        "    def read(self, s, ctx): return s.data\n"
        "def register(r): r.register(C())\n"
    )
    (user / "compression" / "broken.py").write_text("raise RuntimeError('boom')\n")
    (user / "charsets").mkdir()
    (user / "charsets" / "mytbl.tbl").write_text("41=X\n42=Y\n")
    (user / "loose.txt").write_text("x")
    reg = default_registry()
    trust = TrustStore(str(tmp_path / "trust.json"))
    asked = []

    def confirm(path, digest):
        asked.append(os.path.basename(path))
        return not path.endswith("broken.py")

    result = discover(reg, plugin_roots(str(user), None), trust, confirm)
    assert (
        "mine" in result.loaded
        and "swap2" in result.loaded
        and "mytbl" in result.loaded
    )
    assert reg.plugin(Stage.MAPPING, "mine").to_value(0x100) == 0xA100
    assert reg.plugin(Stage.COMPRESSION, "swap2").info.category == "Your plugins"
    assert list(reg.plugin(Stage.CHARSET, "mytbl").entries()) == [
        ("01000001", "X"),
        ("01000010", "Y"),
    ]
    messages = " ".join(str(i) for i in result.issues)
    assert "unknown engine" in messages and "not a compression plugin" in messages
    assert "not trusted" in messages and "loose file" in messages
    assert sorted(asked) == ["broken.py", "swap.py", "wrong_stage.py"]
    # Trusted digests persist; a second discovery asks nothing for swap.py.
    asked.clear()
    reg2 = default_registry()
    discover(
        reg2,
        plugin_roots(str(user), None),
        TrustStore(str(tmp_path / "trust.json")),
        confirm,
    )
    assert "swap.py" not in asked
