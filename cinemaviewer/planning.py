"""Choosing the moment for a planned intermission halfway through a film."""

MIN_FILM_MS = 20 * 60_000           # shorter content (episodes, shorts) gets no planned break
CHAPTER_MARGIN_MS = 3000            # pause this long before the new chapter: stream positions arrive in steps
CREDIT_WORDS = ("credit", "aftitel", "end title", "générique", "generique", "abspann")


def estimate_credits(length_ms):
    """Rough length of the end credits when the source doesn't tell us: 6%, 1 to 10 minutes."""
    return min(10 * 60_000, max(60_000, int(length_ms * 0.06)))


def plan_intermission(length_ms, credits_start_ms=None, chapters=()):
    """Return (pause_ms, reason) or (None, reason).

    The break lands halfway between the start and the beginning of the end credits and, when a
    chapter starts close to that point, just before that chapter so it falls between two scenes.
    `chapters` is a sequence of (start_ms, name).
    """
    if length_ms < MIN_FILM_MS:
        return None, "te kort voor een pauze"

    if credits_start_ms and length_ms * 0.5 < credits_start_ms < length_ms:
        end, reason = credits_start_ms, "aftiteling volgens de server"
    else:
        named = [s for s, name in chapters
                 if s > length_ms * 0.6 and any(w in (name or "").lower() for w in CREDIT_WORDS)]
        if named:
            end, reason = min(named), "hoofdstuk aftiteling"
        else:
            end, reason = length_ms - estimate_credits(length_ms), "geschatte aftiteling"

    middle = end / 2
    window = max(4 * 60_000, end * 0.05)
    nearby = [s for s, _name in chapters if 0 < s < end and abs(s - middle) <= window]
    if nearby:
        chapter = min(nearby, key=lambda s: abs(s - middle))
        return int(max(0, chapter - CHAPTER_MARGIN_MS)), reason + ", bij hoofdstukwissel"
    return int(middle), reason
