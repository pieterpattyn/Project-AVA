"""Project AVA v0.8.2 - canonical residence memory fixes.

Keeps v0.8 + v0.8.1 intact and patches only the residence edge cases found in
live testing: qualifier text such as "zonder n", end-letter corrections, and
canonical use of the stored home location for tools.
"""

import re

import realtime_tools_v081 as v081


base = v081.base
_ORIGINAL_V081_LOAD = base.LocalMemory.load
_ORIGINAL_V081_PROCESSOR = base.process_memory_request


def _residence_from_memory(memory):
    for fact in memory.data.get("facts", []):
        match = re.match(r"^ik\s+woon\s+in\s+(.+)$", str(fact).strip(), re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _canonical_location(location):
    cleaned = str(location).strip(" .,!?:;\"'")
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Natural corrections are instructions, not part of a place name.
    # "Hooglede zonder n" must therefore be stored as "Hooglede".
    cleaned = re.sub(
        r"\s*,?\s+(?:zonder|met)\s+[A-Za-zÀ-ÖØ-öø-ÿ]"
        r"(?:\s+(?:achteraan|op\s+het\s+einde|aan\s+het\s+einde))?\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    # If the user literally spells a place inside the captured location, prefer
    # those letters over the speech recogniser's phonetic guess.
    spelled = re.search(
        r"\b((?:[A-Za-zÀ-ÖØ-öø-ÿ]\s*-\s*){3,}"
        r"[A-Za-zÀ-ÖØ-öø-ÿ])\b",
        cleaned,
    )
    if spelled:
        cleaned = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", spelled.group(1))

    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:]


# v0.8.1's residence extractor resolves this helper dynamically, so replacing it
# here automatically canonicalises new "ik woon in ..." statements.
v081._clean_location = _canonical_location


def _repair_residence_facts(memory):
    old = list(memory.data.get("facts", []))
    new = []
    changed = False
    residence_seen = False

    for fact in old:
        text = str(fact).strip()
        match = re.match(r"^ik\s+woon\s+in\s+(.+)$", text, re.IGNORECASE)
        if not match:
            new.append(text)
            continue

        canonical_location = _canonical_location(match.group(1))
        if not canonical_location:
            changed = True
            continue

        canonical_fact = f"ik woon in {canonical_location}"
        if residence_seen:
            changed = True
            continue

        residence_seen = True
        new.append(canonical_fact)
        if canonical_fact != text:
            changed = True

    if changed:
        memory.data["facts"] = new
        memory.save()
        print("Memory migration: woonplaats opgeschoond.")


def _patched_memory_load_v082(self):
    _ORIGINAL_V081_LOAD(self)
    _repair_residence_facts(self)


def _apply_end_letter_correction(transcript, memory):
    current = _residence_from_memory(memory)
    if not current:
        return None

    # Example: stored "Hoogleden" + "dat is zonder n achteraan" => "Hooglede".
    remove_match = re.search(
        r"\bzonder\s+([A-Za-zÀ-ÖØ-öø-ÿ])\s+"
        r"(?:achteraan|op\s+het\s+einde|aan\s+het\s+einde)\b",
        transcript,
        re.IGNORECASE,
    )
    if remove_match:
        letter = remove_match.group(1)
        if current.casefold().endswith(letter.casefold()):
            corrected = current[:-1]
            changed = v081._set_residence(memory, corrected)
            return (
                f"woonplaats aangepast naar {corrected}"
                if changed
                else f"woonplaats bevestigd: {corrected}"
            )

    add_match = re.search(
        r"\bmet\s+([A-Za-zÀ-ÖØ-öø-ÿ])\s+"
        r"(?:achteraan|op\s+het\s+einde|aan\s+het\s+einde)\b",
        transcript,
        re.IGNORECASE,
    )
    if add_match:
        letter = add_match.group(1)
        if not current.casefold().endswith(letter.casefold()):
            corrected = current + letter.lower()
            changed = v081._set_residence(memory, corrected)
            return (
                f"woonplaats aangepast naar {corrected}"
                if changed
                else f"woonplaats bevestigd: {corrected}"
            )

    return None


def process_memory_request_v082(transcript, memory):
    actions = list(_ORIGINAL_V081_PROCESSOR(transcript, memory) or [])

    correction = _apply_end_letter_correction(transcript, memory)
    if correction:
        actions = [action for action in actions if not action.startswith("woonplaats ")]
        actions.append(correction)

    # A v0.8.1 statement may already have written a residence with a qualifier.
    # Repair it immediately instead of waiting for the next restart.
    before = _residence_from_memory(memory)
    canonical = _canonical_location(before)
    if before and canonical and canonical != before:
        changed = v081._set_residence(memory, canonical)
        if changed:
            actions = [action for action in actions if not action.startswith("woonplaats ")]
            actions.append(f"woonplaats aangepast naar {canonical}")

    return actions


base.LocalMemory.load = _patched_memory_load_v082
base.process_memory_request = process_memory_request_v082
base.TOOL_INSTRUCTIONS += """
Voor vragen als 'wat is het weer bij mij' gebruik je de opgeslagen woonplaats exact zoals die in de persistente geheugencontext staat.
Voeg geen letters of woorden aan een opgeslagen plaatsnaam toe en gebruik correctietekst zoals 'zonder n' nooit als onderdeel van de plaatsnaam.
Als een tool nodig is, roep die liefst meteen aan en geef daarna één compact gesproken antwoord.
"""


if __name__ == "__main__":
    raise SystemExit(base.main())
