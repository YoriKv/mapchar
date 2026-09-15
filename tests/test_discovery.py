from __future__ import annotations

import os

from mapchar.plugins.base import Stage
from mapchar.plugins.discovery import ENV_VAR, discover, plugin_roots
from mapchar.plugins.examples import PLUGIN_README, seed_examples
from mapchar.plugins.registry import default_registry
from mapchar.plugins.trust import TrustStore
from mapchar.project.tables import read_table_file


def read_table(path: str):
    """What the app injects: a ``.tbl`` charset read as a table."""
    return read_table_file(path).table


def test_roots_and_seed(tmp_path, monkeypatch):
    user = tmp_path / "user"
    seed_examples(str(user))
    assert (user / PLUGIN_README).exists()
    # One example per typed folder, all inert until the underscore comes off.
    for folder in ("containers", "compression", "charsets", "mappings"):
        shipped = list((user / folder).iterdir())
        assert shipped and all(p.name.startswith("_") for p in shipped)
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "extra"))
    roots = plugin_roots(str(user), str(tmp_path / "proj"))
    assert [r[0] for r in roots] == [
        str(tmp_path / "extra"),
        str(user),
        str(tmp_path / "proj" / "plugins"),
    ]


def test_seeding_refreshes_its_own_files(tmp_path):
    """Seeding tracks the running build: a stale example is rewritten, and the
    user's own work never is."""
    user = tmp_path / "user"
    seed_examples(str(user))
    example = user / "mappings" / "_banked.toml"
    shipped = example.read_text(encoding="utf-8")
    example.write_text("stale\n", encoding="utf-8")
    # An activated copy.
    mine = user / "mappings" / "banked.toml"
    mine.write_text("mine\n", encoding="utf-8")

    seed_examples(str(user))

    assert example.read_text(encoding="utf-8") == shipped
    assert mine.read_text(encoding="utf-8") == "mine\n"


def test_the_shipped_examples_all_load(tmp_path, registry):
    """Every example is a working plugin, not a sketch: activating them all (the
    underscore comes off) registers each one with no issue but the README."""
    user = tmp_path / "user"
    seed_examples(str(user))
    for folder in os.listdir(user):
        d = user / folder
        if not d.is_dir():
            continue
        for file in os.listdir(d):
            (d / file[1:]).write_text(
                (d / file).read_text(encoding="utf-8"), encoding="utf-8"
            )
    result = discover(
        registry, plugin_roots(str(user), None), TrustStore(None), lambda p, d: True
    )
    assert result.issues == []
    assert registry.plugin(Stage.CONTAINER, "my_container") is not None
    assert registry.plugin(Stage.COMPRESSION, "my_dte") is not None
    assert registry.plugin(Stage.CHARSET, "my_font_order") is not None
    assert registry.plugin(Stage.MAPPING, "my_mapper") is not None
    assert registry.plugin(Stage.MAPPING, "my_table_banked") is not None


def test_discover_presets_code_and_issues(tmp_path, registry):
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
    (user / "loose.toml").write_text("id = 'loose'\n")
    trust = TrustStore(str(tmp_path / "trust.json"))
    asked = []

    def confirm(path, digest):
        asked.append(os.path.basename(path))
        return not path.endswith("declined.py")

    result = discover(
        registry, plugin_roots(str(user), None), trust, confirm, read_table
    )
    assert (
        "mine" in result.loaded
        and "swap2" in result.loaded
        and "mytbl" in result.loaded
    )
    assert registry.plugin(Stage.MAPPING, "mine").to_value(0x100) == 0xA100
    assert registry.plugin(Stage.COMPRESSION, "swap2").info.category == "Your plugins"
    assert list(registry.plugin(Stage.CHARSET, "mytbl").entries()) == [
        ("01000001", "X"),
        ("01000010", "Y"),
    ]
    messages = " ".join(str(i) for i in result.issues)
    assert "unknown engine" in messages and "not a compression plugin" in messages
    assert "RuntimeError: boom" in messages and "loose file" in messages
    assert sorted(asked) == ["broken.py", "swap.py", "wrong_stage.py"]
    # A plugin that broke is a failure, not a refusal.
    assert not any(i.declined for i in result.issues)
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


def test_a_bad_preset_is_an_issue_not_a_crash(tmp_path, registry):
    """Every engine's own parameter reading is inside the guard: one hand-edited
    TOML must not be the reason the app does not start."""
    user = tmp_path / "user"
    (user / "mappings").mkdir(parents=True)
    (user / "compression").mkdir()
    (user / "mappings" / "no_params.toml").write_text('engine = "banked"\n')
    (user / "mappings" / "bad_number.toml").write_text(
        'engine = "banked"\n[params]\nbank_size = "eight"\nbank_base = 0\n'
    )
    (user / "compression" / "bad_width.toml").write_text(
        'engine = "bitpack"\n[params]\nwidth = "six"\n'
    )
    (user / "compression" / "bad_lzss.toml").write_text(
        'engine = "lzss"\n[params]\nwindow_bits = 9\nlength_bits = 4\n'
    )
    (user / "compression" / "wrong_stage.toml").write_text(
        'engine = "bitpack"\nstage = "mappings"\n[params]\nwidth = 6\n'
    )
    (user / "compression" / "good.toml").write_text(
        'id = "ok_pack"\nengine = "bitpack"\n[params]\nwidth = 4\n'
    )

    result = discover(registry, plugin_roots(str(user), None), TrustStore(None), None)

    assert result.loaded == ["ok_pack"]
    by_file = {os.path.basename(i.path): i.message for i in result.issues}
    assert set(by_file) == {
        "no_params.toml",
        "bad_number.toml",
        "bad_width.toml",
        "bad_lzss.toml",
        "wrong_stage.toml",
    }
    assert all(m.startswith("preset load failed") for m in by_file.values())
    assert "bank_size" in by_file["no_params.toml"]
    assert "conflicts with the folder's stage" in by_file["wrong_stage.toml"]


