import tomllib
from pathlib import Path


def test_streamlit_theme_uses_approved_wayfinding_palette():
    config_path = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"
    theme = tomllib.loads(config_path.read_text())["theme"]

    assert theme["base"] == "light"
    assert theme["primaryColor"] == "#0B0B0B"
    assert theme["backgroundColor"] == "#F1F3F4"
    assert theme["secondaryBackgroundColor"] == "#FFFFFF"
    assert theme["textColor"] == "#0B0B0B"
    assert theme["sidebar"]["backgroundColor"] == "#FFCC00"
    assert theme["sidebar"]["textColor"] == "#0B0B0B"
    assert theme["sidebar"]["codeTextColor"] == "#0B0B0B"
    assert theme["yellowColor"] == "#0B0B0B"
    assert theme["yellowBackgroundColor"] == "#FFCC00"
    assert theme["yellowTextColor"] == "#0B0B0B"
    assert "#FFCC00" not in theme["chartCategoricalColors"]
