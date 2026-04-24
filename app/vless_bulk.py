"""Разбор поля ввода: несколько строк URI или base64 blob."""

from app.subscription_client import links_from_base64_text


def split_bulk_vless_lines(raw: str) -> list[str]:
    """Непустые строки; комментарии #... и пустые пропускаются.

    If the field contains a subscription-style base64 blob, expand it to the
    decoded URI list so manual add can handle pasted provider payloads too.
    """
    decoded = links_from_base64_text(raw or "")
    if decoded and not any("://" in line for line in (raw or "").splitlines()):
        return decoded

    out: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if "://" in s:
            out.append(s)
            continue
        decoded_line = links_from_base64_text(s)
        if decoded_line:
            out.extend(decoded_line)
        else:
            out.append(s)
    return out
