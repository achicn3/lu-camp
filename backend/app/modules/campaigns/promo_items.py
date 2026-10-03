"""商品 → 活動定價輸入（PromoItem）。

結帳／報價（sales）與組合包袋裝條碼驗證（ADR-028）共用同一份，判斷才不會兩邊不一致。
"""

from app.modules.campaigns.pricing import PromoItem
from app.modules.inventory.models import BulkBasket, BulkLot, CatalogProduct, SerializedItem
from app.shared.enums import CampaignItemKind, OwnershipType


def serialized_promo_item(item: SerializedItem) -> PromoItem:
    return PromoItem(
        kind=(
            CampaignItemKind.CONSIGNMENT_SERIALIZED
            if item.ownership_type == OwnershipType.CONSIGNMENT
            else CampaignItemKind.OWNED_SERIALIZED
        ),
        category_id=item.category_id,
        brand_id=item.brand_id,
        product_model_id=item.product_model_id,
        serialized_item_id=item.id,
    )


def catalog_promo_item(product: CatalogProduct) -> PromoItem:
    return PromoItem(
        kind=CampaignItemKind.CATALOG,
        category_id=product.category_id,
        brand_id=product.brand_id,
        product_model_id=product.product_model_id,
        catalog_product_id=product.id,
    )


def bulk_promo_item(lot: BulkLot) -> PromoItem | None:
    """散裝只折自有；寄售散裝無抽成模型、永不折（docs/21 §2）。已入籃的來源依籃子比對。"""
    if lot.consignor_id is not None:
        return None
    return PromoItem(
        kind=CampaignItemKind.OWNED_BULK,
        category_id=lot.category_id,
        brand_id=lot.brand_id,
        bulk_basket_id=lot.basket_id,
    )


def basket_promo_item(basket: BulkBasket) -> PromoItem:
    """籃內只有自有散裝（寄售不入籃）。"""
    return PromoItem(
        kind=CampaignItemKind.OWNED_BULK,
        category_id=basket.category_id,
        brand_id=basket.brand_id,
        bulk_basket_id=basket.id,
    )
