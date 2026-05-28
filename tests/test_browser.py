import pytest
import os
import http.server
import threading
from functools import partial
from modules.tools.browser import (
    initialize_browser,
    close_browser,
    browser_goto_url,
    browser_get_page_html,
    browser_evaluate_js,
    browser_get_cookies,
    browser_perform_action,
    browser_observe_page,
)


@pytest.fixture(scope="module")
def server_url():
    directory = os.path.join(os.path.dirname(__file__), "test_browser_fixtures")

    class CookieHandler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Set-Cookie", "test_cookie=test_value; Path=/")
            super().end_headers()

    handler = partial(CookieHandler, directory=directory)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_port
    url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=httpd.serve_forever)
    thread.daemon = True
    thread.start()

    yield url

    httpd.shutdown()
    httpd.server_close()
    thread.join()


@pytest.fixture(scope="module")
def artifacts_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("browser_artifacts"))


@pytest.fixture(scope="module", autouse=True)
def setup_browser(artifacts_dir):
    # Assume local ollama running model llama3.2:3b
    initialize_browser(provider="ollama", model="llama3.2:3b", artifacts_dir=artifacts_dir)
    yield
    close_browser()


@pytest.mark.asyncio
@pytest.mark.ollama
@pytest.mark.browser
async def test_browser_goto_url(server_url):
    url = f"{server_url}/index.html"
    result = await browser_goto_url(url)
    assert url in result
    # Check if some expected text from the site is present in the observation
    assert "shop" in result.lower()


@pytest.mark.asyncio
@pytest.mark.ollama
@pytest.mark.browser
async def test_browser_get_page_html(server_url):
    await browser_goto_url(f"{server_url}/index.html")
    result = await browser_get_page_html()
    assert "HTML content saved to artifact" in result
    artifact_path = result.split(": ")[1]
    assert os.path.exists(artifact_path)
    with open(artifact_path, "r") as f:
        html = f.read()
    assert "<html" in html.lower()
    # ginandjuice.shop content check
    assert "gin" in html.lower() or "juice" in html.lower()


@pytest.mark.asyncio
@pytest.mark.ollama
@pytest.mark.browser
async def test_browser_evaluate_js(server_url):
    await browser_goto_url(f"{server_url}/index.html")
    title = await browser_evaluate_js("document.title")
    assert "gin" in title.lower() or "juice" in title.lower()


@pytest.mark.asyncio
@pytest.mark.ollama
@pytest.mark.browser
async def test_browser_get_cookies(server_url):
    await browser_goto_url(f"{server_url}/index.html")
    cookies = await browser_get_cookies()
    assert isinstance(cookies, str)
    assert "name,value" in cookies.lower()


@pytest.mark.asyncio
@pytest.mark.ollama
@pytest.mark.browser
async def test_browser_observe_page(server_url):
    await browser_goto_url(f"{server_url}/index.html")
    observation = await browser_observe_page("All links on the page")
    assert len(observation) > 0
    assert isinstance(observation, list)
    assert isinstance(observation[0], str)


@pytest.mark.asyncio
@pytest.mark.ollama
@pytest.mark.browser
async def test_browser_perform_action(server_url):
    await browser_goto_url(f"{server_url}/index.html")
    result = await browser_perform_action("scroll down")
    assert result is not None
    assert isinstance(result, str)
