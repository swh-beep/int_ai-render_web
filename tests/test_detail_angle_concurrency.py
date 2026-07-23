from application.details import detail_generation_stage, detail_workflow


def test_internal_angle_qc_gets_five_generation_attempts_by_default():
    assert detail_generation_stage.DETAIL_ANGLE_QC_MAX_ATTEMPTS == 5


def test_internal_angle_styles_cap_per_job_generation_concurrency(monkeypatch):
    monkeypatch.setattr(detail_workflow, "DETAIL_GENERATION_MAX_WORKERS", 20)
    monkeypatch.setattr(detail_workflow, "DETAIL_ANGLE_GENERATION_MAX_WORKERS", 2)

    styles = [
        {"name": "High Angle Overview", "camera_mode": "overview_angle"},
        {"name": "Side Composition (Focus Left)", "camera_mode": "side_angle"},
        {"name": "Side Composition (Focus Right)", "camera_mode": "side_angle"},
        {"name": "Detail: Sofa"},
    ]

    assert detail_workflow._detail_generation_max_workers(styles) == 2


def test_non_angle_details_keep_existing_generation_concurrency(monkeypatch):
    monkeypatch.setattr(detail_workflow, "DETAIL_GENERATION_MAX_WORKERS", 20)
    monkeypatch.setattr(detail_workflow, "DETAIL_ANGLE_GENERATION_MAX_WORKERS", 2)

    styles = [
        {"name": "Detail: Sofa"},
        {"name": "Detail: Table"},
        {"name": "Detail: Lamp"},
        {"name": "Detail: Rug"},
    ]

    assert detail_workflow._detail_generation_max_workers(styles) == 4
