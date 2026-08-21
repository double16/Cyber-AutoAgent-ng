import json

import pytest

from modules.utils.json_repair import (
    parse_json_response,
    parse_json_response_with_metadata,
    repair_json_text,
    strip_js_comments,
)


def test_parse_json_response_repairs_logged_embedded_quotes_and_invalid_apostrophe_escape():
    response = r'''{
      "status": "done",
      "reason": "The artifact includes "schema_version": 1 and it\'s complete.",
      "instructions": ""
    }'''

    assert parse_json_response(response, require_object=True) == {
        "status": "done",
        "reason": 'The artifact includes "schema_version": 1 and it\'s complete.',
        "instructions": "",
    }


def test_repair_json_text_handles_comments_fences_prose_and_trailing_commas():
    response = '''prefix
    ```json
    {
      // keep this out of the payload
      "url": "https://example.test//path",
      "message": "/* literal */",
      "items": [1, 2,],
    }
    ```
    suffix'''

    assert json.loads(repair_json_text(response)) == {
        "url": "https://example.test//path",
        "message": "/* literal */",
        "items": [1, 2],
    }


def test_strip_js_comments_preserves_strings():
    value = strip_js_comments('{"url":"https://example.test//path" /* comment */}')
    assert json.loads(value) == {"url": "https://example.test//path"}


def test_parse_json_response_rejects_non_object_when_requested():
    with pytest.raises(ValueError, match="JSON object"):
        parse_json_response("[1, 2]", require_object=True)


def test_parse_json_response_rejects_non_text_input():
    with pytest.raises(TypeError, match="response must be text"):
        parse_json_response({"approved": True})  # type: ignore[arg-type]


def test_repair_json_text_serialization_can_escape_emoji():
    parsed = parse_json_response('{"message":"😀"}')
    assert json.dumps(parsed, ensure_ascii=True) == '{"message": "\\ud83d\\ude00"}'


def test_parse_json_response_preserves_valid_json_without_repairing():
    response = '{"message":"quoted \\\"text\\\"", "items":[1,2]}'

    assert parse_json_response(response, require_object=True) == {
        "message": 'quoted "text"',
        "items": [1, 2],
    }


def test_parse_json_response_extracts_one_object_from_prose_with_metadata():
    parsed = parse_json_response_with_metadata(
        'Analysis follows. {"approved": false, "feedback": ["Add evidence"]} End.',
        require_object=True,
    )

    assert parsed.value == {"approved": False, "feedback": ["Add evidence"]}
    assert parsed.metadata.extracted is True
    assert parsed.metadata.repaired is False


def test_parse_json_response_unwraps_a_double_quoted_json_fence():
    response = json.dumps('```json\n{"tasks": []}\n```')

    parsed = parse_json_response_with_metadata(response, require_object=True)

    assert parsed.value == {"tasks": []}
    assert parsed.metadata.extracted is True
    assert parsed.metadata.repaired is True


def test_parse_json_response_does_not_unwrap_an_ordinary_json_string():
    with pytest.raises(ValueError, match="JSON object"):
        parse_json_response(json.dumps("not a fenced payload"), require_object=True)


def test_parse_json_response_repairs_an_extracted_object():
    parsed = parse_json_response_with_metadata('Result: {"approved": true,}')

    assert parsed.value == {"approved": True}
    assert parsed.metadata.extracted is True
    assert parsed.metadata.repaired is True


def test_parse_json_response_rejects_ambiguous_multiple_objects():
    with pytest.raises(ValueError, match="multiple JSON values"):
        parse_json_response('{"approved": true} then {"approved": false}', require_object=True)


def test_parse_json_response_accepts_equal_duplicate_objects_separated_by_think_tag():
    parsed = parse_json_response_with_metadata(
        '{"approved": true, "feedback": []}</think>{"approved": true, "feedback": []}',
        require_object=True,
    )

    assert parsed.value == {"approved": True, "feedback": []}
    assert parsed.metadata.extracted is True
    assert parsed.metadata.repaired is False


def test_parse_json_response_accepts_equal_objects_with_different_key_order():
    parsed = parse_json_response('{"approved": true, "feedback": []} {"feedback": [], "approved": true}')

    assert parsed == {"approved": True, "feedback": []}


def test_parse_json_response_discards_invalid_candidate_when_valid_candidates_agree():
    parsed = parse_json_response('{"approved": true} {not valid} {"approved": true}', require_object=True)

    assert parsed == {"approved": True}


def test_parse_json_response_rejects_when_all_candidates_are_invalid():
    with pytest.raises(json.JSONDecodeError):
        parse_json_response('{not valid} {still broken}')


def test_parse_json_response_rejects_truncated_repair_candidate():
    with pytest.raises(json.JSONDecodeError):
        parse_json_response('{"message":"truncated')
