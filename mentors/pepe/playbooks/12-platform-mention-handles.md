# Playbook 12 — Platform-specific @-handles

**Principle:** The same person has different @-handles on different platforms. The handle in a post **must** match the platform.

## The pattern

When cross-posting between platforms (e.g. Instagram → X, LinkedIn → Twitter), if your post text contains an @-mention, **the @-handle must be the platform-correct one** for the destination — not the source.

## Example

Helmut's handles:
- Instagram: `@helmut.hoffer.von.ankershoffen` (dots)
- X (Twitter): `@h_ankershoffen`
- LinkedIn / GitHub: `@helmut-hoffer-von-ankershoffen` (hyphens)

If you post a reel on Instagram with caption `"...with @helmut.hoffer.von.ankershoffen"`, then cross-post to X without rewriting the handle, X readers see a broken mention pointing at no account (or worse, the wrong account).

## The fix

Build a handle-mapping table once, in code or in your memory:

```javascript
const HANDLE_MAP_IG_TO_X = {
  '@helmut.hoffer.von.ankershoffen': '@h_ankershoffen',
  // ... other people who appear in your content
}
```

Apply it in the cross-post pipeline.

## Verification step

After cross-posting, click the resulting post on the destination platform and verify the @-mention is clickable and goes to the right account. (See Playbook 01.)

## Anti-pattern

- ❌ "Just copy the IG caption to X verbatim. Saves time."
- ✅ Run the handle map. Verify the X mention resolves.

## Common gotchas

- Same person, different separators per platform (dots / hyphens / underscores)
- Brand names that exist on some platforms but not others
- Handles that look right but the account was migrated (X 2023+ rename situation)

## Origin

5 Pepe Arturo reels were cross-posted to X on 2026-05-06 with the IG handle `@helmut.hoffer.von.ankershoffen` left in. All 5 mentions broke (no such X account). Handle map added to the cross-poster `integrations/instagram/lib/crosspost.js` afterward.
