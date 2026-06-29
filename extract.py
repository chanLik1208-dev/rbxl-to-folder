#!/usr/bin/env python3
"""
extract.py — Roblox place/model (.rbxl/.rbxlx/.rbxm/.rbxmx) -> Rojo-style file tree.

Goal: produce a folder of files that an AI can read to understand a place,
focused on script source code plus a full hierarchy outline.

Engine selection:
  1. Primary: a small Rust CLI built on the rbx-dom crates (full fidelity).
     Auto-built with `cargo build --release` on first use.
  2. Fallback: a pure-Python parser in this file (XML via stdlib; binary via a
     focused INST/PROP/PRNT decoder that only pulls Name/Source strings).

Both engines emit the SAME intermediate tree, which is then materialized:

  <out>/
    README.md       explanation of the layout for the AI
    tree.txt        full indented hierarchy "Name [ClassName]"
    manifest.json   machine-readable instance list + script file mapping
    src/            Rojo-style mirror; folders only along script-bearing paths

Usage:
    python extract.py <input> [-o OUTDIR] [--engine auto|rust|python]

Env:
    RBXL_FORCE_PYTHON=1   force the Python fallback (same as --engine python)
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys

SCRIPT_CLASSES = {"Script", "LocalScript", "ModuleScript"}
HERE = os.path.dirname(os.path.abspath(__file__))
RUST_DIR = os.path.join(HERE, "rust")


# --------------------------------------------------------------------------- #
# Intermediate tree shape (produced by both engines):
#   node = {"name": str, "class": str, "children": [node, ...],
#           "source": str (script only), "disabled": bool (optional)}
#   root = {"children": [node, ...]}
# --------------------------------------------------------------------------- #


# =========================================================================== #
# Rust engine
# =========================================================================== #
def rust_exe_path():
    name = "rbxl-engine.exe" if os.name == "nt" else "rbxl-engine"
    return os.path.join(RUST_DIR, "target", "release", name)


def ensure_rust_built():
    exe = rust_exe_path()
    if os.path.exists(exe):
        return exe
    if shutil.which("cargo") is None:
        raise RuntimeError("cargo not found")
    print("[extract] building Rust engine (first run, may take a minute)...",
          file=sys.stderr)
    subprocess.run(
        ["cargo", "build", "--release"],
        cwd=RUST_DIR, check=True,
        stdout=sys.stderr, stderr=sys.stderr,
    )
    if not os.path.exists(exe):
        raise RuntimeError("cargo build finished but binary missing")
    return exe


def tree_via_rust(input_path):
    exe = ensure_rust_built()
    proc = subprocess.run(
        [exe, input_path], check=True,
        stdout=subprocess.PIPE, stderr=sys.stderr,
    )
    return json.loads(proc.stdout)


# =========================================================================== #
# Python fallback — XML (.rbxlx / .rbxmx)
# =========================================================================== #
def tree_via_python_xml(input_path):
    import xml.etree.ElementTree as ET

    tree = ET.parse(input_path)
    root = tree.getroot()  # <roblox>

    def parse_item(item):
        cls = item.get("class", "Unknown")
        name = cls
        source = None
        for prop in item.findall("./Properties/*"):
            pname = prop.get("name")
            if pname == "Name" and prop.tag in ("string", "ProtectedString"):
                name = prop.text or ""
            elif pname == "Source":
                source = prop.text or ""
        node = {"name": name, "class": cls, "children": []}
        if cls in SCRIPT_CLASSES:
            node["source"] = source if source is not None else ""
        for child in item.findall("./Item"):
            node["children"].append(parse_item(child))
        return node

    children = [parse_item(it) for it in root.findall("./Item")]
    return {"children": children}


# =========================================================================== #
# Python fallback — binary (.rbxl / .rbxm)
# =========================================================================== #
class _Reader:
    """Little-endian cursor over a bytes buffer."""

    def __init__(self, data):
        self.d = data
        self.p = 0

    def take(self, n):
        b = self.d[self.p:self.p + n]
        if len(b) != n:
            raise EOFError("unexpected end of chunk")
        self.p += n
        return b

    def u8(self):
        return self.take(1)[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def i32(self):
        return struct.unpack("<i", self.take(4))[0]

    def string(self):
        n = self.u32()
        return self.take(n)

    def remaining(self):
        return len(self.d) - self.p


def _decompress_chunk(reader_file):
    """Read one chunk header+payload from the file-level reader.
    Returns (name, payload_bytes) or (name, None) at END."""
    name = reader_file.take(4)
    comp_len = reader_file.u32()
    uncomp_len = reader_file.u32()
    reader_file.take(4)  # reserved
    if comp_len == 0:
        payload = bytes(reader_file.take(uncomp_len))
    else:
        raw = bytes(reader_file.take(comp_len))
        if raw[:4] == b"\x28\xb5\x2f\xfd":  # zstd magic
            import zstandard
            payload = zstandard.ZstdDecompressor().decompress(raw)
        else:  # raw LZ4 block
            import lz4.block
            payload = lz4.block.decompress(raw, uncompressed_size=uncomp_len)
    return name, payload


def _read_referent_array(r, n):
    """n interleaved+zigzag i32, accumulated (cumulative sum)."""
    raw = r.take(n * 4)
    out = []
    last = 0
    for j in range(n):
        u = (raw[0 * n + j] << 24) | (raw[1 * n + j] << 16) \
            | (raw[2 * n + j] << 8) | raw[3 * n + j]
        v = (u >> 1) ^ -(u & 1)   # zigzag decode -> signed
        last += v
        out.append(last)
    return out


def tree_via_python_binary(input_path):
    with open(input_path, "rb") as f:
        data = f.read()

    if not data.startswith(b"<roblox!"):
        raise ValueError("not a binary rbxl/rbxm file")

    # File header: 14-byte magic + version(u16) + classCount(u32)
    #              + instanceCount(u32) + reserved(8)
    rf = _Reader(data)
    rf.take(14)            # <roblox!\x89\xff\r\n\x1a\n
    rf.take(2)             # version
    rf.u32()               # class count
    rf.u32()               # instance count
    rf.take(8)             # reserved

    class_names = {}       # classId -> className
    class_refs = {}        # classId -> [referent, ...] (INST order)
    inst = {}              # referent -> {"name","class","source"?,"children":[],"_parent":None}

    while True:
        try:
            name, payload = _decompress_chunk(rf)
        except EOFError:
            break
        tag = name.rstrip(b"\x00")

        if tag == b"END":
            break
        if payload is None:
            continue
        r = _Reader(payload)

        if tag == b"INST":
            class_id = r.u32()
            class_name = r.string().decode("utf-8", "replace")
            object_format = r.u8()
            count = r.u32()
            refs = _read_referent_array(r, count)
            if object_format == 1:
                r.take(count)  # isService bytes
            class_names[class_id] = class_name
            class_refs[class_id] = refs
            for ref in refs:
                node = {"name": class_name, "class": class_name, "children": []}
                if class_name in SCRIPT_CLASSES:
                    node["source"] = ""
                node["_parent"] = None
                inst[ref] = node

        elif tag == b"PROP":
            class_id = r.u32()
            prop_name = r.string().decode("utf-8", "replace")
            type_id = r.u8()
            # Only String-type Name / Source matter for script extraction.
            if type_id != 0x01 or prop_name not in ("Name", "Source"):
                continue
            refs = class_refs.get(class_id, [])
            for ref in refs:
                s = r.string()
                node = inst.get(ref)
                if node is None:
                    continue
                if prop_name == "Name":
                    node["name"] = s.decode("utf-8", "replace")
                elif prop_name == "Source" and node["class"] in SCRIPT_CLASSES:
                    node["source"] = s.decode("utf-8", "replace")

        elif tag == b"PRNT":
            r.u8()              # version
            count = r.u32()
            childs = _read_referent_array(r, count)
            parents = _read_referent_array(r, count)
            for i in range(count):
                child = inst.get(childs[i])
                if child is not None:
                    child["_parent"] = parents[i]

        # META / SSTR / SIGN / others: ignored.

    # Assemble tree from parent links.
    roots = []
    for ref, node in inst.items():
        pid = node.pop("_parent", None)
        if pid is not None and pid in inst:
            inst[pid]["children"].append(node)
        else:
            roots.append(node)
    return {"children": roots}


def tree_via_python(input_path):
    with open(input_path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"<roblox!"):
        return tree_via_python_binary(input_path)
    return tree_via_python_xml(input_path)


# =========================================================================== #
# Materialization — intermediate tree -> Rojo-style file tree
# =========================================================================== #
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize(name):
    if not name:
        name = "_unnamed"
    name = _INVALID.sub("_", name)
    name = name.rstrip(" .") or "_unnamed"
    if name.upper() in _RESERVED:
        name = "_" + name
    return name[:120]


def script_suffix(cls):
    return {
        "Script": ".server.lua",
        "LocalScript": ".client.lua",
        "ModuleScript": ".lua",
    }[cls]


def has_script_descendant(node):
    if node["class"] in SCRIPT_CLASSES:
        return True
    return any(has_script_descendant(c) for c in node["children"])


def dedupe(name, used):
    base = name
    i = 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    used.add(name)
    return name


def materialize(node, dst_dir, src_root, inst_path, manifest):
    """Write `node` into dst_dir. Returns nothing; appends to manifest."""
    cls = node["class"]
    is_script = cls in SCRIPT_CLASSES
    script_children = [c for c in node["children"] if has_script_descendant(c)]

    entry = {
        "path": inst_path,
        "class": cls,
        "isScript": is_script,
        "file": None,
    }
    if node.get("disabled"):
        entry["disabled"] = True
    manifest.append(entry)

    if is_script:
        suffix = script_suffix(cls)
        source = node.get("source", "")
        if script_children:
            # script with (script-bearing) children -> folder + init file
            folder = os.path.join(dst_dir, sanitize(node["name"]))
            os.makedirs(folder, exist_ok=True)
            init_name = "init" + suffix
            with open(os.path.join(folder, init_name), "w", encoding="utf-8") as fh:
                fh.write(source)
            entry["file"] = os.path.relpath(
                os.path.join(folder, init_name), src_root).replace("\\", "/")
            _emit_children(node, folder, src_root, inst_path, manifest)
        else:
            fname = sanitize(node["name"]) + suffix
            fpath = os.path.join(dst_dir, _unique_file(dst_dir, fname))
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(source)
            entry["file"] = os.path.relpath(fpath, src_root).replace("\\", "/")
    else:
        # non-script container that contains scripts somewhere below
        folder = os.path.join(dst_dir, sanitize(node["name"]))
        os.makedirs(folder, exist_ok=True)
        _emit_children(node, folder, src_root, inst_path, manifest)


def _unique_file(folder, fname):
    if not os.path.exists(os.path.join(folder, fname)):
        return fname
    stem, ext = fname, ""
    for known in (".server.lua", ".client.lua", ".lua"):
        if fname.endswith(known):
            stem, ext = fname[:-len(known)], known
            break
    i = 2
    while os.path.exists(os.path.join(folder, f"{stem}_{i}{ext}")):
        i += 1
    return f"{stem}_{i}{ext}"


def _emit_children(node, folder, src_root, parent_path, manifest):
    used_dir_names = set()
    for child in node["children"]:
        if not has_script_descendant(child):
            continue
        cpath = parent_path + "/" + child["name"]
        materialize(child, folder, src_root, cpath, manifest)


def write_tree_txt(root, path):
    """Render the full hierarchy like the Roblox Studio Explorer:
    a nested tree with ├──/└──/│ branch lines and a [ClassName] tag."""
    out = []

    def walk(node, prefix, is_last, top_level):
        if top_level:
            connector, child_prefix = "", ""
        else:
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")
        flag = " (disabled)" if node.get("disabled") else ""
        out.append(f"{prefix}{connector}{node['name']} [{node['class']}]{flag}")
        kids = node["children"]
        for i, c in enumerate(kids):
            walk(c, child_prefix, i == len(kids) - 1, False)

    tops = root["children"]
    for i, top in enumerate(tops):
        walk(top, "", i == len(tops) - 1, True)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + ("\n" if out else ""))


def count_nodes(root):
    n = 0
    scripts = 0

    def walk(node):
        nonlocal n, scripts
        n += 1
        if node["class"] in SCRIPT_CLASSES:
            scripts += 1
        for c in node["children"]:
            walk(c)

    for top in root["children"]:
        walk(top)
    return n, scripts


README = """\
# Roblox place extraction (for AI reading)

