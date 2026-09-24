"""Add cover cards for the 25 courses that were never exported.

One-off authoring helper for the plan in docs/NEVER-EXPORTED-COURSES.md: it appends a card per
course to tools/covers/cards.json, taking the mechanical fields (title, episode count, runtime,
output folder) from the platform's own listing and leaving the words and the art direction to
the table below. Run it once; afterwards cards.json is the source of truth again.

    python .scratch/never-exported/add-cards.py [--apply]
"""

import argparse
import json
import pathlib

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
CARDS = REPO / "tools" / "covers" / "cards.json"
INDEX = REPO / "verify_fresh" / "index.json"
CATALOG = REPO / "verify_fresh" / "catalog.json"

# course -> (key, english sub-title, tags, art scene, palette)
NEW = {
    "HarmonyOS Next SDK": ("hm-next-sdk", "ARKTS FROM THE GROUND UP",
                           ["ArkTS", "Ability", "UIAbility", "SDK"],
                           "a phone-shaped slab of light assembling itself from modular panels, each panel a component snapping into a stack",
                           "blue and white"),
    "ArkUI应用框架": ("hm-arkui", "DECLARATIVE UI IN ARKTS",
                      ["ArkUI", "声明式", "状态管理", "组件"],
                      "a declarative UI tree drawing itself: component cards falling into a nested layout while state tokens flow down the branches",
                      "teal and graphite"),
    "HarmonyOS应用模型": ("hm-app-model", "ABILITY AND STAGE",
                          ["Stage模型", "Ability", "生命周期", "进程"],
                          "a stage holding three glowing ability capsules wired to a lifecycle clock, one of them crossing a process boundary",
                          "indigo and cyan"),
    "HarmonyOS Next 特色与生态": ("hm-ecosystem", "THE NEXT ECOSYSTEM",
                                 ["元服务", "分布式", "多设备", "生态"],
                                 "one glowing app surface mirrored across a ring of device silhouettes, a distributed bus linking every one",
                                 "violet and lime"),
    "坚果驾考": ("hm-driving-test", "DRIVING-TEST APP",
                 ["ArkTS", "项目实战", "题库", "页面路由"],
                 "a quiz interface assembling itself: question cards stacking into a scroll while a progress ring fills",
                 "orange and navy"),
    "实战案例-简记": ("hm-case-notes", "A NOTES APP, END TO END",
                      ["记事本", "数据存储", "ArkTS", "实战"],
                      "a note card unfolding into a database cylinder and folding back, a small calendar grid behind it",
                      "amber and slate"),
    "daysmatter": ("hm-daysmatter", "COUNTDOWN APP, BUILT",
                   ["倒计时", "组件", "实战", "ArkTS"],
                   "a countdown dial of concentric rings with a date card flipping over at its centre",
                   "rose and deep blue"),
    "实战案例-华夏旅行者": ("hm-case-traveler", "A TRAVEL APP, BUILT",
                            ["旅行", "地图", "实战", "ArkTS"],
                            "a travel screen sliding over a stylised mountain map while route pins light up one by one",
                            "emerald and sand"),
    "鸿蒙就业指导": ("hm-career", "THE HARMONYOS JOB",
                     ["就业", "简历", "面试", "鸿蒙"],
                     "a resume sheet and a device silhouette facing each other across a lit interview table",
                     "gold and charcoal"),
    "鸿蒙认证考试辅导": ("hm-certification", "PASS THE CERTIFICATION",
                        ["认证", "考试", "考点", "鸿蒙"],
                        "a certificate seal at the centre of a ring of exam cards, one card lit as the answer",
                        "blue and gold"),
    "求职之道极速版": ("job-hunt-fast", "THE FAST JOB-HUNT TRACK",
                       ["求职", "简历", "面试", "前端"],
                       "a compressed timeline of interview stages as glowing waypoints, one path pulling ahead of the others",
                       "cyan and magenta"),
    "求职之道常规版": ("job-hunt-standard", "THE FULL JOB-HUNT TRACK",
                       ["求职", "简历", "面试", "职业规划"],
                       "a long ladder of interview stages rising through layered skill planes, every rung labelled",
                       "indigo and amber"),
    "前端+AI（基础篇）": ("fe-ai-basics", "FRONTEND MEETS LLMS",
                         ["LLM", "Prompt", "AI编码", "提效"],
                         "a code pane and a language-model core exchanging glowing prompt cards across a bridge",
                         "violet and cyan"),
    "React源码解析": ("react-source", "REACT, LINE BY LINE",
                      ["Fiber", "Reconciler", "Hooks", "调度"],
                      "a source file unfolding into a fiber tree while a reconciler loop sweeps its leaves",
                      "sky blue and slate"),
    "vue源码解析(新)": ("vue-source", "VUE 3 INTERNALS",
                        ["响应式", "编译器", "渲染器", "Vue3"],
                        "a reactivity graph of glowing dependencies collapsing into a single render pass",
                        "green and graphite"),
    "vue源码分析": ("vue-internals", "INSIDE VUE'S REACTIVITY",
                    ["响应式", "虚拟DOM", "Diff", "依赖收集"],
                    "dependency-collection threads weaving through a component tree toward one watcher node",
                    "teal and white"),
    "vue-router源码分析": ("vue-router-source", "INSIDE VUE ROUTER",
                           ["路由", "匹配器", "导航守卫", "history"],
                           "a route table drawn as a branching map where one path lights up and guards snap shut behind it",
                           "orange and indigo"),
    "从零实现Coding Agent": ("coding-agent", "BUILD A CODING AGENT",
                              ["Agent", "工具调用", "上下文", "LLM"],
                              "an agent loop as a circular track: a terminal cube carrying tool cards around it and dropping patches",
                              "crimson and steel"),
    "AI平台篇（扣子&Dify）": ("ai-platform-coze-dify", "COZE & DIFY, HANDS ON",
                              ["扣子", "Dify", "工作流", "编排"],
                              "a canvas of draggable nodes wiring themselves into an LLM workflow, a play button glowing",
                              "purple and mint"),
    "通用组件库开发": ("component-library", "A COMPONENT LIBRARY",
                       ["组件库", "设计规范", "打包", "文档站"],
                       "a shelf of reusable component cards, one slotted into an app frame while a docs site grows beside it",
                       "blue and coral"),
    "浏览器插件开发": ("browser-extension", "EXTENSIONS, BUILT",
                       ["Manifest", "content script", "背景页", "消息通信"],
                       "a browser window with an extension panel docked to it, message bubbles travelling between page and panel",
                       "yellow and charcoal"),
    "Recoil源码分析": ("recoil-source", "INSIDE RECOIL'S ATOMS",
                       ["atom", "selector", "状态", "订阅"],
                       "atom nodes floating around a selector web with subscriptions lighting along its edges",
                       "cyan and violet"),
    "pinia源码": ("pinia-source", "INSIDE PINIA",
                  ["store", "响应式", "插件", "Vue3"],
                  "a store container whose state blocks broadcast to component leaves along reactive threads",
                  "green and amber"),
    "RxJS": ("rxjs", "STREAMS, OPERATORS, TIME",
             ["Observable", "操作符", "调度器", "响应式"],
             "a marble timeline where operator gates reshape a stream of coloured marbles into another",
             "magenta and navy"),
    "主题切换": ("theme-switching", "THEMING WITHOUT THE MESS",
                 ["CSS变量", "设计令牌", "暗色模式", "主题"],
                 "one interface flipping between light and dark palettes while a token grid re-maps its colours",
                 "grey and gold"),
}


