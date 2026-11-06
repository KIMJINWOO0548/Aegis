"""Browser checks against an explicitly synthetic, isolated local UI fixture.

Requires an already installed Playwright runtime/browser and a built UI. Session,
dashboard and SignalR responses exist only as Playwright routes; this script never
changes gateway authentication, uses a token, contacts equipment or writes control.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def snapshot(origin: float, second: int, value: float, observed_second: int | None = None) -> dict:
    observed_second = second if observed_second is None else observed_second
    stamp = lambda at: datetime.fromtimestamp(origin + at, UTC).isoformat().replace("+00:00", "Z")
    devices = []
    for device_id, offset in (("synthetic-1", 0), ("synthetic-2", 100)):
        observations = [
            {"observationId": f"{device_id}:{key}:{observed_second}", "signalKey": key,
             "value": value + offset + extra, "unit": unit,
             "quality": "GOOD", "observedAtUtc": stamp(observed_second)}
            for key, extra, unit in (("actualFlowLpm", 0, "L/min"), ("supplyPressureKpa", 50, "kPa"))
        ]
        devices.append({"deviceId": device_id, "equipmentId": None, "moduleId": None,
                        "connection": {"state": "CONNECTED"}, "ingestion": None,
                        "observations": observations, "diagnostics": None, "warnings": [],
                        "review": None, "engineState": "ARCHIVE_NOT_CONFIGURED", "nativeSequence": None})
    return {"schemaVersion": "dashboard-1", "generatedAtUtc": stamp(second), "sourceMode": "FIXTURES",
            "upstreamState": "CONNECTED", "message": "Synthetic browser test only",
            "devices": devices, "inference": {"state": "NOT_REGISTERED", "message": "No model"}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "current"), default="current")
    args = parser.parse_args()
    if args.output.exists() or not (args.dist / "index.html").is_file():
        parser.error("Choose a fresh output directory and an existing built UI")
    args.output.mkdir(parents=True)
    from playwright.sync_api import sync_playwright

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(args.dist.resolve())))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = time.time() - 6
    latest = [snapshot(origin, 0, 10)]
    sockets = []
    errors, checks = [], []
    try:
        with sync_playwright() as playwright:
            installed_chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": str(installed_chrome)} if installed_chrome.is_file() else {}))
            page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route(request):
                url = request.request.url
                if "/api/session" in url:
                    request.fulfill(status=200, json={"fixture": "synthetic-session-only"})
                elif "/api/dashboard" in url:
                    request.fulfill(status=200, json=latest[0])
                elif "/hubs/dashboard/negotiate" in url:
                    request.fulfill(status=200, json={"negotiateVersion": 1, "connectionId": "synthetic-only",
                                                     "connectionToken": "synthetic-only", "availableTransports": [
                                                         {"transport": "WebSockets", "transferFormats": ["Text"]}]})
                elif url.startswith(f"http://127.0.0.1:{server.server_port}/"):
                    request.continue_()
                else:
                    request.abort()

            page.route("**/*", route)

            def connect(socket):
                sockets.append(socket)
                def message(value):
                    if '"protocol"' in str(value):
                        socket.send("{}\x1e")
                socket.on_message(message)

            page.route_web_socket("**/hubs/dashboard*", connect)
            page.add_init_script("""window.__syntheticChartClears=0;