This folder was produced by `extract.py` from a Roblox place/model file.

- `tree.txt` — the FULL hierarchy rendered like the Roblox Studio Explorer:
  a nested tree with ├──/└── branch lines and a `[ClassName]` tag per instance.
  Use this for the big picture. (On huge places this file can be large — it is
  mostly Workspace geometry; the scripts are the useful part, see `src/`.)
- `src/` — a Rojo-style mirror of the hierarchy. Folders are created only
  along paths that contain scripts, so it stays focused on code.
  - `Script`      -> `*.server.lua`
  - `LocalScript` -> `*.client.lua`
  - `ModuleScript`-> `*.lua`
  - A script that also has (script-bearing) children becomes a folder whose
    own code lives in `init.server.lua` / `init.client.lua` / `init.lua`.
- `manifest.json` — machine-readable list of instances with their class,
  whether they are a script, and the script file path (if any).

Note: non-script instances without any script descendants are listed in
`tree.txt` only (no empty folders are created for them).
"""


def write_output(root, outdir):
    if os.path.exists(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir)
    src_root = os.path.join(outdir, "src")
    os.makedirs(src_root)

    manifest = []
    used = set()
    for top in root["children"]:
        if not has_script_descendant(top):
            # still record top-level non-script containers in manifest/tree
            manifest.append({"path": top["name"], "class": top["class"],
                             "isScript": False, "file": None})
            continue
        # ensure unique top-level dir name handled inside materialize via sanitize
        materialize(top, src_root, src_root, top["name"], manifest)

    write_tree_txt(root, os.path.join(outdir, "tree.txt"))
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README)


# =========================================================================== #
# Driver
# =========================================================================== #
def get_tree(input_path, engine):
    force_py = engine == "python" or os.environ.get("RBXL_FORCE_PYTHON") == "1"
    if not force_py:
        try:
            return tree_via_rust(input_path), "rust"
        except Exception as e:  # noqa: BLE001
            if engine == "rust":
                raise
            print(f"[extract] Rust engine unavailable ({e}); "
                  f"using Python fallback.", file=sys.stderr)
    return tree_via_python(input_path), "python"


def main():
    ap = argparse.ArgumentParser(
        description="Extract a Roblox place/model into an AI-readable file tree.")
    ap.add_argument("input", help="path to .rbxl/.rbxlx/.rbxm/.rbxmx")
    ap.add_argument("-o", "--out", help="output directory "
                    "(default: <input>_extracted)")
    ap.add_argument("--engine", choices=["auto", "rust", "python"],
                    default="auto")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"error: no such file: {args.input}")

    outdir = args.out or (os.path.splitext(args.input)[0] + "_extracted")

    try:
        root, used_engine = get_tree(args.input, args.engine)
    except ModuleNotFoundError as e:
        sys.exit(f"error: missing Python package for binary fallback: {e}\n"
                 f"  run: pip install lz4 zstandard")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"error: failed to parse {args.input}: {e}")

    write_output(root, outdir)
    total, scripts = count_nodes(root)
    print(f"[extract] engine={used_engine}  instances={total}  "
          f"scripts={scripts}")
    print(f"[extract] output -> {outdir}")


if __name__ == "__main__":
    main()
