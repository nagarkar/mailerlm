"""`FakePeople`: an in-memory peoplelm behind the `Transport` port, for tests.

It answers the methods mailerlm calls with peoplelm's parameter names and result shapes (the
contract in peoplelm's `api.py`). The send guard models do-not-contact, channel blocks and the
task state; the timing windows (awaiting a reply, messages per window) are not modelled, and a
test blocks a person by hand with `guard_blocks`. `down = True` makes every call fail as an
unreachable peoplelm does.

Task moves follow peoplelm's `MOVES`, except that a `mailing-list` task may go from accepted to
rejected (a subscriber who unsubscribes or bounces), which the owner's mapping requires.
"""

from __future__ import annotations

from typing import Any

from mailerlm.people import DOCTOR
from mailerlm.people.client import PeopleError, PeopleUnavailable

MOVES = {
    "proposed": ("requested", "dropped"),
    "dropped": ("proposed",),
    "requested": ("accepted", "rejected"),
    "accepted": ("rejected",),
    "rejected": (),
}
MAIN_PATH = ("proposed", "requested", "accepted")


class FakePeople:
    def __init__(self) -> None:
        self.persons: dict[int, dict[str, Any]] = {}
        self.tasks: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.touches: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.guard_blocks: dict[int, str] = {}
        self.down = False

    def call(self, method: str, params: dict[str, Any]) -> Any:
        if self.down:
            raise PeopleUnavailable(f"peoplelm does not answer. {DOCTOR}")
        self.calls.append(method)
        handler = getattr(self, f"_{method}", None)
        if handler is None:
            raise PeopleError(f"peoplelm: unknown method {method}")
        return handler(**params)

    # persons
    def _record(self, person_id: int) -> dict[str, Any]:
        if person_id not in self.persons:
            raise PeopleError(f"peoplelm: no person {person_id}")
        person = self.persons[person_id]
        return {
            "id": person_id,
            "display_name": person["display_name"],
            "do_not_contact": person["do_not_contact"],
            "blocked_channels": sorted(person["blocks"]),
            "identifiers": [dict(i) for i in person["identifiers"]],
        }

    def _owner(self, channel: str, value: str) -> int | None:
        value = value.strip().lower() if channel == "email" else value.strip()
        for person_id, person in self.persons.items():
            if any(i["channel"] == channel and i["value"] == value for i in person["identifiers"]):
                return person_id
        return None

    def _add_identifier(self, person_id: int, channel: str, value: str) -> None:
        value = value.strip().lower() if channel == "email" else value.strip()
        identifiers = self.persons[person_id]["identifiers"]
        if any(i["channel"] == channel and i["value"] == value for i in identifiers):
            return
        primary = not any(i["channel"] == channel for i in identifiers)
        identifiers.append({"channel": channel, "value": value, "is_primary": primary})

    def add_person(self, email: str, name: str = "", do_not_contact: bool = False) -> int:
        """A person peoplelm knows from elsewhere (a test's setup)."""
        person_id = len(self.persons) + 1
        self.persons[person_id] = {
            "display_name": name,
            "do_not_contact": do_not_contact,
            "blocks": set(),
            "identifiers": [],
        }
        self._add_identifier(person_id, "email", email)
        return person_id

    def _person_resolve(
        self, identifiers: list[list[str]], name: str = "", source: str = "api", about: str = ""
    ) -> dict[str, Any]:
        found = next((p for c, v in identifiers if (p := self._owner(c, v)) is not None), None)
        created = found is None
        if found is None:
            found = len(self.persons) + 1
            self.persons[found] = {
                "display_name": name,
                "do_not_contact": False,
                "blocks": set(),
                "identifiers": [],
            }
        elif name and not self.persons[found]["display_name"]:
            self.persons[found]["display_name"] = name
        for channel, value in identifiers:
            self._add_identifier(found, channel, value)
        return {"person": self._record(found), "created": created}

    def _person_get(self, person_id: int) -> dict[str, Any]:
        record = self._record(person_id)
        record["tasks"] = [dict(t) for t in self.tasks.values() if t["person_id"] == person_id]
        record["touches"] = [t for t in self.touches if t["person_id"] == person_id]
        return record

    def _person_lookup(self, channel: str, value: str) -> dict[str, Any] | None:
        found = self._owner(channel, value)
        return None if found is None else self._record(found)

    def _person_update(self, person_id: int, display_name: str | None = None, **_: Any) -> dict:
        if display_name is not None:
            self.persons[person_id]["display_name"] = display_name
        return self._record(person_id)

    def _person_identify(
        self, person_id: int, channel: str, value: str, source: str = "owner", **_: Any
    ) -> dict:
        holder = self._owner(channel, value)
        if holder is not None and holder != person_id:
            raise PeopleError(f"peoplelm: {value} belongs to person {holder}")
        self._add_identifier(person_id, channel, value)
        return self._record(person_id)

    def _person_primary(self, person_id: int, channel: str, value: str) -> dict:
        value = value.strip().lower() if channel == "email" else value
        for identifier in self.persons[person_id]["identifiers"]:
            if identifier["channel"] == channel:
                identifier["is_primary"] = identifier["value"] == value
        return self._record(person_id)

    def _person_block_channel(
        self, person_id: int, channel: str, reason: str, source: str = "owner"
    ) -> dict:
        self.persons[person_id]["blocks"].add(channel)
        return self._record(person_id)

    def _person_unblock_channel(self, person_id: int, channel: str) -> dict:
        self.persons[person_id]["blocks"].discard(channel)
        return self._record(person_id)

    # tasks
    def _find(self, person_id: int, purpose: str, scope: str) -> dict[str, Any] | None:
        return next(
            (
                t
                for t in self.tasks.values()
                if (t["person_id"], t["purpose"], t["scope"]) == (person_id, purpose, scope)
            ),
            None,
        )

    def _task_propose(
        self, person_id: int, purpose: str, scope: str = "", by: str = "owner", note: str = ""
    ) -> dict[str, Any]:
        self._record(person_id)
        existing = self._find(person_id, purpose, scope)
        if existing is not None:
            return {"task": dict(existing), "created": False}
        task_id = len(self.tasks) + 1
        self.tasks[task_id] = {
            "id": task_id,
            "person_id": person_id,
            "purpose": purpose,
            "scope": scope,
            "state": "proposed",
        }
        self.events.append({"task_id": task_id, "to": "proposed", "by": by, "note": note})
        return {"task": dict(self.tasks[task_id]), "created": True}

    def _task_find(self, person_id: int, purpose: str, scope: str = "") -> dict | None:
        found = self._find(person_id, purpose, scope)
        return dict(found) if found else None

    def _task_move(self, task_id: int, to_state: str, by: str = "owner", note: str = "") -> dict:
        task = self.tasks[task_id]
        if to_state not in MOVES[task["state"]]:
            raise PeopleError(f"peoplelm: task {task_id} is {task['state']}")
        self.events.append(
            {"task_id": task_id, "from": task["state"], "to": to_state, "by": by, "note": note}
        )
        task["state"] = to_state
        return dict(task)

    def _task_advance(self, task_id: int, to_state: str, by: str = "owner", note: str = "") -> dict:
        task = self.tasks[task_id]
        if to_state == "rejected" and task["state"] == "proposed":
            self._task_move(task_id, "requested", by, note)
            return self._task_move(task_id, "rejected", by, note)
        if task["state"] not in MAIN_PATH or to_state not in MAIN_PATH:
            return self._task_move(task_id, to_state, by, note)
        while MAIN_PATH.index(task["state"]) < MAIN_PATH.index(to_state):
            self._task_move(task_id, MAIN_PATH[MAIN_PATH.index(task["state"]) + 1], by, note)
        return dict(task)

    def _scope_rename(self, purpose: str, old: str, new: str) -> dict:
        moved = 0
        for task in self.tasks.values():
            if task["purpose"] == purpose and task["scope"] == old:
                task["scope"] = new
                moved += 1
        return {"renamed": moved}

    def _audience(
        self, purpose: str, scope: str | None = None, states: list[str] | None = None
    ) -> list[dict]:
        out = []
        for task in self.tasks.values():
            if task["purpose"] != purpose or (scope is not None and task["scope"] != scope):
                continue
            if states and task["state"] not in states:
                continue
            record = self._record(task["person_id"])
            emails = [
                i["value"]
                for i in record["identifiers"]
                if i["channel"] == "email" and i["is_primary"]
            ]
            out.append(
                {
                    "person_id": record["id"],
                    "name": record["display_name"],
                    "email": emails[0] if emails else None,
                    "task_id": task["id"],
                    "scope": task["scope"],
                    "state": task["state"],
                    "do_not_contact": record["do_not_contact"],
                    "blocked_channels": record["blocked_channels"],
                }
            )
        return out

    # the send guard and touches
    def _guard_check(
        self,
        person_id: int,
        purpose: str | None = None,
        scope: str = "",
        kind: str = "message",
        at: str | None = None,
        channel: str | None = None,
    ) -> dict:
        person = self.persons[person_id]
        if person["do_not_contact"]:
            return {
                "allowed": False,
                "person_id": person_id,
                "reasons": ["the person is do-not-contact"],
            }
        if channel and channel in person["blocks"]:
            return {
                "allowed": False,
                "person_id": person_id,
                "reasons": [f"the person is do-not-contact on {channel}"],
            }
        reasons = []
        if purpose:
            task = self._find(person_id, purpose, scope)
            if task is None:
                reasons.append(f"no {purpose} task for {scope}: propose it first")
            elif task["state"] in ("dropped", "rejected", "completed"):
                reasons.append(f"the {purpose} task is {task['state']}")
        if person_id in self.guard_blocks:
            reasons.append(self.guard_blocks[person_id])
        return {"allowed": not reasons, "person_id": person_id, "reasons": reasons}

    def _guard_send(
        self,
        person_id: int,
        channel: str,
        producer: str,
        summary: str = "",
        purpose: str | None = None,
        scope: str = "",
        kind: str = "message",
        ref: str = "",
        at: str | None = None,
        override: str | None = None,
    ) -> dict:
        verdict = self._guard_check(person_id, purpose, scope, kind, at, channel)
        if not verdict["allowed"] and not override:
            raise PeopleError("peoplelm: the send guard says no: " + "; ".join(verdict["reasons"]))
        self.touches.append(
            {
                "person_id": person_id,
                "direction": "out",
                "channel": channel,
                "producer": producer,
                "summary": summary,
                "purpose": purpose,
                "scope": scope,
                "ref": ref,
            }
        )
        if purpose:
            task = self._find(person_id, purpose, scope)
            if task and task["state"] == "proposed":
                self._task_move(task["id"], "requested", producer, "touch")
        return {"touch_id": len(self.touches), **verdict}

    def _touch_log(
        self,
        person_id: int,
        channel: str,
        direction: str,
        kind: str,
        producer: str,
        summary: str = "",
        purpose: str | None = None,
        scope: str = "",
        ref: str = "",
        at: str | None = None,
    ) -> dict:
        if direction == "out":
            raise PeopleError("peoplelm: log an outbound message with guard_send")
        self.touches.append(
            {
                "person_id": person_id,
                "direction": direction,
                "channel": channel,
                "kind": kind,
                "producer": producer,
                "ref": ref,
            }
        )
        return {"touch_id": len(self.touches)}

    # what tests read
    def state(self, person_id: int, list_name: str) -> str | None:
        task = self._find(person_id, "mailing-list", list_name)
        return task["state"] if task else None
