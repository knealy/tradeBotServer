"""Light import checks — default pytest run (excludes @pytest.mark.bench)."""


def test_import_event_bus():
    from core.event_bus import EventBus

    assert EventBus is not None


def test_import_json_fast():
    from core.json_fast import dumps_str, loads

    assert loads(dumps_str({"x": 2})) == {"x": 2}
    assert '"x"' in dumps_str({"x": 2})


def test_import_bar_aggregator():
    from core.bar_aggregator import BarAggregator

    assert BarAggregator is not None
