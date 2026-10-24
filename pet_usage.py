"""Codex Pet desktop launcher."""
if __name__ == "__main__":
    try:
        from pet_desktop import main
        main()
    except Exception:
        import ctypes
        import traceback
        from pathlib import Path
        log = Path(__file__).with_name("startup-error.log")
        log.write_text(traceback.format_exc(), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, "桌宠启动失败，请查看同目录 startup-error.log。", "Codex Pet", 16)
