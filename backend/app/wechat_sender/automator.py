"""UI automation to send messages through Weixin 4.x.

Weixin 4.x keeps the ``Ctrl+F`` search → enter → paste flow that WeChat 3.x
had, so the mechanics here are similar. The important change is how we find
the window: matching window title alone is brittle (4.x sometimes shows
"Weixin" in the title bar, sometimes nothing while minimised). We instead
look up the top-level window whose owning process is ``Weixin.exe`` (with a
fallback to the legacy ``WeChat.exe``).
"""
import time

from loguru import logger


class WeChatAutomator:
    _TARGET_EXES = {"weixin.exe", "wechat.exe"}

    def __init__(self) -> None:
        self._hwnd = None

    # ------------------------------------------------------------------
    def _enum_candidate_windows(self) -> list[int]:
        import win32gui
        import win32process
        import psutil  # shipped via pywxdump; available in venv

        results: list[tuple[int, int]] = []

        def cb(hwnd, _acc):
            if not win32gui.IsWindowVisible(hwnd):
                return
            try:
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:  # noqa: BLE001
                return
            try:
                name = psutil.Process(pid).name().lower()
            except Exception:  # noqa: BLE001
                return
            if name in self._TARGET_EXES:
                rect = win32gui.GetWindowRect(hwnd)
                area = max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])
                # Skip tiny utility windows (tray bubbles, popup tips)
                if area > 200 * 200:
                    results.append((hwnd, area))

        win32gui.EnumWindows(cb, None)
        results.sort(key=lambda x: x[1], reverse=True)
        return [h for h, _ in results]

    def find_wechat_window(self) -> bool:
        try:
            hwnds = self._enum_candidate_windows()
            if hwnds:
                self._hwnd = hwnds[0]
                return True
        except ImportError:
            logger.warning("pywin32 / psutil missing; WeChat automation unavailable")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to find Weixin window: {e}")
        return False

    # ------------------------------------------------------------------
    def send_text(self, contact_name: str, text: str) -> bool:
        try:
            import pyautogui
            import pyperclip
            import win32con
            import win32gui
        except ImportError as e:
            logger.error(f"Missing dependency for automation: {e}")
            return False

        if not self._hwnd or not win32gui.IsWindow(self._hwnd):
            if not self.find_wechat_window():
                logger.error("Weixin.exe window not found; is it running?")
                return False

        try:
            # Restore + focus
            win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(self._hwnd)
            time.sleep(0.3)

            # Open search
            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.3)

            # Paste contact name (use clipboard to avoid IME issues with CJK input)
            pyperclip.copy(contact_name)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.5)

            pyautogui.press("enter")
            time.sleep(0.4)

            # In Weixin 4.x the search popup sometimes auto-closes after Enter.
            # The Escape below is harmless if it's already gone.
            pyautogui.press("escape")
            time.sleep(0.15)

            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.2)

            pyautogui.press("enter")
            time.sleep(0.2)

            logger.info(f"Message sent to {contact_name}")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to send message: {e}")
            return False
