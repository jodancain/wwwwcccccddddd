"""UI automation to send messages through Weixin 4.x.

Weixin 4.x keeps the ``Ctrl+F`` search → enter → paste flow that WeChat 3.x
had, so the mechanics here are similar. The important change is how we find
the window: matching window title alone is brittle (4.x sometimes shows
"Weixin" in the title bar, sometimes nothing while minimised). We instead
look up the top-level window whose owning process is ``Weixin.exe`` (with a
fallback to the legacy ``WeChat.exe``).
"""
import json
import os
import subprocess
import sys
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
                title = win32gui.GetWindowText(hwnd) or ""
                if "tray" in title.lower() or "ime" in title.lower():
                    return
                visible = bool(win32gui.IsWindowVisible(hwnd))
                likely_main = title in {"微信", "Weixin", "WeChat"} or visible
                # Skip tiny utility windows (tray bubbles, popup tips)
                if area > 200 * 200 and likely_main:
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

    def _focus_window(self) -> bool:
        import win32con
        import win32gui

        if not self._hwnd or not win32gui.IsWindow(self._hwnd):
            return False

        try:
            win32gui.ShowWindow(self._hwnd, win32con.SW_SHOW)
            win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)
            win32gui.SetForegroundWindow(self._hwnd)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Direct SetForegroundWindow failed: {exc}")

        try:
            import win32com.client

            shell = win32com.client.Dispatch("WScript.Shell")
            shell.SendKeys("%")
            time.sleep(0.1)
            win32gui.SetForegroundWindow(self._hwnd)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Alt-unlock SetForegroundWindow failed: {exc}")

        try:
            import win32api
            import win32process

            foreground = win32gui.GetForegroundWindow()
            current_thread = win32api.GetCurrentThreadId()
            target_thread, _target_pid = win32process.GetWindowThreadProcessId(self._hwnd)
            foreground_thread, _foreground_pid = win32process.GetWindowThreadProcessId(foreground)
            attached: list[int] = []
            for thread_id in {target_thread, foreground_thread}:
                if thread_id and thread_id != current_thread:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached.append(thread_id)
            try:
                win32gui.BringWindowToTop(self._hwnd)
                win32gui.SetActiveWindow(self._hwnd)
                win32gui.SetForegroundWindow(self._hwnd)
                return True
            finally:
                for thread_id in attached:
                    win32process.AttachThreadInput(current_thread, thread_id, False)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to focus Weixin window: {exc}")
            return False

    # ------------------------------------------------------------------
    def send_text(self, contact_name: str, text: str) -> bool:
        if self._send_text_direct(contact_name, text):
            return True
        if os.environ.get("WECHATAI_SEND_NO_SUBPROCESS") == "1" or getattr(sys, "frozen", False):
            return False
        return self._send_text_subprocess(contact_name, text)

    def _send_text_direct(self, contact_name: str, text: str) -> bool:
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
            if not self._focus_window():
                return False
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

    def _send_text_subprocess(self, contact_name: str, text: str) -> bool:
        code = (
            "import json, sys;"
            "from app.wechat_sender.automator import WeChatAutomator;"
            "payload=json.loads(sys.stdin.read());"
            "ok=WeChatAutomator().send_text(payload['contact_name'], payload['text']);"
            "print(json.dumps({'ok': ok}))"
        )
        env = os.environ.copy()
        env["WECHATAI_SEND_NO_SUBPROCESS"] = "1"
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                input=json.dumps({"contact_name": contact_name, "text": text}, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=45,
                env=env,
                cwd=os.getcwd(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0:
                logger.error(f"Weixin subprocess send failed: {proc.stderr[:500]}")
                return False
            payload = json.loads((proc.stdout or "{}").strip().splitlines()[-1])
            ok = bool(payload.get("ok"))
            if ok:
                logger.info(f"Message sent to {contact_name} via subprocess fallback")
            return ok
        except Exception as e:  # noqa: BLE001
            logger.error(f"Weixin subprocess send crashed: {e}")
            return False
