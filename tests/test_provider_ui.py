"""Provider status displayed by the sandbox reflects the running provider."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _provider_display(health):
    if NODE is None:
        pytest.skip("Node.js is needed to execute the sandbox JavaScript")
    html = HTML.read_text(encoding="utf-8")
    escape = html[html.index("function esc("):html.index("\nfunction initials(")]
    check = html[html.index("async function checkProvider()"):html.index("\nasync function seedAndLoad()")]
    js = f"""
    const health = {json.dumps(health, ensure_ascii=True)};
    const box = {{innerHTML: ''}};
    const document = {{getElementById: () => box}};
    async function api() {{ return health; }}
    {escape}
    {check}
    checkProvider().then(() => process.stdout.write(box.innerHTML));
    """
    return subprocess.run([NODE, "-e", js], capture_output=True,
                          encoding="utf-8", check=True).stdout


def test_fake_provider_with_key_is_still_shown_as_fake():
    html = _provider_display({"provider": "FakeProvider",
                              "openrouter_configured": True,
                              "openrouter_model": "example-model"})
    assert "Fake" in html
    assert "Luna qua OpenRouter" not in html
    assert "chưa cấu hình OpenRouter key" not in html


def test_missing_provider_is_shown_as_misconfigured():
    html = _provider_display({"provider": None, "status": "misconfigured",
                              "openrouter_configured": False})
    assert "không" in html.lower() or "lỗi" in html.lower()
    assert "Fake (mô phỏng offline)" not in html


def test_openrouter_model_is_escaped_and_api_key_not_shown():
    html = _provider_display({"provider": "OpenRouterProvider",
                              "openrouter_configured": True,
                              "openrouter_model": "model<custom>",
                              "openrouter_api_key": "private-key-example"})
    assert "OpenRouter" in html
    assert "model&lt;custom&gt;" in html
    assert "private-key-example" not in html
