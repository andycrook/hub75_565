try:
    import ujson as json
except ImportError:
    import json

try:
    import urequests
except ImportError:
    import requests as urequests

try:
    import time
    sleep_ms = time.sleep_ms
except (AttributeError, ImportError):
    import time

    def sleep_ms(value):
        time.sleep(value / 1000.0)

import gc

from hub75_565 import Hub75FrameBuffer, rgb565
import ntp_sync
from wifi_credentials import load_wifi_credentials


DISPLAY_WIDTH = 128
DISPLAY_HEIGHT = 64

WIFI_TIMEOUT_S = 15
SYNC_DS1307 = False

MARKET_REFRESH_MS = 60_000
SCROLL_STEP_MS = 35
TICKER_GAP_PX = 32
BITCOIN_ICON_PATH = "/rgb565/bitcoin_32x32.rgb565"
BITCOIN_ICON_WIDTH = 32
BITCOIN_ICON_HEIGHT = 32
BITCOIN_ICON_X = 80
BITCOIN_ICON_Y = 15

COINS = (
    ("XXBTZUSD", "BTC"),
    ("XETHZUSD", "ETH"),
    ("SOLUSD", "SOL"),
    ("XDGUSD", "DOGE"),
    ("ADAUSD", "ADA"),
    ("XXRPZUSD", "XRP"),
)

API_URL = (
    "https://api.kraken.com/0/public/Ticker"
    "?pair=XBTUSD,ETHUSD,SOLUSD,DOGEUSD,ADAUSD,XRPUSD"
)

BG_COLOR = rgb565(0, 6, 16)
HEADER_COLOR = rgb565(120, 210, 255)
FRAME_COLOR = rgb565(20, 56, 110)
BTC_LABEL_COLOR = rgb565(255, 190, 64)
BTC_PRICE_COLOR = rgb565(255, 255, 255)
POSITIVE_COLOR = rgb565(60, 230, 110)
NEGATIVE_COLOR = rgb565(255, 96, 96)
NEUTRAL_COLOR = rgb565(150, 180, 220)
TICKER_COLOR = rgb565(160, 220, 255)
ERROR_COLOR = rgb565(255, 120, 96)


_TIME_TICKS_MS = getattr(time, "ticks_ms", None)
_TIME_TICKS_DIFF = getattr(time, "ticks_diff", None)
_TIME_TICKS_ADD = getattr(time, "ticks_add", None)


def _ticks_ms():
    if _TIME_TICKS_MS is not None:
        return int(_TIME_TICKS_MS())
    return int(time.time() * 1000)


def _ticks_diff(now_ms, last_ms):
    if _TIME_TICKS_DIFF is not None:
        return int(_TIME_TICKS_DIFF(now_ms, last_ms))
    return int(now_ms - last_ms)


def _ticks_add(base_ms, delta_ms):
    if _TIME_TICKS_ADD is not None:
        return int(_TIME_TICKS_ADD(base_ms, delta_ms))
    return int(base_ms + delta_ms)


def _time_hm():
    now = time.localtime()
    return "%02d:%02d" % (now[3], now[4])


def _time_hms():
    now = time.localtime()
    return "%02d:%02d:%02d" % (now[3], now[4], now[5])


def _format_usd(value):
    value = float(value)
    if value >= 1000.0:
        return "$%d" % int(round(value))
    if value >= 1.0:
        return "$%.2f" % value
    if value >= 0.01:
        return "$%.4f" % value
    return "$%.6f" % value


def _format_change(value):
    value = float(value)
    return "%+.2f%%" % value


def _parse_market_data(payload):
    if payload.get("error"):
        raise RuntimeError("Kraken API error: %s" % ", ".join(payload["error"]))

    result = payload.get("result")
    if not result:
        raise ValueError("No market data returned")

    entries = []
    for pair_key, symbol in COINS:
        coin = result.get(pair_key)
        if not coin:
            continue

        last_values = coin.get("c") or ()
        open_value = coin.get("o")
        high_values = coin.get("h") or ()
        low_values = coin.get("l") or ()

        if not last_values:
            continue

        usd_value = float(last_values[0])
        open_price = float(open_value) if open_value not in (None, "") else usd_value
        if open_price:
            change_value = ((usd_value - open_price) / open_price) * 100.0
        else:
            change_value = 0.0

        high_24h = float(high_values[-1]) if high_values else usd_value
        low_24h = float(low_values[-1]) if low_values else usd_value

        entries.append(
            {
                "pair": pair_key,
                "symbol": symbol,
                "usd": usd_value,
                "change": change_value,
                "high": high_24h,
                "low": low_24h,
            }
        )
    if not entries:
        raise ValueError("No market data returned")
    return entries


def fetch_market_data():
    response = urequests.get(API_URL)
    try:
        status_code = getattr(response, "status_code", 200)
        if status_code != 200:
            raise RuntimeError("HTTP %s from Kraken" % status_code)
        payload = json.loads(response.text)
    finally:
        response.close()
    return _parse_market_data(payload)


def _build_ticker_text(entries, fetch_error=None):
    parts = []
    for entry in entries:
        parts.append(
            "%s %s %s" % (
                entry["symbol"],
                _format_usd(entry["usd"]),
                _format_change(entry["change"]),
            )
        )
    text = " | ".join(parts)
    if fetch_error:
        text += " | NET ERR: %s" % fetch_error
    return text


