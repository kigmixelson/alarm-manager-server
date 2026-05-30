import pytest

from alarm_manager_server.models.incident import Incident, IncidentOwner
from alarm_manager_server.saymon.response import SaymonResponseError, coerce_json_list


def test_coerce_json_list_plain():
    assert coerce_json_list([{"id": "1"}]) == [{"id": "1"}]


def test_coerce_json_list_wrapped():
    assert coerce_json_list({"data": [{"id": "1"}]}) == [{"id": "1"}]


def test_coerce_json_list_invalid():
    with pytest.raises(SaymonResponseError):
        coerce_json_list({"ok": True})


def test_owner_accepts_id_field():
    owner = IncidentOwner.model_validate({"id": "abc", "name": "n"})
    assert owner.id == "abc"


def test_owner_class_id_as_object_id_string():
    owner = IncidentOwner.model_validate(
        {
            "_id": "obj1",
            "name": "Host",
            "class_id": "6633459c134e4ff90521068c",
        }
    )
    assert owner.class_id == "6633459c134e4ff90521068c"


def test_from_api_owner_with_id_not_underscore():
    inc = Incident.from_api(
        {
            "id": "inc-1",
            "entityId": "e1",
            "state": 1,
            "lastState": 2,
            "owner": {"id": "owner-1", "name": "Host", "parent_id": "p1"},
        }
    )
    assert inc.owner is not None
    assert inc.owner.id == "owner-1"
    assert inc.owner.parent_id == ["p1"]
