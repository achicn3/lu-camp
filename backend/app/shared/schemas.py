"""跨模組共用的小型回應模型。"""

from pydantic import BaseModel


class ListCountRead(BaseModel):
    """符合同一組篩選條件的總筆數（清單頁算「第 X / Y 頁」用）。

    每個 `/count` 端點都必須與它的清單端點吃**同一組篩選參數**、走同一條查詢，
    否則畫面上的總頁數會與實際清單對不起來——那比沒有總頁數更糟。
    """

    count: int
