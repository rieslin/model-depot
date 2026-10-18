"""Read ComfyUI's extra_model_paths.yaml without requiring PyYAML.

The program depends only on aiohttp and Pillow. ComfyUI's own Python has PyYAML and then it is used; elsewhere a small
reader covers what that file uses: nested mappings, plain and quoted scalars, block scalars (`|`, `>`), comments.
Anything else (lists, anchors, flow style) raises YamlishError, and the wizard asks the user to add folders by hand.
"""
import re


class YamlishError(ValueError):
    pass


_BOOL = {"true": True, "yes": True, "on": True, "false": False, "no": False, "off": False}
_KEY_RE = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[^\s:#][^:#]*?)\s*:(?:\s+(?P<value>.*))?$""")


def _strip_comment(s):
    """Drop a trailing ` # comment` that is outside quotes."""
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _scalar(text):
    s = text.strip()
    if not s or s in ("~", "null", "Null", "NULL"):
        return None
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if s[0] in "[{&*!|>%@`":
        raise YamlishError("unsupported YAML: %r" % s[:30])
    low = s.lower()
    if low in _BOOL:
        return _BOOL[low]
    return s


def _parse(text):
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlishError("line %d: tab used for indentation" % number)
        lines.append((number, raw))

    pos = 0

    def skip_blank(i):
        while i < len(lines) and not _strip_comment(lines[i][1]).strip():
            i += 1
        return i

    def indent_of(i):
        raw = lines[i][1]
        return len(raw) - len(raw.lstrip(" "))

    def block_scalar(i, parent_indent, style):
        """Lines indented more than the key, joined: `|` keeps line breaks, `>` folds them into spaces."""
        chunk = []
        base = None
        while i < len(lines):
            raw = lines[i][1]
            if raw.strip():
                ind = len(raw) - len(raw.lstrip(" "))
                if ind <= parent_indent:
                    break
                if base is None:
                    base = ind
                chunk.append(raw[min(base, ind):].rstrip())
            else:
                chunk.append("")
            i += 1
        while chunk and not chunk[-1]:
            chunk.pop()
        if style.startswith(">"):
            body = " ".join(c for c in chunk if c)
        else:
            body = "\n".join(chunk)
        if body and not style.endswith("-"):
            body += "\n"
        return body, i

    def mapping(i, indent):
        out = {}
        while True:
            i = skip_blank(i)
            if i >= len(lines):
                return out, i
            ind = indent_of(i)
            if ind < indent:
                return out, i
            number, raw = lines[i]
            if ind > indent:
                raise YamlishError("line %d: unexpected indentation" % number)
            content = _strip_comment(raw).strip()
            if content.startswith("- ") or content == "-":
                raise YamlishError("line %d: lists are not supported" % number)
            m = _KEY_RE.match(content)
            if not m:
                raise YamlishError("line %d: expected `key: value`" % number)
            key = m.group("key").strip()
            if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
                key = key[1:-1]
            value = (m.group("value") or "").strip()
            i += 1
            if value in ("|", "|-", "|+", ">", ">-", ">+"):
                out[str(key)], i = block_scalar(i, indent, value)
            elif value:
                out[str(key)] = _scalar(value)
            else:
                j = skip_blank(i)
                if j < len(lines) and indent_of(j) > indent:
                    out[str(key)], i = mapping(j, indent_of(j))
                else:
                    out[str(key)] = None
        # not reached

    first = skip_blank(pos)
    if first >= len(lines):
        return None
    result, end = mapping(first, indent_of(first))
    end = skip_blank(end)
    if end < len(lines):
        raise YamlishError("line %d: unexpected content" % lines[end][0])
    return result


def loads(text):
    """Parse YAML text. PyYAML when the running Python has it, the small reader otherwise."""
    try:
        import yaml   # noqa: PLC0415 - optional, present in ComfyUI's Python
    except ImportError:
        return _parse(text)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise YamlishError(str(e)) from e
