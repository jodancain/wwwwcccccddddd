from dataclasses import dataclass


@dataclass
class Contact:
    username: str
    nickname: str = ""
    remark: str = ""
    alias: str = ""
    is_group: int = 0


@dataclass
class WeChatMessage:
    local_id: int = 0
    msg_svr_id: str = ""
    talker: str = ""
    sender: str = ""
    type: int = 1
    type_name: str = "text"
    is_sender: int = 0
    content: str = ""
    display_content: str = ""
    create_time: int = 0
    create_date: str = ""
    is_group: int = 0

    def to_dict(self) -> dict:
        return {
            "wechat_local_id": self.local_id,
            "msg_svr_id": self.msg_svr_id,
            "talker": self.talker,
            "sender": self.sender,
            "type": self.type,
            "type_name": self.type_name,
            "is_sender": self.is_sender,
            "content": self.content,
            "display_content": self.display_content,
            "create_time": self.create_time,
            "create_date": self.create_date,
            "is_group": self.is_group,
        }
