"""Actual browser checks and README screenshots; API and Streamlit must already be running."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = os.getenv("DASHBOARD_URL", "http://localhost:8501")
PAGES = [
    ("", "Service overview"),
    ("quality", "Model quality"),
    ("drift", "Data drift"),
    ("latency", "Latency & reliability"),
    ("cost", "Cost & usage"),
    ("incidents", "Incident investigation"),
    ("live", "Inference workbench"),
]


def main():
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/_stcore/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Dashboard did not start")
    directory = ROOT / "docs/screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("BROWSER_CHANNEL") or None)
        page = browser.new_page(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
        for route, title in PAGES:
            page.goto(f"{BASE}/{route}")
            page.get_by_role("heading", name=title, exact=True).wait_for(timeout=45000)
            page.wait_for_selector(
                '[data-testid="stMetric"], [data-testid="stTextArea"], [data-testid="stDataFrame"]'
            )
            # Wait for Streamlit to finish the current script before checking charts/errors.
            page.wait_for_timeout(1500)
            assert page.locator('[data-testid="stException"]').count() == 0, page.content()
            if route not in {"incidents", "live"}:
                try:
                    page.locator('[data-testid="stPlotlyChart"]').first.wait_for(timeout=30000)
                except Exception:
                    page.screenshot(path=str(directory / "debug.png"), full_page=True)
                    print(page.locator("body").inner_text(), flush=True)
                    print(
                        page.locator("[data-testid]").evaluate_all(
                            "(els)=>[...new Set(els.map(e=>e.dataset.testid))]"
                        ),
                        flush=True,
                    )
                    raise
            page.screenshot(path=str(directory / f"{route or 'overview'}.png"), full_page=True)
            print(f"PASS desktop /{route} - {title}", flush=True)
        page.get_by_role("button", name="Classify ticket", exact=True).click()
        page.get_by_text('"event_id"', exact=False).first.wait_for(timeout=15000)
        page.get_by_role("button", name="Record review", exact=True).click()
        page.get_by_text("Evidence recorded.", exact=False).wait_for(timeout=15000)
        assert page.locator('[data-testid="stException"]').count() == 0
        print("PASS live browser inference -> durable feedback", flush=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(BASE)
        page.get_by_role("heading", name="Service overview", exact=True).wait_for(timeout=45000)
        page.wait_for_timeout(1500)
        assert page.locator('[data-testid="stException"]').count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
        page.screenshot(path=str(directory / "mobile.png"), full_page=True)
        print("PASS mobile overview / no horizontal page overflow", flush=True)
        browser.close()
    # Exercise actual HTTP servers and persisted snapshot publication, not TestClient or mocks.
    api = os.getenv("API_URL", "http://localhost:8000")
    with httpx.Client(headers={"X-API-Key": os.getenv("API_KEY", "")}, timeout=30) as client:
        response = client.post(
            f"{api}/v1/predict", json={"text": "Please refund my duplicate payment"}
        )
        response.raise_for_status()
        prediction = response.json()
        evidence = client.post(
            f"{api}/v1/labels",
            json={
                "event_id": prediction["event_id"],
                "label": "billing",
                "external_id": f"smoke-{uuid.uuid4()}",
            },
        )
        evidence.raise_for_status()
        subprocess.run(
            [sys.executable, "-m", "signaldesk.cli", "monitor"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        history = client.get(f"{api}/v1/metrics")
        history.raise_for_status()
        assert history.json()["snapshots"][-1]["quality"]["label_count"] >= 1
    print("PASS HTTP inference -> delayed label -> monitor -> metrics API", flush=True)


if __name__ == "__main__":
    main()
