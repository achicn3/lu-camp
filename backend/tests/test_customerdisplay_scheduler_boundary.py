from pathlib import Path


def test_scheduler_only_drives_background_service() -> None:
    """排程器只管 tick；跨模組順序與 transaction 必須留在 service。"""
    scheduler = (Path(__file__).parents[1] / "app/modules/customerdisplay/scheduler.py").read_text(
        encoding="utf-8"
    )

    assert "CustomerDisplayBackgroundService" in scheduler
    assert "ReturnsService" not in scheduler
    assert "SigningService" not in scheduler
    assert "get_sessionmaker" not in scheduler
    assert ".commit()" not in scheduler
    assert ".rollback()" not in scheduler
