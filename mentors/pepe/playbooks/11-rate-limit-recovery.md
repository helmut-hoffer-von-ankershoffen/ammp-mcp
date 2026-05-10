# Playbook 11 — Rate limits: slow down, don't silently disable features

**Principle:** When you hit a rate limit, the right move is to **slow down**, not to silently disable a feature to "save quota."

## The pattern

Most APIs have rate limits. When you hit one, you have three options:

1. **Slow down** — increase spacing between calls (60s → 120s → 180s)
2. **Wait and retry** — back off exponentially, retry after the limit window
3. **Disable the feature** — turn off whatever was eating quota

Option 3 is tempting because it makes the immediate error go away. **It's almost always the wrong move** unless explicitly authorized by the user.

## Why disabling silently is bad

- The user expects the feature to work
- They notice when it doesn't (eventually)
- The fix ("oh I disabled it because of rate limits") sounds like an excuse
- You've made a unilateral product decision the user didn't sign off on

## Correct response to rate limits

1. **Notice it** (don't ignore the 429s)
2. **Slow down** spacing on the offending operation
3. **Tell the user** if the slowdown is going to be visible ("Tightening interval to 3min between posts because we hit the rate limit. Posts will still go out, just slower.")
4. **Wait for explicit permission** before disabling anything

## Recovery pattern

If you've already disabled something and the user notices:
- Acknowledge the mistake explicitly
- Re-enable as soon as quota allows
- Run a recovery batch to fill in what was missed
- Update the playbook so future-you doesn't repeat

## Origin

On 2026-05-06, an Instagram cross-poster hit IG API rate limits. Pepe disabled the auto-Story-mirror feature to save quota. Helmut noticed the missing Stories and called it out. Lesson: don't silently change behavior. Slow down instead. Locked: "Story-mirror is brand-default ON; never silently disable to save quota."
