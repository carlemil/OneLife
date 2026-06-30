"""Write map ellipse placements from the editor back into the authored YAML.

The content repo is the source of truth, so the map editor's "Save" rewrites the
`map` / `world_exit` keys directly in the source files. We do this as a SURGICAL
text edit — find the entity inside its top-level section and insert/replace a
single inline `field: {x,y,rx,ry}` line — so comments, ordering, indentation and
every other line are left byte-for-byte unchanged. The /content mount must be
read-write (see docker-compose.yml).
"""
import glob
import os
import re

from . import gamestate


def _files():
    content_dir = gamestate.active_dir()
    return sorted(glob.glob(os.path.join(content_dir, "*.yaml"))
                  + glob.glob(os.path.join(content_dir, "*.yml")))


def _fmt(value: dict) -> str:
    """Inline flow mapping in the house style, e.g.
    `{x: 0.42, y: 0.55, rx: 0.06, ry: 0.04, scale: 1.5}` (only the keys present)."""
    return "{" + ", ".join(f"{k}: {value[k]}" for k in ("x", "y", "rx", "ry", "scale")
                           if k in value) + "}"


def _section_bounds(lines: list[str], section: str):
    """[start, end) line range of a top-level list section (e.g. `nodes:`)."""
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"^{re.escape(section)}:\s*$", ln):
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^[A-Za-z_][\w]*:\s*$", lines[j]):   # next top-level list key
            end = j
            break
    return start, end


def _write_field(section: str, entity_id: str, field: str, value: dict) -> None:
    """Find `- id: <entity_id>` within `section` in some file and insert/replace its
    inline `field:` line. Raises FileNotFoundError if no file defines it."""
    new_line_body = f"{field}: {_fmt(value)}"
    id_re = re.compile(rf"^(\s*)-(\s+)id:\s*['\"]?{re.escape(entity_id)}['\"]?\s*$")
    for path in _files():
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        lines = text.splitlines(keepends=True)
        bounds = _section_bounds([l.rstrip("\r\n") for l in lines], section)
        if not bounds:
            continue
        sec_start, sec_end = bounds
        for i in range(sec_start, sec_end):
            m = id_re.match(lines[i].rstrip("\r\n"))
            if not m:
                continue
            dash_col = len(m.group(1))
            child_indent = dash_col + 2
            pad = " " * child_indent
            nl = "\r\n" if lines[i].endswith("\r\n") else "\n"
            # This item spans until the next line indented <= dash_col (next item /
            # dedent / new section). Look for an existing `field:` to replace.
            item_end = sec_end
            for j in range(i + 1, sec_end):
                stripped = lines[j].rstrip("\r\n")
                if stripped.strip() == "":
                    continue
                if len(stripped) - len(stripped.lstrip(" ")) <= dash_col:
                    item_end = j
                    break
            fld_re = re.compile(rf"^{pad}{re.escape(field)}:\s")
            replaced = False
            for j in range(i + 1, item_end):
                if fld_re.match(lines[j]):
                    lines[j] = pad + new_line_body + nl
                    replaced = True
                    break
            if not replaced:
                lines.insert(i + 1, pad + new_line_body + nl)
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))
            return
    raise FileNotFoundError(f"no YAML defines {section[:-1]} {entity_id}")


def set_node_map(node_id: str, map_dict: dict) -> None:
    _write_field("nodes", node_id, "map", map_dict)


def set_node_pos(node_id: str, x, y) -> None:
    """Persist a node's graph-editor position back into the authored YAML as an
    inline `pos: {x, y}` (rounded to whole pixels — sub-pixel drift is noise)."""
    _write_field("nodes", node_id, "pos", {"x": round(float(x)), "y": round(float(y))})


def _fmt_points(points) -> str:
    """Normalized spline waypoints as an inline flow list: `[[x, y], [x, y]]`."""
    return "[" + ", ".join(
        "[" + ", ".join(str(round(float(c), 4)) for c in p) + "]" for p in points) + "]"


def _write_field_anywhere(entity_id: str, field: str, value_body: str) -> None:
    """Like _write_field but not scoped to a top-level section: find `- id:
    <entity_id>` at ANY indentation in any file (edges live inline under nodes OR as
    standalone entries) and insert/replace its inline `field:` line. Comments and
    every other line are left byte-for-byte unchanged."""
    id_re = re.compile(rf"^(\s*)-(\s+)id:\s*['\"]?{re.escape(entity_id)}['\"]?\s*$")
    for path in _files():
        with open(path, encoding="utf-8", newline="") as fh:
            lines = fh.read().splitlines(keepends=True)
        for i in range(len(lines)):
            m = id_re.match(lines[i].rstrip("\r\n"))
            if not m:
                continue
            dash_col = len(m.group(1))
            pad = " " * (dash_col + 2)
            nl = "\r\n" if lines[i].endswith("\r\n") else "\n"
            item_end = len(lines)               # this item ends at the next dedent
            for j in range(i + 1, len(lines)):
                s = lines[j].rstrip("\r\n")
                if s.strip() == "":
                    continue
                if len(s) - len(s.lstrip(" ")) <= dash_col:
                    item_end = j
                    break
            fld_re = re.compile(rf"^{pad}{re.escape(field)}:\s")
            for j in range(i + 1, item_end):
                if fld_re.match(lines[j]):
                    lines[j] = pad + f"{field}: {value_body}" + nl
                    break
            else:
                lines.insert(i + 1, pad + f"{field}: {value_body}" + nl)
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))
            return
    raise FileNotFoundError(f"no YAML defines edge {entity_id}")


def set_edge_road(edge_id: str, points) -> None:
    _write_field_anywhere(edge_id, "road", _fmt_points(points))


def set_cell_field(cell_id: str, field: str, value: dict) -> None:
    _write_field("cells", cell_id, field, value)


def _yaml_str(s) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_standalone_edge(edge: dict) -> str:
    """Append a new edge to the top-level `edges:` section (e.g. edges.yaml), as an
    inline-style block matching the house format. Returns the file path written."""
    block = [
        f"  - id: {edge['id']}",
        f"    from: {edge['from']}",
        f"    to: {edge['to']}",
        f"    label: {_yaml_str(edge['label'])}",
        f"    effects: {{log: {_yaml_str((edge.get('effects') or {}).get('log', ''))}}}",
        f"    sort_order: {int(edge.get('sort_order', 9))}",
    ]
    for path in _files():
        with open(path, encoding="utf-8", newline="") as fh:
            lines = fh.read().splitlines(keepends=True)
        bounds = _section_bounds([l.rstrip("\r\n") for l in lines], "edges")
        if not bounds:
            continue
        _, sec_end = bounds
        nl = "\r\n" if (lines and lines[0].endswith("\r\n")) else "\n"
        if lines and not lines[-1].endswith(("\n", "\r\n")):
            lines[-1] = lines[-1] + nl
        blk = [b + nl for b in block]
        lines[sec_end:sec_end] = blk     # append at end of the edges section
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(lines))
        return path
    raise FileNotFoundError("no YAML has a top-level `edges:` section")
