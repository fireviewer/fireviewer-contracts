from datetime import datetime, timezone

from fireviewer_contracts.backend.schemas import PublicSourceSummary, PublicTimelineEvent


def test_documentary_timeline_retains_source_and_day_precision():
    item = PublicTimelineEvent(
        occurred_at=datetime(2026, 9, 5, tzinfo=timezone.utc), kind="incident", label="Bulletin officiel",
        date_precision="date", record_type="publication", source_id="official-bulletin",
        source_name="Bulletin", source_url="https://example.org/bulletin",
    )
    restored = PublicTimelineEvent.model_validate_json(item.model_dump_json())
    assert restored.date_precision == "date"
    assert restored.record_type == "publication"
    assert restored.source_url == "https://example.org/bulletin"


def test_legacy_timeline_preserves_its_defaults():
    item = PublicTimelineEvent(occurred_at=datetime(2026, 9, 5, tzinfo=timezone.utc), kind="incident", label="Incident")
    assert item.date_precision == "datetime"
    assert item.record_type == "event"
    assert item.source_id is None


def test_documentary_source_can_exist_without_a_participatory_observation():
    item = PublicSourceSummary(
        source_id="official-bulletin", type="text", trust="institutional",
        name="Bulletin officiel", observation_count=0,
    )
    restored = PublicSourceSummary.model_validate_json(item.model_dump_json())
    assert restored.observation_count == 0
    assert restored.name == "Bulletin officiel"
