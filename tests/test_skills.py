import json
from northstar.proc import CommandResult
from northstar import skills


def test_plugin_and_marketplace_lists():
    names = {p.name for p in skills.PLUGINS}
    assert names == {"superpowers", "frontend-design", "playwright", "andrej-karpathy-skills"}
    assert set(skills.marketplaces()) == {
        "anthropics/claude-plugins-official", "multica-ai/andrej-karpathy-skills"}
    assert {n.name for n in skills.NATIVE} == {"caveman", "grill-me"}


def test_installed_plugins_parses_json():
    payload = json.dumps([{"name": "superpowers", "version": "6.0.2"},
                          {"name": "playwright", "version": "1.0.0"}])
    runner = lambda cmd, **kw: CommandResult(0, payload, "")
    got = skills.installed_plugins(runner=runner)
    assert got["superpowers"] == "6.0.2"


def test_install_all_runs_marketplace_then_install_then_update_then_native():
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd if isinstance(cmd, str) else " ".join(cmd))
        return CommandResult(0, "[]", "")
    skills.install_all(runner=runner)
    joined = "\n".join(calls)
    assert "plugin marketplace add anthropics/claude-plugins-official" in joined
    assert "plugin marketplace add multica-ai/andrej-karpathy-skills" in joined
    assert "plugin marketplace update" in joined
    assert "plugin install superpowers@claude-plugins-official" in joined
    assert "plugin update superpowers@claude-plugins-official" in joined
    assert "plugin install andrej-karpathy-skills@karpathy-skills" in joined
    # native installers attempted
    assert any("JuliusBrussee/caveman" in c for c in calls)
    assert any("skills@latest add mattpocock/skills" in c for c in calls)
