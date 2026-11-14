import importlib.util
import json
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "gptdiff_redirect_stubs", Path(__file__).resolve().parents[1] / "scripts" / "gptdiff_redirect_stubs.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BASE = "https://patchling.app/py/"


def make_site(tmp_path):
    for rel in ["index.html", "cli/index.html", "examples/basic/index.html", "404.html",
                "assets/app.js", "search/search_index.json", "sitemap.xml"]:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    (tmp_path / "CNAME").write_text("gptdiff.255labs.xyz\n")
    return tmp_path


def test_every_page_maps_to_same_path(tmp_path):
    site = make_site(tmp_path)
    assert mod.build(site, BASE) == ["", "cli/", "examples/basic/"]
    for rel, want in [("index.html", BASE), ("cli/index.html", BASE + "cli/"),
                      ("examples/basic/index.html", BASE + "examples/basic/")]:
        text = (site / rel).read_text()
        assert f'<link rel="canonical" href="{want}">' in text
        assert f'content="0; url={want}"' in text
        assert f"location.replace({json.dumps(want)} + location.search + location.hash)" in text


def test_404_maps_known_and_falls_back(tmp_path):
    site = make_site(tmp_path)
    mod.build(site, BASE)
    text = (site / "404.html").read_text()
    known = json.loads(re.search(r"var known = (\[.*?\]);", text).group(1))
    assert known == ["cli/", "examples/basic/"]
    assert f"var base = {json.dumps(BASE)};" in text
    assert f'content="0; url={BASE}"' in text


def test_only_stubs_and_cname_remain(tmp_path):
    site = make_site(tmp_path)
    mod.build(site, "https://patchling.app/py")  # trailing slash added
    files = sorted(p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file())
    assert files == ["404.html", "CNAME", "cli/index.html", "examples/basic/index.html", "index.html"]
    assert (site / "CNAME").read_text() == "gptdiff.255labs.xyz\n"
    assert "https://patchling.app/py/cli/" in (site / "cli/index.html").read_text()


def test_embedded_json_escapes_lt(tmp_path):
    assert mod.js_json("</script><!--") == '"\\u003c/script\\u003e\\u003c!--"'
    site = tmp_path
    for rel in ["index.html", "a</script><b/index.html", "x<!--y/index.html"]:
        f = site / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    base = "https://patchling.app/py/?q=</script>"
    mod.build(site, base)
    for f in site.rglob("*.html"):
        text = f.read_text()
        for script in re.findall(r"<script>(.*?)</script>", text, re.S):
            assert "<" not in script, f
        assert text.count("</script>") == 1
    known = re.search(r"var known = (\[.*?\]);", (site / "404.html").read_text()).group(1)
    assert json.loads(known) == ["a</script><b/", "x<!--y/"]
    stub = (site / "a</script><b/index.html").read_text()
    m = re.search(r"location\.replace\((.*?) \+ location\.search", stub)
    assert json.loads(m.group(1)) == base + "/a</script><b/"
