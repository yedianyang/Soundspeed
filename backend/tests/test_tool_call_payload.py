"""build_tool_call_payload 纯函数单测。

覆盖三条路径：
  1. 工具路径（tc 非 None，含 function.name + arguments）
  2. content 路径（tc=None，纯文本推理）
  3. 健壮性（result_dict={} 不抛异常）
"""
import json

from backend.core.events import build_tool_call_payload


class TestToolPath:
    """工具调用路径：tc 非 None。"""

    def _make_result_dict(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": "思考前导",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "mark",
                                    "arguments": '{"a":1}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "gemma-4-test",
        }

    def _make_tc(self) -> dict:
        return {
            "id": "c1",
            "type": "function",
            "function": {
                "name": "mark",
                "arguments": '{"a":1}',
            },
        }

    def test_tool_name_and_arguments(self) -> None:
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.tool_name == "mark"
        assert payload.arguments == '{"a":1}'

    def test_raw_content_captures_message_content(self) -> None:
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.raw_content == "思考前导"

    def test_raw_response_is_valid_json_containing_name(self) -> None:
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.raw_response is not None
        assert len(payload.raw_response) > 0
        parsed = json.loads(payload.raw_response)
        # 原始响应里应含 model 字段
        assert parsed.get("model") == "gemma-4-test"

    def test_ts_is_adopted_from_argument(self) -> None:
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=9999.5,
        )
        assert payload.ts == 9999.5

    def test_tool_id_and_type(self) -> None:
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.tool_id == "c1"
        assert payload.tool_type == "function"

    def test_usage_tokens(self) -> None:
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.prompt_tokens == 10
        assert payload.completion_tokens == 5
        assert payload.total_tokens == 15

    def test_finish_reason_and_model(self) -> None:
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.finish_reason == "tool_calls"
        assert payload.model == "gemma-4-test"

    def test_available_tools_extracted_from_gen_kwargs(self) -> None:
        gen_kwargs = {
            "tools": [
                {"type": "function", "function": {"name": "mark"}},
                {"type": "function", "function": {"name": "query"}},
            ],
            "tool_choice": "auto",
        }
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs=gen_kwargs,
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.available_tools == ("mark", "query")
        assert payload.tool_choice == "auto"

    def test_tool_choice_dict_resolves_to_name(self) -> None:
        gen_kwargs = {
            "tool_choice": {"type": "function", "function": {"name": "mark"}},
        }
        payload = build_tool_call_payload(
            task_type="np",
            tc=self._make_tc(),
            gen_kwargs=gen_kwargs,
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.tool_choice == "mark"


class TestContentPath:
    """纯文本推理路径：tc=None，哨兵 tool_name==""、arguments==""。"""

    def _make_result_dict(self) -> dict:
        return {
            "choices": [
                {
                    "message": {"content": "纯文本输出"},
                    "finish_reason": "stop",
                }
            ],
            "model": "gemma",
        }

    def test_sentinel_fields(self) -> None:
        payload = build_tool_call_payload(
            task_type="query_session",
            tc=None,
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.tool_name == ""
        assert payload.arguments == ""

    def test_tool_id_is_none(self) -> None:
        payload = build_tool_call_payload(
            task_type="query_session",
            tc=None,
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.tool_id is None

    def test_raw_content_contains_text(self) -> None:
        payload = build_tool_call_payload(
            task_type="query_session",
            tc=None,
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.raw_content == "纯文本输出"

    def test_raw_response_nonempty(self) -> None:
        payload = build_tool_call_payload(
            task_type="query_session",
            tc=None,
            gen_kwargs={},
            result_dict=self._make_result_dict(),
            ts=1000.0,
        )
        assert payload.raw_response is not None
        assert len(payload.raw_response) > 0
        parsed = json.loads(payload.raw_response)
        assert parsed.get("model") == "gemma"


class TestRobustness:
    """缺失字段健壮性：result_dict={} 不抛异常。"""

    def test_empty_result_dict_does_not_raise(self) -> None:
        payload = build_tool_call_payload(
            task_type="np",
            tc=None,
            gen_kwargs={},
            result_dict={},
            ts=1000.0,
        )
        assert payload.raw_content is None
        # raw_response 应是 "{}" 或合法 JSON
        assert payload.raw_response is not None
        parsed = json.loads(payload.raw_response)
        assert isinstance(parsed, dict)

    def test_empty_result_dict_tool_path_does_not_raise(self) -> None:
        tc = {"id": "x", "type": "function", "function": {"name": "foo", "arguments": "{}"}}
        payload = build_tool_call_payload(
            task_type="np",
            tc=tc,
            gen_kwargs={},
            result_dict={},
            ts=1000.0,
        )
        assert payload.tool_name == "foo"
        assert payload.raw_content is None

    def test_ts_defaults_to_now_when_not_provided(self) -> None:
        import time
        before = time.time()
        payload = build_tool_call_payload(
            task_type="np",
            tc=None,
            gen_kwargs={},
            result_dict={},
        )
        after = time.time()
        assert before <= payload.ts <= after
