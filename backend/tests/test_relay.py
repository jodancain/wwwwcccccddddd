import unittest

from app.agent.service import WechatAgentService
from app.api.relay import _clean_relay_user_text


class RelayInputTests(unittest.TestCase):
    def test_strips_openclaw_context_envelope(self):
        text = """[Wed 2026-09-23 05:41 PDT] Conversation info (untrusted metadata):
```json
{"chat_id":"example@im.wechat","message_id":"openclaw-weixin:123"}
```

迁移测试"""

        self.assertEqual(_clean_relay_user_text(text), "迁移测试")

    def test_keeps_normal_user_text(self):
        self.assertEqual(_clean_relay_user_text("总结最近聊天记录"), "总结最近聊天记录")

    def test_connectivity_test_detection_is_narrow(self):
        self.assertTrue(WechatAgentService._is_connectivity_test("迁移测试"))
        self.assertTrue(WechatAgentService._is_connectivity_test("Ping"))
        self.assertFalse(WechatAgentService._is_connectivity_test("测试每日总结功能"))


if __name__ == "__main__":
    unittest.main()
