from ibm_watsonx_orchestrate.agent_builder.tools.python_tool import tool


@tool
def echo_message(message: str, uppercase: bool = False) -> str:
    """
    受け取ったメッセージを加工して返すテストツール

    Args:
        message: 加工したいテキスト
        uppercase: Trueにすると大文字に変換する

    Returns:
        加工後のメッセージ
    """
    if uppercase:
        return f"[ECHO] {message.upper()}"
    return f"[ECHO] {message}"
