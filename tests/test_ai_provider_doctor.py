from scripts.ai_provider_doctor import readiness


def test_groq_is_the_minimal_shared_text_provider():
    status = readiness({"GROQ_API_KEY": "secret", "GEMINI_API_KEY": "unused"})
    assert status["groq_configured"] is True
    assert status["shared_text_provider_configured"] is True
    assert status["vertex_enabled_by_configuration"] is False
    assert status["developer_api_key_present"] is True


def test_gemini_developer_key_alone_does_not_enable_student_provider():
    status = readiness({"GEMINI_API_KEY": "secret"})
    assert status["developer_api_key_present"] is True
    assert status["shared_text_provider_configured"] is False


def test_vertex_needs_explicit_flag_project_and_location():
    base = {"GOOGLE_CLOUD_PROJECT": "project", "GOOGLE_CLOUD_LOCATION": "region"}
    assert readiness(base)["vertex_enabled_by_configuration"] is False
    assert readiness({**base, "GEMINI_MINORS_CONTRACT_APPROVED": "true"})[
        "vertex_enabled_by_configuration"
    ] is True
    assert readiness({**base, "GEMINI_MINORS_CONTRACT_APPROVED": "true", "GOOGLE_CLOUD_LOCATION": ""})[
        "vertex_enabled_by_configuration"
    ] is False


def test_doctor_does_not_return_secret_values():
    status = readiness({"GROQ_API_KEY": "sensitive-value", "ANTHROPIC_API_KEY": "another-secret"})
    assert "sensitive-value" not in repr(status)
    assert "another-secret" not in repr(status)
