"""手冊盤點器的路由歸戶（docs/37 P1）。

盤點器是「哪些畫面還沒被手冊拍過」的唯一依據。它報假缺口的代價很具體：
2026-08-21 它說 `/contacts/[id]` 沒有腳本，於是我另寫了一支 20-member-centre.mjs——
其實 03-contacts.mjs 早就點進那一頁拍了 7 張。**假缺口會製造重複工。**
"""

from app.scripts.manual_coverage import _script_routes


def test_route_visited_by_clicking_a_link_is_credited() -> None:
    """腳本用**點連結**進入動態頁時也要算數。

    只認 `${BASE}/contacts/${id}` 這種字面寫法的話，凡是「從列表點進去」的腳本
    一律被判定成沒覆蓋——而那正是店員實際的操作路徑，腳本本來就該那樣走。
    """
    covered = _script_routes(["/contacts", "/contacts/[id]"])
    assert "03-contacts.mjs" in covered["/contacts/[id]"]


def test_direct_navigation_still_credited() -> None:
    """回歸：原本就認得的 `${BASE}/xxx` 寫法不可被改壞。"""
    covered = _script_routes(["/contacts", "/contacts/[id]"])
    assert "03-contacts.mjs" in covered["/contacts"]
