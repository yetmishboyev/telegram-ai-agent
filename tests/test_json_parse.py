"""parse_json_response yordamchisi testlari (3 ta agentda takrorlangan mantiq endi shu yerda)."""
import json
import pytest

from app.ai.agents.json_parse import parse_json_response


class TestParseJsonResponse:
    def test_plain_json_object(self):
        assert parse_json_response('{"a": 1, "b": true}') == {"a": 1, "b": True}

    def test_plain_json_array(self):
        assert parse_json_response('[{"key": "x"}, {"key": "y"}]') == [
            {"key": "x"},
            {"key": "y"},
        ]

    def test_json_fenced_with_json_tag(self):
        raw = '```json\n{"a": 1}\n```'
        assert parse_json_response(raw) == {"a": 1}

    def test_json_fenced_without_tag(self):
        raw = '```\n{"a": 1}\n```'
        assert parse_json_response(raw) == {"a": 1}

    def test_strips_surrounding_whitespace(self):
        assert parse_json_response("   {\"a\": 1}   ") == {"a": 1}

    def test_extracts_json_from_surrounding_prose(self):
        raw = 'Mana natija:\n{"a": 1}\nUmid qilamanki foydali.'
        assert parse_json_response(raw) == {"a": 1}

    def test_raises_json_decode_error_for_non_json_text(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_response("bu umuman JSON emas")

    def test_raises_for_empty_string(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_response("")
