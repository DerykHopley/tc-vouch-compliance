"""The click-burst guard's timing, for the browser checks (#527, #562)."""

DOUBLE_CLICK_MS = 500
"""The front end's DOUBLE_CLICK_MS: after a pointer click on an in-app ask's confirm or the chat's
Close, a click anywhere within that long of the burst's last click presses nothing, even once the
ask has left the page. A person's next click comes after it."""
