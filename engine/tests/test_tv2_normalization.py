from copy import deepcopy

from ir_engine.normalization.sysmon import normalize


def test_nested_and_dotted_normalization_preserves_raw():
    source = {
        "event.provider": "Microsoft-Windows-Sysmon",
        "event.code": 22,
        "process.entity_id": "{00000000-0000-0000-0000-000000000015}",
        "dns.question.name": "Example.ORG.",
        "dns.resolved_ip": ["2001:0db8::1", "bad"],
        "process.pid": "4242",
    }
    before = deepcopy(source)
    result = normalize(source)
    assert source == before
    assert result.supported
    assert result.guid == "00000000-0000-0000-0000-000000000015"
    assert ("domain", "example.org") in result.terms
    assert ("ip", "2001:db8::1") in result.terms
    assert ("ip", "bad") not in result.terms
    assert result.fields["pid"] == 4242


def test_invalid_identity_and_foreign_provider():
    result = normalize({"event": {"provider": "Other", "code": "1"}, "process": {"entity_id": "invalid", "pid": "broken"}})
    assert not result.supported
    assert result.guid is None
    assert result.fields["pid"] is None
