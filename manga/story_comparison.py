"""AIの説明ではなく、JSONの値そのものからストーリーの差分を作る。"""

import copy


def compare_stories(before, after):
    changes = []

    def compare(page, panel, field, old, new):
        if old != new:
            changes.append({"page": page, "panel": panel, "field": field,
                            "before": copy.deepcopy(old), "after": copy.deepcopy(new)})

    for field in ("title", "summary"):
        compare(None, None, field, before[field], after[field])
    old_pages = {page["page"]: page for page in before["pages"]}
    new_pages = {page["page"]: page for page in after["pages"]}
    for number in sorted(set(old_pages) | set(new_pages)):
        old_page, new_page = old_pages.get(number), new_pages.get(number)
        if old_page is None or new_page is None:
            compare(number, None, "page", old_page, new_page)
            continue
        compare(number, None, "page_summary", old_page["summary"], new_page["summary"])
        old_panels = {panel["panel"]: panel for panel in old_page["panels"]}
        new_panels = {panel["panel"]: panel for panel in new_page["panels"]}
        for panel_number in sorted(set(old_panels) | set(new_panels)):
            old, new = old_panels.get(panel_number), new_panels.get(panel_number)
            if old is None or new is None:
                compare(number, panel_number, "panel", old, new)
            else:
                for field in ("description", "dialogue", "narration"):
                    compare(number, panel_number, field, old[field], new[field])
    return changes


def check_improvement_scope(before, after, mode, targets):
    if mode != "特定のページ・コマだけを改善する":
        return
    for change in compare_stories(before, after):
        if not any(change["page"] == target["page"] and (target["panel"] is None or target["panel"] == change["panel"]) for target in targets):
            raise ValueError("指定範囲外の変更があります。対象以外のページ・コマ、タイトル・全体のあらすじを元に戻してください。")
