"""OpenAPI descriptions of the web API routes."""

from __future__ import annotations

from nwn_translator.web.app import create_app


def test_operation_descriptions_stop_before_the_handler_parameters() -> None:
    """A route publishes its prose and HTTP errors, never its Python parameters."""
    spec = create_app().openapi()
    operations = [op for path_item in spec["paths"].values() for op in path_item.values()]
    assert operations
    for operation in operations:
        description = operation.get("description", "")
        assert "Args:" not in description
        assert "Returns:" not in description
    upload = spec["paths"]["/api/translate"]["post"]["description"]
    assert "HTTPException: 429" in upload
