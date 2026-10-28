"""Print the shape of the status page's catalog tree, so "the third level" can be named exactly."""

import json
import pathlib

DATA = pathlib.Path(r"D:\Codes\github\ev-reverse\verify_fresh\upload-status\courses.json")
catalog = json.loads(DATA.read_text(encoding="utf-8"))["catalog"]


def walk(node, depth, budget):
    if depth <= 3:
        kids = node.get("children") or []
        print("  " * depth + f"[depth {depth}] {node.get('title')}  ({node.get('kind')}, "
                             f"{len(kids)} child)")
    for child in (node.get("children") or [])[:budget]:
        if child.get("kind") == "folder":
            walk(child, depth + 1, budget)


print(f"{len(catalog)} root node(s)")
for root in catalog:
    walk(root, 0, 2)
    print()
