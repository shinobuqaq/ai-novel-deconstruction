from app.config import Settings


def test_cors_origins_accepts_comma_separated_value(monkeypatch) -> None:
    monkeypatch.setenv(
        "AND_CORS_ORIGINS",
        "http://127.0.0.1:15173,http://localhost:15173",
    )

    settings = Settings(_env_file=None)

    assert settings.cors_origins == [
        "http://127.0.0.1:15173",
        "http://localhost:15173",
    ]


def test_cors_origins_accepts_json_array(monkeypatch) -> None:
    monkeypatch.setenv(
        "AND_CORS_ORIGINS",
        '["http://127.0.0.1:15173","http://localhost:15173"]',
    )

    settings = Settings(_env_file=None)

    assert settings.cors_origins == [
        "http://127.0.0.1:15173",
        "http://localhost:15173",
    ]
