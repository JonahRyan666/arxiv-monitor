import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("FEISHU_WEBHOOK_URL", "https://example.invalid")
os.environ.setdefault("SILICONFLOW_API_KEY", "test-key")

import arxiv_daily_report as monitor


def response(status_code, content="", text=""):
    result = Mock(status_code=status_code, text=text)
    result.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return result


class TranslationFallbackTests(unittest.TestCase):
    def setUp(self):
        monitor.SILICONFLOW_MODEL = "tencent/Hunyuan-MT-7B"
        monitor.SILICONFLOW_FALLBACK_MODEL = "Qwen/Qwen3.5-9B"
        monitor.SILICONFLOW_RETRIES = 0
        monitor.TRANSLATION_AUTH_FAILED = False

    @patch.object(monitor.requests, "post")
    def test_primary_model_translates_both_fields(self, post):
        post.side_effect = [
            response(200, "Kitaev链中的分数化激发"),
            response(200, "本文研究了量子自旋液体中的分数化激发及其动力学磁响应。"),
        ]

        result = monitor.summarize_with_siliconflow(
            "Fractional excitations in a Kitaev chain",
            "We study fractional excitations and their dynamical magnetic response.",
        )

        self.assertTrue(monitor.is_usable_chinese_summary(result))
        self.assertEqual(
            [call.kwargs["json"]["model"] for call in post.call_args_list],
            ["tencent/Hunyuan-MT-7B", "tencent/Hunyuan-MT-7B"],
        )

    @patch.object(monitor.requests, "post")
    def test_fallback_model_is_used_after_primary_failure(self, post):
        post.side_effect = [
            response(503, text="temporarily unavailable"),
            response(200, "Kitaev链中的分数化激发"),
            response(503, text="temporarily unavailable"),
            response(200, "本文研究了量子自旋液体中的分数化激发及其动力学磁响应。"),
        ]

        result = monitor.summarize_with_siliconflow(
            "Fractional excitations in a Kitaev chain",
            "We study fractional excitations and their dynamical magnetic response.",
        )

        self.assertTrue(monitor.is_usable_chinese_summary(result))
        self.assertEqual(
            [call.kwargs["json"]["model"] for call in post.call_args_list],
            [
                "tencent/Hunyuan-MT-7B",
                "Qwen/Qwen3.5-9B",
                "tencent/Hunyuan-MT-7B",
                "Qwen/Qwen3.5-9B",
            ],
        )
        self.assertFalse(post.call_args_list[1].kwargs["json"]["enable_thinking"])

    @patch.object(monitor.requests, "post")
    def test_invalid_key_stops_without_trying_fallback(self, post):
        post.return_value = response(401, text="invalid token")

        result = monitor.summarize_with_siliconflow(
            "Test title",
            "This is a real abstract with enough text for translation.",
        )

        self.assertIsNone(result)
        self.assertTrue(monitor.TRANSLATION_AUTH_FAILED)
        self.assertEqual(post.call_count, 1)

    @patch.object(monitor.requests, "post")
    def test_non_chinese_primary_result_uses_fallback(self, post):
        post.side_effect = [
            response(200, "Fractional excitations in a Kitaev chain"),
            response(200, "Kitaev链中的分数化激发"),
            response(200, "本文研究了量子自旋液体中的分数化激发及其动力学磁响应。"),
        ]

        result = monitor.summarize_with_siliconflow(
            "Fractional excitations in a Kitaev chain",
            "We study fractional excitations and their dynamical magnetic response.",
        )

        self.assertTrue(monitor.is_usable_chinese_summary(result))
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "Qwen/Qwen3.5-9B")


if __name__ == "__main__":
    unittest.main()
