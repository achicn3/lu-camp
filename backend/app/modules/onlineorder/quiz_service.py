"""「不知道喝什麼」引導推薦（docs/63 §2 M2a；店主 2026-10-09 裁示）。

每店一份：1–3 題、每題 2–4 個答案，每個答案勾「適合的品項」（菜單品項或手沖體驗卡）。
客人答完，被勾到最多次的排第一、其次兩個當備選（排序在客人頁做）；這裡只管設定與發佈。
還沒存過時給一版預設題目（不啟用、沒勾品項），沒勾任何上架品項就不發佈、客人頁不出現入口。
跨模組只經 menu service（CLAUDE.md §2）。
"""

from collections.abc import Collection
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.menu.service import MenuService
from app.modules.onlineorder.experience_repository import MenuExperienceRepository
from app.modules.onlineorder.models import OnlineMenuQuiz
from app.modules.onlineorder.presentation_schemas import MenuQuizRead, MenuQuizWriteRequest
from app.modules.onlineorder.quiz_repository import MenuQuizRepository
from app.shared.exceptions import OnlineQuizInvalid

_DEFAULT_QUESTIONS: list[dict[str, Any]] = [
    {
        "prompt": "今天想來點什麼？",
        "options": [
            {"label": "咖啡", "items": []},
            {"label": "想吃甜的", "items": []},
            {"label": "都想要", "items": []},
        ],
    },
    {
        "prompt": "喜歡什麼味道？",
        "options": [
            {"label": "果香、明亮", "items": []},
            {"label": "堅果、巧克力", "items": []},
            {"label": "順口不苦", "items": []},
        ],
    },
    {
        "prompt": "想怎麼喝？",
        "options": [
            {"label": "黑咖啡", "items": []},
            {"label": "加牛奶", "items": []},
            {"label": "想體驗手沖", "items": []},
        ],
    },
]


class MenuQuizService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MenuQuizRepository(session)
        self._menu = MenuService(session)
        self._experiences = MenuExperienceRepository(session)

    async def get(self, store_id: int) -> MenuQuizRead:
        """目前的設定；還沒存過回預設題目。"""
        row = await self._repo.get(store_id)
        if row is None:
            return MenuQuizRead.model_validate(
                {"is_active": False, "questions": _DEFAULT_QUESTIONS, "is_default": True}
            )
        return MenuQuizRead.model_validate(
            {"is_active": row.is_active, "questions": row.questions, "is_default": False}
        )

    async def save(
        self, store_id: int, body: MenuQuizWriteRequest, *, actor_user_id: int
    ) -> MenuQuizRead:
        """整份覆寫並寫稽核；勾的品項必須是本店、未封存的品項或體驗卡，同一答案不可重複。"""
        await self._validate_refs(store_id, body)
        row = await self._repo.get(store_id, for_update=True)
        before = None if row is None else {"is_active": row.is_active, "questions": row.questions}
        after = body.model_dump(mode="json")
        if row is None:
            row = OnlineMenuQuiz(store_id=store_id)
        row.is_active = body.is_active
        row.questions = after["questions"]
        await self._repo.save(row)
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_ONLINE_MENU_QUIZ",
            entity_type="online_menu_quiz",
            entity_id=str(row.id),
            before=before,
            after=after,
        )
        return MenuQuizRead.model_validate({**after, "is_default": False})

    async def _validate_refs(self, store_id: int, body: MenuQuizWriteRequest) -> None:
        for question in body.questions:
            for option in question.options:
                seen = {(ref.kind, ref.id) for ref in option.items}
                if len(seen) != len(option.items):
                    raise OnlineQuizInvalid(f"「{option.label}」勾了重複的品項")
                for ref in option.items:
                    if ref.kind == "item":
                        item = await self._menu.get(store_id, ref.id)
                        if item is None or item.archived_at is not None:
                            raise OnlineQuizInvalid(f"「{option.label}」勾的品項找不到或已封存")
                    elif await self._experiences.get(store_id, ref.id) is None:
                        raise OnlineQuizInvalid(f"「{option.label}」勾的手沖體驗找不到")

    async def snapshot_quiz(
        self, store_id: int, item_ids: Collection[int], experience_ids: Collection[int]
    ) -> dict[str, Any] | None:
        """發佈用：只留這次有發佈的品項／體驗卡；沒啟用或一個可推薦的都沒有就不發佈。"""
        row = await self._repo.get(store_id)
        if row is None or not row.is_active:
            return None
        published = {"item": set(item_ids), "experience": set(experience_ids)}
        questions = [
            {
                "prompt": q["prompt"],
                "options": [
                    {
                        "label": o["label"],
                        "items": [r for r in o["items"] if r["id"] in published[r["kind"]]],
                    }
                    for o in q["options"]
                ],
            }
            for q in row.questions
        ]
        if not any(o["items"] for q in questions for o in q["options"]):
            return None
        return {"questions": questions}
