from hermes.api import openapi_schema, requires_confirmation


def test_requires_confirmation_for_write_like_commands() -> None:
    assert requires_confirmation('create Task name="Call client" status=Inbox')
    assert requires_confirmation("add Lead firstName=Jane lastName=Doe")
    assert requires_confirmation('move opportunity opp-1 to "Quoted"')
    assert requires_confirmation("intake met Jane at chamber lunch")


def test_read_commands_do_not_require_confirmation() -> None:
    assert not requires_confirmation("find Acme")
    assert not requires_confirmation("renewal audit")
    assert not requires_confirmation("stale leads")


def test_openapi_schema_advertises_command_endpoint() -> None:
    schema = openapi_schema()
    assert schema["openapi"].startswith("3.")
    assert "/command" in schema["paths"]
    assert schema["paths"]["/command"]["post"]["operationId"] == "hermes_command"
