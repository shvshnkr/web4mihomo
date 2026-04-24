"""Разбор поля ввода: несколько строк URI (vless/trojan)."""


def split_bulk_vless_lines(raw: str) -> list[str]:
    """Непустые строки; комментарии #... и пустые пропускаются."""
    out: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        out.append(s)
    return out
