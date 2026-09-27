"""Standalone mock harness for the DOM motor (Stage 1 — no browser, no models).

Same shape as emotion-test.py: drive the real tools/dom.py code with synthetic
accessibility snapshots, print [PASS]/[FAIL] per check, end with an N/N tally.
Run: python dom-motor-test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.dom import (  # noqa: E402
    best_match,
    build_shortlist,
    filter_nodes,
    is_interactive,
    is_send_like,
    parse_aria_snapshot,
    rank_nodes,
    uia_candidates_to_nodes,
)

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"[PASS] {name}")
    else:
        _FAIL += 1
        print(f"[FAIL] {name}")


HOME_SNAPSHOT = (
    '- heading "Aster QA Fixture" [level=1]\n'
    '- searchbox "Search" [ref=e3]\n'
    '- button "Go" [ref=e5]\n'
    '- button "Submit" [ref=e7]\n'
    '- button "Cancel" [ref=e9]\n'
    '- link "Settings" [ref=e11]\n'
    '- button "Open" [ref=e13]\n'
    '- button "Open" [ref=e15]\n'
    '- textbox "Search" [ref=e17] [disabled]\n'
    '- text: "idle"'
)


def main():
    nodes = parse_aria_snapshot(HOME_SNAPSHOT)
    check("parses all 10 snapshot entries", len(nodes) == 10)
    check("extracts refs", nodes[1]["ref"] == "e3")
    check("extracts roles+names", nodes[3]["role"] == "button" and nodes[3]["name"] == "Submit")
    check("marks disabled", nodes[8]["disabled"] is True)
    check("text nodes keep text", nodes[9]["role"] == "text" and nodes[9]["text"] == "idle")

    interactive = filter_nodes(nodes)
    check("filter drops heading/text", [n["role"] for n in interactive] ==
          ["searchbox", "button", "button", "button", "link", "button", "button"])
    check("filter drops disabled", all(n["ref"] != "e17" for n in interactive))
    check("is_interactive agrees", is_interactive({"role": "button", "source": "web"})
          and not is_interactive({"role": "heading", "source": "web"}))

    check("click goal picks Submit", best_match(interactive, "Submit button")["name"] == "Submit")
    check("search goal picks searchbox",
          best_match(interactive, "the search bar at the top")["role"] == "searchbox")
    check("nonsense goal returns None", best_match(interactive, "zorblatt frobnicator") is None)

    dupes = filter_nodes(parse_aria_snapshot('- button "Open" [ref=a]\n- button "Open" [ref=b]'))
    check("duplicate labels stay deterministic",
          [n["ref"] for n in build_shortlist(dupes, "Open")] == ["a", "b"])

    mixed = filter_nodes(parse_aria_snapshot('- button "Open" [ref=a]\n- textbox "Open" [ref=b]'))
    check("type op prefers textbox", rank_nodes(mixed, "Open", op="type")[0]["role"] == "textbox")
    check("click op prefers button", rank_nodes(mixed, "Open", op="click")[0]["role"] == "button")

    many = filter_nodes(parse_aria_snapshot(
        "\n".join(f'- button "Save" [ref=e{i}]' for i in range(40))))
    check("shortlist capped at K", len(build_shortlist(many, "Save", k=18)) == 18)

    check("Send is send-like", is_send_like({"name": "Send", "text": ""}))
    check("Delete is send-like", is_send_like({"name": "Delete message", "text": ""}))
    check("Search is not send-like", not is_send_like({"name": "Search", "text": ""}))

    win_nodes = filter_nodes(uia_candidates_to_nodes([
        {"name": "Save", "type": "ButtonControl", "rect": (10, 10, 60, 30)},
        {"name": "Search", "type": "EditControl", "rect": (10, 40, 200, 65)},
        {"name": "Title bar", "type": "TextControl", "rect": (0, 0, 300, 20)},
    ]))
    check("UIA conversion maps roles and drops text",
          [n["role"] for n in win_nodes] == ["button", "textbox"])
    check("UIA bbox preserved", win_nodes[0]["bbox"] == (10, 10, 60, 30))
    check("UIA goal picks edit field", best_match(win_nodes, "search field")["name"] == "Search")

    spatial = [
        {"role": "button", "name": "File", "text": "", "ref": None,
         "bbox": (1700, 10, 1800, 40), "type": None, "source": "win", "score": 0.0},
        {"role": "button", "name": "File", "text": "", "ref": None,
         "bbox": (100, 900, 200, 930), "type": None, "source": "win", "score": 0.0},
    ]
    check("spatial hint biases top-right",
          rank_nodes(spatial, "file at the top right", screen=(1920, 1080))[0]["bbox"][0] > 1000)

    check("empty snapshot parses to []", parse_aria_snapshot("") == [])
    check("garbage snapshot parses to []", parse_aria_snapshot("hello world") == [])

    total = _PASS + _FAIL
    print(f"\n{_PASS}/{total} checks passed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
