MSG_TYPES = {
    1: "text",
    3: "image",
    34: "voice",
    42: "contact_card",
    43: "video",
    47: "emoji",
    48: "location",
    49: "link",
    50: "voip",
    10000: "system",
    10002: "recall",
}


def get_type_name(type_code: int) -> str:
    return MSG_TYPES.get(type_code, f"unknown_{type_code}")