def test_no_trust_store_means_no_code_runs(tmp_path, registry):
    """Default deny: a gate that opens when nothing can say yes is not a gate."""
    user = tmp_path / "user"
    (user / "compression").mkdir(parents=True)
    (user / "compression" / "evil.py").write_text(
        "raise SystemExit('this must never run')\n"
    )
    result = discover(registry, plugin_roots(str(user), None), None, None)
    assert result.loaded == []
    assert [i.declined for i in result.issues] == [True]
    # ...and with a store but no way to ask, still nothing.
    result = discover(
        registry, plugin_roots(str(user), None), TrustStore(None), confirm=None
    )
    assert result.loaded == [] and result.issues[0].declined


def test_a_declined_plugin_is_not_a_failure(tmp_path, registry):
    user = tmp_path / "user"
    (user / "compression").mkdir(parents=True)
    (user / "compression" / "mine.py").write_text("def register(r): pass\n")
    result = discover(
        registry, plugin_roots(str(user), None), TrustStore(None), lambda p, d: False
    )
    assert [i.declined for i in result.issues] == [True]
    assert "declined" in result.issues[0].message


def test_an_edited_plugin_reloads_without_a_second_prompt(tmp_path, registry):
    """The author loop: a path approved this run reloads when its code changes.
    A new run prompts again, the session set starting empty."""
    user = tmp_path / "user"
    (user / "compression").mkdir(parents=True)
    plugin = user / "compression" / "mine.py"
    plugin.write_text("def register(r): pass\n")
    store = TrustStore(str(tmp_path / "trust.json"))
    asked: list[str] = []

    def confirm(path, digest):
        asked.append(digest)
        return True

    discover(registry, plugin_roots(str(user), None), store, confirm)
    assert len(asked) == 1
    plugin.write_text("VERSION = 2\ndef register(r): pass\n")
    discover(default_registry(), plugin_roots(str(user), None), store, confirm)
    assert len(asked) == 1  # same path, edited code, no prompt
    # A new run trusts the digest it last approved, and nothing else: the same
    # file edited again asks, the session set having started empty.
    fresh = TrustStore(str(tmp_path / "trust.json"))
    discover(default_registry(), plugin_roots(str(user), None), fresh, confirm)
    assert len(asked) == 1
    plugin.write_text("VERSION = 3\ndef register(r): pass\n")
    discover(default_registry(), plugin_roots(str(user), None), fresh, confirm)
    assert len(asked) == 2


def test_a_plugin_with_a_read_only_info_still_registers(tmp_path, registry):
    """The category heading is presentation; the plugin is the point."""
    user = tmp_path / "user"
    (user / "compression").mkdir(parents=True)
    (user / "compression" / "frozen.py").write_text(
        "from mapchar.plugins.base import PluginInfo, Stage\n"
        "class C:\n"
        "    __slots__ = ()\n"
        "    @property\n"
        "    def info(self):\n"
        "        return PluginInfo('frozen', 'Frozen', Stage.COMPRESSION)\n"
        "    def decompress(self, data, ctx): return data\n"
        "def register(r): r.register(C())\n"
    )
    result = discover(
        registry, plugin_roots(str(user), None), TrustStore(None), lambda p, d: True
    )
    assert result.issues == [] and result.loaded == ["frozen"]
    assert registry.plugin(Stage.COMPRESSION, "frozen") is not None


def test_a_preset_with_a_byte_order_mark_and_a_kana_name_loads(tmp_path, registry):
    user = tmp_path / "user"
    (user / "mappings").mkdir(parents=True)
    (user / "mappings" / "bom.toml").write_text(
        'id = "ろむ"\nname = "ロム"\nengine = "banked"\n'
        "[params]\nbank_size = 0x2000\nbank_base = 0xA000\n",
        encoding="utf-8-sig",
    )
    (user / "charsets").mkdir()
    (user / "charsets" / "かな.tbl").write_bytes("41=あ\n42=が\n".encode("cp932"))

    result = discover(
        registry, plugin_roots(str(user), None), TrustStore(None), None, read_table
    )

    assert not result.issues
    assert set(result.loaded) == {"ろむ", "かな"}
    assert registry.plugin(Stage.MAPPING, "ろむ").info.name == "ロム"
    assert dict(registry.plugin(Stage.CHARSET, "かな").entries()) == {
        "01000001": "あ",
        "01000010": "が",
    }
