"""One place that knows the web app is more than one file.

Several tests assert on the app's source text rather than its behaviour. Each of them used to
read web/index.html, which held everything; now the markup is there and the behaviour is in
web/js/*.js, so a `grep`-shaped test that keeps reading only index.html does not fail -- it
passes vacuously, having looked in the wrong file. Go through here instead.
"""
import pathlib

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"


def script_files():
    """The scripts index.html loads, in the order it loads them."""
    import re
    index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'<script src="([^"]+)"></script>', index)
    return [WEB_DIR / ref.lstrip("./") for ref in refs]


def web_source():
    """index.html plus every script it loads, concatenated in load order.

    Which is also the text the browser ends up executing, so a `assertIn` against this asks the
    same question it asked when the app was one file.
    """
    parts = [(WEB_DIR / "index.html").read_text(encoding="utf-8")]
    parts += [path.read_text(encoding="utf-8") for path in script_files()]
    parts.append((WEB_DIR / "app.css").read_text(encoding="utf-8"))
    return "\n".join(parts)
