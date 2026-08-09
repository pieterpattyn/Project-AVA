"""Project AVA v0.8.1 - robust natural-language memory on top of v0.8.

This wrapper deliberately keeps realtime_tools_app.py unchanged as the known-good
v0.8 tools baseline. It patches only the memory-language edge cases found during
live testing and tightens tool-call speech behaviour.
"""

import re

import realtime_tools_app as base


_ORIGINAL_MEMORY_PROCESSOR = base.process_memory_request
_ORIGINAL_MEMORY_LOAD = base.LocalMemory.load

_FILLER_FACTS = {
    "goed",
    "dat goed",
    "dit goed",
    "het goed",
    "goed onthouden",
}


def _normalise_fact(text):
    return text.strip(" .,!?:;\"'").casefold()


def _remove_filler_facts(memory):
    old = list(memory.data.get("facts", []))
    new = [fact for fact in old if _normalise_fact(str(fact)) not in _FILLER_FACTS]
    if new != old:
        memory.data["facts"] = new
        memory.save()
        print("Memory cleanup: betekenisloos legacy-feit verwijderd.")
        return True
    return False


def _patched_memory_load(self):
    _ORIGINAL_MEMORY_LOAD(self)
    _remove_filler_facts(self)


def _clean_location(location):
    cleaned = location.strip(" .,!?:;\"'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:]


def _extract_residence(text):
    patterns = (
        # "Hooglede is de gemeente waar ik woon"
        r"\b([A-Za-zÀ-ÖØ-öø-ÿ'’-]{2,40})\s+is\s+(?:de\s+)?"
        r"(?:gemeente|plaats|stad|dorp)\s+waar\s+ik\s+woon\b",
        # "Ik woon in Hooglede" / "Ik woon te Hooglede"
        r"\bik\s+woon\s+(?:in|te)\s+"
        r"([A-Za-zÀ-ÖØ-öø-ÿ'’ -]{2,60}?)(?=[.!?,]|\s+(?:en|maar|want)\b|$)",
        # "Mijn woonplaats is Hooglede"
        r"\bmijn\s+woonplaats\s+is\s+"
        r"([A-Za-zÀ-ÖØ-öø-ÿ'’ -]{2,60}?)(?=[.!?,]|\s+(?:en|maar|want)\b|$)",
    )

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean_location(match.group(1))
    return ""


def _set_residence(memory, location):
    canonical = f"ik woon in {location}"
    old = list(memory.data.get("facts", []))

    # Residence is a single-valued fact. Replace an older residence rather than
    # accumulating several hometowns as if the user were running for office.
    new = []
    for fact in old:
        normalised = _normalise_fact(str(fact))
        if normalised in _FILLER_FACTS:
            continue
        if re.match(r"^(?:ik|de gebruiker)\s+woon\w*\s+(?:in|te)\b", normalised):
            continue
        if normalised.startswith("mijn woonplaats is "):
            continue
        new.append(str(fact).strip())

    if canonical.casefold() not in {fact.casefold() for fact in new}:
        new.append(canonical)

    changed = new != old
    if changed:
        memory.data["facts"] = new
        memory.save()
    return changed


def _extract_favorite_change(text):
    # Handles natural variants missed by v0.7.2, notably:
    # "Ik wil mijn favoriete kleur nogmaals veranderen naar blauw."
    match = re.search(
        r"\bmijn\s+favoriete\s+"
        r"([\wÀ-ÖØ-öø-ÿ' -]{2,40}?)\s+"
        r"(?:nogmaals\s+|opnieuw\s+|weer\s+)?"
        r"(?:veranderen|wijzigen|aanpassen|verander|wijzig|pas)\s+"
        r"(?:naar|in)\s+(.+?)(?=[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def process_memory_request_v081(transcript, memory):
    actions = list(_ORIGINAL_MEMORY_PROCESSOR(transcript, memory) or [])

    # v0.8 could interpret "Onthoud dat goed" as the literal fact "goed".
    # Remove that artefact both from storage and from the user-facing action log.
    _remove_filler_facts(memory)
    actions = [
        action
        for action in actions
        if not re.search(
            r"feit (?:opgeslagen|stond al in geheugen):\s*(?:goed|dat goed|dit goed)[.!]?$",
            action,
            re.IGNORECASE,
        )
    ]

    residence = _extract_residence(transcript)
    if residence:
        changed = _set_residence(memory, residence)
        residence_action = (
            f"woonplaats aangepast naar {residence}"
            if changed
            else f"woonplaats bevestigd: {residence}"
        )
        if residence_action not in actions:
            actions.append(residence_action)

    favorite_change = _extract_favorite_change(transcript)
    if favorite_change:
        topic, value = favorite_change
        changed = memory.set_preference(topic, value)
        action = (
            f"favoriete {topic} aangepast naar {value}"
            if changed
            else f"favoriete {topic} stond al op {value}"
        )
        # Avoid duplicate actions when a future base parser learns this phrasing.
        if not any(
            existing.casefold().startswith(f"favoriete {topic}".casefold())
            for existing in actions
        ):
            actions.append(action)

    return actions


# Patch only the extension points used dynamically by realtime_tools_app.
base.LocalMemory.load = _patched_memory_load
base.process_memory_request = process_memory_request_v081
base.TOOL_INSTRUCTIONS += """
Als een tool nodig is, roep de tool meteen aan zonder eerst een gesproken tussenzin zoals 'ik check even' of 'momentje'.
Spreek pas nadat het toolresultaat beschikbaar is, zodat de gebruiker één compact antwoord hoort.
Als de geheugenlaag een woonplaats meldt, mag je die gebruiken voor vragen als 'het weer bij mij'.
Als een lokale geheugenactie bevestigd is, zeg nooit dat je die wijziging niet blijvend kunt onthouden.
"""


if __name__ == "__main__":
    raise SystemExit(base.main())
