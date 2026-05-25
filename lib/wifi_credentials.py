DEFAULT_WIFI_CREDENTIALS_PATH = "/WIFI/WIFI.txt"


def _strip_inline_comment(text):
    quote_char = None
    out = []
    for char in text:
        if char in ("'", '"'):
            if quote_char is None:
                quote_char = char
            elif quote_char == char:
                quote_char = None
        if char == "#" and quote_char is None:
            break
        out.append(char)
    return "".join(out).strip()


def _parse_value(raw_value):
    value = str(raw_value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_wifi_credentials(path=DEFAULT_WIFI_CREDENTIALS_PATH):
    values = {}

    with open(path, "r") as fp:
        for line_number, raw_line in enumerate(fp, 1):
            line = _strip_inline_comment(raw_line)
            if not line:
                continue
            if "=" not in line:
                raise ValueError(
                    "Malformed WiFi credentials line %d in %s" % (line_number, path)
                )

            key, raw_value = line.split("=", 1)
            key_name = key.strip().lower()
            if key_name not in ("ssid", "password"):
                continue

            parsed_value = _parse_value(raw_value)
            if not parsed_value:
                raise ValueError(
                    "Missing value for %s in %s" % (key_name, path)
                )
            values[key_name] = parsed_value

    ssid = values.get("ssid")
    password = values.get("password")
    if not ssid or not password:
        raise ValueError(
            "WiFi credentials file must define ssid and password: %s" % path
        )

    return ssid, password


__all__ = ("DEFAULT_WIFI_CREDENTIALS_PATH", "load_wifi_credentials")