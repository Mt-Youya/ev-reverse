# 06 — Read the API’s AES out of the player with an RPC oracle

**What to build:** The player’s own AES, callable from outside the process. A captured request body decrypts through it, and it accepts any buffer rather than one hard-coded message. The key is never recovered and never needs to be: the point is to stop needing it.

**Blocked by:** None — can start immediately

**Status:** ready-for-human

- [ ] A buffer can be handed to the player’s AES and the transformed buffer comes back.
- [ ] A captured request body decrypts to readable plaintext through it.
- [ ] The oracle accepts an arbitrary input, not one captured message.
- [ ] The command used and its output are recorded as the evidence.