const original=CanvasRenderingContext2D.prototype.clearRect;
CanvasRenderingContext2D.prototype.clearRect=function(...args){
 if(this.canvas.closest('.chart'))window.__syntheticChartClears++;
 return original.apply(this,args);
};""")
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
            page.get_by_role("button", name="실시간 신호", exact=True).click()
            page.locator(".chart canvas").wait_for()
            assert sockets, "Synthetic SignalR WebSocket was not established"

            def emit(second, value, observed_second=None):
                latest[0] = snapshot(origin, second, value, observed_second)
                sockets[-1].send(json.dumps({"type": 1, "target": "snapshot", "arguments": [latest[0]]}) + "\x1e")
                page.wait_for_timeout(150)

            emit(1, 80)
            emit(2, 10)
            emit(6, 11)
            emit(7, 999, 1)  # duplicate delayed observation must not replace the received pulse
            page.wait_for_timeout(4100)  # settle the honest current→historical transition
            page.locator(".chart-window select").select_option("30")
            page.wait_for_timeout(350)
            page.evaluate("window.__syntheticChartClears=0")
            page.wait_for_timeout(3300)
            redraws = page.evaluate("window.__syntheticChartClears")
            checks.append({"name": "stable retained series / 3.3s wallclock ticks", "chart_canvas_clears": redraws})
            if args.mode == "current":
                assert redraws == 0, f"Unexpected idle chart repaint: {redraws}"
                # The fixture's final 11 is isolated after a gap at the right grid
                # edge. Check actual canvas paint, not just retained/table data.
                marker_pixels = page.evaluate("""() => {
const host=document.querySelector('.chart');let count=0;
for(const canvas of host.querySelectorAll('canvas')){
 const context=canvas.getContext('2d');const scale=canvas.width/host.clientWidth;
 const x=Math.round((host.clientWidth-27)*scale),y=Math.round(24*scale);
 const width=Math.max(1,Math.round(10*scale)),height=Math.max(1,Math.round((host.clientHeight-56)*scale));
 const pixels=context.getImageData(x,y,width,height).data;
 for(let i=0;i<pixels.length;i+=4){
  if(Math.abs(pixels[i]-113)<9&&Math.abs(pixels[i+1]-131)<9&&Math.abs(pixels[i+2]-157)<9&&pixels[i+3]>220)count++;
 }
}return count;
}""")
                assert marker_pixels > 0, "Final isolated valid sample is present in data but invisible on canvas"
                checks.append({"name": "actual canvas marker for last isolated valid sample", "matching_pixels": marker_pixels, "pass": True})
                summary = page.locator(".inspection-details summary")
                summary.focus()
                page.keyboard.press("Enter")
                assert page.locator(".inspection-details").get_attribute("open") is not None
                table = page.locator(".inspection-table tbody")
                assert table.locator("tr").count() == 5
                assert table.get_by_text("80", exact=True).count() == 1
                assert table.get_by_text("수신 간격", exact=True).count() == 1
                assert table.get_by_text("999", exact=True).count() == 0
                checks.append({"name": "keyboard disclosure + pulse/gap/delayed data", "pass": True})
                toggle = page.locator(".inspection-toggle")
                toggle.focus()
                page.keyboard.press(" ")
                assert toggle.get_attribute("aria-pressed") == "true"
                frozen_table = table.inner_text()
                frozen_times = page.locator(".chart-window-time").inner_text()
                emit(max(8, int(time.time() - origin)), 12)
                assert table.inner_text() == frozen_table
                assert page.locator(".chart-window-time").inner_text() == frozen_times
                assert "FIXTURES" in page.locator(".inspection-state").inner_text()
                assert "고정된 조사 화면" in page.locator(".inspection-state").inner_text()
                page.screenshot(path=str(args.output / "desktop-frozen.png"), full_page=True)
                toggle.focus()
                page.keyboard.press("Enter")
                assert table.locator("tr").count() > 5
                checks.append({"name": "immutable freeze with continuing synthetic collection + resume", "pass": True})
                toggle.click()
                page.get_by_role("combobox", name="차트 신호 선택").select_option("supplyPressureKpa")
                assert page.locator(".inspection-toggle").get_attribute("aria-pressed") == "false"
                assert page.locator(".chart-window select").input_value() == "all"
                checks.append({"name": "signal change resets snapshot and range", "pass": True})
                page.locator(".inspection-toggle").click()
                page.get_by_role("combobox", name="장비 선택").select_option("synthetic-2")
                assert page.locator(".inspection-toggle").get_attribute("aria-pressed") == "false"
                assert "synthetic-2" in page.locator(".inspection-state").inner_text()
                checks.append({"name": "device change resets snapshot context", "pass": True})
                page.locator(".inspection-details summary").click()
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(400)
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), "Page overflows narrow viewport"
                page.screenshot(path=str(args.output / "mobile-table.png"), full_page=True)
                checks.append({"name": "390px viewport, no page horizontal overflow", "pass": True})
            assert not errors, errors
            browser.close()
        record = {"mode": args.mode, "scope": "synthetic browser routes; not real equipment, gateway auth or WebView2",
                  "checks": checks, "page_errors": errors,
                  "built_index_sha256": hashlib.sha256((args.dist / "index.html").read_bytes()).hexdigest()}
        (args.output / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
