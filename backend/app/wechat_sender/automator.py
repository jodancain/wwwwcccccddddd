import time
import subprocess
from loguru import logger


class WeChatAutomator:
    def __init__(self):
        self._hwnd = None

    def find_wechat_window(self):
        try:
            import win32gui
            import win32con

            def callback(hwnd, results):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title == "微信":
                        results.append(hwnd)

            results = []
            win32gui.EnumWindows(callback, results)
            if results:
                self._hwnd = results[0]
                return True
        except ImportError:
            logger.warning("pywin32 not installed, WeChat automation unavailable")
        except Exception as e:
            logger.error(f"Failed to find WeChat window: {e}")
        return False

    def send_text(self, contact_name: str, text: str) -> bool:
        try:
            import win32gui
            import win32con
            import pyautogui
            import pyperclip

            if not self._hwnd:
                if not self.find_wechat_window():
                    return False

            # Bring WeChat to front
            win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(self._hwnd)
            time.sleep(0.3)

            # Open search (Ctrl+F)
            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.3)

            # Type contact name
            pyperclip.copy(contact_name)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.5)

            # Press Enter to select first result
            pyautogui.press("enter")
            time.sleep(0.3)

            # Clear search
            pyautogui.press("escape")
            time.sleep(0.2)

            # Type message
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.2)

            # Send
            pyautogui.press("enter")
            time.sleep(0.2)

            logger.info(f"Message sent to {contact_name}")
            return True

        except ImportError as e:
            logger.error(f"Missing dependency for WeChat automation: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False
