"""Static check of every preset workflow against a ComfyUI + ComfyUI-GGUF checkout.

Usage: python3 tests/test_workflows_static.py /path/to/ComfyUI /path/to/ComfyUI-GGUF

It parses node definitions from source (both the classic INPUT_TYPES style and
the V3 io.Schema style) without importing torch, then checks for each node in
each workflow: the class exists, every input we send is declared, every
required input is sent, combo values we use are valid, and every link points
at an existing node.
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from studiolib import presets  # noqa: E402


def v3_nodes(src: str) -> dict:
    found = {}
    for m in re.finditer(r'node_id="([^"]+)"', src):
        start = m.end()
        # schema block ends at the first "outputs=" or next node_id / class
        nxt = re.search(r"\n\s+outputs=|\nclass |node_id=\"", src[start:])
        block = src[start:start + (nxt.start() if nxt else 4000)]
        inputs = {}
        for im in re.finditer(r'io\.(\w+)\.Input\(\s*"([^"]+)"([^\n]*)', block):
            kind, name, rest = im.groups()
            inputs[name] = {"optional": "optional=True" in rest, "kind": kind, "line": rest}
        found[m.group(1)] = inputs
    return found


def v1_nodes(src: str) -> dict:
    tree = ast.parse(src)
    found = {}
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == "INPUT_TYPES":
                seg = ast.get_source_segment(src, fn) or ""
                inputs = {}
                section = None
                for line in seg.splitlines():
                    if '"required"' in line:
                        section = "required"
                    if '"optional"' in line:
                        section = "optional"
                    if '"hidden"' in line:
                        section = "hidden"
                    for km in re.finditer(r'"([A-Za-z_][\w]*)"\s*:\s*\(', line):
                        if section in ("required", "optional"):
                            inputs[km.group(1)] = {"optional": section == "optional", "line": line}
                found[cls.name] = inputs
    return found


def load_defs(comfy: str, gguf: str) -> dict:
    defs = {}
    files = [os.path.join(comfy, "nodes.py")]
    for d in ("comfy_extras",):
        for f in sorted(os.listdir(os.path.join(comfy, d))):
            if f.endswith(".py"):
                files.append(os.path.join(comfy, d, f))
    files.append(os.path.join(gguf, "nodes.py"))
    for f in files:
        src = open(f, encoding="utf-8-sig").read()
        for k, v in v1_nodes(src).items():
            defs.setdefault(k, v)
        for k, v in v3_nodes(src).items():
            defs[k] = v
    return defs


def sampler_lists(comfy: str):
    src = open(os.path.join(comfy, "comfy", "samplers.py"), encoding="utf-8").read()
    ks = re.search(r"KSAMPLER_NAMES\s*=\s*\[(.*?)\]", src, re.S).group(1)
    samplers = set(re.findall(r'"([^"]+)"', ks)) | {"ddim", "uni_pc", "uni_pc_bh2"}
    sch = set(re.findall(r'SchedulerHandler\(|"(\w+)": SchedulerHandler', src))
    sch = {s for s in sch if s}
    return samplers, sch


def main(comfy, gguf):
    defs = load_defs(comfy, gguf)
    samplers, schedulers = sampler_lists(comfy)
    clip_types = re.search(r'"type": \(\[(.*?)\]', open(os.path.join(comfy, "nodes.py")).read()).group(1)
    clip_types = set(re.findall(r'"([^"]+)"', clip_types))
    problems = 0
    for preset in presets.PRESETS:
        item = presets.normalise_item(preset, {"id": "t", "prompt": "a red fox in snow", "seed": 7})
        wf = presets.build(preset, item, "test/t")
        for nid, node in wf.items():
            ct = node["class_type"]
            if ct not in defs:
                print(f"[{preset}] node {nid}: class {ct} NOT FOUND")
                problems += 1
                continue
            declared = defs[ct]
            for name, val in node["inputs"].items():
                if name not in declared:
                    print(f"[{preset}] {ct}: input '{name}' not declared (declared: {sorted(declared)})")
                    problems += 1
                if isinstance(val, list):
                    if val[0] not in wf:
                        print(f"[{preset}] {ct}: input '{name}' links to missing node {val[0]}")
                        problems += 1
            for name, meta in declared.items():
                if not meta["optional"] and name not in node["inputs"]:
                    print(f"[{preset}] {ct}: required input '{name}' missing")
                    problems += 1
            if "sampler_name" in node["inputs"] and node["inputs"]["sampler_name"] not in samplers:
                print(f"[{preset}] {ct}: unknown sampler {node['inputs']['sampler_name']}")
                problems += 1
            if "scheduler" in node["inputs"] and schedulers and node["inputs"]["scheduler"] not in schedulers:
                print(f"[{preset}] {ct}: unknown scheduler {node['inputs']['scheduler']}")
                problems += 1
            if ct == "CLIPLoader" and node["inputs"]["type"] not in clip_types:
                print(f"[{preset}] CLIPLoader: unknown type {node['inputs']['type']}")
                problems += 1
        print(f"[{preset}] {len(wf)} nodes checked")
    print("schedulers known:", sorted(schedulers))
    print("PROBLEMS:", problems)
    return problems


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1], sys.argv[2]) else 0)