def _draw_status(display, line1, line2=""):
    surface = display.framebuf
    surface.fill(BG_COLOR)
    surface.rect(0, 0, display.width, display.height, FRAME_COLOR)
    surface.text(line1[:16], 4, 18, HEADER_COLOR)
    if line2:
        surface.text(line2[:16], 4, 34, NEUTRAL_COLOR)
    display.show()


def _draw_ticker(surface, y, text, scroll_px, color, width):
    cycle_px = len(text) * 8 + TICKER_GAP_PX
    start_x = width - scroll_px
    while start_x < width:
        surface.text(text, start_x, y, color)
        start_x += cycle_px


def _draw_market_screen(
    display,
    entries,
    ticker_text,
    scroll_px,
    last_fetch_text,
    bitcoin_icon=None,
    fetch_error=None,
):
    surface = display.framebuf
    surface.fill(BG_COLOR)
    surface.rect(0, 0, display.width, display.height, FRAME_COLOR)
    surface.hline(0, 10, display.width, FRAME_COLOR)
    surface.hline(0, 52, display.width, FRAME_COLOR)

    surface.text("CRYPTO", 4, 2, HEADER_COLOR)
    clock_text = _time_hm()
    surface.text(clock_text, display.width - (len(clock_text) * 8) - 4, 2, HEADER_COLOR)

    primary = entries[0]
    surface.text(primary["symbol"] + "/USD", 4, 16, BTC_LABEL_COLOR)
    surface.text(_format_usd(primary["usd"]), 4, 28, BTC_PRICE_COLOR)

    change_color = NEUTRAL_COLOR
    if primary["change"] > 0.0:
        change_color = POSITIVE_COLOR
    elif primary["change"] < 0.0:
        change_color = NEGATIVE_COLOR

    change_text = _format_change(primary["change"])
    status_text = "KRAKEN " + last_fetch_text[-5:]
    surface.text(change_text, 4, 40, change_color)
    #surface.text(status_text, display.width - (len(status_text) * 8) - 4, 40, NEUTRAL_COLOR)

    range_text = "%s-%s" % (_format_usd(primary["low"]), _format_usd(primary["high"]))
    #surface.text(range_text[:16], 60, 16, NEUTRAL_COLOR)

    if bitcoin_icon is not None:
        display.blit(bitcoin_icon, BITCOIN_ICON_X, BITCOIN_ICON_Y)

    ticker_color = TICKER_COLOR if not fetch_error else ERROR_COLOR
    _draw_ticker(surface, 55, ticker_text, scroll_px, ticker_color, display.width)


def _connect_wifi_and_sync():
    ssid, password = load_wifi_credentials()
    wlan = ntp_sync.connect_wifi(ssid, password, timeout=WIFI_TIMEOUT_S)
    try:
        ntp_sync.sync_time(use_ds1307=SYNC_DS1307)
    except Exception as exc:
        print("NTP sync failed:", exc)
    return wlan


def _ensure_wifi(wlan):
    if wlan is not None and wlan.status() == 3:
        return wlan
    return _connect_wifi_and_sync()


def main():
    display = Hub75FrameBuffer(width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT)
    bitcoin_icon = display.load_rgb565_sprite(
        BITCOIN_ICON_PATH,
        BITCOIN_ICON_WIDTH,
        BITCOIN_ICON_HEIGHT,
    )
    wlan = None
    entries = None
    ticker_text = "Connecting to WiFi..."
    fetch_error = None
    last_fetch_text = "--:--:--"
    scroll_px = 0
    next_fetch_ms = 0
    next_scroll_ms = _ticks_ms()

    try:
        _draw_status(display, "Crypto ticker", "WiFi connect...")
        wlan = _connect_wifi_and_sync()
        _draw_status(display, "WiFi connected", "Fetching market")

        while True:
            now = _ticks_ms()

            if entries is None or _ticks_diff(now, next_fetch_ms) >= 0:
                try:
                    wlan = _ensure_wifi(wlan)
                    entries = fetch_market_data()
                    fetch_error = None
                    ticker_text = _build_ticker_text(entries)
                    last_fetch_text = _time_hms()
                    print("Updated crypto prices at", last_fetch_text)
                except Exception as exc:
                    fetch_error = str(exc)
                    print("Crypto fetch failed:", fetch_error)
                    if entries is None:
                        ticker_text = "Crypto fetch failed: %s" % fetch_error
                    else:
                        ticker_text = _build_ticker_text(entries, fetch_error=fetch_error)
                gc.collect()
                next_fetch_ms = _ticks_add(_ticks_ms(), MARKET_REFRESH_MS)

            if _ticks_diff(now, next_scroll_ms) >= 0:
                if entries is None:
                    _draw_status(display, "Crypto ticker", ticker_text)
                else:
                    _draw_market_screen(
                        display,
                        entries,
                        ticker_text,
                        scroll_px,
                        last_fetch_text,
                        bitcoin_icon=bitcoin_icon,
                        fetch_error=fetch_error,
                    )
                    display.show()

                    cycle_px = len(ticker_text) * 8 + TICKER_GAP_PX
                    if cycle_px <= 0:
                        cycle_px = display.width
                    scroll_px = (scroll_px + 1) % cycle_px
                next_scroll_ms = _ticks_add(now, SCROLL_STEP_MS)

            sleep_ms(20)

    except KeyboardInterrupt:
        print("Stopping crypto ticker...")
    finally:
        display.deinit()


if __name__ == "__main__":
    main()