def categories() -> dict:
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    found = {}

    def walk(node, root):
        if node.get("title"):
            found.setdefault(node["title"], root)
        for child in node.get("children", []):
            if child.get("kind") != "video":
                walk(child, root)

    for root in value["roots"]:
        walk(root, root["title"])
    return found


def courses() -> dict:
    """course name -> (episode count, total seconds)."""
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    out = {}
    for record in index["courses"].values():
        detail = record["course_detail"]
        name = detail["self"]["name"]
        files = []

        def gather(node):
            files.extend(f for f in node.get("files", []) if f.get("type") == "video")
            for child in node.get("childs", []):
                gather(child)

        gather(detail)
        out[name] = (len(files), sum(int(f.get("duration") or 0) for f in files))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    document = json.loads(CARDS.read_text(encoding="utf-8"))
    have = {card["key"] for card in document["cards"]}
    roots = categories()
    stats = courses()

    added, skipped = [], []
    for name, (key, en, tags, scene, palette) in NEW.items():
        if key in have:
            skipped.append(key)
            continue
        if name not in stats:
            raise SystemExit(f"{name} is not in the platform's course list")
        episodes, seconds = stats[name]
        hours, minutes = divmod(seconds // 60, 60)
        category = roots.get(name)
        if category is None:
            raise SystemExit(f"{name} has no catalogue category")
        card = {
            "key": key,
            "out": f"{category}/{name}",
            "title": name,
            "en": en,
            "tags": tags,
            "epLabel": f"SERIES / {episodes:02d} EPISODES",
            "ghost": f"{episodes:02d}",
            "eyebrow": category,
            "footerLeft": f"共 {episodes} 集 · 总时长 {hours} 小时 {minutes:02d} 分",
            "footerRight": name,
            "bg": {"scene": scene, "palette": palette},
        }
        if len(en) > 24:
            print(f"  note: {key}'s english sub-title is {len(en)} characters")
        document["cards"].append(card)
        added.append((key, card["out"], card["epLabel"]))

    for key, out, label in added:
        print(f"  + {key:<24} {out:<32} {label}")
    if skipped:
        print(f"  already present: {', '.join(skipped)}")
    print(f"\n{len(added)} card(s) to add, {len(document['cards'])} total")
    if not args.apply:
        print("dry run: cards.json unchanged (pass --apply)")
        return 0

    CARDS.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {CARDS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
