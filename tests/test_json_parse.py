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

    def test_json_followed_by_prose_with_braces(self):
        # Regressiya: model JSONdan keyin { } li izoh yozsa, greedy regex
        # oxirgi } gacha qamrab "Extra data" xatosi berardi.
        raw = '{"a": 1}\n\nIzoh: bu {muhim} natija edi. Yana bir blok: {"b": 2}'
        assert parse_json_response(raw) == {"a": 1}

    def test_fenced_json_with_trailing_prose(self):
        raw = '```json\n{"holat": "yaxshi", "x": [1, 2]}\n```\nQo\'shimcha tushuntirish shu yerda {qavs} bilan.'
        assert parse_json_response(raw) == {"holat": "yaxshi", "x": [1, 2]}

    def test_raises_json_decode_error_for_non_json_text(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_response("bu umuman JSON emas")

    def test_raises_for_empty_string(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_response("")
