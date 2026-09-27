# Discord UI Guide

## Layout
- Far-left strip: server icons (circular)
- Left sidebar: channel list (prefixed #) or DM contact list (names only)
- "Direct Messages" section header is above the DM contact list
- Main area: chat messages for the selected channel/DM
- Top header bar: shows the currently open channel or contact name — this is display-only, NOT a button
- Bottom: message input field, labeled "Message @contactname" or "Message #channelname"

## Navigation — DMs
- To open a DM: click the contact's name in the LEFT SIDEBAR under "Direct Messages"
- After clicking, the chat opens and the contact's name appears in the TOP HEADER
- TOP HEADER = you are already inside the chat — do NOT click that name again
- The message input is now at the bottom of the screen

## Navigation — Servers/Channels
- Click a server icon on the far left to switch server
- Click a channel name (e.g., #general) in the left sidebar to open it
- After clicking, channel name appears in top header — you are inside, stop clicking it

## Typing and sending
- Click the message input at the bottom OR use type_text() if it's already focused
- Press enter to send

## Common element text labels (for smart_click)
- Message input: "Message @name" or "Message #channel"
- New DM button: pencil/edit icon at top of DM list (no text label — use smart_click("new DM button"))
- Search: "Find or start a conversation"
