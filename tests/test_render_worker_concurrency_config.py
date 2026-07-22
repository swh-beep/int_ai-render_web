from application.render.render_room_workflow import _render_variant_max_workers


def test_render_variant_worker_override_is_clamped(monkeypatch):
    monkeypatch.setenv("AI_RENDER_VARIANT_MAX_WORKERS", "1")
    assert _render_variant_max_workers() == 1

    monkeypatch.setenv("AI_RENDER_VARIANT_MAX_WORKERS", "99")
    assert _render_variant_max_workers() == 3


def test_render_variant_worker_override_defaults_safely(monkeypatch):
    monkeypatch.delenv("AI_RENDER_VARIANT_MAX_WORKERS", raising=False)
    assert _render_variant_max_workers() == 3

    monkeypatch.setenv("AI_RENDER_VARIANT_MAX_WORKERS", "invalid")
    assert _render_variant_max_workers() == 3
