"""Browser dialogs (#441): the app asks in its own controls, so a check records every dialog that
opens (dismissing it) and asserts none did."""

from playwright.sync_api import Dialog, Page


def dialogs_of(page: Page) -> list[str]:
    """Every browser dialog that opens on `page` from now on, dismissed and recorded."""
    opened: list[str] = []

    def dismiss(dialog: Dialog) -> None:
        opened.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", dismiss)
    return opened


def no_dialog(opened: list[str]) -> None:
    """None opened: a browser dialog in place of the in-app ask fails here, by name, rather
    than as a wait that times out."""
    assert opened == [], f"a browser dialog opened instead of the in-app ask: {opened}"
